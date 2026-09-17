"""Loopback-only API. Authentication stays in the interactive CLI."""

from __future__ import annotations

import hmac
import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from .errors import InvalidInput, RateLimited, StateError, UpstreamError
from .transport import AuthRequired, ISMUError, private_write


def make_server(client, port=8765):
    token_path = client.transport.state_dir / "api-token"
    if not token_path.exists():
        private_write(token_path, secrets.token_urlsafe(32).encode())
    if token_path.is_symlink() or token_path.stat().st_mode & 0o077:
        raise InvalidInput("API token file must be private (chmod 600).")
    token = token_path.read_text().strip()
    if len(token) < 32:
        raise InvalidInput("Invalid local API token.")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ISMUReader/0.4"

        def log_message(self, *args):
            pass  # Do not put document paths, tokens or personal data in access logs.

        def send_json(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            if host not in ("127.0.0.1", "localhost") or self.headers.get("Origin"):
                self.send_json(403, {"error": "This API is for authenticated local clients."})
                return
            expected = "Bearer " + token
            if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.send_json(401, {"error": "Bearer token required."})
                return
            try:
                parsed = urlsplit(self.path)
                segments = parsed.path.strip("/").split("/")
                query = parse_qs(parsed.query, keep_blank_values=True)

                def params(*allowed, required=()):
                    if (
                        set(query) - set(allowed)
                        or set(required) - set(query)
                        or any(len(v) != 1 or not v[0] for v in query.values())
                    ):
                        raise InvalidInput("Unknown, missing, empty or duplicate query parameter.")
                    return {k: v[0] for k, v in query.items()}

                def integer(value):
                    try:
                        return int(value)
                    except ValueError:
                        raise InvalidInput("Expected an integer query parameter.") from None

                # A new client per request discovers current membership instead of retaining old enrolments.
                from .client import Client

                c = Client(
                    client.transport.state_dir,
                    study=client.context.get("studium"),
                    period=client.context.get("obdobi"),
                    faculty=client.context.get("fakulta"),
                    transport=client.transport,
                )
                if parsed.path == "/v1/status":
                    params()
                    result = c.status()
                elif parsed.path == "/v1/courses":
                    params()
                    result = c.courses()
                elif parsed.path == "/v1/notes":
                    params()
                    result = c.notes()
                elif parsed.path == "/v1/snapshot":
                    params()
                    result = c.snapshot()
                elif parsed.path == "/v1/submissions":
                    params()
                    result = c.open_submissions()
                elif parsed.path == "/v1/calendar":
                    q = params("start", "end", "kinds")
                    result = c.calendar(
                        start=q.get("start"),
                        end=q.get("end"),
                        kinds=q["kinds"].split(",") if "kinds" in q else None,
                    )
                elif parsed.path == "/v1/timetable":
                    params()
                    result = c.timetable()
                elif parsed.path == "/v1/exams":
                    q = params("max_series")
                    result = c.exams(max_series=integer(q.get("max_series", "30")))
                elif parsed.path == "/v1/notices":
                    q = params("board", "max_pages")
                    result = c.notices(
                        board=q.get("board"), max_pages=integer(q.get("max_pages", "1"))
                    )
                elif len(segments) == 3 and segments[:2] == ["v1", "notices"]:
                    q = params("allow_mark_read", required=("allow_mark_read",))
                    if q["allow_mark_read"] != "true":
                        raise InvalidInput("Opening a notice requires allow_mark_read=true.")
                    result = c.notice(segments[2], allow_mark_read=True)
                elif parsed.path == "/v1/mail/folders":
                    params()
                    result = c.mail_folders()
                elif parsed.path == "/v1/mail/messages":
                    q = params("folder", "start", "limit")
                    result = c.mail_messages(
                        folder=q.get("folder"),
                        start=integer(q.get("start", "1")),
                        limit=integer(q.get("limit", "50")),
                    )
                elif len(segments) == 4 and segments[:3] == ["v1", "mail", "messages"]:
                    q = params("folder", "allow_mark_read", required=("folder", "allow_mark_read"))
                    if q["allow_mark_read"] != "true":
                        raise InvalidInput("Reading a mail body requires allow_mark_read=true.")
                    result = c.mail_message(segments[3], folder=q["folder"], allow_mark_read=True)
                elif len(segments) == 4 and segments[:2] == ["v1", "courses"]:
                    code, resource = segments[2:]
                    if resource in (
                        "files",
                        "syllabus",
                        "ropots",
                        "forums",
                        "reservations",
                        "submissions",
                    ):
                        params()
                        result = getattr(c, resource)(code)
                    elif resource in ("text", "submission"):
                        q = params("path", required=("path",))
                        result = (
                            c.read_file(code, q["path"])
                            if resource == "text"
                            else c.submission_box(code, q["path"])
                        )
                    else:
                        self.send_json(404, {"error": "Unknown resource."})
                        return
                else:
                    self.send_json(404, {"error": "Unknown endpoint."})
                    return
                self.send_json(200, result)
            except ISMUError as exc:
                status = (
                    503
                    if isinstance(exc, (AuthRequired, RateLimited))
                    else 502
                    if isinstance(exc, UpstreamError)
                    else 409
                    if isinstance(exc, StateError)
                    else 422
                )
                error = {"error": type(exc).__name__, "code": exc.code, "message": str(exc)}
                if isinstance(exc, UpstreamError) and exc.status_code is not None:
                    error["upstream_status"] = exc.status_code
                self.send_json(status, error)
            except Exception:
                self.send_json(
                    500, {"error": "Unexpected upstream response; no sensitive details logged."}
                )

        def do_POST(self):
            self.send_json(405, {"error": "Read-only API."})

        def do_PUT(self):
            self.do_POST()

        def do_DELETE(self):
            self.do_POST()

        def do_PATCH(self):
            self.do_POST()

    return HTTPServer(("127.0.0.1", port), Handler)


def serve(client, port):
    if not 1024 <= port <= 65535:
        raise InvalidInput("Choose a port between 1024 and 65535.")
    server = make_server(client, port)
    print(f"IS MU read-only API: http://127.0.0.1:{server.server_port}/v1/courses", file=sys.stderr)
    print(
        f"Bearer token file: {(client.transport.state_dir / 'api-token').resolve()}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
