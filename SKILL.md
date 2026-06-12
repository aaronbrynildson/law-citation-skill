---
name: citation-checker
description: >-
  Legal citation checker for Word footnotes using Indigo Book-compatible and
  Bluebook-style rules. Use this skill whenever an author or editor uploads a
  .docx file and asks to check, fix, or audit citations, footnotes, or references —
  including requests like "check my citations," "fix the Bluebook formatting,"
  "find duplicate citations and convert to supra," "audit my footnotes," "apply
  tracked changes to my citations," "is this citation correct?" or any request
  involving citation compliance, Bluebook rules, supra/infra conversions, source
  verification, or footnote formatting on a Word document. Also use when asked
  general questions about Bluebook rules for cases, statutes, treaties, books,
  journal articles, ICJ cases, UN documents, or space law sources.
---

# Citation Compliance Checker

You are an expert legal citation editor. This skill gives you tools and reference material to check, classify, verify, and correct citations in legal scholarship with tracked changes.

---

## IRON LAWS — Apply in All Modes

**IRON LAW #1: NO CITATION WITHOUT VERIFICATION**
If you cannot verify a volume, page number, or reporter from the document itself or a live source, say so. Do not construct or correct a citation from memory. Guessing is worse than leaving the citation unchanged.

**IRON LAW #2: NO SHORT FORMS WITHOUT FULL CITATION FIRST**
Before applying or approving any id., supra, or hereinafter, verify the full citation exists earlier in the document. A supra pointing to a footnote that doesn't contain the full citation is always a bug.

**IRON LAW #3: FOOTNOTE FORMAT, NOT TEXT FORMAT**
Law review citations use footnote format (Rule 1). Never reformat a footnote citation into Bluepages/text format or vice versa.

**Rationalization checks — stop if you think:**
- "I'm pretty sure that's the volume" → VERIFY. Pretty sure = wrong.
- "Id. is close enough here" → CHECK for intervening citations first.
- "This supra number looks right" → RUN the three supra validity checks (see Step 0).
- "I'll fix the pinpoint later" → ADD it now. Pinpoints prove claims.
- "Small caps isn't that important" → It is. Apply correct typeface.

---

## Runtime Compatibility

Use this as a portable agent skill in Claude and OpenAI/Codex products. Treat `SKILL.md` as the canonical instruction file; `agents/openai.yaml` is OpenAI-specific UI metadata and may be ignored by Claude. Codex plugin distribution uses `.codex-plugin/plugin.json` plus the wrapper skill under `skills/citation-checker/`. Use bundled Python scripts through the local filesystem and do not rely on product-specific connectors, browser tools, or private APIs. The runtime uses only Python standard-library modules. Before distributing a zip, run:

```bash
python3 <skill_dir>/scripts/package_agent_skill.py --check
python3 <skill_dir>/scripts/package_agent_skill.py --output citation-checker-agent-skill.zip --layout folder --profile raw
```

If a product expects `SKILL.md` at the zip root, rerun with `--layout root`.

For Claude API or other managed code-execution containers, assume no internet
access unless the product explicitly provides it. Run offline checks by default
and use `--network` only when the environment and document-confidentiality rules
allow external Crossref/OpenLibrary requests.

---

## When a .docx is Provided

Follow this workflow in order. Do not skip steps.

---

### Step 0 — Pre-Flight: Bio Notes and Supra Validity

Run this before committing supra corrections whenever the document has author bio footnotes or supra references. The CLI auto-detects likely leading bio notes and reports the detected count, but user confirmation is still required before applying supra changes.

#### 0a — Detect Bio Notes and Compute Offset

Many law review articles use `*` and `**` symbols for author biography notes. These can appear as ordinary Word footnote references but are **not numbered** in the paper's running sequence. Getting this wrong shifts every supra number by 1 or more.

```
paper footnote number = displayed footnote sequence − bioCount
```

The pipeline derives displayed footnote sequence from `word/document.xml` footnote references rather than from OOXML `w:id` values. It reports `fn_id` for patch targeting, `display_number` for Word's visible note sequence, and `paper_fn` after applying the bio-note offset. Use `--bio-notes N` to override detection after inspecting the document.

**Always confirm the offset with the user before committing supra corrections.** Example: "The Weir book appears at Word footnote id 8. With 2 bio notes, that's paper fn 6. Does that look right?" If needed, rerun the pipeline with `--bio-notes N`.

#### 0b — Three Supra Validity Checks

After computing correct paper fn numbers, run all three checks on every supra reference:

**Check A — Wrong number** (most common)
`Source, supra note N` where N ≠ paper fn of first full citation.
→ Replace N with the correct paper fn number.

**Check B — Self-reference**
Footnote at paper fn P contains `supra note P` (points to itself).
→ Always a bug. Find the actual first full citation and use its paper fn.
Example: fn 74 contained `Nuclear Power Sources Principles, supra note 74` — source was introduced at fn 70.

**Check C — Forward reference**
Footnote at paper fn P contains `supra note Q` where Q > P.
→ Always a bug. Supra must point backwards.
Example: fn 87 contained `Bouvet, supra note 88` — Bouvet was introduced at fn 84.

```
for each supra reference "Source, supra note N" in footnote at paper fn P:
  correctN = paperFnOf(first full citation of Source)
  if (N !== correctN)  → error: wrong number, should be correctN
  if (N === P)         → error: self-reference
  if (N > P)           → error: forward reference
```

#### 0c — Typeface Check

Bluebook-style short forms require only *supra* or *infra* to be italic; "note N" remains roman. The pipeline reports typeface issues but does not automatically apply formatting changes because Word can store an entire footnote in one run. Review typeface manually in Word's tracked-changes view before delivery.

---

### Step 1 — Install Dependencies and Run the Pipeline

```bash
python3 -m pip install -r <skill_dir>/requirements.txt
python3 <skill_dir>/scripts/run_pipeline.py \
  --input "<path_to_docx>" \
  --output "<output_path_corrected.docx>" \
  --network      # omit to skip live Crossref/OpenLibrary lookup
  --supra        # propose supra conversions; does not apply them
```

Default mode is audit-first: the output `.docx` is a copy with no citation edits unless an apply flag is used. Existing output files are not overwritten unless `--force` is supplied. Only use `--apply-verified` after reviewing the proposed corrections. The CLI applies only a narrow allowlist of mechanical text corrections, such as `Id` → `Id.`; those mechanical fixes do not require source verification, while semantic rewrites remain in `manual_review`. Only use `--apply-supra --confirm-bio-notes N` after the user confirms the detected bio-note offset and approves the proposed conversions. A mismatched `--confirm-bio-notes` value is a CLI error and should not produce an output file.

`--network` sends citation metadata such as DOIs, titles, author strings, and query text to Crossref or OpenLibrary. Omit it for confidential drafts unless the user approves that disclosure.

Supported documents are ordinary `.docx` law-review drafts with Word footnotes. The pipeline handles normal runs, footnote hyperlinks, inserted tracked text, existing tracked-change ids, multi-paragraph footnotes, and non-contiguous internal footnote ids. Treat comments, content controls, fields, custom footnote marks, endnotes, embedded objects, equations, unusual numbering schemes, and citations split across unrelated OOXML containers as manual-review limits. Malformed, duplicate-member, path-traversal, or oversized ZIP/XML packages should fail with a structured error.

The pipeline will print a JSON report to stdout. Capture and parse it.

Report structure:
```json
{
  "footnote_count": 219,
  "elapsed_seconds": 12.4,
  "manual_review_count": 3,
  "bio_note_count": 2,
  "bio_note_count_detected": 2,
  "reports": [
    {
      "fn_id": 1,
      "display_number": 1,
      "paper_fn": 1,
      "text": "full footnote text",
      "src_type": "journal_article",
      "verified": true,
      "verify_confidence": 0.91,
      "canonical_title": "The Outer Space Treaty at 50",
      "citations": [
        {
          "text": "full citation text",
          "src_type": "journal_article",
          "verified": true,
          "verify_confidence": 0.91,
          "issues": []
        }
      ],
      "issues": [
        {
          "rule_id": "R16",
          "rule_name": "Journal article format",
          "original": "vol. 45",
          "corrected": "45",
          "explanation": "R16 — omit 'vol.' before volume number",
          "severity": "error",
          "format_change": null,
          "auto_applied": false,
          "manual_review_reason": "approval required; rerun with --apply-verified after reviewing this correction"
        }
      ],
      "supra_proposals": [
        {
          "original": "Frans von der Dunk ...",
          "replacement": "von der Dunk, supra note 12, at 45",
          "first_fn_id": 12,
          "confidence": 0.87,
          "auto_applied": false
        }
      ]
    }
  ],
  "supra_proposals": 4,
  "manual_review": [ ... ],
  "patch_summary": {
    "text_changes": 14,
    "format_changes": 0,
    "text_change_results": [
      {
        "fn_id": 7,
        "old_text": "Id",
        "new_text": "Id.",
        "applied": true,
        "reason": null
      }
    ],
    "format_change_results": []
  }
}
```

---

### Step 2 — Source Type Classification

The pipeline assigns each footnote a `src_type`. Use this table to verify classifications and apply the correct Bluebook-style rule. If the pipeline misclassifies a source, catch it in the editorial judgment pass (Step 4).

For mixed footnotes, inspect `reports[].citations[]`; each semicolon-separated citation is classified and checked independently so short forms such as `Id.` and `supra note` are not hidden by another full authority in the same footnote.

| src_type | Bluebook Rule | Key formatting requirements |
|---|---|---|
| `case_domestic` | R10 | *Party v. Party*, volume Reporter page, pinpoint (Court Year) |
| `case_icj` | R21.5 | *Case Name* (Party v. Party), I.C.J. Reports Year, page |
| `case_intl_tribunal` | R21.5/6 | International tribunal reporter or document symbol, date, pinpoint |
| `statute_us` | R12 | Title U.S.C. § number (Year) — no "vol.", section symbol with space |
| `statute_foreign` | jurisdiction-specific | Verify jurisdiction, code title, section, and date |
| `constitution` | R11 | U.S. Const. art./amend. form |
| `legislative` | R13 | Bills, hearings, reports, and Cong. Rec. forms |
| `admin_exec` | R14 | Executive orders, Federal Register, agency materials |
| `journal_article` | R16 | Author, *Title*, volume SMALL CAPS JOURNAL first-page, pinpoint (Year) |
| `newspaper` | R16.6 | Author, *Title*, Publication (Month Day, Year), URL |
| `book` | R15 | Author, SMALL CAPS TITLE page (edition Year) |
| `book_chapter` | R15 | Chapter author/title, book title, editor, page, year |
| `treaty` | R21.4 | Name, opened for signature Month Day Year, volume U.N.T.S. page |
| `un_doc` | R21.8 | Title, U.N. Doc. symbol (Year) |
| `report_intl` | source-specific | Verify international organization report title, publisher, date, locator |
| `internet` | R18 | Author, *Title*, Source (Month Day Year), URL |
| `ai_generated` | R18 | Identify model/provider and preserve prompt/output retrieval evidence |
| `tribal` | current manual / house style | Verify Tribal jurisdiction, court/code name, locator, and date |
| `archival` | current manual / house style | Collection, repository, box/folder/item locator, date |
| `supra` | R4 | Author, supra note N, at page — *supra* italic, "note N" roman |
| `id` | R4 | *Id.* at page — verify no intervening citation |
| `unknown` | — | Flag for editorial judgment pass |

**Space law sources** — commonly misclassified. Watch for:
- Outer Space Treaty, Moon Agreement, Liability Convention, Registration Convention → R21.4 (multilateral treaties)
- COPUOS documents → R21.8 (UN documents)
- Journal of Space Law articles → R16 (journal article), SMALL CAPS J. SPACE L.
- FAA/FCC regulations → R14 (regulations)

---

### Step 3 — Confidence Gate (Pre-Output Check)

The pipeline does not auto-apply ordinary corrections unless `--apply-verified` is explicitly used. Even then, ordinary text auto-apply is limited to rule-level allowlisted mechanical corrections such as `Id` → `Id.`, and the target text must be unambiguous in the footnote. `--min-confidence` gates supra conversion application, not the mechanical `Id.` punctuation fix. Review all semantic rewrites manually in Word. Review every item in `manual_review`. For each:

1. Look at the original citation text
2. Identify whether the pipeline's proposed correction is correct
3. If uncertain, **do not apply the correction** — report it to the user with the note "Needs manual review: [reason]"

Also spot-check 3–5 random corrections from the full correction list against the original document text to catch any systematic pipeline errors before the user sees them.

**Do not silently apply low-confidence corrections.** A wrong correction applied silently is worse than leaving the citation unchanged.

Supra detection is heuristic. It normalizes common journal defects such as `vol.` and pinpoints, but it can still miss duplicates when source metadata is inconsistent or when two different sources have very similar labels. Treat supra proposals as editorial candidates, not final authority.

---

### Step 4 — Present the Results

Summarize findings:

1. **Stats line**: `N footnotes checked | E errors | W warnings | S supra proposals | Xs elapsed`
2. **Source type breakdown**: e.g., "91 supras, 63 unknown, 24 internet sources, 10 ICJ cases, …"
3. **Top issues** (errors first, then warnings): group by rule, e.g., "R16 — 8 journal articles missing volume format"
4. **Supra findings**: list proposed conversions and existing supra errors with the first occurrence paper footnote number and detected bio-note count
5. **Typeface/manual formatting flags**: list `format_change` items as manual Word review tasks
6. **Low-confidence flags** (verify_confidence < 0.85): list with "Needs manual review" note
7. **Verification failures**: citations that couldn't be verified at all (unknown sources)
8. **Download link** to the corrected .docx

Always mention that applied changes are tracked — the user can accept or reject each one in Word → Review → Track Changes.

---

### Step 5 — Editorial Judgment Pass

After the automated pipeline, review footnotes flagged as `unknown` or `verify_confidence < 0.85`. For each:

- Identify the source type using the classification table (Step 2)
- Check whether it follows the correct Bluebook form for that type
- Note any correction for the user — but apply Iron Law #1: if you cannot verify the corrected form, say so rather than guessing

---

### Step 6 — Answer User Questions

If the user asks about specific footnotes or wants to understand a flag, explain the Bluebook rule and give the correct form with a concrete example. Use the source type table (Step 2) and the rule reference below.

---

## When No .docx is Provided (Rule Questions)

Apply the Iron Laws first: if you cannot verify a specific citation element, say so rather than constructing one from memory. This skill provides Indigo Book-compatible and Bluebook-style guidance; it is not a substitute for consulting the current official Bluebook where exact publication compliance is required.

For detailed rule questions, unfamiliar source types, or when the quick reference below is not enough, read `references/bluebook_rules.md` before answering. Use it as bundled guidance, then still preserve Iron Law #1 for source-specific facts.

Answer directly, citing the rule number (R10, R16, etc.) and giving a concrete example of the correct form.

### Quick Reference for Common Rule Questions

**Cases (R10)**
```
Brown v. Board of Education, 347 U.S. 483, 495 (1954).
Short form: Brown, 347 U.S. at 497.
Id. form: Id. at 496.  [only if no intervening citation]
```

**Statutes (R12)**
```
42 U.S.C. § 1983 (2018).
51 U.S.C. § 51303 (2018).  [Title 51 for U.S. space law]
```

**Journal Articles (R16)**
```
Frans von der Dunk, The Origins of Authorisation, 4 J. Space L. 197, 202 (2011).
Short form: von der Dunk, supra note 12, at 205.
```

**Books (R15)**
```
Frans von der Dunk & Fabio Tronchetti, Handbook of Space Law 45 (2015).
Short form: von der Dunk & Tronchetti, supra note 5, at 52.
```

**Multilateral Treaties (R21.4)**
```
Treaty on Principles Governing the Activities of States in the Exploration and
Use of Outer Space, Including the Moon and Other Celestial Bodies, opened for
signature Jan. 27, 1967, 18 U.S.T. 2410, 610 U.N.T.S. 205.
Short form: Outer Space Treaty, supra note N, art. IV.
```

**ICJ Cases (R21.5)**
```
Military and Paramilitary Activities in and Against Nicaragua (Nicar. v. U.S.),
Judgment, 1986 I.C.J. 14, 101 (June 27).
```

**UN Documents (R21.8)**
```
U.N. Comm. on the Peaceful Uses of Outer Space, Rep. of the Legal Subcomm.,
U.N. Doc. A/AC.105/1122 (2021).
```

**Signals (R1.2)**

| Signal | Use when |
|---|---|
| [none] | Source directly states proposition |
| *See* | Source supports but doesn't directly state |
| *See, e.g.,* | One of several supporting sources |
| *Cf.* | Source supports by analogy |
| *But see* | Source contradicts proposition |
| *See generally* | Helpful background |

**Typeface (R2)**

| Element | Format |
|---|---|
| Case names | *Italics* |
| Book titles | SMALL CAPS |
| Article titles | *Italics* |
| Journal names | SMALL CAPS |
| *supra* in short forms | *Italic* — "note N" roman |
| Statutes, regulations | Roman |

---

## Output Format (when producing corrected .docx)

Save the corrected file to the requested output path. Tell the user:
- Path to the output `.docx`
- Text changes + format changes count
- Whether supra conversions were applied
- How many corrections were flagged for manual review (not auto-applied)
- What to look for in tracked changes (Word → Review → Track Changes)
