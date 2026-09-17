# Python interface and upgrading to 0.4

The client hides IS routing and parsing behind named operations. Returned data
stays JSON-compatible, while the Python package supplies type information for
editors and static checkers. No scheduler, document processor or assistant logic
is included.

## Login and reads

```python
from getpass import getpass
from ismu import Client

client = Client(".ismu-state")
client.login(input("UCO: "), getpass("IS MU password: "))

for course in client.courses():
    syllabus = client.syllabus(course["code"])
    print(course["title"], len(syllabus["sections"]))
```

For scripts after an interactive `ismu login`, construct `Client` and read
directly; saved cookies are loaded automatically. The Python constructor takes
its explicit `state_dir`; the `IS_MU_STATE_DIR` environment shortcut belongs to
the CLI. Passwords are never stored. Login reuses an already authenticated
session and does not switch accounts based on a new username. Use a separate
state directory per account. MFA/challenges still require additional adapters.

`client.login()` invalidates its cached course index. `client.status()` refreshes
that index. Use a fresh client per collection run; instances and state directories
are not intended for concurrent writers.

`course(code)` and `courses()` return independent public copies. Editing a nested
forum or another returned field cannot change the client's later request routing.
Private `detail_params` are excluded from both operations.

## Typed data without changing JSON

```python
import json
from ismu import Client
from ismu.models import Course, Ropots

client = Client(".ismu-state")
courses: list[Course] = client.courses()
tests: Ropots = client.ropots(courses[0]["code"])
payload = json.dumps(tests, ensure_ascii=False)
```

All public read methods, login and ROPOT views have annotated return types.
Nested types include `Course`, `SyllabusSection`, `Ropot`, `Availability`,
`ForumPost`, `CalendarEvent`, `MailMessage`, and `AttemptView`. Import them from
`ismu.models`. The package includes the PEP 561 `py.typed` marker.

`NotRequired` means a key can be absent; `str | None` means a present value can
be null. Types describe source uncertainty rather than filling it with defaults.
For example, a missing syllabus entry has sections and a state but no body text.
These declarations do not validate arbitrary JSON at runtime. Existing indexing,
JSON serialization and CLI/REST consumers keep working.

Types are generated from `docs/openapi.json`, which also contains Python-only
login, download and attempt-view models. Those schemas do not add REST routes.
Regenerate with `python tools/generate_models.py`, then `ruff format src/ismu/models.py`.
CI checks for schema/type drift and checks a consumer fixture with mypy, including
expected failures for misspelled keys and incorrect arguments. This is a public
typing contract check, not a claim that the entire parser implementation is
strictly type-checked.

## Errors callers can distinguish

Every expected library failure subclasses `ISMUError`. Existing base-class
handlers keep working; narrower exceptions support more useful recovery.

| Exception | Meaning | Local REST status |
|---|---|---|
| `AuthRequired` | Missing/expired session or login challenge | 503 |
| `InvalidInput` | Invalid ID, path, argument or answer | 422 |
| `UnsupportedOperation` | Unsupported control/action or missing explicit action flag | 422 |
| `StateError` | Missing, stale or incompatible local state | 409 |
| `AttemptUncertain` | A write may have occurred; do not replay it | Not exposed through REST |
| `NetworkError` | HTTPS connection or response-body failure | 502 |
| `RateLimited` | Upstream HTTP 429 | 503 |
| `ParseError` | An unsupported page structure, possibly an access-denied page | 502 |
| `UpstreamError` | Other upstream HTTP errors or response limits | 502 |

`NetworkError`, `RateLimited` and `ParseError` subclass `UpstreamError`.
`AttemptUncertain` subclasses `StateError`. `UpstreamError.status_code` contains
the upstream HTTP status when available, otherwise `None`. Local filesystem
failures may still raise `OSError`.

```python
from ismu import AuthRequired, Client, InvalidInput, ISMUError, UpstreamError

client = Client(".ismu-state")
try:
    courses = client.courses()
except AuthRequired:
    print("Log in with ismu login and retry.")
except InvalidInput as exc:
    print(f"Correct the request: {exc}")
except UpstreamError as exc:
    print(f"IS could not be read: {exc.code}; HTTP {exc.status_code}")
except ISMUError as exc:
    print(f"Client operation stopped: {exc.code}")
```

CLI and REST errors retain `error` and `message`, adding the stable `code` field
for library errors and `upstream_status` when known. Snapshot and exam-series
failures retain their human-readable `error`, adding `code`. Partial snapshots
can contain authentication errors instead of raising; always inspect `complete`
and `errors`. Snapshot and exam-series collection stop further reads on
`AuthRequired` or `RateLimited`, returning the observations already collected
with `complete=false`. Remaining resources are absent, not empty or successful.
Failures before collection starts still raise. A machine-readable error is not
authorization to retry a write.

## ROPOT convenience objects

See the [ROPOT guide](ropot.md) for `client.ropot(item)`, `question_list()`,
`question(number)`, `Question.answer()` and multiple-input examples. Helpers
return immutable local `Answer` objects tied to the exact captured form and
private state directory. Their construction does not save or submit anything.
The original dictionaries and native-field answer mappings remain supported.

## Migration notes

- Normal successful CLI/REST JSON shapes remain compatible; this release adds
  optional error metadata and Python helpers. There are still 21 REST GET routes.
- `course()` no longer exposes `detail_params` or the mutable internal cache.
- Use `client.login(...)` instead of reaching through `client.transport.login(...)`.
  The old call and old exception imports still work.
- Prefer specific exceptions where useful; `except ISMUError` remains valid.
- **REST behavior change:** upstream/parser/network failures now return 502,
  rate limiting returns 503 and local state failures return 409. Previously many
  of these were grouped under 422. Update consumers that assumed every failure
  other than authentication was 422.
- A helper `Answer` is invalid after a new capture/save/start/submit, even when
  the returned HTML is identical. Read the current question again to build a new
  answer. A successful `save()` followed by `submit(confirm=True)` submits the
  saved form without reusing an old `Answer` object.
- Authentication coverage and upstream parser coverage are unchanged. This
  release was verified with synthetic pages and mocked HTTP, not additional
  student accounts or real course submissions.
