"""
verifier.py — Verify citations against Crossref and OpenLibrary.
Falls back to heuristic structural checks when network unavailable.
"""
from __future__ import annotations
from difflib import SequenceMatcher
import re, time, urllib.request, urllib.parse, json
from dataclasses import dataclass, field
from .classifier import SourceType

USER_AGENT = "citation-checker/2.15.0"

_LAST_CROSSREF = 0.0
_LAST_OPENLIBRARY = 0.0
_DELAY = 0.5  # polite rate limit (seconds)


@dataclass
class VerificationResult:
    verified: bool | None = None   # None = not attempted / unknown
    confidence: float = 0.0        # 0–1
    canonical_title: str | None = None
    canonical_authors: list[str] = field(default_factory=list)
    canonical_year: int | None = None
    canonical_journal: str | None = None
    doi: str | None = None
    isbn: str | None = None
    source: str = "none"           # "crossref", "openlibrary", "heuristic"
    note: str = ""


def verify(text: str, src_type: SourceType, use_network: bool = True) -> VerificationResult:
    if src_type in (SourceType.ID, SourceType.SUPRA, SourceType.INFRA, SourceType.UNKNOWN):
        return VerificationResult(verified=None, source="skipped",
                                   note="short form or unknown — not verified")

    if use_network:
        if src_type == SourceType.JOURNAL_ARTICLE:
            r = _crossref_verify(text)
            if r.verified is not None:
                return r
        elif src_type in (SourceType.BOOK, SourceType.BOOK_CHAPTER):
            r = _openlibrary_verify(text)
            if r.verified is not None:
                return r
            r2 = _crossref_verify(text)
            if r2.verified is not None:
                return r2

    return _heuristic_verify(text, src_type)


# ── Crossref ──────────────────────────────────────────────────────────────────

def _crossref_verify(text: str) -> VerificationResult:
    global _LAST_CROSSREF

    # Extract DOI if present
    doi_m = re.search(r'10\.\d{4,}/\S+', text)
    doi = doi_m.group(0).rstrip('.,') if doi_m else None

    # Extract title (heuristic: italicised text between commas or quotes)
    title_m = re.search(r',\s*([A-Z][^,]{10,80}),\s*\d+', text)
    title = title_m.group(1).strip() if title_m else None

    if not doi and not title:
        return VerificationResult(source="crossref", note="no DOI or title extractable")

    now = time.time()
    gap = _DELAY - (now - _LAST_CROSSREF)
    if gap > 0:
        time.sleep(gap)
    _LAST_CROSSREF = time.time()

    try:
        if doi:
            url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
        else:
            q = urllib.parse.urlencode({
                "query.title": title,
                "rows": 1,
            })
            url = f"https://api.crossref.org/works?{q}"

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        if doi:
            item = data.get("message", {})
        else:
            items = data.get("message", {}).get("items", [])
            if not items:
                return VerificationResult(verified=False, source="crossref",
                                           note="no results from Crossref")
            item = items[0]
            score = item.get("score", 0)
            if score < 30:
                return VerificationResult(verified=False, confidence=0.2,
                                           source="crossref",
                                           note=f"low score {score}")

        return _crossref_item_result(text, item, source_title=title, doi=doi)
    except Exception as e:
        return VerificationResult(source="crossref", note=f"error: {e}")


def _crossref_item_result(
    text: str,
    item: dict,
    *,
    source_title: str | None,
    doi: str | None,
) -> VerificationResult:
    canon_title  = (item.get("title") or [""])[0]
    canon_year   = (item.get("published", {}).get("date-parts") or [[None]])[0][0]
    canon_journal= (item.get("container-title") or [None])[0]
    canon_doi    = item.get("DOI")
    authors      = [
        f"{a.get('given', '')} {a.get('family', '')}".strip()
        for a in item.get("author", [])[:3]
    ]

    cited_title = source_title or _extract_title(text)
    title_score = _title_similarity(cited_title, canon_title) if cited_title and canon_title else None
    cited_year = _extract_year(text)
    year_ok = cited_year is None or canon_year is None or cited_year == canon_year
    author_ok = _author_match(text, authors)
    detail_status, detail_note = _crossref_detail_status(text, item)

    if doi and not cited_title:
        if detail_status is True:
            confidence = 0.9
            note = "DOI resolved and volume/page matched"
        else:
            return VerificationResult(
                verified=None,
                confidence=0.82,
                canonical_title=canon_title,
                canonical_authors=authors,
                canonical_year=canon_year,
                canonical_journal=canon_journal,
                doi=canon_doi,
                source="crossref",
                note=f"DOI resolved; {detail_note}",
            )
    elif title_score is None:
        return VerificationResult(
            verified=None,
            confidence=0.3,
            canonical_title=canon_title,
            canonical_authors=authors,
            canonical_year=canon_year,
            canonical_journal=canon_journal,
            doi=canon_doi,
            source="crossref",
            note="no title available for metadata comparison",
        )
    elif title_score < 0.82:
        return VerificationResult(
            verified=False,
            confidence=0.2,
            canonical_title=canon_title,
            canonical_authors=authors,
            canonical_year=canon_year,
            canonical_journal=canon_journal,
            doi=canon_doi,
            source="crossref",
            note=f"title mismatch ({title_score:.2f})",
        )
    elif not year_ok:
        return VerificationResult(
            verified=False,
            confidence=0.45,
            canonical_title=canon_title,
            canonical_authors=authors,
            canonical_year=canon_year,
            canonical_journal=canon_journal,
            doi=canon_doi,
            source="crossref",
            note=f"year mismatch: citation {cited_year}, source {canon_year}",
        )
    elif detail_status is False:
        return VerificationResult(
            verified=False,
            confidence=0.45,
            canonical_title=canon_title,
            canonical_authors=authors,
            canonical_year=canon_year,
            canonical_journal=canon_journal,
            doi=canon_doi,
            source="crossref",
            note=detail_note,
        )
    elif detail_status is None:
        return VerificationResult(
            verified=None,
            confidence=0.82 if author_ok else 0.72,
            canonical_title=canon_title,
            canonical_authors=authors,
            canonical_year=canon_year,
            canonical_journal=canon_journal,
            doi=canon_doi,
            source="crossref",
            note=f"title/year matched; {detail_note}",
        )
    elif not author_ok:
        return VerificationResult(
            verified=None,
            confidence=0.82,
            canonical_title=canon_title,
            canonical_authors=authors,
            canonical_year=canon_year,
            canonical_journal=canon_journal,
            doi=canon_doi,
            source="crossref",
            note="title/year/volume/page matched; author not confirmed",
        )
    else:
        confidence = 0.92
        note = "title/year/volume/page matched"

    return VerificationResult(
        verified=True,
        confidence=confidence,
        canonical_title=canon_title,
        canonical_authors=authors,
        canonical_year=canon_year,
        canonical_journal=canon_journal,
        doi=canon_doi,
        source="crossref",
        note=note,
    )


# ── OpenLibrary ────────────────────────────────────────────────────────────────

def _openlibrary_verify(text: str) -> VerificationResult:
    global _LAST_OPENLIBRARY

    isbn_m = re.search(r'ISBN[:\s]*([0-9Xx-]{10,17})', text, re.I)
    isbn = re.sub(r'[-\s]', '', isbn_m.group(1)) if isbn_m else None

    title_m = re.search(r',\s*([A-Z][^,]{5,70})(?:\s+\(\d{4}\)|\s+\d{4})', text)
    title = title_m.group(1).strip() if title_m else None

    if not isbn and not title:
        return VerificationResult(source="openlibrary", note="no ISBN or title")

    now = time.time()
    gap = _DELAY - (now - _LAST_OPENLIBRARY)
    if gap > 0:
        time.sleep(gap)
    _LAST_OPENLIBRARY = time.time()

    try:
        if isbn:
            url = f"https://openlibrary.org/isbn/{isbn}.json"
        else:
            q = urllib.parse.urlencode({"q": title, "limit": 1})
            url = f"https://openlibrary.org/search.json?{q}"

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        if isbn:
            canon_title = data.get("title", "")
            canon_isbn  = isbn
            authors = []
            canon_year = _year_from_openlibrary_record(data)
        else:
            docs = data.get("docs", [])
            if not docs:
                return VerificationResult(verified=False, source="openlibrary",
                                           note="no results")
            doc = docs[0]
            canon_title = doc.get("title", "")
            canon_isbn  = (doc.get("isbn") or [None])[0]
            authors = doc.get("author_name") or []
            canon_year = doc.get("first_publish_year")

        cited_title = title or _extract_title(text)
        title_score = _title_similarity(cited_title, canon_title) if cited_title and canon_title else None
        if title_score is not None and title_score < 0.82:
            return VerificationResult(
                verified=False,
                confidence=0.2,
                canonical_title=canon_title,
                canonical_authors=authors[:3],
                canonical_year=canon_year,
                isbn=canon_isbn,
                source="openlibrary",
                note=f"title mismatch ({title_score:.2f})",
            )

        cited_year = _extract_year(text)
        if cited_year is not None and canon_year is not None and cited_year != canon_year:
            return VerificationResult(
                verified=False,
                confidence=0.45,
                canonical_title=canon_title,
                canonical_authors=authors[:3],
                canonical_year=canon_year,
                isbn=canon_isbn,
                source="openlibrary",
                note=f"year mismatch: citation {cited_year}, source {canon_year}",
            )

        author_ok = _author_match(text, authors)
        if not isbn and (not author_ok or canon_year is None):
            missing = []
            if not author_ok:
                missing.append("author not confirmed")
            if canon_year is None:
                missing.append("source year not available")
            return VerificationResult(
                verified=None,
                confidence=0.78 if not author_ok else 0.82,
                canonical_title=canon_title,
                canonical_authors=authors[:3],
                canonical_year=canon_year,
                isbn=canon_isbn,
                source="openlibrary",
                note="title matched; " + "; ".join(missing),
            )
        confidence = 0.88

        return VerificationResult(
            verified=True, confidence=confidence,
            canonical_title=canon_title,
            canonical_authors=authors[:3],
            canonical_year=canon_year,
            isbn=canon_isbn,
            source="openlibrary",
            note="metadata matched",
        )
    except Exception as e:
        return VerificationResult(source="openlibrary", note=f"error: {e}")


# ── Metadata comparison helpers ────────────────────────────────────────────────

_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "with",
}


def _normalise_words(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return [w for w in words if w not in _STOPWORDS]


def _title_similarity(left: str, right: str) -> float:
    left_norm = " ".join(_normalise_words(left))
    right_norm = " ".join(_normalise_words(right))
    if not left_norm or not right_norm:
        return 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_set = set(left_norm.split())
    right_set = set(right_norm.split())
    jaccard = len(left_set & right_set) / max(1, len(left_set | right_set))
    return max(sequence, jaccard)


def _extract_title(text: str) -> str | None:
    patterns = [
        r',\s*([A-Z][^,]{5,120}),\s*\d+\s+[A-Z]',
        r',\s*([A-Z][^,]{5,120})\s+\(\d{4}\)',
        r'\*\s*([^*]{5,120})\s*\*',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1).strip()
    return None


def _extract_year(text: str) -> int | None:
    years = re.findall(r'\((\d{4})\)|\b(19\d{2}|20\d{2})\b', text)
    flattened = [part for match in years for part in match if part]
    if not flattened:
        return None
    try:
        return int(flattened[-1])
    except ValueError:
        return None


def _year_from_openlibrary_record(data: dict) -> int | None:
    raw_values = [
        data.get("publish_date"),
        data.get("first_publish_date"),
        data.get("created", {}).get("value") if isinstance(data.get("created"), dict) else None,
    ]
    for raw in raw_values:
        if not raw:
            continue
        m = re.search(r'\b(1[6-9]\d{2}|20\d{2})\b', str(raw))
        if m:
            return int(m.group(1))
    return None


def _crossref_detail_status(text: str, item: dict) -> tuple[bool | None, str]:
    cited = _extract_journal_details(text)
    if cited is None:
        return None, "citation volume/page details not extractable"

    cited_volume, cited_first_page = cited
    source_volume = str(item.get("volume") or "").strip()
    source_first_page = _first_page(item.get("page"))

    if cited_volume and source_volume and cited_volume != source_volume:
        return False, f"volume mismatch: citation {cited_volume}, source {source_volume}"
    if cited_first_page and source_first_page and cited_first_page != source_first_page:
        return False, f"first-page mismatch: citation {cited_first_page}, source {source_first_page}"
    if not source_volume or not source_first_page:
        missing = []
        if not source_volume:
            missing.append("volume")
        if not source_first_page:
            missing.append("first page")
        return None, "source did not provide " + " and ".join(missing)
    return True, "volume and first page matched"


def _extract_journal_details(text: str) -> tuple[str | None, str | None] | None:
    m = re.search(
        r",\s*(?P<volume>\d+)\s+[A-Z][A-Za-z0-9\s\.'&-]+?\s+(?P<first_page>\d+)"
        r'(?:,\s*(?:at\s+)?\d+(?:[–-]\d+)?)?\s*\(\d{4}\)',
        text,
    )
    if not m:
        return None
    return m.group("volume"), m.group("first_page")


def _first_page(value) -> str | None:
    if not value:
        return None
    m = re.search(r'\d+', str(value))
    return m.group(0) if m else None


def _author_match(text: str, authors: list[str]) -> bool:
    if not authors:
        return False
    lowered = text.lower()
    for author in authors[:3]:
        pieces = _normalise_words(author)
        if not pieces:
            continue
        family = pieces[-1]
        if len(family) > 2 and re.search(rf'\b{re.escape(family)}\b', lowered):
            return True
    return False


# ── Heuristic fallback ─────────────────────────────────────────────────────────

_JOURNAL_STRUCT = re.compile(
    r'\d+\s+[A-Z][A-Za-z\s\.]+\d+\s*(?:\(\d{4}\))?'
)
_CASE_STRUCT = re.compile(
    r'.+\s+v\.?\s+.+,\s+\d+\s+\S+\s+\d+'
)
_TREATY_STRUCT = re.compile(
    r'(?:Treaty|Convention|Protocol|Agreement|Charter).+,\s+\w+\.\s+\d+,\s+\d{4}'
    r'|opened\s+for\s+signature'
    r'|\d+\s+U\.N\.T\.S\.'
    r'|\d+\s+I\.L\.M\.'
)
_BOOK_STRUCT = re.compile(
    r'[A-Z][a-z]+.+\(\d{4}\)'
    r'|\(\d+\w*\s+ed\.\s+\d{4}\)'
)


def _heuristic_verify(text: str, src_type: SourceType) -> VerificationResult:
    t = text.strip()

    checks = {
        SourceType.JOURNAL_ARTICLE: (_JOURNAL_STRUCT, 0.6),
        SourceType.CASE_DOMESTIC:   (_CASE_STRUCT,    0.7),
        SourceType.CASE_ICJ:        (_CASE_STRUCT,    0.65),
        SourceType.TREATY:          (_TREATY_STRUCT,  0.7),
        SourceType.BOOK:            (_BOOK_STRUCT,    0.55),
        SourceType.BOOK_CHAPTER:    (_BOOK_STRUCT,    0.5),
    }

    pattern, conf = checks.get(src_type, (None, 0.4))
    if pattern and pattern.search(t):
        return VerificationResult(verified=None, confidence=conf,
                                   source="heuristic",
                                   note="structurally plausible; not source-verified")
    return VerificationResult(verified=None, confidence=0.3,
                               source="heuristic",
                               note="insufficient structure to verify")
