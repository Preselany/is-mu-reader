"""Normal HTTPS sign-in, private session persistence, bounded GET requests."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from . import models
from .errors import AuthRequired as AuthRequired
from .errors import ISMUError as ISMUError
from .errors import NetworkError, RateLimited, StateError, UnsupportedOperation, UpstreamError

BASE = "https://is.muni.cz"
HOSTS = {"is.muni.cz", "muni.islogin.cz"}


def private_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Transport:
    def __init__(self, state_dir: str | Path, *, delay: float = 0.6, max_bytes: int = 20_000_000):
        self.state_dir = Path(state_dir)
        self.cookie_path = self.state_dir / "session.json"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "ISMUReader/0.4 (unofficial IS MU client)"
        self.delay = delay
        self.max_bytes = max_bytes
        self.last_request = 0.0
        self.request_count = 0
        self._load()

    def _load(self):
        if self.cookie_path.is_symlink():
            raise StateError("Session file must be a private regular file (chmod 600).")
        if not self.cookie_path.exists():
            return
        if not self.cookie_path.is_file() or self.cookie_path.stat().st_mode & 0o077:
            raise StateError("Session file must be a private regular file (chmod 600).")
        try:
            cookies = json.loads(self.cookie_path.read_text())
            if not isinstance(cookies, list):
                raise ValueError("Expected a cookie list")
            jar = requests.cookies.RequestsCookieJar()
            for cookie in cookies:
                if (
                    not isinstance(cookie, dict)
                    or not isinstance(cookie.get("domain"), str)
                    or cookie["domain"].lstrip(".") not in HOSTS
                    or not isinstance(cookie.get("name"), str)
                    or not isinstance(cookie.get("value"), str)
                    or not isinstance(cookie.get("path", "/"), str)
                ):
                    raise ValueError("Invalid cookie entry")
                jar.set_cookie(requests.cookies.create_cookie(**cookie))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AuthRequired(
                "Cannot load the saved session. Choose a new private --state-dir and log in."
            ) from None
        self.session.cookies.update(jar)

    def _save(self):
        cookies = []
        for c in self.session.cookies:
            if c.domain.lstrip(".") in HOSTS:
                cookies.append(
                    dict(
                        name=c.name,
                        value=c.value,
                        domain=c.domain,
                        path=c.path,
                        secure=c.secure,
                        expires=c.expires,
                        rest=c._rest,
                        discard=c.discard,
                    )
                )
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)
        private_write(self.cookie_path, json.dumps(cookies).encode())

    @staticmethod
    def _validate_url(url):
        p = urlsplit(url)
        if (
            p.scheme != "https"
            or p.hostname not in HOSTS
            or p.port not in (None, 443)
            or p.username
            or p.password
        ):
            raise UnsupportedOperation("Refusing a request outside the IS MU HTTPS hosts.")

    @staticmethod
    def _validate_read(url):
        p = urlsplit(url)
        if p.hostname == "muni.islogin.cz" and p.path.startswith("/login/"):
            return  # Read the login page only to detect an expired session.
        params = parse_qs(p.query.replace(";", "&"), keep_blank_values=True)
        base = {"fakulta", "obdobi", "studium", "lang"}
        routes = {
            "/auth/student/predmety": base,
            "/auth/student/index_ajax": base | {"option", "vsechna_studia", "predmet_id"},
            "/auth/student/poznamkove_bloky_nahled": base,
            "/auth/student/studijni_materialy": base,
            "/auth/student/prihl_na_zkousky": base | {"predmet", "serie"},
            "/auth/calendar/": base,
            "/auth/rozvrh/zobraz/muj": base,
            "/auth/vyveska/": {"lang"},
            "/auth/noticeboard/": {"lang"},
            "/auth/mail/": {"folder_id", "count", "start", "order_by", "desc", "lang"},
            "/auth/elearning/test_pruchod_el_student": base | {"jen_predmet"},
            "/auth/elearning/io/": base | {"so", "qurl", "prejit", "mode", "predmet"},
            "/auth/dok/fmgr_api": {"url", "strom"},
            "/auth/diskuse/diskusni_forum_predmet": {"guz"},
        }
        allowed = routes.get(p.path)
        if re.fullmatch(
            r"/auth/noticeboard/[A-Za-z0-9_-]+/(?:[A-Za-z0-9_-]+/)?(?:stranka/[0-9]+/)?", p.path
        ):
            allowed = {"lang"}
        if p.path == "/auth/mail/":
            for name in ("folder_id", "start", "count"):
                if name in params and (
                    len(params[name]) != 1 or not re.fullmatch(r"[0-9]+", params[name][0])
                ):
                    raise UnsupportedOperation("Invalid mail identifier or paging parameter.")
            if "order_by" in params and params["order_by"] != ["datum"]:
                raise UnsupportedOperation("Only date-ordered mailbox listing is supported.")
            if "desc" in params and params["desc"] != ["1"]:
                raise UnsupportedOperation("Only descending mailbox listing is supported.")
        if p.path.startswith("/auth/discussion/predmetove/"):
            allowed = base | {"strana", "page", "from", "posun"}
        if p.path.startswith("/auth/el/"):
            if p.path.lower().endswith((".qref", ".qdef")):
                raise UnsupportedOperation("Opening ROPOT attempts is disabled.")
            allowed = {"mode"} if p.path.endswith(".qwarp") else set()
            if "mode" in params and params["mode"] != ["all"]:
                raise UnsupportedOperation("Only read-all syllabus mode is supported.")
        if allowed is None or set(params) - allowed:
            raise UnsupportedOperation("This route or action is not in the read-only allowlist.")
        if p.path.endswith("/index_ajax") and params.get("option") != ["predmet-option-info"]:
            raise UnsupportedOperation("Only the course-information AJAX operation is allowed.")
        if p.path == "/auth/elearning/io/":
            qurl = params.get("qurl", [""])[0]
            if (
                not qurl.startswith("/el/")
                or not qurl.endswith(".qwarp")
                or ".." in qurl.split("/")
                or params.get("mode") != ["all"]
                or params.get("so", ["na"]) != ["na"]
            ):
                raise UnsupportedOperation("Only the read-all course syllabus view is supported.")
        if p.path.endswith("/fmgr_api"):
            if params.get("strom", ["1"]) != ["1"] or not params.get("url", [""])[0].startswith(
                "/el/"
            ):
                raise UnsupportedOperation("Only course file inventories are supported.")
        decoded = unquote(p.path)
        if ".." in decoded.split("/") or "\\" in decoded:
            raise UnsupportedOperation("Invalid read path.")

    def _validate_method(self, method, url):
        if method != "GET" and not (
            method == "POST"
            and urlsplit(url).hostname == "muni.islogin.cz"
            and urlsplit(url).path.startswith("/login/")
        ):
            raise UnsupportedOperation("Only the normal sign-in form may be submitted.")

    def _request(self, url, *, method="GET", data=None, read_only=False, url_validator=None):
        url = urljoin(BASE, url)
        for _ in range(12):
            self._validate_url(url)
            if url_validator is not None:
                url_validator(url)
            if read_only:
                self._validate_read(url)
            self._validate_method(method, url)
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            for attempt in range(3 if method == "GET" else 1):
                self.last_request = time.monotonic()
                self.request_count += 1
                try:
                    response = self.session.request(
                        method, url, data=data, timeout=(10, 45), allow_redirects=False, stream=True
                    )
                    break
                except (requests.ConnectionError, requests.Timeout) as exc:
                    if method == "GET" and attempt < 2:
                        time.sleep(1.0 * (2**attempt))
                        continue
                    raise NetworkError(
                        f"HTTPS request failed ({type(exc).__name__}); no response body logged."
                    ) from None
                except requests.RequestException as exc:
                    raise NetworkError(
                        f"HTTPS request failed ({type(exc).__name__}); no response body logged."
                    ) from None
            if response.status_code in (301, 302, 303, 307, 308):
                target = urljoin(url, response.headers.get("Location", ""))
                self._validate_url(target)
                if method == "POST" and response.status_code in (307, 308):
                    response.close()
                    raise UnsupportedOperation(
                        "Refusing to replay a POST through a preserving redirect."
                    )
                method, data, url = "GET", None, target
                response.close()
                continue
            if response.status_code == 429:
                response.close()
                raise RateLimited(
                    "IS MU rate limit reached. Stop and retry later.", status_code=429
                )
            if response.status_code >= 400:
                code = response.status_code
                response.close()
                raise UpstreamError(f"IS MU returned HTTP {code}.", status_code=code)
            chunks, size = [], 0
            try:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UpstreamError(f"Response exceeds the {self.max_bytes}-byte limit.")
                    chunks.append(chunk)
            except requests.RequestException as exc:
                raise NetworkError(
                    f"HTTPS response was interrupted ({type(exc).__name__}); retry the read later."
                ) from None
            finally:
                response.close()
            response._content = b"".join(chunks)
            response._content_consumed = True
            # IS MU documents and HTML are UTF-8; requests otherwise assumes Latin-1 for text/plain.
            if "charset=" not in response.headers.get("Content-Type", "").lower():
                response.encoding = "utf-8"
            return response
        raise UpstreamError("Too many authentication redirects.")

    @staticmethod
    def _is_login(response):
        return urlsplit(response.url).hostname == "muni.islogin.cz" or (
            "text/html" in response.headers.get("Content-Type", "")
            and 'name="credential_1"' in response.text
        )

    def get(self, url):
        response = self._request(url, read_only=True)
        if self._is_login(response):
            raise AuthRequired(
                "Session is absent or expired. Run ismu login; no browser is required."
            )
        if not any(c.domain.lstrip(".") == "is.muni.cz" for c in self.session.cookies):
            raise AuthRequired("No authenticated IS MU session is available.")
        self._save()
        return response

    def login(self, username: str, password: str) -> models.LoginResult:
        response = self._request(BASE + "/auth/")
        if not self._is_login(response):
            self._save()
            return {"authenticated": True, "already_authenticated": True}
        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.select_one("form.islogin_form")
        if not form or not form.select_one('input[name="credential_1"]'):
            raise AuthRequired(
                "The normal password form is unavailable; additional authentication may be required."
            )
        action = urljoin(response.url, form.get("action", ""))
        self._validate_url(action)
        fields = {x["name"]: x.get("value", "") for x in form.select('input[type="hidden"][name]')}
        submit = form.select_one('button[type="submit"][name], input[type="submit"][name]')
        if submit:
            fields[submit["name"]] = submit.get("value", "")
        fields.update(credential_0=username, credential_1=password)
        try:
            result = self._request(action, method="POST", data=fields)
        finally:
            fields.clear()
        if self._is_login(result):
            raise AuthRequired(
                "Sign-in did not complete; no automatic retry. Additional authentication or corrected credentials may be needed."
            )
        check = self._request(BASE + "/auth/student/predmety")
        if self._is_login(check) or not BeautifulSoup(check.text, "html.parser").select_one(
            "#app_content"
        ):
            raise AuthRequired("Sign-in could not be verified on the student page.")
        self._save()
        return {"authenticated": True, "already_authenticated": False, "password_saved": False}
