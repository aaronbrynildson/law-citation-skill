"""
patcher.py — Apply tracked text and formatting changes to a .docx.
Writes OOXML <w:del>/<w:ins> pairs for text changes,
<w:rPrChange> for formatting changes.
"""
from __future__ import annotations
import copy, shutil, zipfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from .ooxml import DocxFormatError, parse_xml_bytes, read_zip_member, validate_docx_zip

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
wt = lambda tag: f"{{{W}}}{tag}"

DEL_TAG  = wt("del")
INS_TAG  = wt("ins")
RPR_TAG  = wt("rPr")
RPRC_TAG = wt("rPrChange")
R_TAG    = wt("r")
T_TAG    = wt("t")
FN_TAG   = wt("footnote")
P_TAG    = wt("p")
DT_TAG   = wt("delText")
XML_SPACE = f"{{{XML}}}space"

AUTHOR   = "CitationChecker"
ATTR_AUT = wt("author")
ATTR_DAT = wt("date")
ATTR_ID  = wt("id")


@dataclass
class TextChange:
    fn_id: int
    old_text: str
    new_text: str
    label: str = ""


@dataclass
class FormatChange:
    fn_id: int
    target_text: str
    prop: str          # "italic" | "small_caps" | "bold" | "underline"
    add: bool          # True = add property, False = remove
    label: str = ""


@dataclass
class _RunSpan:
    run: Any
    text_elem: Any
    parent: Any
    start: int
    end: int
    text: str
    inside_inserted: bool = False


_PROP_TAGS = {
    "italic":     wt("i"),
    "bold":       wt("b"),
    "small_caps": wt("smallCaps"),
    "underline":  wt("u"),
}


def apply_changes(
    src_docx: str,
    dst_docx: str,
    text_changes: list[TextChange],
    fmt_changes: list[FormatChange],
    *,
    overwrite: bool = False,
) -> dict:
    """
    Copy src_docx → dst_docx, then apply all changes in-place on dst_docx.
    Returns a summary dict.
    """
    if Path(src_docx).resolve() == Path(dst_docx).resolve():
        raise ValueError("output .docx path must be different from input path")
    if not Path(src_docx).exists():
        raise DocxFormatError(f"input .docx not found: {src_docx}")
    if Path(dst_docx).exists() and not overwrite:
        raise ValueError("output .docx already exists; choose a new path or pass overwrite=True")

    next_revision_id, xml_bytes = _read_patch_inputs(src_docx)

    Path(dst_docx).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_docx, dst_docx)
    if xml_bytes is None:
        return {
            "text_changes": 0,
            "format_changes": 0,
            "text_change_results": [],
            "format_change_results": [],
            "note": "no footnotes.xml",
        }

    tree = parse_xml_bytes(xml_bytes, "word/footnotes.xml")

    # Index footnotes by id
    fn_index: dict[int, Any] = {}
    for fn_elem in tree.findall(f".//{FN_TAG}"):
        fid = fn_elem.get(wt("id"), "")
        try:
            fn_index[int(fid)] = fn_elem
        except ValueError:
            pass

    tc_count = 0
    fc_count = 0
    text_change_results: list[dict] = []
    format_change_results: list[dict] = []
    rev_id = [next_revision_id]

    for index, tc in enumerate(text_changes):
        fn_elem = fn_index.get(tc.fn_id)
        applied = False
        reason = None
        if fn_elem is None:
            reason = "footnote id not found"
        else:
            for para in fn_elem.findall(f".//{P_TAG}"):
                changed = _para_replace(para, tc.old_text, tc.new_text, rev_id, tc.label)
                if changed:
                    tc_count += 1
                    applied = True
                    break
            if not applied:
                reason = "target text not found in footnote paragraphs"
        text_change_results.append({
            "index": index,
            "fn_id": tc.fn_id,
            "old_text": tc.old_text,
            "new_text": tc.new_text,
            "label": tc.label,
            "applied": applied,
            "reason": reason,
        })

    for index, fc in enumerate(fmt_changes):
        fn_elem = fn_index.get(fc.fn_id)
        applied = False
        reason = None
        if fn_elem is None:
            reason = "footnote id not found"
            format_change_results.append({
                "index": index,
                "fn_id": fc.fn_id,
                "target_text": fc.target_text,
                "prop": fc.prop,
                "add": fc.add,
                "label": fc.label,
                "applied": False,
                "reason": reason,
            })
            continue
        prop_tag = _PROP_TAGS.get(fc.prop)
        if prop_tag is None:
            reason = "unsupported format property"
            format_change_results.append({
                "index": index,
                "fn_id": fc.fn_id,
                "target_text": fc.target_text,
                "prop": fc.prop,
                "add": fc.add,
                "label": fc.label,
                "applied": False,
                "reason": reason,
            })
            continue
        for para in fn_elem.findall(f".//{P_TAG}"):
            changed = _apply_format_all(para, fc.target_text, prop_tag, fc.add, rev_id)
            if changed:
                fc_count += 1
                applied = True
                break
        if not applied:
            reason = "target text not found in footnote paragraphs"
        format_change_results.append({
            "index": index,
            "fn_id": fc.fn_id,
            "target_text": fc.target_text,
            "prop": fc.prop,
            "add": fc.add,
            "label": fc.label,
            "applied": applied,
            "reason": reason,
        })

    # Write back
    new_xml = ET.tostring(tree, encoding="UTF-8", xml_declaration=True)
    _replace_in_zip(dst_docx, "word/footnotes.xml", new_xml)

    return {
        "text_changes": tc_count,
        "format_changes": fc_count,
        "text_change_results": text_change_results,
        "format_change_results": format_change_results,
    }


def _read_patch_inputs(src_docx: str) -> tuple[int, bytes | None]:
    """Validate all Word XML before any output file is created."""
    try:
        z = zipfile.ZipFile(src_docx, 'r')
    except zipfile.BadZipFile as exc:
        raise DocxFormatError(f"not a readable .docx ZIP package: {src_docx}") from exc

    with z:
        validate_docx_zip(z)
        next_revision_id = _next_revision_id_in_package(z)
        if "word/footnotes.xml" not in z.namelist():
            return next_revision_id, None
        xml_bytes = read_zip_member(z, "word/footnotes.xml")

    return next_revision_id, xml_bytes


# ── Text replacement ──────────────────────────────────────────────────────────

def _para_replace(para, old: str, new: str, rev_id: list, label: str) -> bool:
    """Find old_text in the paragraph's accepted text and replace with tracked change."""
    if not old:
        return False
    spans, text = _accepted_text_spans(para)
    idx = text.find(old)
    if idx < 0:
        return False

    end = idx + len(old)
    affected = _spans_for_range(spans, idx, end)
    return _replace_span(affected, idx, end, new, rev_id, label)


def _replace_span(
    spans: list[_RunSpan],
    span_start: int,
    span_end: int,
    new_text: str,
    rev_id: list,
    label: str,
) -> bool:
    if not spans:
        return False
    if any(span.inside_inserted for span in spans):
        return False

    parent = spans[0].parent
    if any(span.parent is not parent for span in spans):
        return False

    insert_at = list(parent).index(spans[0].run)
    first = spans[0]
    last = spans[-1]
    before = first.text[:max(0, span_start - first.start)]
    after = last.text[min(len(last.text), span_end - last.start):]

    if before:
        r_before = copy.deepcopy(first.run)
        _set_run_text(r_before, T_TAG, before)
        parent.insert(insert_at, r_before)
        insert_at += 1

    del_elem = _revision_element(DEL_TAG, rev_id)
    for span in spans:
        frag_start = max(span_start, span.start) - span.start
        frag_end = min(span_end, span.end) - span.start
        if frag_start >= frag_end:
            continue
        r_del = copy.deepcopy(span.run)
        _set_run_text(r_del, DT_TAG, span.text[frag_start:frag_end])
        del_elem.append(r_del)
    parent.insert(insert_at, del_elem)
    insert_at += 1

    ins_elem = _revision_element(INS_TAG, rev_id)
    r_ins = copy.deepcopy(first.run)
    _set_run_text(r_ins, T_TAG, new_text)
    ins_elem.append(r_ins)
    parent.insert(insert_at, ins_elem)
    insert_at += 1

    if after:
        r_after = copy.deepcopy(last.run)
        _set_run_text(r_after, T_TAG, after)
        parent.insert(insert_at, r_after)

    for span in spans:
        parent.remove(span.run)

    return True


# ── Format changes ────────────────────────────────────────────────────────────

def _apply_format_all(para, target: str, prop_tag: str, add: bool, rev_id: list) -> bool:
    if not target:
        return False
    spans, text = _accepted_text_spans(para)
    idx = text.find(target)
    if idx < 0:
        return False

    affected = _spans_for_range(spans, idx, idx + len(target))
    return _format_span(affected, idx, idx + len(target), prop_tag, add, rev_id)


def _format_span(
    spans: list[_RunSpan],
    span_start: int,
    span_end: int,
    prop_tag: str,
    add: bool,
    rev_id: list,
) -> bool:
    if not spans:
        return False
    if any(span.inside_inserted for span in spans):
        return False

    parent = spans[0].parent
    if any(span.parent is not parent for span in spans):
        return False

    insert_at = list(parent).index(spans[0].run)
    new_runs: list[Any] = []
    for span in spans:
        frag_start = max(span_start, span.start) - span.start
        frag_end = min(span_end, span.end) - span.start

        before = span.text[:max(0, frag_start)]
        target = span.text[max(0, frag_start):max(0, frag_end)]
        after = span.text[max(0, frag_end):]

        if before:
            r_before = copy.deepcopy(span.run)
            _set_run_text(r_before, T_TAG, before)
            new_runs.append(r_before)

        if target:
            r_target = copy.deepcopy(span.run)
            _set_run_text(r_target, T_TAG, target)
            _format_run(r_target, prop_tag, add, rev_id)
            new_runs.append(r_target)

        if after:
            r_after = copy.deepcopy(span.run)
            _set_run_text(r_after, T_TAG, after)
            new_runs.append(r_after)

    for run in new_runs:
        parent.insert(insert_at, run)
        insert_at += 1
    for span in spans:
        parent.remove(span.run)
    return True


def _accepted_text_spans(para) -> tuple[list[_RunSpan], str]:
    spans: list[_RunSpan] = []
    pieces: list[str] = []
    pos = 0
    for run, parent, inside_inserted in _iter_visible_runs(para):
        text_elems = run.findall(T_TAG)
        if not text_elems:
            continue
        text = "".join(text_elem.text or "" for text_elem in text_elems)
        spans.append(_RunSpan(
            run=run,
            text_elem=text_elems[0],
            parent=parent,
            start=pos,
            end=pos + len(text),
            text=text,
            inside_inserted=inside_inserted,
        ))
        pieces.append(text)
        pos += len(text)
    return spans, "".join(pieces)


def _iter_visible_runs(elem, inside_deleted: bool = False, inside_inserted: bool = False):
    for child in elem:
        child_deleted = inside_deleted or child.tag == DEL_TAG
        child_inserted = inside_inserted or child.tag == INS_TAG
        if child.tag == R_TAG:
            if not child_deleted:
                yield child, elem, child_inserted
            continue
        yield from _iter_visible_runs(child, child_deleted, child_inserted)


def _spans_for_range(spans: list[_RunSpan], start: int, end: int) -> list[_RunSpan]:
    return [span for span in spans if span.end > start and span.start < end]


def _revision_element(tag: str, rev_id: list):
    elem = ET.Element(tag)
    elem.set(ATTR_ID, str(rev_id[0]))
    rev_id[0] += 1
    elem.set(ATTR_AUT, AUTHOR)
    elem.set(ATTR_DAT, _revision_timestamp())
    return elem


def _revision_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _next_revision_id_in_package(z: zipfile.ZipFile) -> int:
    max_id = 0
    for name in z.namelist():
        if not (name.startswith("word/") and name.endswith(".xml")):
            continue
        root = parse_xml_bytes(read_zip_member(z, name), name)
        max_id = max(max_id, _max_revision_id(root))
    return max_id + 1


def _max_revision_id(tree) -> int:
    max_id = 0
    for tag in (DEL_TAG, INS_TAG, RPRC_TAG):
        for elem in tree.findall(f".//{tag}"):
            raw = elem.get(ATTR_ID)
            if raw is None:
                continue
            try:
                max_id = max(max_id, int(raw))
            except ValueError:
                continue
    return max_id


def _set_run_text(run, tag: str, value: str):
    text_elems = list(run.findall(T_TAG)) + list(run.findall(DT_TAG))
    if text_elems:
        text_elem = text_elems[0]
        text_elem.tag = tag
        for extra in text_elems[1:]:
            run.remove(extra)
    else:
        text_elem = ET.SubElement(run, tag)
    _set_text_value(text_elem, value)


def _set_text_value(text_elem, value: str):
    text_elem.text = value
    if value[:1].isspace() or value[-1:].isspace():
        text_elem.set(XML_SPACE, "preserve")
    else:
        text_elem.attrib.pop(XML_SPACE, None)


def _format_run(r, prop_tag: str, add: bool, rev_id: list):
    rpr = r.find(RPR_TAG)
    if rpr is None:
        rpr = ET.SubElement(r, RPR_TAG)
        r.insert(0, rpr)

    # Save current rPr as rPrChange
    orig_rpr = copy.deepcopy(rpr)
    # Remove any existing rPrChange from the copy
    for old_rprc in orig_rpr.findall(RPRC_TAG):
        orig_rpr.remove(old_rprc)

    rprc = ET.SubElement(rpr, RPRC_TAG)
    rprc.set(ATTR_ID,  str(rev_id[0])); rev_id[0] += 1
    rprc.set(ATTR_AUT, AUTHOR)
    rprc.set(ATTR_DAT, _revision_timestamp())
    rprc.append(orig_rpr)

    if add:
        if rpr.find(prop_tag) is None:
            ET.SubElement(rpr, prop_tag)
    else:
        existing = rpr.find(prop_tag)
        if existing is not None:
            rpr.remove(existing)


# ── Zip helper ────────────────────────────────────────────────────────────────

def _replace_in_zip(zip_path: str, member: str, new_bytes: bytes):
    import os, tempfile
    p = Path(zip_path)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{p.stem}.", suffix=".tmp.docx", dir=p.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(p, 'r') as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
            validate_docx_zip(zin)
            for item in zin.infolist():
                if item.filename == member:
                    zout.writestr(item, new_bytes)
                else:
                    zout.writestr(item, read_zip_member(zin, item.filename))
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
