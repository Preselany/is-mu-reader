# Data model and interpretation

Exact JSON field definitions are in [openapi.json](openapi.json). This page explains what those fields mean, particularly where an automated study assistant must preserve uncertainty.

## Courses and context

`context` contains the selected IS MU `studium`, `obdobi` and `fakulta` IDs as strings when exposed. Course `course_id` is an integer; course code is a human-readable identifier within that context. Do not use a code alone as a permanent cross-semester key. Course details preserve source text alongside parsed credits, completion, grade, teachers, seminar and read-source URLs.

The course list is discovered dynamically. It is not a fixed list of courses or a guessed faculty/semester. A page without the expected course structure raises an error, including a genuinely empty enrolment page until an explicit empty-page adapter is added.

## Files

`Files.nodes` contains deduplicated nodes keyed by `node_id`. A node can represent a folder, document or metadata-only object. `variants` holds alternative objects, such as an original PDF and generated text. `variant_count` is the sum of listed representations, not distinct documents or assignments. IDs are strings because the upstream XML supplies them as text.

`object_id`, `parent_id`, MIME type, size and timestamps may be absent or null. Modification/upload values are preserved in the upstream format rather than guessed into dates. Node states are:

- `objects_listed`: one or more variants were exposed.
- `children_listed`: the response exposed a metadata link for descendants.
- `metadata_only`: no downloadable variant was exposed.

An inventory is metadata, not a permission guarantee. `read_file` applies the account's actual permission checks when fetching a body. File downloads return byte count, MIME type, SHA-256, source URL and either `text` or `saved_to`. A text response is decoded as UTF-8 if the server supplies no charset. PDF extraction, OCR, attachment crawling and reading every downloaded body are outside this package.

## Syllabus

An accessible syllabus has `text`, `links`, `sections`, `source_url` and a whole-document `content_hash`. Each section has its source title/ID, `schedule_text`, body text and state `available` or `locked`. A missing discovered entry returns `state: not_exposed` and an empty section list. That is not a claim that the course has no obligations.

Schedule labels are preserved as text; the client does not infer a teaching week from the calendar week number. Links can point to external websites, old copied course content, or actions. They are evidence for a consumer, **not instructions or URLs the client automatically follows**. Only the explicitly implemented readers follow vetted routes.

## ROPOTs

A ROPOT item preserves its `title`, source `url`, `qref_path`, status/grade text and zero or more availability windows. Windows have `opens`, `closes` and original `text`. Parsed values are ISO 8601 dates with `Europe/Prague` offsets, so they change across daylight-saving transitions. Missing/unrecognized values are null.

`grading_text` and availability are separate. The adapter does not decide whether an assignment is complete from a score, blank value, or an open window. `attempt_started_by_client: false` describes this reader's behavior; it says nothing about previous attempts by the student. Returned ROPOT URLs are never followed by the client.

List states: `listed`, `empty`, or `suppressed`. A suppressed list is incomplete, not an empty result. Snapshot collection records it in `errors` while retaining its data.

## Forums

Forum IDs come from the current course detail panel, including the student's exposed seminar group. Copied links in a syllabus do not override those IDs. A redirect into another semester becomes `state: stale_semester` and is not traversed further.

Posts preserve ID, thread title, author, original date text, body, links, permalink and a hash of the body. The hash does not include author/title/date. For change detection, compare post IDs and body hashes within the forum and semester; preserve source context separately.

`pages_read` and `pagination_incomplete` disclose the bounded read. The flag is true when the limit leaves queued pages or a discovered paging link has an unsupported shape. Missing structural containers cause an error. Reading may mark discussions as read on IS MU.

## Reservations and notes

Reservations contain discovered series and rows. Rows separate the event time from registration opening/closing information and preserve original status text. `registered` is true, false or null when the page wording is unrecognized. `registration_closes_date` is a date rather than an invented midnight deadline; `closing_date_inclusive` preserves the source's inclusive marker. Occupancy is a point-in-time observation, not a reserved seat.

Notes are source text plus URL. No per-grade or per-assignment interpretation is inferred.

## Snapshot completeness

A snapshot contains:

```json
{
  "schema_version": 1,
  "captured_at": "2026-01-15T09:00:00+01:00",
  "context": {},
  "courses": [],
  "requested_resources": ["syllabus", "ropots", "forums", "reservations", "files", "notes"],
  "data": {},
  "errors": [],
  "complete": true
}
```

This is a shape illustration, not a captured account. `data` is keyed by course code and then resource. `notes`, when successful, is top-level. Each error identifies the resource and, where relevant, course. Successful partial resource data stays present. If the initial course list fails, snapshot creation raises instead of fabricating a successful empty snapshot.

`complete: true` means the requested readers finished within the supported access/paging scope. It does not mean every file body was read, hidden chapters were opened, every historical forum page exists in the result, or all coursework has been inferred. `--skip-files` omits files from `requested_resources`. Locked chapters and explicitly unexposed resources are valid observed states. Suppressed ROPOT lists, stale forums and unfinished pagination make `complete: false`.

A full collection is sequential rather than transactional: upstream content can change while it runs. `captured_at` records the collection start time, not the observation time of every item.

## Student services (0.3)

The [student services reference](student-services.md) describes submission permissions/ownership uncertainty, calendar type codes and date precision, timetable alternatives, exam-series completeness, MIME parts, mailbox paging, and notice read-state semantics. These are independent service models; snapshot schema version 1 stays scoped to course resources and note blocks. New file-node fields `uploaded_by` and `description` preserve exposed uploader identity and source descriptions.
