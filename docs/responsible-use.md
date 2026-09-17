# Permissions, licensing and responsible use

IS MU Reader is an independent interoperability client, not an official university API or an approved university application. Publishing this source does not establish permission for a particular deployment. This page is practical guidance, not a legal opinion or a guarantee of compliance.

## Code and content have different rights

The [MIT license](../LICENSE) covers this project's implementation and documentation. Dependencies retain their own licenses. The source distribution contains synthetic test fixtures and does not bundle university software, teaching materials, student records, private messages, credentials or university logos. References to IS MU and Masaryk University identify the system the client works with; they imply no endorsement.

An account's ability to read content does not grant permission to republish it. Keep collected data private unless you have the necessary rights and a lawful basis for sharing it. Do not upload real account captures to issues or pull requests. See [security and privacy](../SECURITY.md).

## Check the operator's requirements before connecting

The [IS MU usage rules](https://is.muni.cz/napoveda/jine/pravidla?zoomy_is=1), particularly rule 6, require prior operator consent for bulk operations and prohibit overloading the service. Ask the operators at `istech@fi.muni.cz` about your intended scope and frequency before bulk collection. Built-in delays and bounded requests are safeguards, not an approved usage quota.

[MU's IT rules](https://it.muni.cz/pravidla), especially duties 2, 5 and 6, also cover credential confidentiality, approved software, technical restrictions and protected data. Check with the responsible IT administrator whether your intended use is approved; this project has obtained no such approval. Follow university requirements for storing and sharing data.

## Development and operation

- Use only your own authorized identity and access. Keep credentials local; never send them to maintainers.
- Respect permission failures, locked resources and operator instructions. Do not evade authentication, access restrictions or rate limits.
- Use offline synthetic tests for development. Live verification needs appropriate authorization and private storage.
- ROPOT actions can affect an assessment. Follow the course's assessment conditions and obtain any required instructor permission before using a custom client. An explicit write flag confirms a software action; it does not grant academic authorization.

These external obligations do not add restrictions to the MIT license. Users remain responsible for their use of the service. Rules can change; consult the linked official sources rather than relying solely on this summary. Sources reviewed on 17 September 2026.
