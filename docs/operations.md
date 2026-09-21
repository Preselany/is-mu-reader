# Operating the reader

## State and secrets

Choose a persistent private directory and use it consistently:

```bash
export IS_MU_STATE_DIR="$PWD/.ismu-state"
ismu login --username YOUR_UCO
ismu status
```

On Windows PowerShell:

```powershell
$env:IS_MU_STATE_DIR = Join-Path $env:LOCALAPPDATA "ISMUReader\state"
.venv\Scripts\ismu.exe login --username YOUR_UCO
.venv\Scripts\ismu.exe status
```

On Windows Command Prompt, use `set "IS_MU_STATE_DIR=%LOCALAPPDATA%\ISMUReader\state"` instead. Environment variables apply to the current terminal; pass `--state-dir` explicitly when starting from another terminal or scheduler.

The directory contains `session.json` and, after starting the REST service, `api-token`. On Linux, private files use mode 600 and state directories use mode 700. On Windows, files are created with a protected ACL granting full control only to the current Windows user, with inherited grants disabled. This applies to cookies, API tokens, ROPOT caches, snapshots and downloads. Existing secret files with broader permissions are refused before reading. State directories are tightened when saving; existing download parent directories retain their permissions.

Use a dedicated state directory beneath a trusted local parent. Choose local NTFS on Windows or a filesystem with working POSIX permissions on Linux. FAT/exFAT, network shares and cloud-synced state directories are not supported. Symlinks and Windows reparse points at state-file/output destinations or directories being created/secured are rejected. Parent paths must be trusted; this is not protection against hostile path replacement. Do not use your home directory itself as `--state-dir`, because its permissions would be tightened.

This is permission-based storage, not encryption. Administrators and other processes running as you remain outside its protection. Windows ACL behavior follows the native [protected DACL mechanism](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information). No administrator privileges are needed for ordinary files owned by your account.

Files and CLI output use UTF-8. For snapshots/downloads, use `--output` rather than shell redirection: Windows PowerShell 5.1 can transcode redirected text, and shell-created files do not receive the client's private permissions. UTF-8 answer JSON with or without a BOM is accepted. When moving to another computer or OS, create a fresh state directory and log in again; do not copy active ROPOT caches across accounts or attempt to resume an assessment by migrating local state.

The shipped `.gitignore` excludes default state, common private directories, snapshots, tokens and HAR files. Store custom-named reports and downloaded coursework outside the source repository; ignore patterns cannot recognize every private filename. Use one account and one writer per state directory. Do not share a state directory across multiple concurrent collectors.

An existing authenticated session is reused even if a different username is supplied to `login`. To switch accounts, choose another state directory. Deleting the local cookie file forgets the local session but does not revoke it on the server. To rotate the local API token, stop the server, remove its `api-token` file, and restart it. No token value is printed by the server.

## Building a daily assistant

First establish the required operator and IT approval described in [permissions and responsible use](responsible-use.md). Scheduled collection is not authorized merely because the client supports it. Choose approved storage and delivery channels for the data involved.

Use the CLI or Python library in the scheduler of your choice. Scheduling and message delivery are not built into this package. A recommended integration sequence is:

1. Run `snapshot` into a private file using a saved session.
2. Check its exit status, `complete`, `errors`, and `requested_resources`.
3. Compare the new snapshot to the last complete one, keeping partial data visibly marked.
4. Build a digest from newly observed posts, changed syllabus text, ROPOT windows and reservation events, with source links.
5. Let the delivery integration send that digest using its own credentials and authorization.

Keep collection separate from task interpretation. Do not turn an empty grade or missing page into a completed/unfinished assignment. Preserve the teaching-week labels supplied by the course instead of assuming calendar weeks. Files are inventoried, not all downloaded or interpreted. The [snapshot example](../examples/snapshot.py) demonstrates private storage and explicit handling of partial results.

Reuse a session until it expires. On `AuthRequired`, request an interactive login rather than repeatedly submitting a password. Avoid frequent polling, and back off on HTTP 429 or repeated failures. No runtime dependency on this project's original author, assistant application, browser, or workspace path is required.

Snapshots and exam-series collections stop further reads if the session expires
or IS returns HTTP 429. Already collected results remain available with
`complete=false` and the triggering error; unvisited resources are absent.
Other per-resource errors allow the bounded collection to continue.

## Local REST service

Run `ismu serve --port 8765` in the foreground. The server is serialized and intended for local trusted clients. `/v1/snapshot` can block other API requests while gathering data; the CLI is a better fit for scheduled full scans. Stop the server with Ctrl+C. No background service is installed automatically.

Never place this service behind a public reverse proxy without redesigning its authentication and isolation model. It is deliberately a loopback tool, not a hosted portal. The token protects against unauthenticated local requests; it does not protect against malware or another process running as the same OS user.

## Troubleshooting

| Symptom | Meaning and next step |
|---|---|
| CLI `AuthRequired` / REST 503 | Session is absent, expired, or login needs another step. Run interactive login with the same state directory. |
| Login reports already authenticated for another username | Existing cookie jar wins. Use a new state directory for the other account. |
| No courses / course-list structure error | Check the study/period selector and page language. Empty-enrolment pages are not yet distinguished from changed markup. |
| Unknown course / ambiguous code | Discover codes with `courses`; select the intended study/period. |
| Unexpected read route | IS MU changed a redirect or action. Inspect a sanitized route shape and update the allowlist narrowly. Do not disable it. |
| Parser structure error | The expected page container was absent, access changed, or markup changed. Retain the error rather than treating it as an empty resource. |
| ROPOT `suppressed` | The server withheld the list. The result is incomplete. |
| `pagination_incomplete` | A resource is bounded or incomplete. Forums/notices support `max_pages` up to 20; mailbox lists expose `next_start` and `upstream_truncated`; exams expose `max_series`. |
| Locked / `not_exposed` | Respect the source state. There may still be relevant obligations elsewhere. |
| Binary file error | Use `read ... --output PATH`; the REST text route cannot return binaries. |
| Response too large | The 20 MB cap was reached. Large media remains inventory-only by default. |
| HTTP 429 | Stop and retry later with less frequent collection. |
| Private-file permission error | On Linux, inspect the file and use mode 600. On Windows, use a new dedicated state directory on local NTFS and log in again; broad/inherited ACLs are refused. Never discard an uncertain ROPOT attempt to bypass its replay guard; inspect that attempt in IS MU first. |
| Cannot load the saved session | The local cookie file is malformed. Choose a new private `--state-dir` and run `login`; the original file is preserved. |

## Known coverage limits

The first live validation covered one Czech-language student account. MFA, English-only page variants, every faculty's custom workflows, alternate SSO, empty course lists, cross-account concurrency, embedded PDF/OCR interpretation and third-party learning systems have not been implemented or comprehensively tested. File inventories can include internal references or generated variants that are not ordinary documents.

Mailbox HTTPS access is implemented; forwarded messages absent from IS remain outside its coverage. Full notice and mail-body/attachment opening requires explicit read-state consent. Mailbox header paging is local, and an upstream-truncated table remains incomplete. See the [student services guide](student-services.md) for these boundaries.

Forum reads can update unread state. Snapshots are observations gathered over time, not a transaction. Browser access working does not guarantee a stable standalone interface for every feature; this package documents and tests only the implemented readers.
