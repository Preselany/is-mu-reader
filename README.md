# IS MU Reader

An unofficial Python client for [IS MU](https://is.muni.cz). It signs in through the normal HTTPS login form and turns student pages and the native file inventory into structured JSON. **No browser or browser automation is needed for the supported password login.**

Use it as a Python library, a JSON command-line tool, or a local REST API. Course collection stays read-only; a separate explicit ROPOT interface can start attempts, read questions, save answers and submit native single-page forms. It is an access layer for developers building their own IS MU integrations. It is not affiliated with Masaryk University.

## What it reads

| Resource | Available data |
|---|---|
| Enrolments | Course codes, titles, credits, teachers, enrolled seminar, source links |
| Materials | Recursive file/folder inventory, IDs, variants, sizes, timestamps; text and binary downloads |
| Interactive syllabi | Accessible sections, source text, links, schedule text, locked states |
| ROPOTs | Read-only metadata; explicit native single-page question, save and submit operations |
| Forums | Current course and enrolled-group posts, authors, links, content hashes |
| Reservations | Events, opening dates, capacities, current registration state |
| Note blocks | Current study's note-block page as text |
| Submission boxes | Open-box index, course folders, native permissions and visible file metadata |
| Calendar and timetable | Native dated events, hidden-layer metadata, weekly pattern, rooms and exceptions |
| Exams | Discovered exam/registration series, capacity, windows and registration state |
| IS mail | Folder/header listings, RFC822/MIME bodies, attachment metadata and downloads |
| Noticeboard | Paged cards; explicit full-message reads that may change upstream read status |

The adapter combines an observed internal JSON/HTML course endpoint, native XML file metadata, and HTML parsers. It is **not an official general-purpose IS MU API**. Markup can change. Initial end-to-end verification used one student account; other faculties, page languages and authentication setups may need additional adapters.

## Quick start

**Before connecting:** read [permissions and responsible use](docs/responsible-use.md). IS MU requires prior operator consent for bulk operations; request pacing does not replace permission. This project has no university approval. Its MIT license covers the source, not access to IS MU or redistribution of collected content.

Requires Python 3.11+ on Linux or macOS and an IS MU student account with a supported password login. Clone the repository, then install it locally:

```bash
git clone https://github.com/Preselany/is-mu-reader.git
cd is-mu-reader
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
ismu login --username YOUR_UCO
ismu status
ismu courses --table
```

The password is prompted without echo and is never saved. Session cookies are saved in `.ismu-state/` relative to the current working directory, with private file permissions. For use from different directories, set `IS_MU_STATE_DIR` to an absolute private directory or pass `--state-dir PATH` before the command. Use a separate state directory for each account. An existing authenticated session takes precedence over a new login username.

After installation, `./ismu` is also available as a shortcut to this project's `.venv`; its state directory follows the same rules as the installed console command.

```bash
# Replace CODE with a code returned by `ismu courses`.
ismu syllabus CODE
ismu files CODE
ismu ropots CODE
ismu forums CODE
ismu reservations CODE
ismu notes
ismu read CODE um/notes.txt
ismu read CODE um/handout.pdf --output ./downloads/handout.pdf
ismu snapshot --output .ismu-state/snapshot.json
```

Commands return UTF-8 JSON unless `--table` is used. `snapshot` retains per-resource failures and marks incomplete collections. The file inventory includes alternative representations of a document; its variant count is not the number of distinct documents.

## Student services

```bash
ismu submissions
ismu submissions CODE
ismu submission-box CODE ode/homework-1/
ismu calendar --start 2026-09-14 --end 2026-09-21
ismu timetable
ismu exams
ismu mail folders
ismu mail list
ismu notices
```

See the [student services reference](docs/student-services.md) for Python methods, parameters, MIME attachment downloads, paging, permissions and live-verification limits. Calendar data can span multiple semesters. Hidden or empty submission-box contents do not establish whether homework was submitted. Opening full mail or notices requires `--allow-mark-read` (REST: `allow_mark_read=true`) because IS can mark them read.

## Python library

```python
from ismu import Client, AuthRequired

client = Client(".ismu-state")
try:
    courses = client.courses()
    code = courses[0]["code"]
    syllabus = client.syllabus(code)
    materials = client.files(code)
    ropots = client.ropots(code)
    discussions = client.forums(code)
except AuthRequired:
    raise SystemExit("Run ismu login, then retry.")
```

Methods return ordinary JSON-compatible dictionaries and lists, annotated with public `TypedDict` models from `ismu.models` for autocomplete and static checking. There is no custom JSON encoder or runtime model conversion. Both course readers return independent copies without internal request parameters. Create a fresh client for each collection run so that course membership is refreshed. See the [Python reference](docs/api.md#python-interface), [Python ergonomics and migration guide](docs/python.md), and the [snapshot example](examples/snapshot.py).

For programmatic authentication, use `client.login(username, password)` with an in-memory password. It uses the same normal login as the CLI. `AuthRequired`, `InvalidInput`, `NetworkError`, `RateLimited` and `ParseError` let callers distinguish login, input and upstream failures; all remain subclasses of `ISMUError`.

## ROPOT questions and submission

The separate `RopotSession` Python interface and `ismu ropot` commands can inspect an entry, start an attempt, read questions, save draft answers, and submit once with a verified receipt. The complete flow was tested on an official **ungraded public demo**, using anonymous cookies. No course assessment was started or submitted during development.

Use `client.ropot(item)` to create a session from a listed ROPOT. After an explicitly started attempt, `attempt.question(1).answer("your answer")` builds a local answer without requiring an HTML field name. Pass it to `save()` or `submit()` when ready; building an answer sends nothing. Read the [ROPOT guide](docs/ropot.md) for the complete flow. Starting can consume an attempt or start a timer. Writes are disabled by default; daily snapshots and REST reads do not invoke them. Native single-page forms are supported; custom widgets and multi-page submission need further adapters.

## Local REST API

```bash
ismu serve --port 8765
# In another terminal, with the same state directory:
python examples/local_api.py /v1/courses
```

The server binds only to `127.0.0.1`. Each request requires the bearer token saved in the state's private `api-token` file. The example reads it locally without printing it. Login remains an interactive CLI operation. The server rejects browser Origin requests and does not support remote deployment.

| GET endpoint | Resource |
|---|---|
| `/v1/status` | Live authentication and selected study |
| `/v1/courses` | Current enrolments with details |
| `/v1/courses/{code}/files` | Recursive file inventory |
| `/v1/courses/{code}/syllabus` | Interactive syllabus |
| `/v1/courses/{code}/ropots` | ROPOT metadata |
| `/v1/courses/{code}/forums` | Current course/group posts |
| `/v1/courses/{code}/reservations` | Reservation series |
| `/v1/courses/{code}/text?path=um%2Fnotes.txt` | Course-relative text file |
| `/v1/notes` | Note blocks |
| `/v1/snapshot` | Course collection with completeness and errors |
| `/v1/submissions` | Boxes explicitly open to the account |
| `/v1/courses/{code}/submissions` | Visible submission folders |
| `/v1/courses/{code}/submission?path=ode%2Fhomework-1%2F` | Box permissions and visible files |
| `/v1/calendar` | Native calendar events, optional date/kind filters |
| `/v1/timetable` | Weekly timetable and exception text |
| `/v1/exams` | Exam/registration series across the selected study |
| `/v1/mail/folders` | Mailbox folders and counts |
| `/v1/mail/messages` | Paged message headers |
| `/v1/mail/messages/{id}?folder=FOLDER_ID&allow_mark_read=true` | Explicit MIME body/attachment metadata read |
| `/v1/notices` | Noticeboard cards, optional board/paging filters |
| `/v1/notices/{id}?allow_mark_read=true` | Explicit full-notice read |

There are **21 REST GET endpoints**. The [API reference](docs/api.md) documents parameters, errors and CLI equivalents. [OpenAPI 3.1](docs/openapi.json) describes every REST route and response model.

## Reliability and scope

- Only known read routes are allowed, including on redirects. Requests are paced, bounded and authenticated. IS MU has state-changing GET routes, so this client does not accept arbitrary URLs.
- Reading a discussion may update its unread state, just as reading it on the website does. No registration, posting, uploading or deletion methods are provided. ROPOT actions require the explicit attempt interface and are never called by the scanner or REST server.
- Locked or unavailable material stays locked. File metadata does not guarantee that its body is accessible. Downloads are capped at 20 MB; binary files need an explicit destination.
- Forum reads are bounded to five pages per forum by default. Partial paging and suppressed ROPOT lists make snapshots incomplete. An empty grade is not treated as proof of unfinished work.
- Czech page labels are used by the parsers. Dates are interpreted in `Europe/Prague`; automatic language selection is not implemented.
- This package stays at the IS MU access layer. Document extraction, task inference, Telegram delivery and scheduling belong in downstream applications. See [operating guidance](docs/operations.md).

## Documentation and development

- [API and CLI reference](docs/api.md)
- [Python ergonomics, error handling and migration](docs/python.md)
- [Student services: submissions, calendar, exams, mail, notices](docs/student-services.md)
- [ROPOT questions and submission](docs/ropot.md)
- [Data semantics](docs/data-model.md)
- [Authentication and upstream protocol](docs/protocol.md)
- [Operation, limits and troubleshooting](docs/operations.md)
- [Contributing and tests](CONTRIBUTING.md)
- [Security and private data](SECURITY.md)
- [Permissions, licensing and responsible use](docs/responsible-use.md)
- [Changelog](CHANGELOG.md)

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python tools/generate_models.py --check
mypy --follow-imports=silent --ignore-missing-imports --warn-unused-ignores tests/typing/public_api.py
ruff check .
ruff format --check .
python -m openapi_spec_validator docs/openapi.json
python -m build
```

All automated tests use synthetic data and local mocks; they do not log into IS MU. The CI workflow tests Python 3.11–3.13 on Linux. macOS is intended to work but has not been live-tested; Windows permission handling is not supported yet.

## License

[MIT](LICENSE). The license applies to this project's code and documentation. Downloaded teaching materials, student records, forum content, dependencies, and university branding retain their own rights and are not bundled with the project. No institutional endorsement is implied.
