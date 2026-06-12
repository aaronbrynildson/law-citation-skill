#!/usr/bin/env python3
"""
Citation-Checker Pipeline — CLI entry point.
Runs extraction → classification → verification → Bluebook checking →
supra detection → OOXML tracked-change patching.

Usage:
  python3 run_pipeline.py --input doc.docx --output out.docx [--network] [--supra] [--max-fn N]
"""
from __future__ import annotations

import argparse, json, sys, time
from pathlib import Path

try:
    from .core.extractor  import extract_footnotes
    from .core.classifier import SourceType, classify
    from .core.verifier   import verify
    from .core.bluebook   import CitationIssue, check_all
    from .core.supra      import analyse_supras, detect_bio_note_count, paper_footnote_number, split_citations, validate_existing_supras
    from .core.patcher    import apply_changes, TextChange, FormatChange
    from .core.ooxml      import CitationCheckerError
except ImportError:  # Allow direct execution as scripts/run_pipeline.py
    sys.path.insert(0, str(Path(__file__).parent))
    from core.extractor  import extract_footnotes
    from core.classifier import SourceType, classify
    from core.verifier   import verify
    from core.bluebook   import CitationIssue, check_all
    from core.supra      import analyse_supras, detect_bio_note_count, paper_footnote_number, split_citations, validate_existing_supras
    from core.patcher    import apply_changes, TextChange, FormatChange
    from core.ooxml      import CitationCheckerError


DEFAULT_MIN_CONFIDENCE = 0.85
SHORT_FORM_TYPES = {SourceType.ID, SourceType.SUPRA, SourceType.INFRA, SourceType.UNKNOWN}
FORMAT_REVIEW_REASON = (
    "formatting changes are report-only; apply manually in Word to avoid over-broad OOXML run formatting"
)
APPLY_APPROVAL_REASON = "approval required; rerun with --apply-verified after reviewing this correction"
AUTO_APPLY_TEXT_RULES = {
    ("R4", "Id. missing period"),
}


def _manual_review_entry(fn_id: int, issue: dict, reason: str) -> dict:
    return {
        "fn_id": fn_id,
        "rule_id": issue["rule_id"],
        "rule_name": issue["rule_name"],
        "original": issue["original"],
        "corrected": issue["corrected"],
        "format_change": issue["format_change"],
        "reason": reason,
    }


def _issue_dict(issue: CitationIssue) -> dict:
    return {
        "rule_id": issue.rule_id,
        "rule_name": issue.rule_name,
        "original": issue.original,
        "corrected": issue.corrected,
        "explanation": issue.explanation,
        "severity": issue.severity,
        "format_change": issue.format_change,
        "auto_applied": False,
        "manual_review_reason": None,
    }


def _supra_issue_dict(issue) -> dict:
    return {
        "rule_id": "R4",
        "rule_name": issue.rule_name,
        "original": issue.original,
        "corrected": issue.corrected,
        "explanation": issue.explanation,
        "severity": issue.severity,
        "format_change": None,
        "auto_applied": False,
        "manual_review_reason": "supra validity corrections require manual review",
    }


def _id_context_issue(fn_id: int, previous_full_fn_id: int | None) -> CitationIssue:
    if previous_full_fn_id is None:
        return CitationIssue(
            rule_id="R4",
            rule_name="Id. has no preceding full authority",
            original="Id.",
            corrected="Id.",
            explanation="R4 — this Id. could not be tied to an immediately preceding full authority; review manually.",
            severity="error",
        )
    return CitationIssue(
        rule_id="R4",
        rule_name="Id. context candidate",
        original="Id.",
        corrected="Id.",
        explanation=f"R4 — Id. appears to refer to the immediately preceding full authority in footnote {previous_full_fn_id}; verify same authority and pinpoint before approval.",
        severity="info",
    )


def _parse_format_change(value: str) -> tuple[str, str, str] | None:
    parts = value.split(":", 2)
    if len(parts) != 3:
        return None
    action, prop, target = (part.strip() for part in parts)
    if not action or not prop or not target:
        return None
    return action, prop, target


def _change_review_reason(report: dict, issue: dict) -> str | None:
    original = issue.get("original") or ""
    if not original:
        return "empty original text for proposed change"
    occurrences = report["text"].count(original)
    if occurrences != 1:
        return f"ambiguous target occurrence count: {occurrences}"
    if issue.get("manual_review_reason"):
        return issue["manual_review_reason"]
    return None


def _auto_apply_rule_reason(issue: dict) -> str | None:
    rule_key = (issue.get("rule_id"), issue.get("rule_name"))
    if rule_key not in AUTO_APPLY_TEXT_RULES:
        return "rule is not in the safe auto-apply allowlist"
    return None


def _text_change_review_reason(
    report: dict,
    issue: dict,
    *,
    apply_verified: bool,
) -> str | None:
    if not apply_verified:
        return APPLY_APPROVAL_REASON

    rule_reason = _auto_apply_rule_reason(issue)
    if rule_reason:
        return rule_reason

    change_reason = _change_review_reason(report, issue)
    if change_reason:
        return change_reason

    return None


def _footnote_format_spans(fn) -> tuple[list[dict], str]:
    spans: list[dict] = []
    pieces: list[str] = []
    pos = 0
    for para_index, para in enumerate(fn.paras):
        if para_index:
            pieces.append(" ")
            pos += 1
        for run in para.runs:
            if not run.text:
                continue
            start = pos
            end = start + len(run.text)
            spans.append({
                "start": start,
                "end": end,
                "italic": run.italic,
                "small_caps": run.small_caps,
                "bold": run.bold,
                "underline": run.underline,
            })
            pieces.append(run.text)
            pos = end
    return spans, "".join(pieces)


def _filter_satisfied_format_issues(issues: list[CitationIssue], fn) -> list[CitationIssue]:
    spans, text = _footnote_format_spans(fn)
    filtered: list[CitationIssue] = []
    for issue in issues:
        parsed = _parse_format_change(issue.format_change or "")
        if parsed is None:
            filtered.append(issue)
            continue
        action, prop, target = parsed
        satisfied = _target_format_satisfied(text, spans, target, prop, add=(action == "add"))
        if satisfied is True:
            continue
        filtered.append(issue)
    return filtered


def _target_format_satisfied(text: str, spans: list[dict], target: str, prop: str, *, add: bool) -> bool | None:
    start = text.find(target)
    if not target or start < 0:
        return None
    if text.find(target, start + 1) >= 0:
        return None
    end = start + len(target)
    affected = [span for span in spans if span["end"] > start and span["start"] < end]
    if not affected:
        return None
    values = [bool(span.get(prop)) for span in affected]
    return all(values) if add else not any(values)


def _citation_reports_for_footnote(
    fn,
    text: str,
    current_fn_id: int,
    previous_full_fn_id: int | None,
    use_network: bool,
):
    citation_reports = []
    issues: list[dict] = []
    has_full_authority = False
    id_context_fn_id = previous_full_fn_id
    for citation in split_citations(text):
        src_type = classify(citation)
        vr = verify(citation, src_type, use_network=use_network)
        citation_issues = _filter_satisfied_format_issues(check_all(citation, src_type), fn)
        if src_type == SourceType.ID:
            citation_issues.append(_id_context_issue(current_fn_id, id_context_fn_id))
        issue_dicts = [_issue_dict(issue) for issue in citation_issues]
        issues.extend(issue_dicts)
        citation_reports.append({
            "text": citation,
            "src_type": src_type.value,
            "verified": vr.verified,
            "verify_confidence": round(vr.confidence, 3),
            "canonical_title": vr.canonical_title,
            "issues": issue_dicts,
        })
        if src_type not in SHORT_FORM_TYPES:
            has_full_authority = True
            id_context_fn_id = current_fn_id
    return citation_reports, issues, has_full_authority


def run(
    src: str,
    dst: str,
    use_network: bool,
    analyse_supra: bool,
    max_fn: int | None,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    apply_verified: bool = False,
    apply_supra_changes: bool = False,
    bio_note_count: int | None = None,
    confirm_bio_notes: int | None = None,
    force_output: bool = False,
):
    t0 = time.time()
    footnotes = extract_footnotes(src)
    if max_fn:
        footnotes = footnotes[:max_fn]
    detected_bio_note_count = detect_bio_note_count(footnotes)
    effective_bio_note_count = detected_bio_note_count if bio_note_count is None else bio_note_count
    supra_apply_allowed = apply_supra_changes and confirm_bio_notes == effective_bio_note_count

    reports = []
    previous_full_fn_id: int | None = None
    for fn in footnotes:
        text = fn.full_text
        current_paper_fn = paper_footnote_number(fn, effective_bio_note_count)
        citation_reports, issues, has_full_authority = _citation_reports_for_footnote(
            fn,
            text,
            current_paper_fn,
            previous_full_fn_id,
            use_network,
        )
        first_citation = citation_reports[0] if citation_reports else {
            "src_type": SourceType.UNKNOWN.value,
            "verified": None,
            "verify_confidence": 0.0,
            "canonical_title": None,
        }
        verified_values = [c["verified"] for c in citation_reports]
        if any(v is False for v in verified_values):
            verified = False
        elif verified_values and all(v is True for v in verified_values):
            verified = True
        else:
            verified = None
        confidence_values = [c["verify_confidence"] for c in citation_reports]
        verify_confidence = min(confidence_values) if confidence_values else 0.0
        reports.append({
            "fn_id": fn.fn_id,
            "display_number": fn.display_number,
            "paper_fn": current_paper_fn,
            "text": text,
            "src_type": first_citation["src_type"],
            "verified": verified,
            "verify_confidence": round(verify_confidence, 3),
            "canonical_title": first_citation["canonical_title"],
            "issues": issues,
            "citations": citation_reports,
            "supra_proposals": [],
        })
        if has_full_authority:
            previous_full_fn_id = current_paper_fn

    for issue in validate_existing_supras(footnotes, bio_note_count=effective_bio_note_count):
        for report in reports:
            if report["fn_id"] == issue.fn_id:
                report["issues"].append(_supra_issue_dict(issue))
                break

    # Supra detection
    supra_props = (
        analyse_supras(footnotes, bio_note_count=effective_bio_note_count)
        if (analyse_supra or apply_supra_changes)
        else []
    )
    if supra_props:
        fn_map = {r["fn_id"]: r for r in reports}
        for sp in supra_props:
            if sp.fn_id in fn_map:
                fn_map[sp.fn_id]["supra_proposals"].append({
                    "original": sp.original,
                    "replacement": sp.replacement,
                    "first_fn_id": sp.first_fn_id,
                    "confidence": round(sp.confidence, 3),
                    "auto_applied": False,
                })

    # Build changes
    text_changes: list[TextChange] = []
    fmt_changes: list[FormatChange] = []
    pending_text_targets: list[dict] = []
    manual_review: list[dict] = []

    for r in reports:
        fn_id = r["fn_id"]
        for issue in r["issues"]:
            has_text_change = (
                issue["original"]
                and issue["corrected"]
                and issue["original"] != issue["corrected"]
            )
            has_format_change = bool(issue["format_change"])
            if not has_text_change and not has_format_change:
                continue

            if has_format_change:
                issue["manual_review_reason"] = FORMAT_REVIEW_REASON
                manual_review.append(_manual_review_entry(fn_id, issue, FORMAT_REVIEW_REASON))
                if not has_text_change:
                    continue

            if has_text_change:
                review_reason = _text_change_review_reason(
                    r,
                    issue,
                    apply_verified=apply_verified,
                )
                if review_reason:
                    issue["manual_review_reason"] = review_reason
                    manual_review.append(_manual_review_entry(fn_id, issue, review_reason))
                    continue

            if has_text_change:
                text_changes.append(TextChange(
                    fn_id=fn_id,
                    old_text=issue["original"],
                    new_text=issue["corrected"],
                    label=f"{issue['rule_id']}: {issue['rule_name']}",
                ))
                pending_text_targets.append({
                    "kind": "issue",
                    "fn_id": fn_id,
                    "issue": issue,
                })

    supra_apply_blocked = False
    if apply_supra_changes and not supra_apply_allowed:
        supra_apply_blocked = True
    elif supra_apply_allowed:
        fn_map = {r["fn_id"]: r for r in reports}
        for sp in supra_props:
            report = fn_map.get(sp.fn_id)
            if report is None:
                continue
            proposal = next(
                (
                    item for item in report["supra_proposals"]
                    if item["original"] == sp.original and item["replacement"] == sp.replacement
                ),
                None,
            )
            if sp.confidence < min_confidence:
                manual_review.append({
                    "fn_id": sp.fn_id,
                    "rule_id": "R4",
                    "rule_name": "Supra conversion below confidence threshold",
                    "original": sp.original,
                    "corrected": sp.replacement,
                    "format_change": None,
                    "reason": f"supra proposal confidence {sp.confidence:.3f} is below {min_confidence:.2f}",
                })
                continue
            occurrences = report["text"].count(sp.original)
            if occurrences != 1:
                manual_review.append({
                    "fn_id": sp.fn_id,
                    "rule_id": "R4",
                    "rule_name": "Supra conversion ambiguous target",
                    "original": sp.original,
                    "corrected": sp.replacement,
                    "format_change": None,
                    "reason": f"ambiguous target occurrence count: {occurrences}",
                })
                continue
            text_changes.append(TextChange(
                fn_id=sp.fn_id,
                old_text=sp.original,
                new_text=sp.replacement,
                label=f"R4: Supra conversion (first at FN {sp.first_fn_id})",
            ))
            pending_text_targets.append({
                "kind": "supra",
                "fn_id": sp.fn_id,
                "proposal": proposal,
                "original": sp.original,
                "corrected": sp.replacement,
                "first_fn_id": sp.first_fn_id,
            })

    patch_summary = apply_changes(src, dst, text_changes, fmt_changes, overwrite=force_output)
    for change_result, target in zip(patch_summary.get("text_change_results", []), pending_text_targets):
        if change_result.get("applied"):
            if target["kind"] == "issue":
                target["issue"]["auto_applied"] = True
            elif target.get("proposal") is not None:
                target["proposal"]["auto_applied"] = True
            continue

        reason = "tracked change was not applied: " + (change_result.get("reason") or "unknown patcher failure")
        if target["kind"] == "issue":
            issue = target["issue"]
            issue["manual_review_reason"] = reason
            manual_review.append(_manual_review_entry(target["fn_id"], issue, reason))
        else:
            manual_review.append({
                "fn_id": target["fn_id"],
                "rule_id": "R4",
                "rule_name": "Supra conversion not applied",
                "original": target["original"],
                "corrected": target["corrected"],
                "format_change": None,
                "reason": reason,
            })

    elapsed = round(time.time() - t0, 2)
    total_errors   = sum(i["severity"] == "error"   for r in reports for i in r["issues"])
    total_warnings = sum(i["severity"] == "warning" for r in reports for i in r["issues"])

    result = {
        "footnote_count": len(footnotes),
        "elapsed_seconds": elapsed,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "supra_proposals": len(supra_props),
        "manual_review_count": len(manual_review),
        "manual_review": manual_review,
        "bio_note_count": effective_bio_note_count,
        "bio_note_count_detected": detected_bio_note_count,
        "auto_apply_policy": {
            "min_confidence": min_confidence,
            "apply_verified": apply_verified,
            "apply_supra_changes": apply_supra_changes,
            "apply_format_changes": False,
            "confirm_bio_notes": confirm_bio_notes,
            "force_output": force_output,
            "supra_apply_blocked": supra_apply_blocked,
        },
        "reports": reports,
        "patch_summary": patch_summary,
    }
    print(json.dumps(result, indent=2))
    return result


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description="Bluebook citation checker")
    ap.add_argument("--input",   required=True, help="Source .docx path")
    ap.add_argument("--output",  required=True, help="Output .docx path (with tracked changes)")
    ap.add_argument("--network", action="store_true", help="Enable live Crossref/OpenLibrary verification")
    ap.add_argument("--supra",   action="store_true", help="Detect duplicate citations and propose supra conversions")
    ap.add_argument("--apply-supra", action="store_true", help="Apply proposed supra conversions as tracked changes")
    ap.add_argument("--force", action="store_true", help="Overwrite --output if it already exists")
    ap.add_argument(
        "--apply-verified",
        action="store_true",
        help="Apply reviewed mechanical allowlisted corrections as tracked changes",
    )
    ap.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help="Minimum proposal confidence required before applying supra conversions",
    )
    ap.add_argument("--max-fn",  type=int, default=None, help="Limit to first N footnotes (testing)")
    ap.add_argument(
        "--bio-notes",
        type=int,
        default=None,
        help="Override detected leading author bio/acknowledgment footnote count",
    )
    ap.add_argument(
        "--confirm-bio-notes",
        type=int,
        default=None,
        help="Required with --apply-supra; must equal the effective bio-note count",
    )
    args = ap.parse_args(argv)
    if args.apply_supra and args.confirm_bio_notes is None:
        ap.error("--apply-supra requires --confirm-bio-notes N after manual offset review")
    try:
        if args.apply_supra:
            detected_for_cli = detect_bio_note_count(extract_footnotes(args.input))
            effective_for_cli = detected_for_cli if args.bio_notes is None else args.bio_notes
            if args.confirm_bio_notes != effective_for_cli:
                raise ValueError(
                    "--apply-supra confirmation mismatch: "
                    f"confirmed {args.confirm_bio_notes}, effective bio-note count is {effective_for_cli}"
                )
        run(
            args.input,
            args.output,
            args.network,
            args.supra,
            args.max_fn,
            min_confidence=args.min_confidence,
            apply_verified=args.apply_verified,
            apply_supra_changes=args.apply_supra,
            bio_note_count=args.bio_notes,
            confirm_bio_notes=args.confirm_bio_notes,
            force_output=args.force,
        )
    except (CitationCheckerError, ValueError) as exc:
        print(json.dumps({
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        }), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
