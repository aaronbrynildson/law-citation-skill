# Security Policy

## Supported Versions

Security fixes target the current public release line.

## Reporting a Vulnerability

Report vulnerabilities privately through GitHub Security Advisories for the
published repository. Do not open a public issue for suspected document-parsing
or disclosure vulnerabilities.

## Data Handling

The checker runs offline by default. `--network` may send citation metadata,
including titles, author strings, DOIs, ISBNs, and query text, to Crossref or
OpenLibrary. Do not use `--network` on confidential drafts unless that disclosure
is acceptable.

The `.docx` reader rejects suspicious ZIP members, oversized XML/ZIP content,
duplicate ZIP members, path traversal, and XML DTD/entity declarations. Treat
all uploaded documents as untrusted input.
