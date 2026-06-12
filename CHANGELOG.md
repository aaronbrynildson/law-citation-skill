# Changelog

## 2.15.0

- Split skill packaging into raw Claude/OpenAI upload artifacts and Codex
  plugin artifacts.
- Added public plugin metadata URLs plus a dedicated `PRIVACY.md` notice.
- Added a rule index to `references/bluebook_rules.md`.
- Updated GitHub Actions workflow steps to current major action releases.
- Added explicit XML DTD/entity rejection before XML parsing.
- Avoid creating output `.docx` files when package-wide XML validation fails.
- Suppressed typeface false positives when the target text is already formatted
  correctly in Word runs.
- Tightened the compatibility checker skill-name validation.

## 2.14.0

- Added a repo-scoped Codex marketplace entry.
- Added a committed Word-style `.docx` fixture with relationships, styles,
  hyperlinks, footnotes, and tracked changes.
- Added public `SECURITY.md` and `CONTRIBUTING.md`.

## 2.13.0

- Added Codex plugin manifest and wrapper skill.
- Removed the runtime `lxml` dependency; the checker now uses only the Python
  standard library.
- Added Claude/OpenAI compatibility documentation and zip packaging validation.
