---
name: citation-checker
description: >-
  Legal citation checker for Word footnotes using Indigo Book-compatible and
  Bluebook-style rules. Use this skill whenever an author or editor uploads a
  .docx file and asks to check, fix, or audit citations, footnotes, or references,
  including citation compliance, tracked changes, Id., supra, source verification,
  or footnote formatting in legal scholarship.
---

# Citation Checker Plugin Wrapper

This Codex plugin wrapper points to the canonical skill instructions at
`../../SKILL.md`. Read that file completely before auditing a document, then use
the shared runtime files at `../../scripts/` and `../../references/`.

Do not copy this wrapper directory as a standalone skill. For raw Claude/OpenAI
skill installs, copy or zip the repository root so `SKILL.md`, `scripts/`, and
`references/` stay together.

Run the pipeline from the plugin root:

```bash
python3 scripts/run_pipeline.py --input "<path_to_docx>" --output "<output_docx>"
```
