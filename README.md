# Citation Checker Skill

Portable Claude/OpenAI agent skill for auditing legal citation footnotes in Word `.docx` files. It extracts footnotes, classifies source types, checks Indigo Book-compatible and Bluebook-style citation patterns, verifies selected sources through public APIs when requested, and can write tracked-change `.docx` output for approved corrections. The runtime uses only the Python standard library.

This project does not include or replace the official Bluebook. For publication-critical work, use this as an audit assistant and consult the current official citation manual or journal style guide.

## Features

- `.docx` footnote extraction that preserves Word run boundaries and hyperlink relationship targets.
- Source classification for cases, statutes, books, journal articles, treaties, UN documents, internet sources, and related legal materials.
- Audit-first tracked changes: proposed corrections are reported in `manual_review` unless an apply flag is used.
- Supra proposal and existing-supra validation workflow with author bio-note offset detection and `--bio-notes` override.
- Optional Crossref/OpenLibrary lookups with `--network`.
- Standard-library unittest coverage for the core safety regressions.
- Typeface issues are reported for manual Word review; the CLI does not auto-apply formatting changes.

## Install

There are three install modes:

### OpenAI/Codex Skill Folder

For repository-scoped Codex discovery, clone or copy this entire folder to:

```bash
.agents/skills/citation-checker
```

For user-scoped installs, use the skill directory documented by your Codex
surface. Current public Codex docs document `$HOME/.agents/skills`; some Codex
desktop/local environments still use `$HOME/.codex/skills`.

OpenAI/Codex skill installation uses the full folder, including `SKILL.md`,
`agents/`, `references/`, and `scripts/`.

### OpenAI/Codex Plugin

This repository is also plugin-ready. It includes `.codex-plugin/plugin.json`
and a wrapper skill under `skills/citation-checker/` so Codex can package it as
an installable plugin. It also includes a repo-scoped marketplace at
`.agents/plugins/marketplace.json`, pointing at this plugin root with
`source.path: "./"`. Validate the plugin wrapper before publishing:

```bash
python3 /path/to/plugin-creator/scripts/validate_plugin.py .
```

Users can add the public GitHub marketplace directly, for example:

```bash
codex plugin marketplace add abrynild90/citation-checker-skill
```

Codex plugins are the recommended installable distribution unit for reusable
skills. Build the plugin artifact separately from raw skill uploads:

```bash
python3 scripts/package_agent_skill.py \
  --output citation-checker-codex-plugin.zip \
  --layout folder \
  --profile plugin
```

If you publish under a different repository name, update this command, the
plugin metadata, and the `project.urls` values in `pyproject.toml`.

Do not copy only `skills/citation-checker/` as a standalone skill. That
directory is a plugin wrapper that intentionally points back to the repository
root for `SKILL.md`, `scripts/`, and `references/`.

### Claude Skill

Package the skill folder as a zip and upload or install it in the Claude surface
you are using:

```bash
python3 scripts/package_agent_skill.py --check
python3 scripts/package_agent_skill.py \
  --output citation-checker-agent-skill.zip \
  --layout folder \
  --profile raw
```

If a Claude or OpenAI upload flow expects `SKILL.md` at the zip root, rebuild
with `--layout root --profile raw`. Raw skill zips intentionally contain only
the runtime skill files: `SKILL.md`, `agents/`, `scripts/`, `references/`,
`requirements.txt`, and `LICENSE`. They exclude Codex plugin scaffolding,
repository docs, tests, and CI files. The shared `SKILL.md` frontmatter follows
the portable agent-skill convention; `agents/openai.yaml` is optional OpenAI UI
metadata and is safe for Claude to ignore.

### CLI Only

For local command-line use, install the Python CLI:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install .
```

`pip install .` installs the `citation-checker` CLI and Python modules only. It
does not install the repository as an auto-discovered agent skill; use the
folder copy/clone or zip packaging step above for that.

## Product Compatibility

| Product surface | Status | Notes |
|---|---|---|
| OpenAI Codex CLI/IDE/app raw skill | Supported | Use `.agents/skills/citation-checker` or the user skill path documented by your Codex surface; package with `--profile raw`. |
| OpenAI Codex plugin | Supported | `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`, and `skills/citation-checker/` are included; package with `--profile plugin`. |
| Claude Code | Supported | Use the folder directly or package with `--profile raw`; scripts run with the local Python standard library. |
| claude.ai custom skill upload | Supported for code-execution accounts | Upload the generated raw zip; network lookups still require the product/network settings to allow them. |
| Claude API Skills | Supported for offline checks in code execution | Uses only Python standard-library modules. Do not rely on `--network` where the Claude API container has no internet access. |

No third-party Python package is required. `requirements.txt` is intentionally
empty so `python3 -m pip install -r requirements.txt` is a no-op in managed
containers.

## Network Privacy

By default, the checker runs offline. When `--network` is used, citation metadata
such as DOIs, titles, author strings, and query text may be sent to Crossref or
OpenLibrary for verification. Do not use `--network` for confidential drafts
unless that disclosure is acceptable. See `PRIVACY.md` for the repository
privacy notice used by the Codex plugin metadata.

Network verification is intentionally narrow: journal articles use Crossref;
books use OpenLibrary and then Crossref fallback. Cases, statutes, treaties, and
most other legal sources are checked structurally and flagged for editorial
review rather than source-verified.

## Operational Limits

The checker is designed for ordinary `.docx` law-review drafts with Word
footnotes. It handles visible text in normal runs, hyperlinks in footnotes,
inserted text in tracked changes, existing tracked-change ids, multi-paragraph
footnotes, and non-contiguous internal footnote ids.

Unsupported or manual-review cases include comments, content controls, fields,
custom footnote marks, endnotes, embedded objects, equations, unusual numbering
schemes, and citations split across unrelated OOXML containers. Malformed,
duplicate-member, path-traversal, or oversized ZIP/XML packages fail with a
structured error instead of being repaired.

Supra detection is heuristic. It normalizes common journal/article defects such
as `vol.` and pinpoints, but it can still miss duplicates when source metadata is
substantially inconsistent or when two different sources share very similar
labels. Treat supra proposals as editorial candidates, not final authority.

## CLI Usage

Audit citations and produce an output `.docx` copy plus JSON report:

```bash
citation-checker \
  --input path/to/article.docx \
  --output path/to/article_corrected.docx \
  --network \
  --supra
```

Safe defaults:

- `--supra` proposes supra conversions but does not apply them.
- `--apply-supra --confirm-bio-notes N` applies proposed supra conversions after manual approval of the bio-note offset; a mismatched confirmation exits with a structured error and does not write output.
- `--apply-verified` enables only a narrow allowlist of mechanical text fixes after manual approval, such as `Id` to `Id.`.
- Semantic rewrites and source-dependent corrections remain in `manual_review`; the tool does not apply them automatically.
- Supra proposals below the confidence threshold are reported in `manual_review`.
- `--bio-notes N` overrides the detected count of leading author bio/acknowledgment notes.
- Existing output files are not overwritten unless `--force` is supplied.
- Formatting/typeface issues are report-only and appear in `manual_review`; only text replacements are eligible for tracked-change application.

Useful test run:

```bash
python3 scripts/run_pipeline.py \
  --input path/to/article.docx \
  --output path/to/article_checked.docx \
  --max-fn 20
```

## Report Shape

The pipeline prints JSON with:

- `reports`: per-footnote classification, verification confidence, issues, and supra proposals.
- `reports[].fn_id`: the internal OOXML footnote id used for patching.
- `reports[].display_number`: the visible footnote sequence derived from `word/document.xml`.
- `reports[].paper_fn`: the visible note number after subtracting confirmed leading bio-note offsets.
- `reports[].citations`: per-citation classification and issues inside mixed footnotes.
- `manual_review`: corrections withheld because they need review.
- `patch_summary`: count and per-change status for tracked text changes actually written. CLI formatting changes remain report-only.
- `bio_note_count` and `bio_note_count_detected`: paper-number offset details for supra review.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/package_agent_skill.py --check
python3 scripts/package_agent_skill.py --output /tmp/citation-checker-agent-skill.zip --layout folder --profile raw
python3 scripts/package_agent_skill.py --output /tmp/citation-checker-codex-plugin.zip --layout folder --profile plugin
```

## Release Notes

Version 2.15 focuses on Claude/OpenAI compatibility and public-release safety:

- Python 3.9 compatibility.
- Standard-library-only runtime; removed the prior `lxml` dependency for managed code-execution compatibility.
- XML DTD/entity declarations are rejected before parsing untrusted `.docx` XML.
- Output `.docx` files are not created when package-wide XML validation fails.
- Typeface findings use actual Word run formatting to suppress already-correct italics or small caps.
- Portable `SKILL.md` metadata for Claude and OpenAI/Codex agent skill loaders.
- `agents/openai.yaml` metadata for OpenAI skill lists and chips.
- Codex plugin manifest and wrapper skill under `.codex-plugin/` and `skills/citation-checker/`.
- Repo-scoped Codex marketplace at `.agents/plugins/marketplace.json` for direct marketplace installation.
- `scripts/package_agent_skill.py` compatibility validation plus separate raw skill and Codex plugin packaging profiles.
- Raw Claude/OpenAI skill zips exclude plugin scaffolding, CI, tests, and repository release files.
- Codex plugin metadata includes repository, homepage, privacy, and terms URLs.
- `references/bluebook_rules.md` now starts with a rule index for faster agent routing.
- Committed Word-style `.docx` corpus fixture with relationships, styles, footnotes, hyperlinks, and tracked changes.
- Exact footnote text reconstruction across Word runs, including hyperlink relationship targets.
- Displayed footnote numbering derived from `word/document.xml` instead of assuming OOXML ids equal paper note numbers.
- Multi-run tracked-change replacement.
- Per-change patch status reporting; reports mark `auto_applied` only after patch success.
- Revision IDs are allocated after scanning all `word/*.xml` parts, not just `word/footnotes.xml`.
- Unique atomic temp files for OOXML rewrites to avoid clobbering sibling files.
- Output overwrite protection with explicit `--force`.
- Audit-first application with explicit `--apply-verified`; only mechanical allowlisted text fixes are applied.
- Defensive ZIP/XML validation with structured errors for malformed or suspicious `.docx` packages.
- Missing input files return structured JSON errors from the CLI.
- Nested tracked-change edits inside existing insertions are skipped and reported.
- Metadata-matching verification instead of trusting the first API hit.
- Offline heuristic checks no longer claim source verification.
- OpenLibrary title-search matches require author and year support before positive verification.
- Citation-level checks inside mixed footnotes.
- `Id.` short-form classification and context review.
- Existing supra self-reference, forward-reference, and wrong-note validation.
- Existing supra validation treats same-label multiple-source matches as ambiguity instead of forcing the first note.
- `--apply-supra` requires explicit `--confirm-bio-notes`.
- Installable `citation_checker_skill` package and `citation-checker` CLI.
- Safer supra proposal/application split; typeface on converted supra forms remains a manual review item to avoid ambiguous formatting targets.
- Journal supra dedupe normalizes common defects such as `vol.` before matching.
- Bio-note offset detection for supra note numbers.
- CI builds wheel/sdist, checks distribution metadata, installs both artifacts, runs `pip check`, executes tests, and runs installed CLI smoke tests.
- Runtime User-Agent and package metadata are aligned with the release version.
- GitHub-ready metadata, tests, and license.
