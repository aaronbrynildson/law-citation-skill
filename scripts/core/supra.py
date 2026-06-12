"""
supra.py — Detect duplicate citations and propose supra/infra conversions.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from .classifier import SourceType, source_key, classify
from .extractor import Footnote

_BIO_NOTE_RE = re.compile(
    r"\bis\s+(?:a|an)\s+"
    r"(?:professor|researcher|assistant|lecturer|postdoctoral|student|fellow|scholar|"
    r"associate|partner|counsel|attorney|editor|candidate)\b"
    r"|\bthanks?\b.*\b(?:comments|assistance|research|support)\b"
    r"|\b(?:author|authors)\s+(?:thanks?|acknowledges?)\b",
    re.I,
)


@dataclass
class SupraProposal:
    fn_id: int
    original: str
    replacement: str
    first_fn_id: int
    src_type: SourceType
    confidence: float = 0.8


@dataclass
class SupraValidityIssue:
    fn_id: int
    original: str
    corrected: str
    rule_name: str
    explanation: str
    severity: str = "error"


@dataclass
class _SupraCandidate:
    note: int
    source_key: str


def detect_bio_note_count(footnotes: list[Footnote]) -> int:
    """Count leading author bio/acknowledgment notes that do not consume paper note numbers."""
    count = 0
    for fn in footnotes:
        text = fn.full_text.strip()
        if text and _BIO_NOTE_RE.search(text):
            count += 1
            continue
        break
    return count


def paper_footnote_number(fn: Footnote | int, bio_note_count: int) -> int:
    """Map Word footnote ids to paper footnote numbers after leading symbol notes."""
    display_number = fn.display_number if isinstance(fn, Footnote) else fn
    return display_number - bio_note_count if display_number > bio_note_count else display_number


def analyse_supras(footnotes: list[Footnote], bio_note_count: int | None = None) -> list[SupraProposal]:
    """
    Walk footnotes in order. Build a source_key → first_fn_id map.
    For each subsequent citation with the same key, propose a supra form.
    """
    if bio_note_count is None:
        bio_note_count = detect_bio_note_count(footnotes)

    first_occurrence: dict[str, tuple[int, SourceType]] = {}
    proposals: list[SupraProposal] = []

    for fn in footnotes:
        text = fn.full_text
        current_paper_fn = paper_footnote_number(fn, bio_note_count)
        citations = split_citations(text)

        for cit in citations:
            cit = cit.strip()
            if len(cit) < 10:
                continue
            src_type = classify(cit)
            if src_type in (
                SourceType.SUPRA,
                SourceType.INFRA,
                SourceType.UNKNOWN,
                SourceType.CASE_DOMESTIC,
                SourceType.CASE_ICJ,
                SourceType.CASE_INTL_TRIBUNAL,
                SourceType.STATUTE_US,
                SourceType.STATUTE_FOREIGN,
                SourceType.CONSTITUTION,
                SourceType.TREATY,
            ):
                continue

            key = source_key(cit, src_type)
            if not key or len(key) < 8:
                continue

            if key not in first_occurrence:
                first_occurrence[key] = (current_paper_fn, src_type)
            else:
                first_fn_id, first_type = first_occurrence[key]
                if first_fn_id == current_paper_fn:
                    continue  # same footnote, not a duplicate

                author = _extract_author(cit, src_type)
                if not author:
                    continue
                pinpoint = _pinpoint_from(cit)
                replacement = _make_supra(author, first_fn_id, pinpoint, src_type)

                proposals.append(SupraProposal(
                    fn_id=fn.fn_id,
                    original=cit,
                    replacement=replacement,
                    first_fn_id=first_fn_id,
                    src_type=src_type,
                    confidence=0.9,
                ))

    return proposals


def validate_existing_supras(
    footnotes: list[Footnote],
    bio_note_count: int | None = None,
) -> list[SupraValidityIssue]:
    """Validate existing "Author, supra note N" references against earlier full citations."""
    if bio_note_count is None:
        bio_note_count = detect_bio_note_count(footnotes)

    first_by_label: dict[str, list[_SupraCandidate]] = {}
    issues: list[SupraValidityIssue] = []

    for fn in footnotes:
        current_paper_fn = paper_footnote_number(fn, bio_note_count)
        for cit in split_citations(fn.full_text):
            src_type = classify(cit)
            if src_type == SourceType.SUPRA:
                issues.extend(_validate_supra_citation(cit, fn.fn_id, current_paper_fn, first_by_label))
                continue
            if src_type in (
                SourceType.ID,
                SourceType.INFRA,
                SourceType.UNKNOWN,
                SourceType.CASE_DOMESTIC,
                SourceType.CASE_ICJ,
                SourceType.CASE_INTL_TRIBUNAL,
                SourceType.STATUTE_US,
                SourceType.STATUTE_FOREIGN,
                SourceType.CONSTITUTION,
                SourceType.TREATY,
            ):
                continue
            key = source_key(cit, src_type)
            if not key:
                continue
            for label in _label_keys_for_full_citation(cit, src_type):
                if not label:
                    continue
                candidates = first_by_label.setdefault(label, [])
                if not any(candidate.source_key == key for candidate in candidates):
                    candidates.append(_SupraCandidate(note=current_paper_fn, source_key=key))

    return issues


def split_citations(text: str) -> list[str]:
    return _split_citations(text)


def _split_citations(text: str) -> list[str]:
    """Split a footnote into individual citations without splitting reporters.

    Semicolons are reliable citation separators. Periods are only treated as
    separators when the next token clearly begins a new citation, such as Id.,
    a signal, or a new case name.
    """
    citations: list[str] = []
    for semicolon_part in re.split(r';\s+', text):
        citations.extend(_CITATION_SENTENCE_BOUNDARY_RE.split(semicolon_part))
    return [p.strip() for p in citations if p.strip()]


_CITATION_SENTENCE_BOUNDARY_RE = re.compile(
    r'(?<=\.)\s+'
    r'(?=(?:'
    r'Id\.?\b'
    r'|(?:See\s+also|See\s+generally|See|Cf\.|But\s+see|Accord|Compare|Contra|E\.g\.,?)\s+'
    r'|[A-Z][A-Za-z0-9\'’.-]*(?:\s+[A-Z][A-Za-z0-9\'’.-]*){0,8}\s+v\.?\s+'
    r'))',
    re.I,
)


_SUPRA_CITE_RE = re.compile(
    r'(?P<label>[A-Z][A-Za-z0-9\'’.\s-]{1,80}?),\s+supra\s+note\s+(?P<note>\d+)',
    re.I,
)


def _validate_supra_citation(
    citation: str,
    fn_id: int,
    current_paper_fn: int,
    first_by_label: dict[str, list[_SupraCandidate]],
) -> list[SupraValidityIssue]:
    issues: list[SupraValidityIssue] = []
    for match in _SUPRA_CITE_RE.finditer(citation):
        original = match.group(0)
        label = _normalise_label(match.group("label"))
        cited_note = int(match.group("note"))
        candidates = first_by_label.get(label, [])
        candidate_notes = sorted({candidate.note for candidate in candidates})
        correct_note = candidate_notes[0] if len(candidate_notes) == 1 else None
        cited_note_matches_candidate = cited_note in candidate_notes

        if cited_note == current_paper_fn:
            issues.append(SupraValidityIssue(
                fn_id=fn_id,
                original=original,
                corrected=_replace_supra_note(original, correct_note) if correct_note else original,
                rule_name="Supra self-reference",
                explanation="R4 — supra references must point to an earlier full citation, not the same footnote.",
            ))
        elif cited_note > current_paper_fn:
            issues.append(SupraValidityIssue(
                fn_id=fn_id,
                original=original,
                corrected=_replace_supra_note(original, correct_note) if correct_note else original,
                rule_name="Supra forward reference",
                explanation="R4 — supra references must point backward to an earlier full citation.",
            ))

        if not candidates:
            issues.append(SupraValidityIssue(
                fn_id=fn_id,
                original=original,
                corrected=original,
                rule_name="Supra full citation not found",
                explanation="R4 — no earlier full citation matching this supra label was found.",
                severity="warning",
            ))
        elif len(candidate_notes) > 1:
            if not cited_note_matches_candidate:
                issues.append(SupraValidityIssue(
                    fn_id=fn_id,
                    original=original,
                    corrected=original,
                    rule_name="Supra ambiguous label",
                    explanation=(
                        "R4 — multiple earlier full citations share this supra label "
                        f"(candidate paper footnotes: {', '.join(str(note) for note in candidate_notes)}); "
                        "review manually."
                    ),
                    severity="warning",
                ))
            else:
                issues.append(SupraValidityIssue(
                    fn_id=fn_id,
                    original=original,
                    corrected=original,
                    rule_name="Supra ambiguous label",
                    explanation=(
                        "R4 — this supra label matches multiple earlier full citations; "
                        "the cited note is one candidate, but the source identity still needs manual review."
                    ),
                    severity="info",
                ))
        elif cited_note != correct_note:
            issues.append(SupraValidityIssue(
                fn_id=fn_id,
                original=original,
                corrected=_replace_supra_note(original, correct_note),
                rule_name="Supra wrong note number",
                explanation=f"R4 — this supra should point to paper footnote {correct_note}, where the first matching full citation appears.",
            ))
    return issues


def _replace_supra_note(original: str, note: int | None) -> str:
    if note is None:
        return original
    return re.sub(r'\bsupra\s+note\s+\d+\b', f"supra note {note}", original, flags=re.I)


def _normalise_label(label: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', label.lower())


def _label_keys_for_full_citation(text: str, src_type: SourceType) -> set[str]:
    if src_type == SourceType.TREATY:
        return set()
    author_segment = re.sub(
        r'^(See\s+also|See\s+generally|See|Cf\.|But\s+see|Accord|Compare|Contra|E\.g\.,?)\s+',
        '',
        text.strip(),
        flags=re.I,
    ).split(",", 1)[0].strip()
    short = _short_author_label(author_segment)
    keys = {_normalise_label(short)} if short else set()
    parts = author_segment.split()
    if parts:
        keys.add(_normalise_label(parts[-1]))
    return {key for key in keys if key}


def _short_author_label(author_segment: str) -> str:
    cleaned = re.sub(r'\s+', ' ', author_segment).strip()
    if not cleaned:
        return ""
    first_author = re.split(r'\s+(?:&|and)\s+', cleaned, maxsplit=1)[0].strip()
    parts = first_author.split()
    if not parts:
        return ""
    particles = {"da", "de", "del", "der", "di", "du", "la", "le", "van", "von"}
    start = len(parts) - 1
    while start > 0 and parts[start - 1].lower().strip(".") in particles:
        start -= 1
    return " ".join(parts[start:])


def _extract_author(text: str, src_type: SourceType) -> str:
    """Extract short author or party name for supra form."""
    # Strip leading signal
    t = re.sub(r'^(See\s+also|See\s+generally|See|Cf\.|But\s+see|Accord|Compare|Contra|E\.g\.,?)\s+', '', text, flags=re.I)

    if src_type in (SourceType.CASE_DOMESTIC, SourceType.CASE_ICJ, SourceType.CASE_INTL_TRIBUNAL):
        m = re.match(r'(.+?)\s+v\.?\s+', t, re.I)
        if m:
            name = m.group(1).strip()
            # Shorten to last word of first party
            parts = name.split()
            return parts[-1] if parts else name
        return t[:20]

    if src_type == SourceType.TREATY:
        return ""

    author_segment = t.split(",", 1)[0].strip()
    if author_segment:
        return _short_author_label(author_segment)

    return t[:20].strip(',. ')


def _pinpoint_from(text: str) -> str:
    """Extract pinpoint reference from a citation string."""
    m = re.search(r',\s*at\s+(\d+(?:[–-]\d+)?)', text)
    if m:
        return f", at {m.group(1)}"
    m = re.search(r',\s*(\d+(?:[–-]\d+)?)(?=\s*\(\d{4}\))', text)
    if m:
        return f", at {m.group(1)}"
    m = re.search(r',\s*¶\s*(\d+)', text)
    if m:
        return f", ¶ {m.group(1)}"
    m = re.search(r',\s*art(?:icle|\.)\s*(\w+)', text, re.I)
    if m:
        return f", art. {m.group(1)}"
    return ""


def _make_supra(author: str, first_fn_id: int, pinpoint: str, src_type: SourceType) -> str:
    """Build the supra citation string."""
    return f"{author}, supra note {first_fn_id}{pinpoint}."
