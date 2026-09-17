"""IS service operations shared by the Python client, CLI and local REST adapter."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import models
from . import parsers as p
from . import resources as r
from .errors import InvalidInput, ParseError, RateLimited, UnsupportedOperation
from .transport import BASE, AuthRequired, ISMUError, private_write


def number(value, name):
    if not re.fullmatch(r"[0-9]+", str(value)):
        raise InvalidInput(f"{name} must be a numeric IS identifier.")
    return str(value)


def bounded(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidInput(f"{name} must be between {low} and {high}.")
    return value


def board_path(board):
    if board is None:
        return "/auth/vyveska/"
    # Explicit board/section selector, never a message permalink or operation.
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)?", board):
        raise InvalidInput("board must be a board or board/section name returned by IS.")
    return "/auth/noticeboard/" + board.strip("/") + "/"


class StudentServices:
    """Mixin for Client; all operations use the same authenticated account."""

    def _service_url(self, path, **params):
        from .client import semicolon_query

        if not self.context:
            self.status()
        return path + "?" + semicolon_query({**self.context, **params})

    def open_submissions(self) -> models.OpenSubmissions:
        """List boxes explicitly open to this user in the selected study/period."""
        response = self.transport.get(self._service_url("/auth/student/studijni_materialy"))
        return r.open_submissions(response.text, response.url)

    def submissions(self, code: str) -> models.Submissions:
        """Discover visible submission folders plus the open-box overlay.

        Absence from the open list is not evidence that work is late or submitted.
        Call submission_box for a folder's permission details and visible files.
        """
        root = self._root(code)
        opened = self.open_submissions()
        inventory = self.files(code)
        # Numeric and short faculty aliases can both occur in links from IS.
        course = self.course(code)

        def canonical(path):
            parts = path.strip("/").split("/")
            return "/".join(parts[2:]) if len(parts) >= 5 else path

        prefix = canonical(root + "ode/") + "/"
        available = {
            canonical(x["path"]): x
            for x in opened["items"]
            if x["course"] == code and x["semester"] == course["semester"]
        }
        items = {}
        for node in inventory["nodes"]:
            path = node.get("path") or ""
            key = canonical(path)
            if path.endswith("/") and key.startswith(prefix) and not node["variants"]:
                items[key] = {
                    "node_id": node["node_id"],
                    "path": path,
                    "title": node["title"],
                    "open_for_submission": True if key in available else None,
                    "submission_status": "unknown",
                    "source_url": BASE + "/auth" + path,
                }
        for key, item in available.items():
            items.setdefault(key, dict(item, node_id=None, submission_status="unknown"))
        return {
            "course": code,
            "source_url": inventory["source_url"],
            "items": list(items.values()),
            "count": len(items),
            "scope": "visible_folders_and_open_boxes",
            "state": "listed" if items else "empty",
            "open_index_url": opened["source_url"],
        }

    def submission_box(self, code: str, relative: str) -> models.SubmissionBox:
        """Read one course-relative ode/ directory; never upload or alter files."""
        if not relative.startswith("ode/") or not relative.endswith("/"):
            raise InvalidInput(
                "Supply a course-relative ode/.../ folder path with a trailing slash."
            )
        path = self._path(code, relative)
        response = self.transport.get(BASE + "/auth" + quote(path, safe="/._-"))
        result = r.submission_folder(response.text, response.url, path)
        result["course"] = code
        # Inventory also preserves files not displayed in a readable directory listing.
        from .client import semicolon_query

        inv = self.transport.get("/auth/dok/fmgr_api?" + semicolon_query({"url": path, "strom": 1}))
        nodes = p.file_tree(inv.text)
        files = [n for n in nodes if n["variants"]]
        result["visible_files"] = files
        identity = str(
            r.embedded_json(response.text, "is.Design.init").get("session", {}).get("uco", "")
        )
        own = [n for n in files if identity and n.get("uploaded_by") == identity]
        result["own_files"] = own
        if own:
            result["submission_status"] = "own_files_visible"
        # An empty or insert-only view cannot prove non-submission/completion.
        return result

    def calendar(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        kinds: str | Sequence[str] | None = None,
    ) -> models.Calendar:
        """Native calendar events; optional local date-overlap filtering [start, end).

        No recurrence expansion or task inference. IS may expose multiple semesters;
        source date bounds are descriptive, not a promise of complete coverage.
        """
        if (start is None) != (end is None):
            raise InvalidInput("Supply both start and end dates, or neither.")
        if start is not None:
            try:
                first, last = date.fromisoformat(start), date.fromisoformat(end)
            except (ValueError, TypeError):
                raise InvalidInput("Calendar dates must be YYYY-MM-DD.") from None
            if first >= last:
                raise InvalidInput("Calendar end must be after start (exclusive).")
        if kinds is not None:
            if isinstance(kinds, str):
                kinds = [kinds]
            if not kinds or set(kinds) - (set(r.CALENDAR_KINDS.values()) | {"unknown"}):
                raise InvalidInput("Unknown calendar kind.")
        response = self.transport.get(self._service_url("/auth/calendar/"))
        result = r.calendar(response.text, response.url)

        def matches(event):
            if kinds and event["kind"] not in kinds:
                return False
            if start is None:
                return True
            begins = date.fromisoformat(event["start"][:10])
            ends = date.fromisoformat((event["end"] or event["start"])[:10])
            # Date-only interval ends are exclusive; zero-length/provider point events remain visible.
            lower = ends > first if event["all_day"] and ends > begins else ends >= first
            return begins < last and lower

        result["items"] = [e for e in result["items"] if matches(e)]
        result.update(
            count=len(result["items"]),
            requested_start=start,
            requested_end=end,
            requested_kinds=list(kinds) if kinds else None,
        )
        return result

    def timetable(self) -> models.Timetable:
        """Weekly timetable with source exception text; calendar supplies dated occurrences."""
        response = self.transport.get(self._service_url("/auth/rozvrh/zobraz/muj"))
        return r.timetable(response.text, response.url)

    def exams(self, *, max_series: int = 30) -> models.Exams:
        """All exposed exam/registration series, including teaching reservations."""
        bounded(max_series, 1, 100, "max_series")
        response = self.transport.get(self._service_url("/auth/student/prihl_na_zkousky"))
        result = r.exam_index(response.text, response.url)
        series = result["series"]
        result["series_count"] = len(series)
        result["pagination_incomplete"] = len(series) > max_series
        result["series"] = []
        result["errors"] = []
        for entry in series[:max_series]:
            try:
                page = self.transport.get(entry["source_url"])
                parsed = p.reservations(page.text, page.url)
                result["series"].append(dict(entry, items=parsed["items"], text=parsed["text"]))
            except ISMUError as exc:
                result["errors"].append(
                    {
                        "course_id": entry["course_id"],
                        "series_id": entry["series_id"],
                        "error": str(exc),
                        "code": exc.code,
                    }
                )
                if isinstance(exc, (AuthRequired, RateLimited)):
                    break
        result["complete"] = not (result["errors"] or result["pagination_incomplete"])
        return result

    def mail_folders(self) -> models.MailFolders:
        """Mailbox folders/counts and forwarding indicator; no message bodies."""
        response = self.transport.get("/auth/mail/")
        parsed = r.mail_listing(response.text, response.url)
        return {
            "source_url": response.url,
            "items": parsed["folders"],
            "count": len(parsed["folders"]),
            "forwarding_enabled": parsed["forwarding_enabled"],
        }

    def mail_messages(
        self, *, folder: str | int | None = None, start: int = 1, limit: int = 50
    ) -> models.MailListing:
        """Page the native header table locally; bodies are not downloaded.

        IS's full interface ignores native start/count parameters. start is a
        1-based local offset; limit is 1–100. Upstream omissions remain explicit.
        """
        from .client import semicolon_query

        bounded(start, 1, 1_000_000, "start")
        bounded(limit, 1, 100, "limit")
        params = {"order_by": "datum", "desc": 1}
        if folder is not None:
            params["folder_id"] = number(folder, "folder")
        response = self.transport.get("/auth/mail/?" + semicolon_query(params))
        result = r.mail_listing(response.text, response.url)
        result.pop("pagination_urls")
        # Counts supplied by IS describe the selected folder; never equate one page with a mailbox.
        total = result["total_count"]
        returned = len(result["items"])
        truncated = total is None or returned < total
        result["items"] = result["items"][start - 1 : start - 1 + limit]
        consumed = start - 1 + len(result["items"])
        result.update(
            count=len(result["items"]),
            start=start,
            limit=limit,
            next_start=consumed + 1 if consumed < returned else None,
            pagination_incomplete=truncated or consumed < returned,
            pagination_mode="local",
            upstream_returned_count=returned,
            upstream_truncated=truncated,
        )
        return result

    def _mail_export(self, message_id, folder):
        from .client import semicolon_query

        mid, fid = number(message_id, "message_id"), number(folder, "folder")
        params = {
            "handle_up": "Proveď",
            "mail_type_up": "marked",
            "mail_handle_action_up": "download",
            "mark": mid,
            "folder_id": fid,
        }
        response = self._view_with_read_tracking(BASE + "/auth/mail/?" + semicolon_query(params))
        if response.headers.get("Content-Type", "").split(";")[0].lower() not in (
            "message/rfc822",
            "application/mbox",
        ):
            raise ParseError("Mail export was unavailable; no message content returned.")
        return response

    def mail_message(
        self, message_id: str | int, *, folder: str | int, allow_mark_read: bool = False
    ) -> models.MailMessage:
        """RFC822 parts; native export marks unread mail read, so require opt-in."""
        if allow_mark_read is not True:
            raise UnsupportedOperation(
                "Mail export can mark the message read. Set allow_mark_read=True explicitly."
            )
        response = self._mail_export(message_id, folder)
        return r.mail_message(response.content, message_id, response.url)[0]

    def download_mail(
        self,
        message_id: str | int,
        *,
        folder: str | int,
        destination: str | Path,
        part_id: str | None = None,
        allow_mark_read: bool = False,
    ) -> models.MailDownload:
        """Save the native mail export or a MIME attachment to a new private file."""
        if allow_mark_read is not True:
            raise UnsupportedOperation(
                "Mail export can mark the message read. Set allow_mark_read=True explicitly."
            )
        dest = Path(destination)
        if dest.exists():
            raise InvalidInput("Destination already exists; choose a new filename.")
        response = self._mail_export(message_id, folder)
        message, attachments = r.mail_message(response.content, message_id, response.url)
        if part_id is not None and part_id not in attachments:
            raise InvalidInput("Unknown attachment part_id; use mail_message to discover it.")
        data = attachments[part_id] if part_id is not None else response.content
        private_write(dest, data)
        return {
            "id": str(message_id),
            "part_id": part_id,
            "saved_to": str(dest.resolve()),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def notices(self, *, board: str | None = None, max_pages: int = 1) -> models.Notices:
        """Read notice cards without opening messages. Overview is curated, not exhaustive."""
        bounded(max_pages, 1, 20, "max_pages")
        path = board_path(board)
        queue, seen, items, boards = [path], set(), {}, {}
        scope = None
        while queue and len(seen) < max_pages:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            response = self.transport.get(current)
            parsed = r.notices(response.text, response.url)
            scope = parsed["scope"]
            items.update({x["id"]: x for x in parsed["items"]})
            boards.update({x["url"]: x for x in parsed["board_links"]})
            for url in parsed["pagination_urls"]:
                # Pagination stays in the selected board. Full message links are never followed.
                u = urlsplit(url)
                if (
                    u.hostname != "is.muni.cz"
                    or u.query
                    or (
                        u.path != path
                        and not re.fullmatch(re.escape(path) + r"stranka/[0-9]+/", u.path)
                    )
                ):
                    raise ParseError("Unexpected noticeboard pagination route.")
                if u.path not in seen and u.path not in queue:
                    queue.append(u.path)
        return {
            "source_url": BASE + path,
            "items": list(items.values()),
            "count": len(items),
            "pages_read": len(seen),
            "pagination_incomplete": bool(queue),
            "scope": scope,
            "board_links": list(boards.values()),
        }

    def notice(self, notice_id: str | int, *, allow_mark_read: bool = False) -> models.NoticeDetail:
        """Explicitly open a notice; IS may update its read state."""
        if allow_mark_read is not True:
            raise UnsupportedOperation(
                "Opening a notice can mark it read. Set allow_mark_read=True explicitly."
            )
        mid = number(notice_id, "notice_id")
        # Fixed observed view operation; arbitrary URLs/actions are never accepted here.
        target = BASE + "/auth/noticeboard/?operace=dej_zpravu;zprava_id=" + mid

        response = self._view_with_read_tracking(target)
        return r.notice_detail(response.text, response.url, mid)

    def _view_with_read_tracking(self, target):
        """Exact opt-in view request, with the same restriction on every redirect."""

        def validate(url):
            if url == target:
                return
            u = urlsplit(url)
            if u.hostname == "muni.islogin.cz" and u.path.startswith("/login/"):
                return
            raise ParseError("Unexpected view redirect; no action was followed.")

        response = self.transport._request(target, url_validator=validate)
        if self.transport._is_login(response):
            raise AuthRequired("Run ismu login before opening this resource.")
        self.transport._save()
        return response
