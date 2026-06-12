"""
classifier.py — Classify citation text into SourceType.
Uses regex heuristics in priority order.
"""
from __future__ import annotations
import re
from enum import Enum


class SourceType(Enum):
    ID                 = "id"
    SUPRA              = "supra"
    INFRA              = "infra"
    CASE_DOMESTIC      = "case_domestic"
    CASE_ICJ           = "case_icj"
    CASE_INTL_TRIBUNAL = "case_intl_tribunal"
    STATUTE_US         = "statute_us"
    STATUTE_FOREIGN    = "statute_foreign"
    CONSTITUTION       = "constitution"       # R11
    LEGISLATIVE        = "legislative"        # R13: bills, hearings, committee reports, Cong. Rec.
    ADMIN_EXEC         = "admin_exec"         # R14: exec. orders, Fed. Reg., agency docs
    TREATY             = "treaty"
    BOOK               = "book"
    BOOK_CHAPTER       = "book_chapter"
    JOURNAL_ARTICLE    = "journal_article"
    NEWSPAPER          = "newspaper"
    UNPUBLISHED        = "unpublished"        # R17: manuscripts, working papers, forthcoming
    AI_GENERATED       = "ai_generated"
    UN_DOC             = "un_doc"
    REPORT_INTL        = "report_intl"
    TRIBAL             = "tribal"
    ARCHIVAL           = "archival"
    INTERNET           = "internet"
    UNKNOWN            = "unknown"


# ── Compiled patterns ──────────────────────────────────────────────────────────

_SUPRA_RE  = re.compile(r'\bsupra\s+note\b', re.I)
_INFRA_RE  = re.compile(r'\binfra\s+note\b', re.I)
_ID_RE     = re.compile(r'^\s*[Ii]d\.?\b')

_ICJ_RE    = re.compile(
    r'\bI\.?C\.?J\.?\b'
    r'|\bInternational\s+Court\s+of\s+Justice\b'
    r'|\bI\.C\.J\.\s+Reports\b',
    re.I
)

_ICC_RE    = re.compile(
    r"\bICC\b|\bInt['’`]l\s+Crim\.\s*Ct\b"
    r'|\bICTY\b|\bICTR\b|\bSCSL\b|\bSTL\b'
    r'|\bPermanent\s+Court\s+of\s+Arbitration\b'
    r'|\bITLOS\b|\bWTO\s+Panel\b|\bAAA\b',
    re.I
)

_US_REPORTER_RE = re.compile(
    r'\b\d+\s+U\.S\.\s+\d+'
    r'|\b\d+\s+S\.\s*Ct\.\s+\d+'
    r'|\b\d+\s+F\.\d*d?\s+\d+'
    r'|\b\d+\s+F\.\s+Supp\.'
    r'|\b\d+\s+(?!(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.\s+\d{4}\b)[A-Z][a-z]*\.\s+\d+',  # generic reporter
    re.I
)

_UN_DOC_RE = re.compile(
    r'\bU\.N\.\s+Doc\b'
    r'|\bA/RES/\d+'
    r'|\bS/RES/\d+'
    r'|\bG\.A\.\s+Res\.\s+\d+'
    r'|\bS\.C\.\s+Res\.\s+\d+'
    r'|\bUNGAOR\b|\bUNSCOR\b',
    re.I
)

_TREATY_RE = re.compile(
    r'\bTreaty\b|\bConvention\b|\bProtocol\b|\bAgreement\b|\bCharter\b'
    r'|\bStatute\s+of\b|\bOuter\s+Space\s+Treaty\b'
    r'|\bU\.N\.T\.S\b|\bI\.L\.M\b|\bE\.T\.S\b'
    r'|\bT\.I\.A\.S\b|\bU\.S\.T\b',
    re.I
)

_INTL_ORG_REPORT_RE = re.compile(
    r'\bICAO\b|\bITU\b|\bWIPO\b|\bWMO\b|\bUNIDROIT\b|\bUNCITRAL\b'
    r'|\bOECD\b|\bWTO\b|\bIMO\b|\bIEA\b|\bNASA\b|\bESA\b'
    r'|\bORBICOMM\b|\bCOMSAT\b',
    re.I
)

_CONSTITUTION_RE = re.compile(
    r'\bU\.S\.\s+Const\b'
    r'|\bConst\.\s+art\.'
    r'|\bConst\.\s+amend\.',
    re.I
)

_LEGISLATIVE_RE = re.compile(
    r'\bH\.R\.\s*\d+'
    r'|\bHearing\s+on\b'
    r'|\bHearing\s+Before\b'
    r'|\bHearings?\s+Before\b'
    r'|\bS\.\s*Rep\.\s*No\.'
    r'|\bH\.R\.\s*Rep\.\s*No\.'
    r'|\bH\.\s*Rep\.\s*No\.'
    r'|\bCong\.\s*Rec\b'
    r'|\b\d+\s+Cong\.\s+Rec\b'
    r'|\bConf\.\s*Rep\.\s*No\.',
    re.I
)

_ADMIN_EXEC_RE = re.compile(
    r'\bExec(?:utive)?\.?\s*Order\b'
    r'|\bFed(?:eral)?\.\s*Reg\.\b'
    r'|\bFederal\s+Register\b'
    r'|\b\d+\s+Fed\.\s*Reg\b'
    r'|\bAdvisory\s+Circular\b'
    r'|\bPresidential\s+Proclamation\b',
    re.I
)

_UNPUBLISHED_RE = re.compile(
    r'\bunpublished\b'
    r'|\bforthcoming\b'
    r'|\bWorking\s+Paper\b'
    r'|\bmanuscript\s+on\s+file\b',
    re.I
)

_AI_GENERATED_RE = re.compile(
    r'\bChatGPT\b|\bOpenAI\b|\bClaude\b|\bGemini\b|\bCopilot\b'
    r'|\bAI[-\s]?generated\b|\blarge\s+language\s+model\b|\bLLM\b'
    r'|\bprompt\b.*\b(?:response|output)\b',
    re.I
)

_TRIBAL_RE = re.compile(
    r'\bTribal\s+(?:Code|Court|Ct\.)\b'
    r'|\b(?:Navajo|Cherokee|Choctaw|Chickasaw|Muscogee|Hopi|Lakota|Ojibwe)\s+'
    r'(?:Nation|Code|Sup\.?\s+Ct\.?|Tribal\s+Ct\.?)\b'
    r'|\bCourt\s+of\s+the\s+[A-Z][A-Za-z\s]+Nation\b',
    re.I
)

_ARCHIVAL_RE = re.compile(
    r'\b(?:archive|archives|archival)\b'
    r'|\b(?:box|folder|collection|papers|manuscript)\s+\d+\b'
    r'|\b[A-Z][A-Za-z\s]+ Papers\b'
    r'|\b(?:Library|Archives),\s+[A-Z]',
    re.I
)

_USC_RE = re.compile(
    r'\b\d+\s+U\.S\.C\.\s+§'
    r'|\bPub\.\s+L\.\s+No\.'
    r'|\b\d+\s+C\.F\.R\.\s+§'
    r'|\bStat\.\s+\d+',
    re.I
)

_JOURNAL_RE = re.compile(
    r'\b(?:vol\.\s*)?\d+\s+[A-Z][A-Za-z\s\.]+(?:L\.|Law|J\.|Rev\.|Q\.|Int[\'l]*|Annals|Bull\.|Surv\.|Stud\.|Y\.?B\.?)\s+\d+'
    r'|\bJournal\s+of\s+[A-Z]'
    r'|\bInt[\'l]*\s+(?:&\s+)?[A-Z][a-z].*L(?:aw|\.)',
    re.I
)

_NEWSPAPER_RE = re.compile(
    r'\bN\.Y\.\s+Times\b|\bWall\s+St\.\s+J\b|\bWashington\s+Post\b'
    r'|\bGuardian\b|\bFinancial\s+Times\b|\bReuters\b|\bAP\b',
    re.I
)

_INTERNET_RE = re.compile(
    r'https?://|www\.\w+\.\w{2,}'
    r'|\(last\s+visited'
    r'|\(last\s+accessed',
    re.I
)

_BOOK_CHAPTER_RE = re.compile(
    r'\bin\s+[A-Z].*(?:ed\.|eds\.)\s*,?\s+\d{4}'
    r'|\bchapter\b|\bch\.\s+\d',
    re.I
)

_BOOK_RE = re.compile(
    r'\((\d{4})\)\s*[.,]?\s*$'  # ends with year (possibly followed by . or ,)
    r'|\(\d+\w*\s+ed\.\s+\d{4}\)'   # edition + year
    r'|\(ed\.\)',
    re.I
)


def classify(text: str) -> SourceType:
    t = text.strip()

    # International courts
    if _ICJ_RE.search(t):
        return SourceType.CASE_ICJ
    if _ICC_RE.search(t):
        return SourceType.CASE_INTL_TRIBUNAL

    # Newer/current manual categories that should not be misclassified as ordinary cases or URLs.
    if _TRIBAL_RE.search(t):
        return SourceType.TRIBAL
    if _AI_GENERATED_RE.search(t):
        return SourceType.AI_GENERATED

    # Domestic cases
    if _US_REPORTER_RE.search(t):
        return SourceType.CASE_DOMESTIC

    # Constitutions (R11) — before statutes to avoid misclassifying "U.S. Const."
    if _CONSTITUTION_RE.search(t):
        return SourceType.CONSTITUTION

    # UN docs
    if _UN_DOC_RE.search(t):
        return SourceType.UN_DOC

    # Treaties
    if _TREATY_RE.search(t):
        return SourceType.TREATY

    # Legislative materials (R13) — before statutes (some patterns overlap)
    if _LEGISLATIVE_RE.search(t):
        return SourceType.LEGISLATIVE

    # Administrative / executive (R14) — before statutes (CFR can appear in both)
    if _ADMIN_EXEC_RE.search(t) and not _USC_RE.search(t):
        return SourceType.ADMIN_EXEC

    # Intl org reports
    if _INTL_ORG_REPORT_RE.search(t) and not _JOURNAL_RE.search(t):
        return SourceType.REPORT_INTL

    # US statutes / CFR
    if _USC_RE.search(t):
        return SourceType.STATUTE_US

    # Journal articles
    if _JOURNAL_RE.search(t):
        return SourceType.JOURNAL_ARTICLE

    # Newspapers
    if _NEWSPAPER_RE.search(t):
        return SourceType.NEWSPAPER

    # Unpublished / forthcoming / working papers (R17)
    if _UNPUBLISHED_RE.search(t):
        return SourceType.UNPUBLISHED

    # Archival / historical sources
    if _ARCHIVAL_RE.search(t):
        return SourceType.ARCHIVAL

    # Internet
    if _INTERNET_RE.search(t):
        return SourceType.INTERNET

    # Book chapter (before book)
    if _BOOK_CHAPTER_RE.search(t):
        return SourceType.BOOK_CHAPTER

    # Book
    if _BOOK_RE.search(t):
        return SourceType.BOOK

    # Short forms only after full-source patterns get a chance to match.
    if _ID_RE.search(t):
        return SourceType.ID
    if _SUPRA_RE.search(t):
        return SourceType.SUPRA
    if _INFRA_RE.search(t):
        return SourceType.INFRA

    return SourceType.UNKNOWN


def source_key(text: str, src_type: SourceType) -> str | None:
    """Return a normalised deduplication key for supra detection."""
    if src_type in (SourceType.ID, SourceType.SUPRA, SourceType.INFRA):
        return None
    t = _normalise_source_text(text)
    if src_type == SourceType.JOURNAL_ARTICLE:
        key = _journal_source_key(t)
        if key:
            return key
    if src_type in (SourceType.BOOK, SourceType.BOOK_CHAPTER):
        key = _book_source_key(t)
        if key:
            return key
    # Strip leading signals
    for sig in ("see also", "see generally", "see", "cf.", "but see", "accord",
                "compare", "contra", "e.g.,", "e.g."):
        if t.startswith(sig):
            t = t[len(sig):].lstrip(" ,")
            break
    # Normalise whitespace and punctuation
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'[,;.]+$', '', t)
    # Drop pinpoint pages before the year so duplicate full citations with
    # different pincites still map to the same source.
    t = re.sub(r',\s*(?:at\s+)?\d+(?:[–-]\d+)?(?=\s*\(\d{4}\))', '', t)
    # For cases: use first two tokens (parties)
    if src_type in (SourceType.CASE_DOMESTIC, SourceType.CASE_ICJ,
                    SourceType.CASE_INTL_TRIBUNAL):
        m = re.match(r'(.+?)\s+v\.?\s+(.+?)[\s,]', t, re.I)
        if m:
            return f"case:{m.group(1).strip()} v {m.group(2).strip()}"
    # For books/journals: use first 40 chars of title area
    return t[:60]


def _normalise_source_text(text: str) -> str:
    t = text.lower().strip()
    for sig in ("see also", "see generally", "see", "cf.", "but see", "accord",
                "compare", "contra", "e.g.,", "e.g."):
        if t.startswith(sig):
            t = t[len(sig):].lstrip(" ,")
            break
    t = re.sub(r'\bvol\.\s*', '', t)
    t = re.sub(r'\s+', ' ', t)
    t = re.sub(r'[,;.]+$', '', t)
    return t


def _normalise_key_part(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', value.lower())


def _journal_source_key(t: str) -> str | None:
    parts = [part.strip() for part in t.split(",")]
    if len(parts) < 3:
        return None
    author = _normalise_key_part(parts[0])
    title = _normalise_key_part(parts[1])
    year_m = re.search(r'\((\d{4})\)', t)
    year = year_m.group(1) if year_m else ""
    m = re.search(
        r"\b(?P<volume>\d+)\s+(?P<journal>[a-z][a-z0-9\s\.'&-]+?)\s+"
        r"(?P<first_page>\d+)(?:,\s*(?:at\s+)?\d+(?:[–-]\d+)?)?\s*\(\d{4}\)",
        t,
        re.I,
    )
    if not (author and title and m):
        return None
    journal = _normalise_key_part(m.group("journal"))
    return f"journal:{author}:{title}:{m.group('volume')}:{journal}:{m.group('first_page')}:{year}"


def _book_source_key(t: str) -> str | None:
    parts = [part.strip() for part in t.split(",", 1)]
    if len(parts) < 2:
        return None
    author = _normalise_key_part(parts[0])
    title = re.sub(r'\s+\d+(?:[–-]\d+)?\s*\(\d{4}\).*$', '', parts[1]).strip()
    title_key = _normalise_key_part(title)
    year_m = re.search(r'\((\d{4})\)', t)
    if not (author and title_key):
        return None
    return f"book:{author}:{title_key}:{year_m.group(1) if year_m else ''}"
