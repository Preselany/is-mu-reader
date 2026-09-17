# Student services reference

Added in version 0.3. These adapters expose IS MU data through the existing HTTPS session. They do not extract PDF/DOCX contents, decide what a student should do, deliver notifications, or run a scheduler. All identifiers and examples below are synthetic or placeholders.

## Submission boxes (Odevzdávárny)

```python
opened = client.open_submissions()
boxes = client.submissions("CODE")
box = client.submission_box("CODE", "ode/homework-1/")
```

```bash
ismu submissions
ismu submissions CODE
ismu submission-box CODE ode/homework-1/
```

| REST GET | Meaning |
|---|---|
| `/v1/submissions` | Boxes explicitly open to this user in the selected study/period |
| `/v1/courses/{code}/submissions` | Visible folders under `ode/`, combined with the open-box index |
| `/v1/courses/{code}/submission?path=ode%2Fhomework-1%2F` | One folder's permissions, description, children and visible file metadata |

The open index is the **Vám otevřené odevzdávárny** section of the study-materials page. Course discovery uses the recursive file inventory. Folder details decode the native `is.fmgr.set(...)` JSON; no JavaScript is executed. Only course-relative `ode/.../` directories are accepted by the detail reader.

`open_for_submission: true` is positive evidence from the open index. A course folder absent from that index receives `null`, not a guessed closed/overdue state. `visibility` is `readable`, `insert_only`, or `metadata_only`. File-manager `w` means permission to insert; `r` means permission to read. `permissions`, `attributes`, and `inherited_attributes` preserve upstream names and values with HTML removed from descriptions. Time-limited permissions, when present, remain in that native structure: there is no fabricated universal deadline field.

`submission_box_code` preserves IS's classification (`1` was observed for a box and `2` for its parent area). Course discovery deliberately returns candidate folders, including organizational subfolders; consumers can inspect details to distinguish them.

`visible_files` is the file inventory exposed beneath the selected folder. `own_files` requires a node's exposed uploader ID to equal the authenticated user's ID. `submission_status: own_files_visible` only means that matching files were found. It does **not** mean accepted, correct, graded, or complete. Otherwise the status remains `unknown`, even if the list is empty: an insert-only folder may hide prior uploads. No upload, replacement, deletion, or grading operation is provided. Use the existing course-relative file reader to download accessible material.

Verification covered a live empty current-semester index, a populated earlier-semester index, 31 folders in one course, and an insert-only box. Positive ownership matching and permission date preservation are synthetic-test cases; no homework was submitted during development.

## Calendar and timetable

```python
calendar = client.calendar(start="2026-09-14", end="2026-09-21")
classes = client.calendar(start="2026-09-14", end="2026-09-21", kinds=["timetable"])
weekly_pattern = client.timetable()
```

```bash
ismu calendar --start 2026-09-14 --end 2026-09-21 --kind timetable
ismu timetable
```

REST routes: `/v1/calendar?start=2026-09-14&end=2026-09-21&kinds=timetable` and `/v1/timetable`.

Calendar dates must be supplied together, as `YYYY-MM-DD`. The range includes `start` and excludes `end`. Filtering happens locally after reading the native event array; it includes events overlapping the requested dates. Omit both dates to return every event exposed in the response. REST `kinds` accepts a comma-separated list; repeat CLI `--kind` for multiple kinds.

| `kind` | Native type codes | Meaning |
|---|---|---|
| `notice` | `5_1` | Events from noticeboards |
| `exam_registration` | `6_1` | Exam/teaching registration boundaries |
| `exam` | `6_8` | Exam events |
| `ropot` | `6_3` | ROPOT availability events |
| `semester` | `6_4` | Semester milestones |
| `holiday` | `6_5` | Holidays |
| `timetable` | `8_1`, `8_2` | Scheduled teaching occurrences |
| `online_teaching` | `8_4` | Online teaching |
| `unknown` | Any new code | Preserved for forward compatibility |

The event array comes from `RozvrhKalendar.init` inside `is.Design.init`. Types `6_8` and `8_2` are advertised by the native legend; other listed types were present in the live sample. No synchronization token is created or calendar preference changed. Hidden layers remain included, with `hidden_in_ui` and `hidden_type_codes` showing the source preference.

Timed events receive `Europe/Prague` UTC offsets, including daylight-saving changes. All-day events remain dates. `end` can be null; the client does not invent a duration. The upstream all-day end convention is preserved, including zero-length holiday records. `id` is a SHA-256 of the provider identifier, type, start/end and title; a changed event can therefore receive a different normalized ID. Preserve `provider_id` and source context if your application needs its own reconciliation policy.

IS may return several semesters at once. `coverage_start`/`coverage_end` describe the supplied data, not a completeness guarantee for a requested date range. `upstream_event_count` is measured before local filtering. Calendar items can include **registration deadlines**, which are distinct from the actual event. Registration series for teaching can appear under exam-related calendar types.

`/v1/timetable` returns the selected semester's weekly pattern, course/group labels, rooms, times and original exception text. Weekday numbers are ISO-style Monday=1 through Sunday=7. It keeps alternative lecture slots and irregularity text; it does not decide which alternative is mandatory or expand recurrence itself. For dated occurrences use the calendar reader.

## Exams and registration series

```python
exams = client.exams(max_series=30)
```

```bash
ismu exams --max-series 30
```

REST: `/v1/exams?max_series=30`. Allowed limit: 1–100. The reader loads the selected study's exam overview, discovers its series, then reads at most the requested number of series. It also retains registered rows from the overview as `registered_items` and preserves the overview text, including courses with no announced dates.

IS uses the same upstream application for exams and teaching-seat reservations. Each discovered series has `kind: teaching_reservation` when the source labels it that way; otherwise `exam_or_other` preserves the ambiguity. Rows reuse the existing reservation model: event time, capacity, occupied places, registration state/windows and source text. Only date precision exposed by the parser is normalized; the full source text retains finer details.

`series_count` is the number discovered, even if the read limit is lower. `pagination_incomplete`, per-series `errors`, and `complete` make partial collection explicit. No registration, cancellation, exchange, or enrolment action is followed. `reservations(code)` remains available for course-panel series.

## IS mailbox

No second password is needed for the HTTPS adapter. IS also documents IMAP, but that separate protocol requires a secondary password and is not implemented here.

```python
folders = client.mail_folders()
page = client.mail_messages(folder="10", start=1, limit=50)
message = client.mail_message("20", folder="10", allow_mark_read=True)
client.download_mail("20", folder="10", destination="downloads/message.eml", allow_mark_read=True)
client.download_mail(
    "20", folder="10", part_id="1.2", destination="downloads/attachment.bin", allow_mark_read=True
)
```

```bash
ismu mail folders
ismu mail list --folder 10 --start 1 --limit 50
ismu mail read 20 --folder 10 --allow-mark-read
ismu mail download 20 --folder 10 --allow-mark-read --output downloads/message.eml
ismu mail download 20 --folder 10 --allow-mark-read --part 1.2 --output downloads/attachment.bin
```

| REST GET | Meaning |
|---|---|
| `/v1/mail/folders` | Folder IDs, names/types, counts and forwarding indicator |
| `/v1/mail/messages?folder=10&start=1&limit=50` | One page of message headers and read flags |
| `/v1/mail/messages/20?folder=10&allow_mark_read=true` | Decoded message headers, MIME text parts, attachment metadata |

Replace numeric examples with IDs from the listing. Message reads require a folder ID. List `start` is a one-based offset (1–1,000,000); `limit` is 1–100. The default folder is the one IS selects when the parameter is omitted. The native full interface returns the header table even when given paging parameters, so the adapter reads that table (within the response-size cap) and slices it locally. `pagination_mode` is `local`; `upstream_returned_count` records the source table size. Use `next_start` to continue through that table. `upstream_truncated` means the returned table was shorter than its declared total, or the total was missing. In that case there may be unavailable records even when `next_start` is null. `pagination_incomplete` means more visible messages remain after this page or the upstream table may be incomplete; it does not promise that earlier offsets were fetched. Concurrent mailbox changes can shift offsets, so consumers should deduplicate by message ID.

Message bodies use the native single-message **download** operation with the existing session. **Export marks an unread message as read**, as verified on a previously unread message. Therefore body and attachment access requires `allow_mark_read=True` in Python, `--allow-mark-read` in the CLI, or `allow_mark_read=true` in REST. Without that opt-in, the call fails before any upstream request. Header/folder listings do not open messages. The ordinary transport blocks export actions; the opted-in view uses an exact URL validator on redirects. No automatic flag restoration is performed by the library. The development check was restored to its prior unread state. The returned MIME body parts include their media type (`text/plain` or `text/html`); HTML is untrusted text, not safe markup for direct rendering. No remote images, tracking URLs, or body links are fetched. Headers preserve repeated values as arrays.

Attachment metadata includes a MIME `part_id`, filename, MIME type, decoded byte count and SHA-256. Download a selected attachment or the native complete mail export through Python/CLI. The complete export may retain an mbox `From ` separator. Binary downloads deliberately have no REST route, matching the existing course-file interface. Destinations are explicit, private, and must not already exist; upstream attachment filenames never determine a filesystem path.

Mail forwarding is reported as a boolean. Forwarded messages not retained in the IS mailbox cannot be retrieved from this adapter. Archived IS communications, when exposed as a mailbox folder, are listed like other folders. There are no send, reply, draft, explicit flag-editing, move, delete or address-book methods; viewing side effects are disclosed separately.

## Noticeboard (Vývěska)

```python
cards = client.notices()
board = client.notices(board="FI/zpravy", max_pages=2)
# Explicit: opening a notice may change its read status on IS.
body = client.notice("12", allow_mark_read=True)
```

```bash
ismu notices
ismu notices --board FI/zpravy --max-pages 2
ismu notice 12 --allow-mark-read
```

REST: `/v1/notices?board=FI%2Fzpravy&max_pages=2`. The omitted-board overview is curated; it does not enumerate every notice in every board. `board_links` provides discovered collection links. A board selector is a board name or `board/section`; full message permalinks are not accepted. Pagination follows only that board, with a 1–20 page limit (default 1), deduplicates message IDs and reports `pagination_incomplete`.

Cards preserve ID, title, author, dates, priority (`important`, `targeted`, `normal`), unread state, permalink and any exposed preview. Preview can be null. Listing does not open full messages.

Full notice retrieval is an explicit Python/CLI operation because upstream viewing can affect read tracking. `allow_mark_read=False` fails before any request. REST also supports `/v1/notices/{id}?allow_mark_read=true`. Missing or false opt-in returns HTTP 422 before fetching the message. No automatic read-state restoration, starring, posting, or other notice mutation is included. The development read-state check was restored to its original unread state and verified on the source board.

## Collection boundaries and validation

The original `snapshot()` contract remains a collection of course resources plus note blocks. It does not silently add mailbox bodies or these new services. Applications compose these methods as needed and decide their own storage, polling, analysis, and notification policies.

Live checks used one authorized student account and its accessible current/earlier enrolments. Synthetic tests cover parser changes, permissions, unknown/hidden calendar types, range boundaries, MIME decoding, pagination, strict route/query validation and REST dispatch. Other account layouts/languages can still need adapters. Unknown source structure should produce an error rather than be treated as proof that no records exist.

Official background: [study materials](https://is.muni.cz/napoveda/student/materialy_student), [calendar](https://is.muni.cz/napoveda/komunikace/kalendar), [mail](https://is.muni.cz/napoveda/komunikace/mail), [noticeboard](https://is.muni.cz/napoveda/komunikace/vyveska). These explain the applications; they do not promise stability of the internal routes used here.
