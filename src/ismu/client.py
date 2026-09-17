"""Typed read-only operations. No registration, posting or test-attempt methods."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from . import models
from . import parsers as p
from .errors import AuthRequired, InvalidInput, ParseError, RateLimited, UnsupportedOperation
from .ropot import RopotSession
from .services import StudentServices
from .transport import BASE, ISMUError, Transport, private_write


def semicolon_query(values):
    return ";".join(
        quote(str(k), safe="") + "=" + quote(str(v), safe="") for k, v in values.items()
    )


class Client(StudentServices):
    """Read one account's current enrolments through a private HTTP session.

    Selectors are optional IS MU IDs, discovered from the student page when
    omitted. Use a fresh Client for each polling run to refresh course membership.
    This class and its requests.Session are not intended for concurrent use.
    """

    def __init__(
        self,
        state_dir: str | Path = ".ismu-state",
        *,
        study: str | int | None = None,
        period: str | int | None = None,
        faculty: str | int | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.transport = transport or Transport(state_dir)
        self.context = {
            k: str(v)
            for k, v in [("studium", study), ("obdobi", period), ("fakulta", faculty)]
            if v is not None
        }
        self._courses = None

    def login(self, username: str, password: str) -> models.LoginResult:
        """Sign in normally; retain cookies, never the password.

        An existing authenticated session is reused, not switched to username.
        Use a separate state directory for a different account. The course cache
        is invalidated even if the login fails.
        """
        if not isinstance(username, str) or not username.strip():
            raise InvalidInput("username must be a non-empty string.")
        if not isinstance(password, str) or not password:
            raise InvalidInput("password must be a non-empty string.")
        self._courses = None
        return self.transport.login(username, password)

    def ropot(self, item: models.Ropot | str, *, allow_writes: bool = False) -> RopotSession:
        """Create an attempt interface from a listed item or plain qref path.

        Creation makes no request and starts no attempt. Call inspect explicitly.
        """
        path = item.get("qref_path") if isinstance(item, dict) else item
        if not isinstance(path, str) or not path:
            raise InvalidInput("The selected ROPOT has no usable qref_path.")
        return RopotSession(self.transport.state_dir, path, allow_writes=allow_writes)

    def status(self) -> models.Status:
        """Verify the live session and refresh the selected course index."""
        r = self.transport.get(
            "/auth/student/predmety" + ("?" + semicolon_query(self.context) if self.context else "")
        )
        context, items = p.course_index(r.text)
        self.context = context or self.context
        self._courses = items
        return {
            "authenticated": True,
            "course_count": len(items),
            "context": self.context.copy(),
            "browser_required": False,
        }

    def courses(self, *, details: bool = True) -> list[models.Course]:
        """List selected enrolments, optionally loading each course's panel."""
        if self._courses is None:
            self.status()
        if details:
            for c in self._courses:
                if "faculty" not in c:
                    self._detail(c)
        return [self._public_course(c) for c in self._courses]

    @staticmethod
    def _public_course(course):
        # Callers may edit the returned data without changing later routing.
        return deepcopy({k: v for k, v in course.items() if k != "detail_params"})

    def _detail(self, c):
        values = c.get("detail_params")
        if (
            not values
            or values.get("option") != "predmet-option-info"
            or str(values.get("predmet_id")) != str(c["course_id"])
        ):
            raise ParseError("Cannot discover the read-only course detail request.")
        allowed = {"option", "vsechna_studia", "obdobi", "fakulta", "predmet_id", "studium"}
        values = {k: v for k, v in values.items() if k in allowed}
        url = BASE + "/auth/student/index_ajax?" + semicolon_query(values)
        r = self.transport.get(url)
        try:
            html = r.json()["html"]
        except (ValueError, KeyError, TypeError):
            raise ParseError("Course detail endpoint returned an unexpected response.") from None
        if not isinstance(html, str):
            raise ParseError("Course detail endpoint did not return HTML text.")
        c.update(p.course_detail(html, url))

    def course(self, code: str) -> models.Course:
        if self._courses is None:
            self.status()
        matches = [c for c in self._courses if c["code"] == code]
        if len(matches) != 1:
            raise InvalidInput("Course must identify exactly one current enrolment.")
        c = matches[0]
        if "faculty" not in c:
            self._detail(c)
        return self._public_course(c)

    def _root(self, code):
        c = self.course(code)
        return f"/el/{c['faculty']}/{c['semester']}/{c['code']}/"

    def _path(self, code, relative):
        if any(x in relative for x in ("?", "#", ";", "\\", "\x00")):
            raise InvalidInput("Supply a plain course-relative path without query parameters.")
        decoded = unquote(relative)
        if decoded != relative or relative.startswith("/") or ".." in PurePosixPath(relative).parts:
            raise InvalidInput("Path must stay inside the selected course.")
        return self._root(code) + relative

    def files(self, code: str) -> models.Files:
        """Return the native recursive XML inventory as nodes and variants."""
        root = self._root(code)
        url = BASE + "/auth/dok/fmgr_api?" + semicolon_query({"url": root, "strom": 1})
        r = self.transport.get(url)
        nodes = p.file_tree(r.text)
        return {
            "course": code,
            "source_url": url,
            "format": "native_xml",
            "nodes": nodes,
            "node_count": len(nodes),
            "variant_count": sum(len(n["variants"]) for n in nodes),
        }

    def read_file(
        self, code: str, relative: str, *, destination: str | Path | None = None
    ) -> models.FileRead:
        """Read text or save a bounded download under the selected course root.

        ``relative`` is a plain, unencoded path. Binary data requires a destination.
        Existing destination files are refused; e-learning attempt files are blocked.
        """
        path = self._path(code, relative)
        if not PurePosixPath(relative).suffix or PurePosixPath(relative).suffix.lower() in (
            ".qref",
            ".qdef",
            ".qwarp",
        ):
            raise UnsupportedOperation(
                "Use the metadata/syllabus commands for e-learning objects; attempts are not opened."
            )
        r = self.transport.get(BASE + "/auth" + quote(path, safe="/._-"))
        mime = r.headers.get("Content-Type", "").split(";")[0]
        if mime == "text/html" and p.soup(r.text).select_one("#app_header"):
            raise ParseError(
                "The file request returned an IS application/error page, not file content."
            )
        result = {
            "course": code,
            "path": path,
            "mime_type": mime,
            "bytes": len(r.content),
            "sha256": hashlib.sha256(r.content).hexdigest(),
            "source_url": r.url,
        }
        if destination:
            dest = Path(destination)
            if dest.exists():
                raise InvalidInput("Destination already exists; choose a new filename.")
            private_write(dest, r.content)
            result["saved_to"] = str(dest.resolve())
        elif mime.startswith("text/") or PurePosixPath(relative).suffix in (
            ".txt",
            ".hs",
            ".cs",
            ".py",
            ".tex",
        ):
            result["text"] = r.text
        else:
            raise UnsupportedOperation("This is a binary file. Use --output to save it.")
        return result

    def syllabus(self, code: str) -> models.Syllabus:
        """Read all exposed syllabus sections, preserving locked-section states."""
        c = self.course(code)
        entry = c.get("syllabus_entry_url")
        if not entry:
            return {"course": code, "state": "not_exposed", "sections": []}
        q = p.query(entry).get("qurl")
        if (
            not q
            and urlsplit(entry).hostname == "is.muni.cz"
            and urlsplit(entry).path.endswith("/index.qwarp")
        ):
            q = urlsplit(entry).path.removeprefix("/auth")
        root = self._root(code)
        if (
            not q
            or not q.startswith(root)
            or not q.endswith(".qwarp")
            or "?" in q
            or ".." in PurePosixPath(q).parts
        ):
            raise ParseError("Unexpected syllabus target; refusing to guess a route.")
        url = BASE + "/auth" + q + "?mode=all"
        r = self.transport.get(url)
        return dict(p.syllabus(r.text, r.url), course=code)

    def ropots(self, code: str) -> models.Ropots:
        """List ROPOT availability and grade text without opening an attempt."""
        c = self.course(code)
        url = (
            BASE
            + "/auth/elearning/test_pruchod_el_student?"
            + semicolon_query({"jen_predmet": c["course_id"]})
        )
        r = self.transport.get(url)
        return dict(p.ropots(r.text, r.url), course=code)

    def forums(self, code: str, *, max_pages: int = 5) -> models.Forums:
        """Read current course/group forums, bounded to max_pages per forum."""
        if not 1 <= max_pages <= 20:
            raise InvalidInput("max_pages must be between 1 and 20.")
        c = self.course(code)
        result = []
        for forum in c["forums"]:
            forum_id = forum.get("forum_id")
            if not forum_id or not forum_id.isdigit():
                raise ParseError("Unexpected course forum reference.")
            r = self.transport.get("/auth/diskuse/diskusni_forum_predmet?guz=" + forum_id)
            expected = f"/auth/discussion/predmetove/{c['faculty']}/{c['semester']}/"
            if not urlsplit(r.url).path.startswith(expected):
                result.append(
                    {
                        "forum_id": forum_id,
                        "state": "stale_semester",
                        "source_url": r.url,
                        "posts": [],
                    }
                )
                continue
            main = p.soup(r.text).select_one("#discussion")
            if not main:
                raise ParseError("Forum index is missing.")
            posts_link = next(
                (
                    a["url"]
                    for a in p.links(main, r.url)
                    if urlsplit(a["url"]).path.endswith("/prispevky/")
                ),
                None,
            )
            if not posts_link:
                raise ParseError("Forum post-list link is missing.")
            queue = [posts_link]
            seen = set()
            posts = {}
            unsupported_paging = False
            while queue and len(seen) < max_pages:
                url = queue.pop(0)
                if url in seen:
                    continue
                # Only follow pagination of this same read-only post list.
                if urlsplit(url).path != urlsplit(posts_link).path:
                    unsupported_paging = True
                    continue
                params = p.query(url)
                if any(
                    k
                    not in (
                        "fakulta",
                        "obdobi",
                        "studium",
                        "strana",
                        "page",
                        "from",
                        "posun",
                        "lang",
                    )
                    for k in params
                ):
                    unsupported_paging = True
                    continue
                seen.add(url)
                page = self.transport.get(url)
                parsed = p.forum_posts(page.text, page.url)
                posts.update({x["id"]: x for x in parsed["posts"]})
                for a in parsed["pagination_links"]:
                    if a["url"] not in seen and a["url"] not in queue:
                        queue.append(a["url"])
            result.append(
                {
                    "forum_id": forum_id,
                    "title": forum["title"],
                    "state": "listed",
                    "source_url": r.url,
                    "posts": list(posts.values()),
                    "pages_read": len(seen),
                    "pagination_incomplete": bool(queue) or unsupported_paging,
                }
            )
        return {
            "course": code,
            "forums": result,
            "post_count": sum(len(x["posts"]) for x in result),
        }

    def notes(self) -> models.Notes:
        """Return the selected study's note-block page as source text."""
        if not self.context:
            self.status()
        r = self.transport.get(
            "/auth/student/poznamkove_bloky_nahled?" + semicolon_query(self.context)
        )
        main = p.soup(r.text).select_one("#app_content")
        if not main:
            raise ParseError("Notes page structure changed.")
        return {"source_url": r.url, "text": p.clean_text(main)}

    def reservations(self, code: str) -> models.Reservations:
        """Read reservation series, occupancy, windows and registration state."""
        c = self.course(code)
        series = []
        for url in c["reservation_urls"]:
            params = p.query(url)
            if set(params) - {"obdobi", "predmet", "serie"} or params.get("predmet") != str(
                c["course_id"]
            ):
                raise ParseError("Unexpected reservation list parameters.")
            r = self.transport.get(url)
            series.append(dict(p.reservations(r.text, r.url), series_id=params.get("serie")))
        return {"course": code, "series": series, "state": "listed" if series else "not_exposed"}

    def snapshot(self, *, include_files: bool = True) -> models.Snapshot:
        """Collect accessible resources, retaining partial data and explicit errors.

        ``complete`` means all requested readers finished within the configured
        bounds. It does not mean that hidden content is accessible or that every
        task has been inferred. Suppressed lists and partial forums are incomplete.
        Authentication failures and rate limits stop further reads, retaining
        observations collected so far with complete=False.
        """
        operations = ["syllabus", "ropots", "forums", "reservations"] + (
            ["files"] if include_files else []
        )
        result = {
            "schema_version": 1,
            "captured_at": datetime.now(p.TZ).isoformat(),
            "courses": self.courses(),
            "context": self.context.copy(),
            "requested_resources": operations + ["notes"],
            "data": {},
            "errors": [],
        }
        for c in result["courses"]:
            code = c["code"]
            result["data"][code] = {}
            for op in operations:
                try:
                    value = getattr(self, op)(code)
                    result["data"][code][op] = value
                    if op == "ropots" and value.get("state") == "suppressed":
                        raise ParseError(
                            "ROPOT list was suppressed by IS MU; collection is incomplete."
                        )
                    if op == "forums" and any(
                        f.get("pagination_incomplete") or f.get("state") == "stale_semester"
                        for f in value["forums"]
                    ):
                        raise ParseError(
                            "Some forum pages are unavailable or outside the pagination limit."
                        )
                except ISMUError as exc:
                    result["errors"].append(
                        {"course": code, "resource": op, "error": str(exc), "code": exc.code}
                    )
                    if isinstance(exc, (AuthRequired, RateLimited)):
                        result["complete"] = False
                        return result
        try:
            result["notes"] = self.notes()
        except ISMUError as exc:
            result["errors"].append({"resource": "notes", "error": str(exc), "code": exc.code})
        result["complete"] = not result["errors"]
        return result
