# Authentication and upstream protocol

This document records observed read routes, not a university-supported contract. The initial implementation was verified with an ordinary student account in September 2026. Other account types and page variants have not been exhaustively tested. No instructor API key is required by these readers.

## Layers

```text
CLI / localhost REST / Python caller
                  |
                Client       enrolment discovery, resource selection, snapshots
                  |
               Transport     HTTPS session, allowlists, pacing, limits, cookies
                  |
                IS MU        XML metadata, JSON with HTML, server-rendered HTML
                  |
                parsers      pure text/HTML/XML -> JSON-compatible structures
```

- `transport.py` owns authentication and every network request.
- `parsers.py` and `resources.py` contain pure parsing functions; no JavaScript is executed.
- `client.py` selects enrolled courses and composes readers; `services.py` adds student-service operations.
- `cli.py` formats command output and handles interactive login.
- `server.py` exposes a small token-protected, loopback-only REST surface.

Requests are made with [Requests sessions](https://requests.readthedocs.io/en/latest/user/advanced/#session-objects), using their connection reuse and cookie handling. HTML is parsed with Beautiful Soup. XML is parsed with the Python standard library after rejecting DTD/entity declarations.

## Normal login

1. Fetch `https://is.muni.cz/auth/` and follow the normal HTTPS redirects.
2. Find `form.islogin_form` on `muni.islogin.cz` with `credential_1`.
3. Copy the form's hidden inputs and actual named submit button, then supply `credential_0` and `credential_1` in a form POST to its observed action.
4. Verify the authenticated student page and save only session cookies.

The submit button is significant; merely posting username/password and hidden fields did not complete the observed flow. The client does not execute login scripts, scrape browser profiles, or rely on a browser-exported cookie jar. Passwords are not persisted or logged. Login POSTs are not retried and 307/308 credential-preserving redirects are rejected. TLS verification stays enabled.

Only `is.muni.cz` and `muni.islogin.cz` on HTTPS port 443 are allowed. A missing password form or an additional challenge produces `AuthRequired`. MFA, institutional SSO alternatives and challenge completion are not implemented. Authentication refresh requires interactive login; unattended password storage is not part of this client.

## Observed readers

Query examples below use IS MU's semicolon separators. Values are URL-encoded by the client. IDs are discovered rather than hard-coded.

| Upstream route | Request/source | Interpretation |
|---|---|---|
| `/auth/student/predmety` | Optional selected study/period/faculty | Course cards plus embedded `is.Design.init(...)` JSON configuration |
| `/auth/student/index_ajax` | `option=predmet-option-info` and observed course-detail parameters | JSON object's `html` field supplies course metadata and current resource links |
| `/auth/dok/fmgr_api` | `url=/el/{faculty}/{semester}/{code}/;strom=1` | Native recursive XML file inventory; nodes and variants are deduplicated |
| `/auth/el/{faculty}/{semester}/{code}/...` | Course-relative document path | Text or bounded binary body |
| `/auth/el/.../index.qwarp?mode=all` | Discovered syllabus entry | Redirect may lead to `/auth/elearning/io/` with `qurl`, `mode=all`, `so=na` |
| `/auth/elearning/test_pruchod_el_student` | `jen_predmet={course_id}` | Per-course ROPOT metadata avoids a potentially suppressed global list |
| `/auth/diskuse/diskusni_forum_predmet` | `guz={observed_forum_id}` | Resolves the current course/group forum; semester is verified |
| `/auth/discussion/predmetove/.../prispevky/` | Observed post-list and supported pagination links | Forum posts; bounded traversal |
| `/auth/student/prihl_na_zkousky` | Observed `obdobi`, `predmet`, `serie` | Reservation list only; action parameters are refused |
| `/auth/student/poznamkove_bloky_nahled` | Selected study context | Note-block page text |

This is a structured wrapper over accessible resources, not a claim that every browser feature has a corresponding supported JSON endpoint. ROPOT answers, registrations and private staff functions are deliberately outside the reader interface.

## Student-service routes added in 0.3

| Upstream route | Reader |
|---|---|
| `/auth/student/studijni_materialy` | Open submission boxes for the selected study/period |
| `/auth/el/.../ode/.../` | Native `is.fmgr.set` folder JSON; permissions and visible children |
| `/auth/calendar/` | Literal event array in `RozvrhKalendar.init`; no synchronization-token operation |
| `/auth/rozvrh/zobraz/muj` | Weekly timetable cells and source exception text |
| `/auth/student/prihl_na_zkousky` | Exam overview and discovered series; register/unregister parameters blocked |
| `/auth/mail/` | Folder/header table; adapter applies bounded local offset/count |
| `/auth/mail/?handle_up=Prove%C4%8F;mail_type_up=marked;mail_handle_action_up=download;mark={id};folder_id={folder}` | Opt-in single-message RFC822 view; can mark mail read and is blocked by the ordinary read allowlist |
| `/auth/vyveska/`, `/auth/noticeboard/{board}/{section}/` | Notice cards and same-board `/stranka/{page}/` pagination |
| `/auth/noticeboard/?operace=dej_zpravu;zprava_id={id}` | Opt-in full-notice view outside the ordinary read allowlist; can change read tracking |

Mail export and full-notice viewing require `allow_mark_read=True` (REST: `allow_mark_read=true`); redirect validation binds each to its exact chosen view and login handling. The package does not restore read flags automatically. No mail-send or other mailbox mutation is permitted. Message and attachment download uses the same login session; no IMAP password is stored. See [student services](student-services.md) for interpretation and verification limits.

## Read boundary and transport limits

The transport allowlists both path and query-field shapes on every read, including redirects. It is not sufficient to allow all GETs: some IS MU GET links change data. Unknown actions are refused. Arbitrary external syllabus links are returned as source references, never fetched by a generic crawler.

Requests are spaced by at least 0.6 seconds. Connection/time-out failures before response-body reading receive at most two GET retries with 1-second and 2-second backoff. Interrupted response bodies fail explicitly and are not automatically replayed. HTTP errors, including 429, stop that read. There is no retry loop for passwords.

Connection timeout is 10 seconds, socket read timeout 45 seconds, redirect limit 12, and response-body cap 20,000,000 bytes. These are per-request/socket limits, not an overall deadline for a snapshot. File-tree and syllabus reads can still be large within that cap. Parallel crawling is not implemented.

## Explicit ROPOT attempt protocol

Version 0.2 adds a separate opt-in module, documented in [ROPOT interaction](ropot.md). The ordinary transport still permits only its login POST. `RopotTransport` permits form POSTs only to one selected ROPOT entry route when writes are explicitly enabled. Starting uses observed `new`/`slozit` fields; saving and submission use the actual `uloz_str_*` and `uloz` buttons. Hidden fields are preserved, including duplicate names. The anonymous demo uses `/elearning/test_pruchod?testurl=...`; authenticated course entry uses `/auth/elearning/test_pruchod_el_student?testurl=...`. No question/answer REST endpoint has been enabled.
