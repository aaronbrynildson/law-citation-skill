# Privacy

Citation Checker runs offline by default. In the default mode, `.docx` files,
footnote text, citation metadata, reports, and corrected output files remain on
the machine or managed code-execution environment where the tool is run.

If `--network` is supplied, the checker may send citation metadata to public
metadata services for narrow verification:

- Journal article titles, DOIs, author names, years, and query text may be sent
  to Crossref.
- Book titles, author names, years, ISBNs, and query text may be sent to
  OpenLibrary.

Do not use `--network` for confidential drafts unless that disclosure is
acceptable under the applicable client, journal, court, employer, or classroom
rules. Cases, statutes, treaties, and most other legal sources are checked
structurally and flagged for editorial review rather than source-verified.

This repository does not operate a server and does not collect telemetry.
