"""
extractor.py — Extract footnotes from a .docx in their accepted state.
Skips <w:del> runs; accepts <w:r> and <w:ins><w:r> runs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import zipfile
from typing import Any
from .ooxml import DocxFormatError, parse_xml_member, validate_docx_zip

W  = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R  = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
wt = lambda tag: f"{{{W}}}{tag}"
rt = lambda tag: f"{{{R}}}{tag}"
relt = lambda tag: f"{{{REL}}}{tag}"

DEL_TAG = wt("del")
INS_TAG = wt("ins")
R_TAG   = wt("r")
T_TAG   = wt("t")
TAB_TAG = wt("tab")
BR_TAG  = wt("br")
RPR_TAG = wt("rPr")
FN_TAG  = wt("footnote")
BODY_TAG= wt("body")
HYPERLINK_TAG = wt("hyperlink")
FOOTNOTE_REF_TAG = wt("footnoteReference")

I_TAG  = wt("i")
B_TAG  = wt("b")
SC_TAG = wt("smallCaps")
U_TAG  = wt("u")

def _has_prop(rpr, tag):
    if rpr is None:
        return False
    el = rpr.find(tag)
    if el is None:
        return False
    val = el.get(wt("val"), "true")
    return val.lower() not in ("false", "0", "none")


@dataclass
class Run:
    text: str
    italic: bool = False
    bold: bool = False
    small_caps: bool = False
    underline: bool = False
    inside_ins: bool = False
    elem: Any = field(default=None, repr=False)


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)

    @property
    def text(self):
        return "".join(r.text for r in self.runs)


@dataclass
class Footnote:
    fn_id: int
    xml_elem: Any
    display_number: int
    paras: list[Paragraph] = field(default_factory=list)

    @property
    def full_text(self):
        return " ".join(p.text for p in self.paras).strip()


def _parse_paragraph(p_elem, rels: dict[str, str] | None = None) -> Paragraph:
    para = Paragraph()
    for run, inside_ins in _iter_runs(p_elem, rels=rels or {}):
        if isinstance(run, Run):
            parsed = run
        else:
            parsed = _parse_run(run, inside_ins=inside_ins)
        if parsed.text:
            para.runs.append(parsed)
    return para


def _iter_runs(elem, inside_ins: bool = False, rels: dict[str, str] | None = None):
    """Yield visible runs recursively, including hyperlinks and inserted text."""
    rels = rels or {}
    for child in elem:
        if child.tag == DEL_TAG:
            continue
        if child.tag == INS_TAG:
            yield from _iter_runs(child, inside_ins=True, rels=rels)
        elif child.tag == R_TAG:
            yield child, inside_ins
        elif child.tag == HYPERLINK_TAG:
            collected = list(_iter_runs(child, inside_ins=inside_ins, rels=rels))
            for item in collected:
                yield item
            target = rels.get(child.get(rt("id"), ""))
            if target:
                display = "".join(
                    (_parse_run(run, inside).text if not isinstance(run, Run) else run.text)
                    for run, inside in collected
                )
                if target not in display:
                    yield Run(text=f" ({target})", inside_ins=inside_ins), inside_ins
        else:
            yield from _iter_runs(child, inside_ins=inside_ins, rels=rels)


def _parse_run(r_elem, inside_ins: bool) -> Run:
    rpr = r_elem.find(RPR_TAG)
    texts = []
    for child in r_elem:
        if child.tag == T_TAG:
            texts.append(child.text or "")
        elif child.tag == TAB_TAG:
            texts.append("\t")
        elif child.tag == BR_TAG:
            texts.append("\n")
    return Run(
        text="".join(texts),
        italic=_has_prop(rpr, I_TAG),
        bold=_has_prop(rpr, B_TAG),
        small_caps=_has_prop(rpr, SC_TAG),
        underline=_has_prop(rpr, U_TAG),
        inside_ins=inside_ins,
        elem=r_elem,
    )


def extract_footnotes(docx_path: str) -> list[Footnote]:
    """
    Extract all footnotes from a .docx file.
    Returns Footnote objects in document/display order.

    `fn_id` is the internal OOXML id used for patching. It is not necessarily
    the displayed footnote number. `display_number` is derived from
    word/document.xml footnoteReference order when available, with a sequential
    fallback for minimal test fixtures that only contain footnotes.xml.
    """
    path = Path(docx_path)
    footnote_elems: dict[int, Any] = {}

    try:
        z = zipfile.ZipFile(path)
    except FileNotFoundError as exc:
        raise DocxFormatError(f"input .docx not found: {path}") from exc
    except PermissionError as exc:
        raise DocxFormatError(f"input .docx is not readable: {path}") from exc
    except zipfile.BadZipFile as exc:
        raise DocxFormatError(f"not a readable .docx ZIP package: {path}") from exc

    with z:
        validate_docx_zip(z)
        if "word/footnotes.xml" not in z.namelist():
            return []
        root = parse_xml_member(z, "word/footnotes.xml")
        rels = _read_footnote_rels(z)
        reference_order = _read_footnote_reference_order(z)

    P_TAG = wt("p")

    for fn_elem in root.findall(f".//{FN_TAG}"):
        fn_id_str = fn_elem.get(wt("id"), "-999")
        try:
            fn_id = int(fn_id_str)
        except ValueError:
            continue
        if fn_id <= 0:
            continue  # skip separator / continuation footnotes

        footnote_elems[fn_id] = fn_elem

    if reference_order:
        ordered_ids = [fn_id for fn_id in reference_order if fn_id in footnote_elems]
    else:
        ordered_ids = sorted(footnote_elems)

    footnotes: list[Footnote] = []
    for display_number, fn_id in enumerate(ordered_ids, start=1):
        fn_elem = footnote_elems[fn_id]
        fn = Footnote(
            fn_id=fn_id,
            display_number=display_number,
            xml_elem=fn_elem,
        )
        for p in fn_elem.findall(f".//{P_TAG}"):
            para = _parse_paragraph(p, rels=rels)
            if para.runs:
                fn.paras.append(para)

        footnotes.append(fn)

    return footnotes


def _read_footnote_reference_order(z: zipfile.ZipFile) -> list[int]:
    if "word/document.xml" not in z.namelist():
        return []
    root = parse_xml_member(z, "word/document.xml")

    seen: set[int] = set()
    order: list[int] = []
    for ref in _iter_footnote_references(root):
        raw = ref.get(wt("id"))
        try:
            fn_id = int(raw) if raw is not None else 0
        except ValueError:
            continue
        if fn_id <= 0 or fn_id in seen:
            continue
        seen.add(fn_id)
        order.append(fn_id)
    return order


def _iter_footnote_references(elem, inside_deleted: bool = False):
    for child in elem:
        child_deleted = inside_deleted or child.tag == DEL_TAG
        if child.tag == FOOTNOTE_REF_TAG and not child_deleted:
            yield child
        yield from _iter_footnote_references(child, child_deleted)


def _read_footnote_rels(z: zipfile.ZipFile) -> dict[str, str]:
    rel_path = "word/_rels/footnotes.xml.rels"
    if rel_path not in z.namelist():
        return {}
    root = parse_xml_member(z, rel_path)
    result: dict[str, str] = {}
    for rel in root.findall(f".//{relt('Relationship')}"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if rel_id and target:
            result[rel_id] = target
    return result
