# API and CLI reference

Version: package `0.4.0`, REST prefix `/v1`, snapshot `schema_version: 1`. The machine-readable contract is [openapi.json](openapi.json). The package is an alpha; consumers should tolerate additional fields and check the changelog before upgrading.

## Command line

```text
ismu [--state-dir PATH] [--study ID] [--period ID] [--faculty ID] COMMAND ...
```

Global flags must precede the command. `--state-dir` takes precedence over `IS_MU_STATE_DIR`; the default is `.ismu-state` relative to the current working directory. Study, period and faculty selectors are numeric IS MU identifiers, not year strings. Omitted context is discovered from the authenticated course page. The returned context is authoritative for the request.

| Command | Arguments | Result |
|---|---|---|
| `login` | `--username YOUR_UCO` | Prompt for password, verify student page, save session |
| `status` | None | Live session check and refreshed course count/context |
| `courses` | `--basic`, `--table` | Enrolments; basic omits course-detail requests; table changes output format |
| `files` | `CODE` | File nodes and representations, recursively |
| `read` | `CODE RELATIVE_PATH [--output PATH]` | Text content or explicitly saved binary |
| `syllabus` | `CODE` | Full accessible syllabus and section states |
| `ropots` | `CODE` | ROPOT list metadata |
| `forums` | `CODE` | Current course/group posts, five pages per forum |
| `reservations` | `CODE` | Current reservation series and registration information |
| `notes` | None | Note-block source text |
| `submissions` | `[CODE]` | Open-box index, or one course's visible submission folders |
| `submission-box` | `CODE ode/PATH/` | Folder permissions and visible files |
| `calendar` | `[--start DATE --end DATE] [--kind KIND ...]` | Native calendar events |
| `timetable` | None | Weekly timetable pattern and exceptions |
| `exams` | `[--max-series 30]` | Exam/registration overview and bounded series reads |
| `mail` | `folders/list/read/download` | HTTPS mailbox operations; see services reference |
| `notices` | `[--board BOARD/SECTION] [--max-pages 1]` | Noticeboard cards |
| `notice` | `ID --allow-mark-read` | Explicit full-message opening, which may mark it read |
| `snapshot` | `[--output PATH] [--skip-files]` | Collection with per-resource errors |
| `ropot` | `inspect/start/questions/save/submit QREF` plus explicit flags | Separate [ROPOT attempt interface](ropot.md) |
| `serve` | `[--port 8765]` | Foreground localhost API; Ctrl+C stops it |

Course codes are case-sensitive and must identify exactly one enrolment in the selected study/period. Use `courses` to discover them. There is no arbitrary-course or arbitrary-URL fetch operation.

Paths supplied to `read` are plain, unencoded paths relative to the course root, for example `um/lecture 1.txt`. Absolute paths, traversal, percent-encoded paths and query parameters are refused. `.qref`, `.qdef` and `.qwarp` files use dedicated metadata/syllabus readers instead. Existing download destinations are not overwritten. Snapshot output uses an atomic private write and **does replace** an existing snapshot at that path.

### Output and exit codes

Normal JSON goes to stdout. Structured command errors go to stderr as `{"error":"ErrorClass","message":"..."}`. `argparse` usage errors and server startup messages are human-readable stderr output. A saved snapshot prints only its path, course count, completeness and errors; the file contains the full snapshot.

| Exit code | Meaning |
|---|---|
| `0` | Command completed |
| `1` | Reader error, local file error, or incomplete snapshot |
| `2` | Authentication required, or command-line usage error |
| `130` | Interrupted with Ctrl+C |

Resource commands preserve states such as `suppressed` or `pagination_incomplete` in JSON and may exit successfully with partial data. The combined snapshot promotes those conditions to errors and exits `1`.

## Python interface

```python
from ismu import Client, ISMUError, AuthRequired

client = Client(
    ".ismu-state",
    study=None,
    period=None,
    faculty=None,
)
```

The Python constructor uses its `state_dir` argument; unlike the CLI, it does not read `IS_MU_STATE_DIR` automatically. Methods below make authenticated HTTP reads as needed. Full mail and notice opening are separately opt-in because IS may change their read status. See the [student services reference](student-services.md) for field semantics and complete examples. Course index/detail results are cached for the lifetime of the client. `status()` refreshes the index; making a new client each run is the simplest polling pattern.

| Method | Return model | Notes |
|---|---|---|
| `login(username, password)` | `LoginResult` | Normal sign-in; invalidates cached courses; never stores the password |
| `status()` | `Status` | Live check; absence of cookies raises `AuthRequired` |
| `courses(details=True)` | `list[Course]` | `details=False` returns code/title/course ID only |
| `course(code)` | `Course` | Independent public copy of one current enrolment, without internal request fields |
| `ropot(item_or_qref, allow_writes=False)` | `RopotSession` | Construct an explicit attempt interface; no HTTP until inspection/action |
| `files(code)` | `Files` | Native XML normalized into nodes and variants |
| `read_file(code, relative, destination=None)` | `FileRead` | Binary needs a destination; a saved result omits `text` |
| `syllabus(code)` | `Syllabus` | `not_exposed` if no syllabus entry was discovered |
| `ropots(code)` | `Ropots` | Does not open the returned test URL |
| `forums(code, max_pages=5)` | `Forums` | `max_pages` is 1–20 per forum |
| `reservations(code)` | `Reservations` | Does not follow register/unregister actions |
| `notes()` | `Notes` | Source text, not a gradebook normalization |
| `open_submissions()` | `OpenSubmissions` | Boxes explicitly open to the user |
| `submissions(code)` | `Submissions` | Visible course folders plus open-box overlay |
| `submission_box(code, relative)` | `SubmissionBox` | `ode/.../` folder permissions and visible metadata |
| `calendar(start=None, end=None, kinds=None)` | `Calendar` | Date-overlap filtering; end exclusive; no browser |
| `timetable()` | `Timetable` | Weekly pattern, not recurrence expansion |
| `exams(max_series=30)` | `Exams` | Limit 1–100; explicit completeness and errors |
| `mail_folders()` | `MailFolders` | Counts and forwarding indicator |
| `mail_messages(folder=None, start=1, limit=50)` | `MailListing` | One-based paging; limit 1–100 |
| `mail_message(message_id, folder=..., allow_mark_read=False)` | `MailMessage` | MIME bodies/attachment metadata; requires explicit read-state opt-in |
| `download_mail(message_id, folder=..., destination=..., part_id=None, allow_mark_read=False)` | Download metadata | Opt-in view, private new file; optional MIME attachment selector |
| `notices(board=None, max_pages=1)` | `Notices` | Card listings; limit 1–20 pages |
| `notice(notice_id, allow_mark_read=False)` | `NoticeDetail` | Explicit opt-in required because upstream read state may change |
| `snapshot(include_files=True)` | `Snapshot` | Partial results retained; inspect `complete` and `errors` |

`AuthRequired` subclasses `ISMUError`. A snapshot may retain an authentication error against a resource rather than raising it after collection has started. Local filesystem failures can raise `OSError`. Client instances are synchronous and not thread-safe. Use one process per state directory; parallel writers to the same session are not supported.

`client.login(username, password)` is the public sign-in method for callers with an in-memory password. The older `client.transport.login(...)` remains available for compatibility. Prefer the CLI prompt for interactive use. Never put credentials in source files, URLs, command-line arguments or logs. MFA/challenge flows are not implemented. When a session is already authenticated, login reuses it; it does not switch accounts based on the supplied username.

Return model names above refer to public `TypedDict` declarations in `ismu.models` (except the explicit `RopotSession` object). Runtime values remain dictionaries and lists. See [Python typing, error categories and 0.4 migration](python.md).

## REST API

Start with `ismu serve`. Bind address is fixed to `127.0.0.1`; the CLI permits ports 1024–65535. The state directory's `api-token` is created on first startup and reused thereafter. All reads require:

```http
Authorization: Bearer <contents of the local api-token file>
```

The example [local_api.py](../examples/local_api.py) loads the token and performs a read. A typical call is:

```bash
python examples/local_api.py /v1/courses
python examples/local_api.py '/v1/courses/CODE/text?path=um%2Fnotes.txt'
```

Routes and response schemas are listed in the [OpenAPI document](openapi.json). The text and submission-folder routes require `path`; a mail body requires `folder` and `allow_mark_read=true`; full notices also require `allow_mark_read=true`. Calendar, exams, mailbox lists and notices accept the parameters documented in the [services reference](student-services.md). Version 0.3 rejects unknown, empty or duplicate query parameters with HTTP 422. Study/period selection is a server startup option, not a per-request parameter.

Every successful response is UTF-8 JSON with `Cache-Control: no-store`. Each request discovers enrolments using a fresh client over the same session. The server handles one request at a time. `/v1/snapshot` can take minutes; a response is not streamed and there is no job queue. Applications can compose service reads separately; the snapshot remains scoped to courses and note blocks.

| HTTP status | Meaning |
|---|---|
| `200` | Result returned; a snapshot may still have `complete: false` |
| `401` | Missing or incorrect local bearer token |
| `403` | Browser Origin or non-local Host rejected |
| `404` | Unknown endpoint/resource |
| `405` | POST, PUT, PATCH or DELETE rejected |
| `409` | Incompatible local session/state |
| `422` | Invalid reader input or unsupported operation |
| `500` | Unexpected server error; no upstream body is logged |
| `502` | Upstream HTTP/network/parser failure |
| `503` | IS MU authentication is missing/expired or IS returned a rate limit |

Errors have an `error` string and may have a `message`. Library errors also have a stable `code`, and upstream HTTP failures include `upstream_status` when known. Snapshot/exam partial errors preserve their message and add `code`. These categories do not authorize retrying attempt writes. Unsupported HTTP methods such as OPTIONS/HEAD use the base HTTP server's `501` response. The service is a local convenience adapter, not a production multi-user server. It has no CORS, TLS termination, remote bind, upload, login or binary-download route.
