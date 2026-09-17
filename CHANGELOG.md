# Changelog

## Public source publication — 2026-09-17

- Publish the source on [GitHub](https://github.com/Preselany/is-mu-reader) under MIT, with synthetic fixtures and no private account data.
- Document the separate operator, software and assessment permissions required for use. Request pacing does not replace operator consent for bulk collection.
- Enable private vulnerability reporting and add package metadata links.

## 0.4.0 — Python interface polish

- Add public `Client.login()` and `Client.ropot(item_or_qref)` entry points. Course reads return independent public copies without `detail_params`; editing returned data cannot alter cached routing.
- Annotate public reads and ROPOT views with JSON-compatible `TypedDict` return models. Ship `py.typed`, a schema-driven generator and a static consumer contract check.
- Add immutable `Question`, `AnswerField`, `Choice` and `Answer` helpers. Answers can use displayed question numbers and field positions; stale/cross-state answers and duplicate field answers are refused before writing. Native field mappings remain supported.
- Add distinct input, unsupported-operation, state, network, rate-limit, parser and upstream errors while preserving `ISMUError`/`AuthRequired` and legacy import paths. CLI/REST and partial collections expose machine-readable error codes.
- Stop snapshot and exam-series collection after rate limiting or session expiry, retaining partial results. Reject malformed session files with an actionable error, without exposing cookie data.
- Include the type checker in development dependencies so the documented checks and CI work in a fresh environment.
- **REST compatibility change:** upstream/parser/network failures use 502, rate limits use 503, and state failures use 409 rather than grouping these failures under 422. Successful response shapes and the 21 read routes remain compatible.
- Document migration, typing and the ergonomic attempt flow. Verify offline using synthetic data and mocks; no new live course attempts or submissions were performed.

## 0.3.0 — 2026-09-16

- Add submission-box discovery and permission/visible-file reads, native calendar events, weekly timetable, exam-series overview, IS mailbox reads/exports, and paginated noticeboard cards.
- Add eleven REST GET routes (21 total), Python/CLI methods, response schemas and a student-services guide.
- Support RFC822/MIME exports with explicit destinations. Full mail and notice reads require opt-in because live checks confirmed that viewing can mark items read. Header/card listing remains separate.
- Reject unknown, empty and duplicate REST query parameters. Preserve existing course snapshot scope; no scheduler, document extractor, task inference or messaging integration is added.
- Add synthetic contract tests and live checks on one account's current and earlier enrolments. Submission acceptance/grading and arbitrary time-limited permissions are not inferred.

## 0.2.0 — explicit ROPOT attempts

- Native single-page ROPOT inspection, question extraction, draft saving and submission through Python/CLI.
- Mathematical image alternatives retained in readable questions.
- Separate anonymous cookie jar for public demos; hidden tokens kept in private caches.
- Explicit write flags, submission receipt verification and persistent protection against replay after an uncertain write.
- Complete flow verified on an official ungraded public demo; no course attempt started or submitted.
- Read-only scanner and REST behavior preserved.

## 0.1.0 — initial local release

- Standalone HTTP password login with private session persistence.
- Dynamic discovery of current courses and enrolled seminar groups.
- Native recursive XML file inventory and bounded text/binary downloads.
- Syllabus, ROPOT metadata, course/group forums, reservation and note-block readers.
- JSON CLI, Python client, token-protected local REST API and combined snapshots.
- Explicit locked, suppressed and partial states; source links and content hashes.
- Portable packaging, MIT license, OpenAPI specification, examples and offline CI checks.

Live verification covered one student account. No package-registry release has been created by this project.
