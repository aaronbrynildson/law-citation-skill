"""
bluebook.py — Legal citation rule checks.
Returns CitationIssue objects for each violation found.

Coverage:
  R1/R2   Typeface
  R4      Short forms (id., supra, infra, hereinafter)
  R5      Signals
  R6      Quotations and parentheticals
  R8      Abbreviations
  R10     Domestic cases
  R11     Constitutions
  R12     Statutes (US Code, CFR, Public Laws, session laws)
  R13     Legislative materials (bills, hearings, committee reports, Cong. Rec.)
  R14     Administrative/executive materials (exec. orders, Fed. Reg., agency docs)
  R15     Books (including edited volumes)
  R16     Journal articles
  R16.6   Newspapers and magazines
  R17     Unpublished and forthcoming works
  R18     Internet and AI-generated sources
  R21.4   Treaties and international agreements
  R21.5   ICJ and international tribunals
  R21.8   UN documents (resolutions, GAOR, docs)
  CURRENT Tribal materials
  CURRENT Archival and historical materials
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from .classifier import SourceType


@dataclass
class CitationIssue:
    rule_id: str
    rule_name: str
    original: str
    corrected: str
    explanation: str
    start: int = 0
    end: int = 0
    severity: str = "warning"    # "error" | "warning" | "info"
    format_change: str | None = None   # "add:italic:target" or "remove:italic:target"


def check_all(text: str, src_type: SourceType) -> list[CitationIssue]:
    issues: list[CitationIssue] = []
    issues.extend(check_r5_signals(text))
    issues.extend(check_r4_short_forms(text, src_type))
    issues.extend(check_r6_quotations(text))
    issues.extend(check_r8_abbreviations(text))
    issues.extend(check_r1_typeface(text, src_type))
    if src_type == SourceType.CASE_DOMESTIC:
        issues.extend(check_r10_cases(text))
    if src_type == SourceType.CONSTITUTION:
        issues.extend(check_r11_constitutions(text))
    if src_type == SourceType.STATUTE_US:
        issues.extend(check_r12_statutes(text))
    if src_type == SourceType.LEGISLATIVE:
        issues.extend(check_r13_legislative(text))
    if src_type == SourceType.ADMIN_EXEC:
        issues.extend(check_r14_admin(text))
    if src_type in (SourceType.BOOK, SourceType.BOOK_CHAPTER):
        issues.extend(check_r15_books(text))
    if src_type == SourceType.JOURNAL_ARTICLE:
        issues.extend(check_r16_journals(text))
    if src_type == SourceType.NEWSPAPER:
        issues.extend(check_r16_6_newspaper(text))
    if src_type == SourceType.UNPUBLISHED:
        issues.extend(check_r17_unpublished(text))
    if src_type == SourceType.AI_GENERATED:
        issues.extend(check_r18_ai_generated(text))
    if src_type == SourceType.INTERNET:
        issues.extend(check_r18_internet(text))
    if src_type == SourceType.TREATY:
        issues.extend(check_r21_treaties(text))
    if src_type in (SourceType.CASE_ICJ, SourceType.CASE_INTL_TRIBUNAL):
        issues.extend(check_r21_icj(text))
    if src_type == SourceType.UN_DOC:
        issues.extend(check_r21_un_docs(text))
    if src_type == SourceType.TRIBAL:
        issues.extend(check_r22_tribal(text))
    if src_type == SourceType.ARCHIVAL:
        issues.extend(check_r23_archival(text))
    return issues


def check_footnote(text: str, src_type: SourceType) -> list[CitationIssue]:
    return check_all(text, src_type)


# ── R5: Signals ────────────────────────────────────────────────────────────────

_SIGNALS = ["See", "See also", "See generally", "Cf.", "But see",
            "Accord", "Compare", "Contra", "E.g.,"]
_SIGNAL_PAT = re.compile(
    r'\b(see\s+also|see\s+generally|see|cf\.|but\s+see|accord|compare|contra|e\.g\.,?)\b',
    re.I
)

def check_r5_signals(text: str) -> list[CitationIssue]:
    issues = []
    m = _SIGNAL_PAT.match(text.strip())
    if m:
        raw = m.group(1)
        if raw[0].islower():
            cap = raw[0].upper() + raw[1:]
            issues.append(CitationIssue(
                rule_id="R5", rule_name="Signal capitalisation",
                original=raw, corrected=cap,
                explanation="R5 — signal at the start of a citation sentence must be capitalised.",
                severity="error",
            ))
        if raw.lower() == "cf." and "(" not in text:
            issues.append(CitationIssue(
                rule_id="R5", rule_name="Cf. requires parenthetical",
                original=text, corrected=text,
                explanation="R5 — 'Cf.' requires an explanatory parenthetical.",
                severity="warning",
            ))
    for m2 in re.finditer(r';\s+(' + '|'.join(re.escape(s) for s in _SIGNALS) + r')\b', text, re.I):
        raw2 = m2.group(1)
        if raw2[0].isupper() and raw2 not in ("E.g.,",):
            lc = raw2[0].lower() + raw2[1:]
            issues.append(CitationIssue(
                rule_id="R5", rule_name="Signal lowercase after semicolon",
                original=raw2, corrected=lc,
                explanation="R5 — signal after a semicolon (within the same citation sentence) must be lowercase.",
                severity="error",
            ))
    return issues


# ── R4: Short forms (supra / infra / Id. / hereinafter) ──────────────────────

def check_r4_short_forms(text: str, src_type: SourceType) -> list[CitationIssue]:
    issues = []
    t = text.strip()

    if src_type == SourceType.ID:
        issues.append(CitationIssue(
            rule_id="R4",
            rule_name="Id. requires immediately preceding authority check",
            original=t,
            corrected=t,
            explanation="R4 — verify that Id. refers to the immediately preceding cited authority and that no intervening authority breaks the reference.",
            severity="warning",
        ))
        if re.fullmatch(r'Id\.\s*', t):
            issues.append(CitationIssue(
                rule_id="R4",
                rule_name="Id. pinpoint review",
                original=t,
                corrected=t,
                explanation="R4 — add or verify a pinpoint when the proposition depends on a specific page or section.",
                severity="info",
            ))

    # supra/infra must be followed by "note"
    for m in re.finditer(r'\b(supra|infra)\s+(?!note\b)(\d+)', t, re.I):
        issues.append(CitationIssue(
            rule_id="R4", rule_name="Supra/infra missing 'note'",
            original=m.group(0), corrected=f"{m.group(1)} note {m.group(2)}",
            explanation="R4 — 'supra' and 'infra' must be followed by 'note' before the footnote number.",
            severity="error",
        ))

    # supra must not be used for cases or statutes
    if src_type in (SourceType.CASE_DOMESTIC, SourceType.STATUTE_US) and re.search(r'\bsupra\b', t, re.I):
        issues.append(CitationIssue(
            rule_id="R4", rule_name="Supra not permitted for cases or statutes",
            original=t, corrected=t,
            explanation="R4 — 'supra' may not be used for cases or statutes. Use the standard short form (e.g., Smith, 500 U.S. at 15; § 1983).",
            severity="error",
        ))

    # Id. must have period
    for m in re.finditer(r'\bId\b(?!\.)', t):
        issues.append(CitationIssue(
            rule_id="R4", rule_name="Id. missing period",
            original="Id", corrected="Id.",
            explanation="R4 — 'Id.' always requires a period.",
            severity="error",
        ))

    # supra italic
    for m in re.finditer(r'\bsupra\b', t, re.I):
        issues.append(CitationIssue(
            rule_id="R4", rule_name="Supra typeface",
            original="supra", corrected="supra",
            explanation="R4 — 'supra' should be italicised; 'note N' must remain roman.",
            severity="info",
            format_change="add:italic:supra",
        ))
        break

    # infra italic
    for m in re.finditer(r'\binfra\b', t, re.I):
        issues.append(CitationIssue(
            rule_id="R4", rule_name="Infra typeface",
            original="infra", corrected="infra",
            explanation="R4 — 'infra' should be italicised.",
            severity="info",
            format_change="add:italic:infra",
        ))
        break

    # hereinafter: flag for editorial verification of first use
    for m in re.finditer(r'\[hereinafter\s+(?P<label>[^\]]+)\]', t, re.I):
        issues.append(CitationIssue(
            rule_id="R4", rule_name="Hereinafter — verify first use",
            original=m.group(0), corrected=m.group(0),
            explanation="R4 — '[hereinafter X]' must appear in the first full citation of this source. Verify this is that citation.",
            severity="info",
        ))

    return issues


# ── R6: Quotations / parentheticals ──────────────────────────────────────────

def check_r6_quotations(text: str) -> list[CitationIssue]:
    issues = []
    for m in re.finditer(r'\(\s*"([A-Z])', text):
        issues.append(CitationIssue(
            rule_id="R6", rule_name="Quoted parenthetical capitalisation",
            original=m.group(0), corrected=m.group(0),
            explanation="R6 — if the quoted passage does not begin a sentence or is not a proper noun, it should begin with a lowercase letter.",
            severity="info",
        ))
    # Alterations should use brackets not parentheses
    for m in re.finditer(r'"[^"]*\([A-Za-z]+\)[^"]*"', text):
        issues.append(CitationIssue(
            rule_id="R6", rule_name="Alteration should use brackets",
            original=m.group(0), corrected=m.group(0),
            explanation="R6 — alterations to quoted text should use brackets [ ], not parentheses ( ).",
            severity="warning",
        ))
    # Ellipsis: ". . ." not "..."
    for m in re.finditer(r'\.{3}', text):
        issues.append(CitationIssue(
            rule_id="R6", rule_name="Ellipsis format",
            original="...", corrected=". . .",
            explanation="R6 — Bluebook uses spaced ellipsis (. . .) not unspaced (...).",
            severity="warning",
        ))
        break
    return issues


# ── R8: Abbreviations ─────────────────────────────────────────────────────────

_ABBREV_ERRORS = {
    "United States": "U.S.",
    "International": "Int'l",
    "University": "Univ.",
    "Association": "Ass'n",
    "Department": "Dep't",
    "Government": "Gov't",
    "Organization": "Org.",
    "Organisation": "Org.",
    "Amendment": "Amend.",
}

def check_r8_abbreviations(text: str) -> list[CitationIssue]:
    issues = []
    for full, abbrev in _ABBREV_ERRORS.items():
        if re.search(r'\b' + re.escape(full) + r'\b', text):
            issues.append(CitationIssue(
                rule_id="R8", rule_name=f"Standard abbreviation: {full}",
                original=full, corrected=full,
                explanation=f"R8 — review whether '{full}' should be abbreviated as '{abbrev}' in the citation component; do not abbreviate words inside titles without source-specific confirmation.",
                severity="info",
            ))
    # "Section N" → "§ N"
    for m in re.finditer(r'\bSection\s+\d+', text):
        issues.append(CitationIssue(
            rule_id="R8", rule_name="'Section' → '§'",
            original=m.group(0), corrected=m.group(0),
            explanation="R8 — review whether this citation component should use the § symbol; do not rewrite prose or source titles automatically.",
            severity="warning",
        ))
        break
    return issues


# ── R1/R2: Typeface ───────────────────────────────────────────────────────────

def check_r1_typeface(text: str, src_type: SourceType) -> list[CitationIssue]:
    issues = []
    if src_type in (SourceType.CASE_DOMESTIC, SourceType.CASE_ICJ,
                    SourceType.CASE_INTL_TRIBUNAL):
        case_name = _case_name_target(text)
        if case_name:
            issues.append(CitationIssue(
                rule_id="R1", rule_name="Case name typeface",
                original=case_name, corrected=case_name,
                explanation="R1 — in law review footnotes, case names should be in italics.",
                severity="info",
                format_change=f"add:italic:{case_name}",
            ))
    if src_type == SourceType.JOURNAL_ARTICLE:
        m = re.search(r',\s*([A-Z][^,]{5,80}),\s*\d+', text)
        if m:
            issues.append(CitationIssue(
                rule_id="R1", rule_name="Article title typeface",
                original=m.group(1), corrected=m.group(1),
                explanation="R1 — article titles in law review footnotes should be in italics.",
                severity="info",
                format_change=f"add:italic:{m.group(1)}",
            ))
    if src_type in (SourceType.BOOK, SourceType.BOOK_CHAPTER):
        m = re.search(r',\s*([A-Z][^,(]{5,80})(?:\s*\(|\s*\d)', text)
        if m:
            target = re.sub(r'\s+\d+\s*$', '', m.group(1)).strip()
            issues.append(CitationIssue(
                rule_id="R2", rule_name="Book title typeface",
                original=target, corrected=target,
                explanation="R2 — book titles in law review footnotes should be in SMALL CAPS.",
                severity="info",
                format_change=f"add:small_caps:{target}",
            ))
    # Treaty names: roman type
    if src_type == SourceType.TREATY:
        if re.search(r'[*_]', text):
            issues.append(CitationIssue(
                rule_id="R2", rule_name="Treaty name typeface",
                original=text, corrected=text,
                explanation="R2 — treaty names should be in roman type, not italic or SMALL CAPS.",
                severity="info",
            ))
    return issues


def _case_name_target(text: str) -> str | None:
    m = re.match(r'^(.+?\s+v\.?\s+.+?)(?=,\s+\d|\s+\d+\s+[A-Z])', text, re.I)
    if m:
        return m.group(1).strip()
    m = re.match(r'^(.+?\s+v\.?\s+.+?)(?=,\s)', text, re.I)
    return m.group(1).strip() if m else None


# ── R10: Domestic cases ───────────────────────────────────────────────────────

def check_r10_cases(text: str) -> list[CitationIssue]:
    issues = []
    for m in re.finditer(r'\bvs\.', text, re.I):
        issues.append(CitationIssue(
            rule_id="R10", rule_name="Case name 'v.' not 'vs.'",
            original=m.group(0), corrected="v.",
            explanation="R10 — case names use 'v.' not 'vs.'",
            severity="error",
        ))
    for m in re.finditer(r'\bIn\s+Re\b', text):
        issues.append(CitationIssue(
            rule_id="R10", rule_name="'In re' capitalisation",
            original=m.group(0), corrected="In re",
            explanation="R10 — 'In re' should not capitalise 're'.",
            severity="error",
        ))
    for m in re.finditer(r',\s*(\d+)-(\d+)', text):
        if int(m.group(2)) - int(m.group(1)) > 0:
            issues.append(CitationIssue(
                rule_id="R10", rule_name="En-dash in page range",
                original=f"{m.group(1)}-{m.group(2)}",
                corrected=f"{m.group(1)}–{m.group(2)}",
                explanation="R10 — page ranges should use an en-dash (–), not a hyphen (-).",
                severity="warning",
            ))
    for m in re.finditer(r'\bUnited\s+States\s+of\s+America\b', text):
        issues.append(CitationIssue(
            rule_id="R10", rule_name="Party name: use 'United States'",
            original=m.group(0), corrected="United States",
            explanation="R10 — when the United States is a party, use 'United States', not 'United States of America'.",
            severity="error",
        ))
    for m in re.finditer(r'\b(Inc\.|Corp\.|Ltd\.)\s*,', text):
        issues.append(CitationIssue(
            rule_id="R10", rule_name="Omit business designation unless sole identifier",
            original=m.group(0), corrected=m.group(0).replace(m.group(1), "").strip(),
            explanation="R10 — omit 'Inc.', 'Corp.', 'Ltd.' from party names unless the designation is the only way to identify the party.",
            severity="warning",
        ))
    # Missing pinpoint for U.S. reporter cites
    if re.search(r'\d+\s+U\.S\.\s+\d+', text) and not re.search(r'\d+\s+U\.S\.\s+\d+,\s*\d+', text):
        issues.append(CitationIssue(
            rule_id="R10", rule_name="Pinpoint citation missing",
            original=text, corrected=text,
            explanation="R10 — include a pinpoint page number after the first page when citing a specific proposition.",
            severity="warning",
        ))
    return issues


# ── R11: Constitutions ────────────────────────────────────────────────────────

def check_r11_constitutions(text: str) -> list[CitationIssue]:
    issues = []
    if re.search(r'\bConstitution\b', text, re.I) and not re.search(r'\bConst\.', text):
        issues.append(CitationIssue(
            rule_id="R11", rule_name="Constitution abbreviation",
            original="Constitution", corrected="Const.",
            explanation="R11 — 'Constitution' should be abbreviated 'Const.' in citations.",
            severity="warning",
        ))
    for m in re.finditer(r'\bArticle\s+(I{1,3}V?|VI{0,3}|IX|X[IVX]*|\d+)\b', text):
        issues.append(CitationIssue(
            rule_id="R11", rule_name="Constitution: use 'art.' not 'Article'",
            original=m.group(0), corrected=f"art. {m.group(1)}",
            explanation="R11 — use 'art.' (not 'Article') in constitutional citations.",
            severity="warning",
        ))
    for m in re.finditer(r'\bAmendment\s+(\w+)\b', text, re.I):
        issues.append(CitationIssue(
            rule_id="R11", rule_name="Constitution: use 'amend.' not 'Amendment'",
            original=m.group(0), corrected=f"amend. {m.group(1)}",
            explanation="R11 — use 'amend.' (not 'Amendment') in constitutional citations.",
            severity="warning",
        ))
    return issues


# ── R12: Statutes ─────────────────────────────────────────────────────────────

def check_r12_statutes(text: str) -> list[CitationIssue]:
    issues = []
    # CFR missing §
    for m in re.finditer(r'C\.F\.R\.\s+(\d)', text):
        issues.append(CitationIssue(
            rule_id="R12", rule_name="CFR section symbol missing",
            original=m.group(0), corrected=f"C.F.R. § {m.group(1)}",
            explanation="R12 — C.F.R. citations should include the § symbol before the section number.",
            severity="warning",
        ))
    # U.S.C. missing §
    for m in re.finditer(r'U\.S\.C\.\s+(\d)', text):
        issues.append(CitationIssue(
            rule_id="R12", rule_name="U.S.C. section symbol missing",
            original=m.group(0), corrected=f"U.S.C. § {m.group(1)}",
            explanation="R12 — U.S.C. citations should include the § symbol before the section number.",
            severity="error",
        ))
    # Multiple sections: need §§ and en-dash
    for m in re.finditer(r'§\s*(\d+)\s*[-–]\s*(\d+)', text):
        if '-' in m.group(0):
            issues.append(CitationIssue(
                rule_id="R12", rule_name="Multiple sections: use §§ and en-dash",
                original=m.group(0), corrected=f"§§ {m.group(1)}–{m.group(2)}",
                explanation="R12 — when citing multiple consecutive sections, use '§§' and an en-dash (–).",
                severity="warning",
            ))
    # Missing year parenthetical
    if re.search(r'U\.S\.C\..*§', text) and not re.search(r'\(\d{4}\)', text):
        issues.append(CitationIssue(
            rule_id="R12", rule_name="Statute missing year parenthetical",
            original=text, corrected=text,
            explanation="R12 — verify whether this U.S.C. citation needs a code-edition year under the governing style guide; current-code citations may not require one.",
            severity="warning",
        ))
    # Session law bare Stat. without Pub. L. No.
    if re.search(r'\bStat\.\s+\d', text) and not re.search(r'Pub\.\s*L\.\s*No\.', text):
        issues.append(CitationIssue(
            rule_id="R12", rule_name="Session law: include Pub. L. No.",
            original=text, corrected=text,
            explanation="R12 — session law citations should include 'Pub. L. No. XXX-XX, Stat. XXX (Year)'.",
            severity="warning",
        ))
    # Edition ordinals
    for wrong, right in [("2nd", "2d"), ("3rd", "3d")]:
        if wrong in text:
            issues.append(CitationIssue(
                rule_id="R12", rule_name="Edition ordinal format",
                original=wrong, corrected=right,
                explanation=f"R12 — Bluebook ordinals: '{wrong}' → '{right}'.",
                severity="error",
            ))
    return issues


# ── R13: Legislative materials ────────────────────────────────────────────────

def check_r13_legislative(text: str) -> list[CitationIssue]:
    issues = []
    t = text.strip()

    # Bills: H.R. or S. — need Congress number
    bill_m = re.search(r'\b(H\.R\.|S\.)\s*\d+', t)
    if bill_m and not re.search(r'\d+(st|nd|rd|th)\s+Cong\.', t, re.I):
        issues.append(CitationIssue(
            rule_id="R13", rule_name="Bill: missing Congress number",
            original=bill_m.group(0), corrected=bill_m.group(0),
            explanation="R13 — bill citations must include the Congress number and session, e.g., H.R. 1234, 117th Cong. (2021).",
            severity="warning",
        ))

    # Hearings: need "Before the [Committee]" and Congress number
    if re.search(r'\bHearing\b', t, re.I):
        if not re.search(r'Before\s+the\s+[A-Z]', t):
            issues.append(CitationIssue(
                rule_id="R13", rule_name="Hearing: missing committee",
                original=t, corrected=t,
                explanation="R13 — hearing citations must identify the committee: 'Hearing on X Before the S. Comm. on Y, NNth Cong. N (Year)'.",
                severity="warning",
            ))
        if not re.search(r'\d+(st|nd|rd|th)\s+Cong\.', t, re.I):
            issues.append(CitationIssue(
                rule_id="R13", rule_name="Hearing: missing Congress number",
                original=t, corrected=t,
                explanation="R13 — hearing citations must include the Congress number.",
                severity="warning",
            ))

    # Committee reports: S. Rep. / H.R. Rep. — need Congress number and "at" for pinpoint
    rep_m = re.search(r'\b(S\.\s*Rep\.|H\.R\.\s*Rep\.|H\.\s*Rep\.)\s*No\.\s*(\d+)', t)
    if rep_m:
        if not re.search(r'\d+(st|nd|rd|th)\s+Cong\.', t, re.I):
            issues.append(CitationIssue(
                rule_id="R13", rule_name="Committee report: missing Congress number",
                original=rep_m.group(0), corrected=rep_m.group(0),
                explanation="R13 — committee report citations must include the Congress number, e.g., S. Rep. No. 111-5, at 12 (2009).",
                severity="warning",
            ))
        if re.search(r'\d+', t) and not re.search(r'\bat\s+\d+', t):
            issues.append(CitationIssue(
                rule_id="R13", rule_name="Committee report: use 'at N' for pinpoint",
                original=t, corrected=t,
                explanation="R13 — use 'at N' (not 'p. N') for pinpoint pages in committee reports.",
                severity="info",
            ))

    # Congressional Record format
    cong_rec = re.search(r'\bCong(?:ress(?:ional)?)?\.\s*Rec(?:ord)?\b', t, re.I)
    if cong_rec:
        raw = cong_rec.group(0)
        if raw.strip() != "Cong. Rec.":
            issues.append(CitationIssue(
                rule_id="R13", rule_name="Congressional Record abbreviation",
                original=raw, corrected="Cong. Rec.",
                explanation="R13 — Congressional Record is abbreviated 'Cong. Rec.'",
                severity="error",
            ))
        if not re.search(r'\d+\s+Cong\.\s+Rec\.', t):
            issues.append(CitationIssue(
                rule_id="R13", rule_name="Congressional Record: include volume",
                original=t, corrected=t,
                explanation="R13 — Congressional Record citations should include the volume: e.g., 157 Cong. Rec. S1234 (daily ed. Mar. 1, 2011) (statement of Sen. X).",
                severity="warning",
            ))

    return issues


# ── R14: Administrative / executive materials ─────────────────────────────────

def check_r14_admin(text: str) -> list[CitationIssue]:
    issues = []
    t = text.strip()

    # Executive Orders
    eo_m = re.search(r'\bExec(?:utive)?\.?\s*Order(?:\s*No\.?)?\s*(\d+)', t, re.I)
    if eo_m:
        raw = eo_m.group(0)
        correct = f"Exec. Order No. {eo_m.group(1)}"
        if raw.strip() != correct:
            issues.append(CitationIssue(
                rule_id="R14", rule_name="Executive Order format",
                original=raw, corrected=correct,
                explanation="R14 — Executive Orders are cited as 'Exec. Order No. NNNNN, N C.F.R. N (Year)'.",
                severity="warning",
            ))
        if not re.search(r'C\.F\.R\.|Fed\.\s*Reg\.', t):
            issues.append(CitationIssue(
                rule_id="R14", rule_name="Executive Order: cite C.F.R. or Fed. Reg.",
                original=t, corrected=t,
                explanation="R14 — Executive Order citations should include a C.F.R. or Federal Register reference.",
                severity="info",
            ))

    # "Federal Register" spelled out
    if re.search(r'\bFederal\s+Register\b', t, re.I):
        issues.append(CitationIssue(
            rule_id="R14", rule_name="Federal Register abbreviation",
            original="Federal Register", corrected="Fed. Reg.",
            explanation="R14 — 'Federal Register' should be abbreviated 'Fed. Reg.' in citations.",
            severity="warning",
        ))

    # Agency documents missing year
    if re.search(r'\b(Advisory\s+Circular|AC\s+\d|Order\s+\d{4,})\b', t, re.I):
        if not re.search(r'\(\d{4}\)', t):
            issues.append(CitationIssue(
                rule_id="R14", rule_name="Agency document: missing year",
                original=t, corrected=t,
                explanation="R14 — agency documents should include the issuance year in parentheses.",
                severity="warning",
            ))

    return issues


# ── R15: Books ────────────────────────────────────────────────────────────────

def check_r15_books(text: str) -> list[CitationIssue]:
    issues = []
    for wrong, right in [("2nd", "2d"), ("3rd", "3d")]:
        if wrong in text:
            issues.append(CitationIssue(
                rule_id="R15", rule_name="Edition ordinal format",
                original=wrong, corrected=right,
                explanation=f"R15 — Bluebook ordinals: '{wrong}' → '{right}'.",
                severity="error",
            ))
    if re.search(r'\bpp\.\s+\d', text):
        for m in re.finditer(r'\bpp\.\s+(\d)', text):
            issues.append(CitationIssue(
                rule_id="R15", rule_name="Remove 'pp.' page prefix",
                original=m.group(0), corrected=m.group(1),
                explanation="R15 — do not use 'pp.' before page numbers in Bluebook citations.",
                severity="error",
            ))
    if not re.search(r'\(\d{4}\)', text) and not re.search(r'\(\w+\s+ed\.\s+\d{4}\)', text):
        issues.append(CitationIssue(
            rule_id="R15", rule_name="Book missing year parenthetical",
            original=text, corrected=text,
            explanation="R15 — book citations must include the publication year (and edition if not first) in parentheses.",
            severity="warning",
        ))
    if re.search(r'\(\s*edited\s+by\b', text, re.I):
        issues.append(CitationIssue(
            rule_id="R15", rule_name="Edited volume: use 'ed.' not 'edited by'",
            original="edited by", corrected="ed.",
            explanation="R15 — edited volumes use 'ed.' or 'eds.', not 'edited by'.",
            severity="warning",
        ))
    return issues


# ── R16: Journal articles ─────────────────────────────────────────────────────

def check_r16_journals(text: str) -> list[CitationIssue]:
    issues = []
    for m in re.finditer(r'\bvol\.\s+(\d)', text, re.I):
        issues.append(CitationIssue(
            rule_id="R16", rule_name="Remove 'vol.' volume prefix",
            original=m.group(0), corrected=m.group(1),
            explanation="R16 — do not use 'vol.' before the volume number in journal citations.",
            severity="error",
        ))
    for m in re.finditer(r',\s*no\.\s*\d+\s*,', text, re.I):
        issues.append(CitationIssue(
            rule_id="R16", rule_name="Omit issue number",
            original=m.group(0), corrected=",",
            explanation="R16 — Bluebook journal citations omit the issue number.",
            severity="warning",
        ))
    # Pinpoint missing
    if re.search(r'\d+\s+\w[\w\s\.]+\d+\s*\(\d{4}\)', text):
        if not re.search(r'\d+,\s*\d+\s*\(\d{4}\)', text):
            issues.append(CitationIssue(
                rule_id="R16", rule_name="Journal article: pinpoint missing",
                original=text, corrected=text,
                explanation="R16 — include a pinpoint page after the first page when citing a specific passage.",
                severity="warning",
            ))
    # Year missing
    if not re.search(r'\(\d{4}\)\s*\.?\s*$', text.strip()):
        issues.append(CitationIssue(
            rule_id="R16", rule_name="Journal article: missing year",
            original=text, corrected=text,
            explanation="R16 — journal article citations must end with the publication year in parentheses.",
            severity="error",
        ))
    return issues


# ── R16.6: Newspapers and magazines ──────────────────────────────────────────

_FULL_MONTHS = ["January","February","March","April","May","June",
                "July","August","September","October","November","December"]
_ABBREV_MONTHS = ["Jan.","Feb.","Mar.","Apr.","May","June",
                  "July","Aug.","Sept.","Oct.","Nov.","Dec."]
_NO_ABBREV = {"May", "June", "July"}

def check_r16_6_newspaper(text: str) -> list[CitationIssue]:
    issues = []
    if not re.search(r'\(\w+\.?\s+\d{1,2},?\s+\d{4}\)', text):
        issues.append(CitationIssue(
            rule_id="R16.6", rule_name="Newspaper: missing date parenthetical",
            original=text, corrected=text,
            explanation="R16.6 — newspaper/magazine citations must include the full date, e.g., (Mar. 15, 2023).",
            severity="error",
        ))
    for full, abbrev in zip(_FULL_MONTHS, _ABBREV_MONTHS):
        if full not in _NO_ABBREV and re.search(r'\b' + full + r'\b', text):
            issues.append(CitationIssue(
                rule_id="R16.6", rule_name=f"Abbreviate month: {full}",
                original=full, corrected=abbrev,
                explanation=f"R16.6 — months should be abbreviated per T12: '{full}' → '{abbrev}'.",
                severity="warning",
            ))
    m = re.search(r',\s*([A-Z][^,]{5,80}),\s*[A-Z]', text)
    if m:
        issues.append(CitationIssue(
            rule_id="R16.6", rule_name="Newspaper article title typeface",
            original=m.group(1), corrected=m.group(1),
            explanation="R16.6 — newspaper/magazine article titles should be in italics.",
            severity="info",
            format_change=f"add:italic:{m.group(1)}",
        ))
    return issues


# ── R17: Unpublished / forthcoming works ─────────────────────────────────────

def check_r17_unpublished(text: str) -> list[CitationIssue]:
    issues = []
    t = text.strip()
    if re.search(r'\bunpublished\b', t, re.I):
        if not re.search(r'\(unpublished\s+manuscript\b', t, re.I):
            issues.append(CitationIssue(
                rule_id="R17", rule_name="Unpublished: parenthetical format",
                original=t, corrected=t,
                explanation="R17 — unpublished manuscripts should include '(unpublished manuscript)' and institutional affiliation if available.",
                severity="warning",
            ))
    if re.search(r'\bforthcoming\b', t, re.I):
        if not re.search(r'\(forthcoming\s+\d{4}\)', t, re.I):
            issues.append(CitationIssue(
                rule_id="R17", rule_name="Forthcoming: include year",
                original=t, corrected=t,
                explanation="R17 — forthcoming works should be cited as '(forthcoming YYYY)'.",
                severity="warning",
            ))
    if re.search(r'\bWorking\s+Paper\b', t, re.I):
        if not re.search(r'No\.\s*\d+', t):
            issues.append(CitationIssue(
                rule_id="R17", rule_name="Working paper: include series number",
                original=t, corrected=t,
                explanation="R17 — working papers should include the series name and paper number where available.",
                severity="info",
            ))
    return issues


# ── R18: Internet sources ─────────────────────────────────────────────────────

def check_r18_internet(text: str) -> list[CitationIssue]:
    issues = []
    if "last visited" not in text.lower() and "last accessed" not in text.lower():
        issues.append(CitationIssue(
            rule_id="R18", rule_name="Missing 'last visited' date",
            original=text, corrected=text,
            explanation="R18 — internet citations must include '(last visited Mon. Day, Year)'.",
            severity="error",
        ))
    for m in re.finditer(r'last\s+visited\s+([A-Za-z]+)\s+(\d+)', text, re.I):
        month = m.group(1)
        if month in _FULL_MONTHS and month not in _NO_ABBREV:
            idx = _FULL_MONTHS.index(month)
            issues.append(CitationIssue(
                rule_id="R18", rule_name="Abbreviate month in 'last visited'",
                original=month, corrected=_ABBREV_MONTHS[idx],
                explanation="R18 — months in 'last visited' parentheticals should be abbreviated per T12.",
                severity="warning",
            ))
    # Bare URL with no title
    if re.search(r'https?://\S+', text) and not re.search(r',\s*\*?[A-Z]', text):
        issues.append(CitationIssue(
            rule_id="R18", rule_name="Internet citation: include title",
            original=text, corrected=text,
            explanation="R18 — internet citations should include the author (if available) and page/article title before the URL.",
            severity="info",
        ))
    return issues


def check_r18_ai_generated(text: str) -> list[CitationIssue]:
    issues = []
    if not re.search(r'\b(ChatGPT|Claude|Gemini|Copilot|OpenAI|Anthropic|Google)\b', text, re.I):
        issues.append(CitationIssue(
            rule_id="R18",
            rule_name="AI source: identify model/provider",
            original=text,
            corrected=text,
            explanation="R18 — AI-generated output citations should identify the tool or model/provider used.",
            severity="warning",
        ))
    if not re.search(r'\b(prompt|query|output|response|transcript|screenshot|PDF)\b', text, re.I):
        issues.append(CitationIssue(
            rule_id="R18",
            rule_name="AI source: document prompt/output",
            original=text,
            corrected=text,
            explanation="R18 — AI-output citations should preserve enough prompt/output detail for verification.",
            severity="warning",
        ))
    if not re.search(r'https?://|perma\.cc|archive\.org|last\s+visited|screenshot|PDF', text, re.I):
        issues.append(CitationIssue(
            rule_id="R18",
            rule_name="AI source: preserve retrieval evidence",
            original=text,
            corrected=text,
            explanation="R18 — AI-output citations should include durable retrieval evidence such as a saved PDF, screenshot, or archive link where required.",
            severity="warning",
        ))
    return issues


# ── R21.4: Treaties ───────────────────────────────────────────────────────────

def check_r21_treaties(text: str) -> list[CitationIssue]:
    issues = []
    if not re.search(r'\d+\s+U\.N\.T\.S\.|\d+\s+I\.L\.M\.|\d+\s+U\.S\.T\.|\d+\s+T\.I\.A\.S\.', text):
        issues.append(CitationIssue(
            rule_id="R21.4", rule_name="Treaty: cite U.N.T.S., I.L.M., or U.S.T.",
            original=text, corrected=text,
            explanation="R21.4 — treaties should be cited to U.N.T.S., I.L.M., U.S.T., or T.I.A.S. where available.",
            severity="warning",
        ))
    if re.search(r'\bopened\s+for\s+signature\b', text, re.I):
        if not re.search(r'\b[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4}\b', text):
            issues.append(CitationIssue(
                rule_id="R21.4", rule_name="Treaty date format",
                original=text, corrected=text,
                explanation="R21.4 — treaty opening date should be 'Mon. D, YYYY' (e.g., Jan. 27, 1967).",
                severity="warning",
            ))
    if re.search(r'entered\s+into\s+force', text, re.I):
        if not re.search(r'entered\s+into\s+force\s+[A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4}', text, re.I):
            issues.append(CitationIssue(
                rule_id="R21.4", rule_name="Treaty entry-into-force date format",
                original=text, corrected=text,
                explanation="R21.4 — entry-into-force date should follow the same 'Mon. D, YYYY' format.",
                severity="info",
            ))
    for m in re.finditer(r'\bArticle\s+(\w+)', text):
        issues.append(CitationIssue(
            rule_id="R21.4", rule_name="Treaty article: use 'art.' not 'Article'",
            original=m.group(0), corrected=f"art. {m.group(1)}",
            explanation="R21.4 — use 'art.' not 'Article' in treaty citations.",
            severity="warning",
        ))
    # Multilateral: omit parties
    if re.search(r'\bopened\s+for\s+signature\b', text, re.I):
        if re.search(r'\b(U\.S\.?|United\s+States)\s+[-–]\s+\w+', text):
            issues.append(CitationIssue(
                rule_id="R21.4", rule_name="Multilateral treaty: omit parties",
                original=text, corrected=text,
                explanation="R21.4 — omit parties for multilateral treaties. Include parties only for bilateral agreements.",
                severity="warning",
            ))
    return issues


# ── Newer current-manual categories ───────────────────────────────────────────

def check_r22_tribal(text: str) -> list[CitationIssue]:
    issues = []
    issues.append(CitationIssue(
        rule_id="CURRENT",
        rule_name="Tribal source: verify current dedicated form",
        original=text,
        corrected=text,
        explanation="Current manual / house style — Tribal materials may have dedicated forms; verify jurisdiction, court/code name, section or reporter, and date against the controlling source.",
        severity="warning",
    ))
    if not re.search(r'\b(?:Nation|Tribe|Tribal|Code|Ct\.|Court)\b', text):
        issues.append(CitationIssue(
            rule_id="CURRENT",
            rule_name="Tribal source: identify jurisdiction",
            original=text,
            corrected=text,
            explanation="Current manual / house style — identify the Tribal jurisdiction clearly enough for retrieval.",
            severity="warning",
        ))
    return issues


def check_r23_archival(text: str) -> list[CitationIssue]:
    issues = []
    if not re.search(r'\b(?:box|folder|collection|papers|archive|archives|library)\b', text, re.I):
        issues.append(CitationIssue(
            rule_id="CURRENT",
            rule_name="Archival source: include collection details",
            original=text,
            corrected=text,
            explanation="Current manual / house style — archival citations should include collection, repository, and locator details where available.",
            severity="warning",
        ))
    if not re.search(r'\b(?:box|folder)\s+\d+', text, re.I):
        issues.append(CitationIssue(
            rule_id="CURRENT",
            rule_name="Archival source: include box/folder locator",
            original=text,
            corrected=text,
            explanation="Current manual / house style — include box, folder, item, or other locator details when available.",
            severity="info",
        ))
    return issues


# ── R21.5/6: ICJ and international tribunals ──────────────────────────────────

def check_r21_icj(text: str) -> list[CitationIssue]:
    issues = []
    for wrong, right in [("ICJ Rep.", "I.C.J. Reports"), ("ICJ Reports", "I.C.J. Reports"),
                         ("I.C.J. Rep.", "I.C.J. Reports")]:
        if wrong in text:
            issues.append(CitationIssue(
                rule_id="R21.5", rule_name="ICJ reporter abbreviation",
                original=wrong, corrected=right,
                explanation="R21.5 — the ICJ reporter is 'I.C.J. Reports'.",
                severity="error",
            ))
    if not re.search(r'\(\w+\.?\s+\d{1,2},?\s+\d{4}\)\s*\.?\s*$', text.strip()):
        if not re.search(r'\(\d{4}\)\s*\.?\s*$', text.strip()):
            issues.append(CitationIssue(
                rule_id="R21.5", rule_name="ICJ: include decision date",
                original=text, corrected=text,
                explanation="R21.5 — ICJ citations should end with the decision date or year in parentheses.",
                severity="warning",
            ))
    for m in re.finditer(r'\bpara\.\s+(\d+)', text, re.I):
        issues.append(CitationIssue(
            rule_id="R21.5", rule_name="ICJ: use ¶ not 'para.'",
            original=m.group(0), corrected=f"¶ {m.group(1)}",
            explanation="R21.5 — use the ¶ symbol for paragraph references in ICJ citations.",
            severity="warning",
        ))
    if re.search(r'\badvisory\s+opinion\b', text, re.I) and not re.search(r'Advisory\s+Opinion', text):
        issues.append(CitationIssue(
            rule_id="R21.5", rule_name="ICJ Advisory Opinion: capitalise",
            original="advisory opinion", corrected="Advisory Opinion",
            explanation="R21.5 — 'Advisory Opinion' should be capitalised in ICJ citations.",
            severity="warning",
        ))
    return issues


# ── R21.8: UN Documents ───────────────────────────────────────────────────────

def check_r21_un_docs(text: str) -> list[CitationIssue]:
    issues = []
    for m in re.finditer(r'\bUNGA\s+Res\.', text, re.I):
        issues.append(CitationIssue(
            rule_id="R21.8", rule_name="GA Resolution abbreviation",
            original=m.group(0), corrected="G.A. Res.",
            explanation="R21.8 — General Assembly resolutions are cited as 'G.A. Res.', not 'UNGA Res.'",
            severity="error",
        ))
    for m in re.finditer(r'\bUNSC\s+Res\.', text, re.I):
        issues.append(CitationIssue(
            rule_id="R21.8", rule_name="SC Resolution abbreviation",
            original=m.group(0), corrected="S.C. Res.",
            explanation="R21.8 — Security Council resolutions are cited as 'S.C. Res.', not 'UNSC Res.'",
            severity="error",
        ))
    for m in re.finditer(r'\bUNGAOR\b', text):
        issues.append(CitationIssue(
            rule_id="R21.8", rule_name="GAOR abbreviation",
            original="UNGAOR", corrected="U.N. GAOR",
            explanation="R21.8 — use 'U.N. GAOR', not 'UNGAOR'.",
            severity="error",
        ))
    for m in re.finditer(r'\bU\.?N\.?\s+Doc(?:ument)?\.?\b', text, re.I):
        raw = m.group(0)
        if raw.strip() != "U.N. Doc.":
            issues.append(CitationIssue(
                rule_id="R21.8", rule_name="UN Document symbol format",
                original=raw, corrected="U.N. Doc.",
                explanation="R21.8 — UN documents are cited as 'U.N. Doc. [symbol]'.",
                severity="warning",
            ))
    if re.search(r'U\.N\.\s+Doc\.', text) and not re.search(r'\(\d{4}\)', text):
        issues.append(CitationIssue(
            rule_id="R21.8", rule_name="UN Doc: missing year",
            original=text, corrected=text,
            explanation="R21.8 — UN document citations should include the year in parentheses.",
            severity="warning",
        ))
    if re.search(r'U\.N\.\s+GAOR', text):
        if not re.search(r'\d+(st|nd|rd|th)\s+Sess\.', text, re.I):
            issues.append(CitationIssue(
                rule_id="R21.8", rule_name="GAOR: include session number",
                original=text, corrected=text,
                explanation="R21.8 — U.N. GAOR citations should include 'NNth Sess., Supp. No. NN, at N, U.N. Doc. A/XXXX (Year)'.",
                severity="warning",
            ))
    return issues
