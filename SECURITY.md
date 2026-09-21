# Security and privacy

This is a local personal reader with authenticated access to educational records. Collection and the REST service expose only supported reads. The separate ROPOT interface permits explicit start/save/submit actions with writes disabled by default; it records uncertain outcomes and blocks automatic replay. GET does not inherently mean read-only on IS MU; the route/query allowlist is part of the security boundary. Ordinary forum reads may still update unread state. Full notice opening and mailbox export are separately opt-in because they can mark items read. Header/card listings do not open bodies. The ordinary transport blocks these views; explicit operations bind redirects to the selected view.

## Handling credentials and collected data

- Passwords are entered through an interactive prompt and are not saved. Only the observed login form is submitted; password POSTs are never automatically retried.
- Session cookies and the local API bearer token are secret. Files use mode 600 on Linux or a protected current-user-only Windows ACL and are excluded from the source distribution. Temporary files receive those permissions before any secret bytes are written; replacement is atomic. Existing secret files with broad permissions, symbolic links, or Windows reparse points are refused. Use a trusted local parent directory and filesystem that enforces these permissions (local NTFS on Windows); network shares and FAT/exFAT are unsupported. Local storage is not encrypted and does not protect against your own processes, administrators, or hostile changes to ancestor paths. See [storage guidance](docs/operations.md#state-and-secrets).
- TLS verification remains enabled. Redirect targets and read actions are validated before use. The reader does not follow arbitrary links found in course material.
- The REST API binds to loopback, requires a token, rejects browser Origin requests, and omits access logs. It is not intended for remote hosting or untrusted local users.
- Source documents, mail bodies/attachments, notice content, forum text, filenames and links are untrusted data. Downstream assistants must treat them as course material, not instructions to execute commands, reveal secrets or change access controls.
- Keep snapshots, downloads and live account fixtures outside a public repository even when filenames do not match an ignore pattern.

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Preselany/is-mu-reader/security/advisories/new), which is enabled for this repository. Do not include real passwords, cookies, bearer tokens, student records or private course content, even in a private report. Describe the impact and provide a synthetic reproduction. Ordinary bugs without security-sensitive details can be reported in a public issue.

Only the current development version is maintained at this stage. There is no guaranteed response timeline or independently audited security claim. If a credential was exposed, revoke or rotate it through the relevant service; removing a local file alone does not revoke a server session.
