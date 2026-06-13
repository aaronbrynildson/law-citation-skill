from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import warnings
from unittest import mock
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core.classifier import SourceType, classify
from core.bluebook import check_r1_typeface, check_r8_abbreviations, check_r12_statutes, check_r21_treaties
from core.extractor import extract_footnotes
from core.ooxml import DocxFormatError, DocxSecurityError, parse_xml_bytes
from core.patcher import FormatChange, TextChange, apply_changes
from core.supra import analyse_supras, detect_bio_note_count, validate_existing_supras
from core.verifier import VerificationResult, _crossref_item_result, _openlibrary_verify
from package_agent_skill import build_skill_zip, check_skill
from run_pipeline import _parse_format_change, main, run

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path: Path, footnotes_xml: str) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("word/footnotes.xml", footnotes_xml)


def make_full_docx(
    path: Path,
    footnotes_xml: str,
    rels_xml: str | None = None,
    footnote_reference_ids: list[int] | None = None,
    document_extra_xml: str = "",
) -> None:
    refs_xml = "".join(
        f'<w:r><w:footnoteReference w:id="{fn_id}"/></w:r>'
        for fn_id in (footnote_reference_ids or [])
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>
</Types>""")
        z.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""")
        z.writestr("word/document.xml", f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Body</w:t></w:r>{document_extra_xml}{refs_xml}</w:p></w:body></w:document>""")
        z.writestr("word/footnotes.xml", footnotes_xml)
        if rels_xml is not None:
            z.writestr("word/_rels/footnotes.xml.rels", rels_xml)


def make_python_docx_with_footnotes(path: Path, footnotes_xml: str, footnote_reference_ids: list[int]) -> bool:
    try:
        from docx import Document
    except Exception:
        return False

    doc = Document()
    doc.add_paragraph("Body with footnotes")
    doc.save(path)

    with ZipFile(path, "r") as zin:
        members = {info.filename: zin.read(info.filename) for info in zin.infolist()}

    refs_xml = "".join(
        f'<w:p><w:r><w:footnoteReference w:id="{fn_id}"/></w:r></w:p>'
        for fn_id in footnote_reference_ids
    )
    document_xml = members["word/document.xml"].decode("utf-8").replace("</w:body>", f"{refs_xml}</w:body>")
    content_types = members["[Content_Types].xml"].decode("utf-8")
    if "/word/footnotes.xml" not in content_types:
        content_types = content_types.replace(
            "</Types>",
            '<Override PartName="/word/footnotes.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/></Types>',
        )
    rels = members["word/_rels/document.xml.rels"].decode("utf-8")
    if "relationships/footnotes" not in rels:
        rels = rels.replace(
            "</Relationships>",
            '<Relationship Id="rIdFootnotes" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" '
            'Target="footnotes.xml"/></Relationships>',
        )

    members["word/document.xml"] = document_xml.encode("utf-8")
    members["[Content_Types].xml"] = content_types.encode("utf-8")
    members["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    members["word/footnotes.xml"] = footnotes_xml.encode("utf-8")

    with ZipFile(path, "w", ZIP_DEFLATED) as zout:
        for name, data in members.items():
            zout.writestr(name, data)
    return True


def read_footnotes_xml(path: Path) -> str:
    with ZipFile(path) as z:
        return z.read("word/footnotes.xml").decode("utf-8")


def run_quiet(*args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return run(*args, **kwargs)


class PipelineTests(unittest.TestCase):
    def test_pipeline_preserves_word_run_boundaries_without_extra_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "split.docx"
            dst = tmp_path / "split_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe ver</w:t></w:r><w:r><w:t>sus Wade, 410 U.S. 113 (1973).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, 1)

            self.assertEqual(result["reports"][0]["text"], "Roe versus Wade, 410 U.S. 113 (1973).")
            self.assertNotIn("ver sus", result["reports"][0]["text"])

    def test_low_confidence_corrections_are_manual_review_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "id.docx"
            dst = tmp_path / "nested" / "id_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, 1)
            out_xml = read_footnotes_xml(dst)

            self.assertEqual(result["manual_review_count"], 1)
            self.assertEqual(result["patch_summary"]["text_changes"], 0)
            self.assertTrue(dst.exists())
            self.assertNotIn("<w:ins", out_xml)

    def test_apply_verified_applies_mechanical_id_period_without_source_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "id.docx"
            dst = tmp_path / "id_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, 1, apply_verified=True)
            accepted_text = extract_footnotes(str(dst))[0].full_text
            id_issue = next(issue for issue in result["reports"][0]["issues"] if issue["rule_name"] == "Id. missing period")

            self.assertEqual(result["patch_summary"]["text_changes"], 1)
            self.assertTrue(id_issue["auto_applied"])
            self.assertEqual(accepted_text, "Id. at 45.")

    def test_patcher_replaces_text_across_multiple_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "split.docx"
            dst = tmp_path / "split_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe ver</w:t></w:r><w:r><w:t>sus Wade, 410 U.S. 113 (1973).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(
                str(src),
                str(dst),
                [TextChange(fn_id=1, old_text="Roe versus Wade", new_text="Roe v. Wade")],
                [],
            )
            accepted_text = extract_footnotes(str(dst))[0].full_text

            self.assertEqual(summary["text_changes"], 1)
            self.assertEqual(accepted_text, "Roe v. Wade, 410 U.S. 113 (1973).")

    def test_patcher_reports_failed_text_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "missing.docx"
            dst = tmp_path / "missing_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Existing citation.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(
                str(src),
                str(dst),
                [TextChange(fn_id=1, old_text="Not present", new_text="Replacement")],
                [],
            )

            self.assertEqual(summary["text_changes"], 0)
            self.assertFalse(summary["text_change_results"][0]["applied"])
            self.assertEqual(summary["text_change_results"][0]["reason"], "target text not found in footnote paragraphs")

    def test_patcher_does_not_leave_output_when_package_validation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "bad_extra_part.docx"
            dst = tmp_path / "bad_extra_part_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )
            with ZipFile(src, "a", ZIP_DEFLATED) as z:
                z.writestr("word/comments.xml", "<w:comments><w:comment>")

            with self.assertRaises(DocxFormatError):
                apply_changes(str(src), str(dst), [TextChange(fn_id=1, old_text="Id", new_text="Id.")], [])

            self.assertFalse(dst.exists())

    def test_patcher_rejects_same_input_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "same.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            with self.assertRaises(ValueError):
                apply_changes(str(src), str(src), [TextChange(fn_id=1, old_text="Id", new_text="Id.")], [])

    def test_patcher_refuses_existing_output_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "overwrite.docx"
            dst = tmp_path / "overwrite_out.docx"
            dst.write_text("existing draft", encoding="utf-8")
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            with self.assertRaises(ValueError):
                apply_changes(str(src), str(dst), [TextChange(fn_id=1, old_text="Id", new_text="Id.")], [])
            self.assertEqual(dst.read_text(encoding="utf-8"), "existing draft")

            summary = apply_changes(
                str(src),
                str(dst),
                [TextChange(fn_id=1, old_text="Id", new_text="Id.")],
                [],
                overwrite=True,
            )
            self.assertEqual(summary["text_changes"], 1)

    def test_cli_refuses_existing_output_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "cli_overwrite.docx"
            dst = tmp_path / "cli_overwrite_out.docx"
            dst.write_text("existing draft", encoding="utf-8")
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                code = main(["--input", str(src), "--output", str(dst)])

            self.assertEqual(code, 2)
            self.assertEqual(dst.read_text(encoding="utf-8"), "existing draft")
            self.assertEqual(json.loads(stderr.getvalue())["error"]["type"], "ValueError")

    def test_patcher_does_not_nest_tracked_changes_inside_existing_insertion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "inside_insert.docx"
            dst = tmp_path / "inside_insert_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:ins w:id="7" w:author="A" w:date="2026-01-01T00:00:00Z"><w:r><w:t>Id at 45.</w:t></w:r></w:ins></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(str(src), str(dst), [TextChange(fn_id=1, old_text="Id", new_text="Id.")], [])
            out_xml = read_footnotes_xml(dst)

            self.assertEqual(summary["text_changes"], 0)
            self.assertFalse(summary["text_change_results"][0]["applied"])
            self.assertEqual(out_xml.count("<w:ins"), 1)
            self.assertNotIn("<w:del", out_xml)

    def test_patcher_unique_temp_file_preserves_existing_sibling_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "collision.docx"
            dst = tmp_path / "collision_out.docx"
            sibling_tmp = tmp_path / "collision_out_tmp.docx"
            sibling_tmp.write_text("keep me", encoding="utf-8")
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(str(src), str(dst), [TextChange(fn_id=1, old_text="Id", new_text="Id.")], [])

            self.assertEqual(summary["text_changes"], 1)
            self.assertEqual(sibling_tmp.read_text(encoding="utf-8"), "keep me")

    def test_patcher_replaces_text_across_multiple_text_nodes_in_one_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "multi_text.docx"
            dst = tmp_path / "multi_text_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id</w:t><w:t> at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(str(src), str(dst), [TextChange(fn_id=1, old_text="Id at", new_text="Id. at")], [])
            accepted_text = extract_footnotes(str(dst))[0].full_text

            self.assertEqual(summary["text_changes"], 1)
            self.assertEqual(accepted_text, "Id. at 45.")

    def test_format_change_splits_single_run_to_target_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "format.docx"
            dst = tmp_path / "format_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(
                str(src),
                str(dst),
                [],
                [FormatChange(fn_id=1, target_text="Space Law Today", prop="italic", add=True)],
            )
            root = ET.fromstring(read_footnotes_xml(dst).encode("utf-8"))
            runs = []
            for run in root.findall(f".//{{{W}}}r"):
                text = "".join(t.text or "" for t in run.findall(f".//{{{W}}}t"))
                if text:
                    runs.append((text, run.find(f"{{{W}}}rPr/{{{W}}}i") is not None))

            self.assertEqual(summary["format_changes"], 1)
            self.assertIn(("Space Law Today", True), runs)
            self.assertIn(("Jane Author, ", False), runs)
            self.assertIn((", 4 J. Space L. 1 (2020).", False), runs)

    def test_bio_notes_offset_supra_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "supra.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Alex is a professor of law.</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="3"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            footnotes = extract_footnotes(str(src))
            proposals = analyse_supras(footnotes)

            self.assertEqual(detect_bio_note_count(footnotes), 1)
            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0].first_fn_id, 1)
            self.assertEqual(proposals[0].original, "Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).")
            self.assertEqual(proposals[0].replacement, "Author, supra note 1, at 7.")
            self.assertNotIn("*", proposals[0].replacement)

    def test_document_reference_order_controls_supra_numbers_with_noncontiguous_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "noncontiguous.docx"
            dst = tmp_path / "noncontiguous_out.docx"
            make_full_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="9"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                footnote_reference_ids=[2, 9],
            )

            footnotes = extract_footnotes(str(src))
            proposals = analyse_supras(footnotes, bio_note_count=0)
            result = run_quiet(str(src), str(dst), False, True, None)

            self.assertEqual([(fn.fn_id, fn.display_number) for fn in footnotes], [(2, 1), (9, 2)])
            self.assertEqual(proposals[0].first_fn_id, 1)
            self.assertEqual(proposals[0].replacement, "Author, supra note 1, at 7.")
            self.assertEqual(result["reports"][0]["fn_id"], 2)
            self.assertEqual(result["reports"][0]["paper_fn"], 1)
            self.assertEqual(result["reports"][1]["fn_id"], 9)
            self.assertEqual(result["reports"][1]["paper_fn"], 2)

    def test_python_docx_based_package_round_trip_with_footnotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "python_docx.docx"
            dst = tmp_path / "python_docx_out.docx"
            created = make_python_docx_with_footnotes(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="-1"><w:p><w:r><w:t /></w:r></w:p></w:footnote>
  <w:footnote w:id="0"><w:p><w:r><w:t /></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                [2],
            )
            if not created:
                self.skipTest("python-docx not available")

            summary = apply_changes(str(src), str(dst), [TextChange(fn_id=2, old_text="Id", new_text="Id.")], [])
            footnotes = extract_footnotes(str(dst))
            with ZipFile(dst) as z:
                names = z.namelist()
                for name in names:
                    if name.endswith((".xml", ".rels")):
                        ET.fromstring(z.read(name))

            self.assertIn("word/document.xml", names)
            self.assertEqual(summary["text_changes"], 1)
            self.assertEqual(footnotes[0].fn_id, 2)
            self.assertEqual(footnotes[0].full_text, "Id. at 45.")

    def test_committed_word_style_fixture_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "word_style_footnotes.docx"
            dst = tmp_path / "word_style_footnotes_out.docx"
            shutil.copy2(ROOT / "tests" / "fixtures" / "word_style_footnotes.docx", src)

            footnotes = extract_footnotes(str(src))
            summary = apply_changes(str(src), str(dst), [TextChange(fn_id=5, old_text="Id", new_text="Id.")], [])
            patched = extract_footnotes(str(dst))
            out_xml = read_footnotes_xml(dst)

            self.assertEqual([(fn.fn_id, fn.display_number) for fn in footnotes], [(2, 1), (5, 2)])
            self.assertEqual(footnotes[1].full_text, "Id at 121. See source page (https://example.com/source).")
            self.assertEqual(summary["text_changes"], 1)
            self.assertEqual(patched[1].full_text, "Id. at 121. See source page (https://example.com/source).")
            self.assertIn('w:id="89"', out_xml)
            self.assertIn('w:id="90"', out_xml)

    def test_malformed_docx_zip_raises_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad.docx"
            src.write_bytes(b"not a zip file")

            with self.assertRaises(DocxFormatError):
                extract_footnotes(str(src))

    def test_malformed_footnotes_xml_raises_structured_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad_xml.docx"
            make_docx(src, b"<w:footnotes><w:footnote>".decode("utf-8"))

            with self.assertRaises(DocxFormatError):
                extract_footnotes(str(src))

    def test_xml_doctype_raises_security_error(self):
        with self.assertRaises(DocxSecurityError):
            parse_xml_bytes(b'<?xml version="1.0"?><!DOCTYPE x [<!ELEMENT x ANY>]><x/>', "word/footnotes.xml")

    def test_xml_entity_raises_security_error(self):
        with self.assertRaises(DocxSecurityError):
            parse_xml_bytes(b'<?xml version="1.0"?><!ENTITY x "unsafe"><x/>', "word/footnotes.xml")

    def test_duplicate_zip_members_raise_security_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "duplicate.docx"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with ZipFile(src, "w", ZIP_DEFLATED) as z:
                    z.writestr("word/footnotes.xml", "<a/>")
                    z.writestr("word/footnotes.xml", "<a/>")

            with self.assertRaises(DocxSecurityError):
                extract_footnotes(str(src))

    def test_large_zip_member_raises_security_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "large.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}"><w:footnote w:id="1"><w:p><w:r><w:t>Large</w:t></w:r></w:p></w:footnote></w:footnotes>""",
            )

            with mock.patch("core.ooxml.MAX_MEMBER_SIZE", 10):
                with self.assertRaises(DocxSecurityError):
                    extract_footnotes(str(src))

    def test_cli_returns_structured_error_for_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "bad.docx"
            dst = Path(tmp) / "out.docx"
            src.write_bytes(b"not a zip file")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                code = main(["--input", str(src), "--output", str(dst)])

            self.assertEqual(code, 2)
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["error"]["type"], "DocxFormatError")

    def test_cli_returns_structured_error_for_missing_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "missing.docx"
            dst = Path(tmp) / "out.docx"
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                code = main(["--input", str(src), "--output", str(dst)])

            self.assertEqual(code, 2)
            self.assertFalse(dst.exists())
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["error"]["type"], "DocxFormatError")
            self.assertIn("not found", payload["error"]["message"])

    def test_cli_apply_supra_confirmation_mismatch_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "supra_mismatch.docx"
            dst = tmp_path / "supra_mismatch_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Alex is a professor of law.</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                code = main([
                    "--input", str(src),
                    "--output", str(dst),
                    "--apply-supra",
                    "--confirm-bio-notes", "0",
                ])

            self.assertEqual(code, 2)
            self.assertFalse(dst.exists())
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["error"]["type"], "ValueError")

    def test_bio_note_offset_uses_display_order_not_internal_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "bio_noncontiguous.docx"
            make_full_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="5"><w:p><w:r><w:t>Alex is a professor of law.</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="8"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="13"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                footnote_reference_ids=[5, 8, 13],
            )

            footnotes = extract_footnotes(str(src))
            proposals = analyse_supras(footnotes)

            self.assertEqual(detect_bio_note_count(footnotes), 1)
            self.assertEqual([(fn.fn_id, fn.display_number) for fn in footnotes], [(5, 1), (8, 2), (13, 3)])
            self.assertEqual(proposals[0].first_fn_id, 1)
            self.assertEqual(proposals[0].replacement, "Author, supra note 1, at 7.")

    def test_apply_supra_does_not_write_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "supra.docx"
            dst = tmp_path / "supra_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(
                str(src),
                str(dst),
                False,
                True,
                None,
                apply_supra_changes=True,
                confirm_bio_notes=0,
            )
            accepted_text = " ".join(fn.full_text for fn in extract_footnotes(str(dst)))

            self.assertEqual(result["patch_summary"]["text_changes"], 1)
            self.assertIn("Author, supra note 1, at 7.", accepted_text)
            self.assertNotIn("*supra*", accepted_text)

    def test_apply_supra_does_not_apply_ambiguous_supra_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "supra_existing.docx"
            dst = tmp_path / "supra_existing_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Author, supra note 1, at 5; Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(
                str(src),
                str(dst),
                False,
                True,
                None,
                apply_supra_changes=True,
                confirm_bio_notes=0,
            )

            self.assertEqual(result["patch_summary"]["text_changes"], 1)
            self.assertEqual(result["patch_summary"]["format_changes"], 0)

    def test_pipeline_routes_format_changes_to_manual_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "format_manual.docx"
            dst = tmp_path / "format_manual_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None, apply_verified=True)
            reasons = [item["reason"] for item in result["manual_review"]]

            self.assertEqual(result["patch_summary"]["format_changes"], 0)
            self.assertTrue(any("formatting changes are report-only" in reason for reason in reasons))

    def test_pipeline_does_not_flag_already_italicized_case_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "italic_case.docx"
            dst = tmp_path / "italic_case_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:rPr><w:i/></w:rPr><w:t>Roe v. Wade</w:t></w:r><w:r><w:t>, 410 U.S. 113, 120 (1973).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None)
            names = [issue["rule_name"] for issue in result["reports"][0]["issues"]]

            self.assertNotIn("Case name typeface", names)

    def test_pipeline_does_not_flag_already_small_caps_book_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "smallcaps_book.docx"
            dst = tmp_path / "smallcaps_book_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, </w:t></w:r><w:r><w:rPr><w:smallCaps/></w:rPr><w:t>Space Law Book</w:t></w:r><w:r><w:t> 45 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None)
            names = [issue["rule_name"] for issue in result["reports"][0]["issues"]]

            self.assertNotIn("Book title typeface", names)

    def test_apply_supra_requires_confirmed_bio_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "supra.docx"
            dst = tmp_path / "supra_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--input", str(src), "--output", str(dst), "--apply-supra"])

            result = run_quiet(str(src), str(dst), False, True, None, apply_supra_changes=True)
            self.assertTrue(result["auto_apply_policy"]["supra_apply_blocked"])
            self.assertEqual(result["patch_summary"]["text_changes"], 0)
            self.assertFalse(result["reports"][1]["supra_proposals"][0]["auto_applied"])

    def test_current_manual_categories_are_classified_for_review(self):
        self.assertEqual(
            classify("ChatGPT, response to prompt about orbital debris (OpenAI, Jan. 1, 2026)."),
            SourceType.AI_GENERATED,
        )
        self.assertEqual(
            classify("Navajo Nation Code tit. 17, § 101 (2024)."),
            SourceType.TRIBAL,
        )
        self.assertEqual(
            classify("Jane Smith Papers, box 2, folder 4, University Archives."),
            SourceType.ARCHIVAL,
        )
        self.assertEqual(
            classify("Roe v. Wade, 410 U.S. 113, 120 (1973); Author, supra note 1."),
            SourceType.CASE_DOMESTIC,
        )
        self.assertEqual(classify("Id. at 45."), SourceType.ID)
        self.assertEqual(
            classify("Hearing Before the Subcomm. on Space and Aeronautics, 117th Cong. 5 (2021)."),
            SourceType.LEGISLATIVE,
        )
        self.assertEqual(
            classify(
                "Treaty on Principles Governing the Activities of States in the Exploration and Use of Outer Space, "
                "opened for signature Jan. 27, 1967, 18 U.S.T. 2410, 610 U.N.T.S. 205."
            ),
            SourceType.TREATY,
        )
        self.assertEqual(
            classify(
                "Treaty on Principles Governing the Activities of States in the Exploration and Use of Outer Space, "
                "opened for signature 27 Jan. 1967, 18 U.S.T. 2410, 610 U.N.T.S. 205."
            ),
            SourceType.TREATY,
        )

    def test_case_typeface_target_keeps_multiword_parties(self):
        issues = check_r1_typeface(
            "Brown v. Board of Education, 347 U.S. 483, 495 (1954).",
            SourceType.CASE_DOMESTIC,
        )

        self.assertEqual(issues[0].original, "Brown v. Board of Education")
        self.assertEqual(issues[0].format_change, "add:italic:Brown v. Board of Education")

    def test_mixed_footnote_catches_id_short_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "mixed.docx"
            dst = tmp_path / "mixed_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe v. Wade, 410 U.S. 113, 120 (1973).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Id. at 121; Brown v. Board of Education, 347 U.S. 483, 495 (1954).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None)
            mixed_report = result["reports"][1]

            self.assertEqual(mixed_report["src_type"], "id")
            self.assertEqual([c["src_type"] for c in mixed_report["citations"]], ["id", "case_domestic"])
            self.assertTrue(any(issue["rule_name"] == "Id. context candidate" for issue in mixed_report["issues"]))

    def test_same_footnote_id_uses_immediately_prior_full_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "same_fn_id.docx"
            dst = tmp_path / "same_fn_id_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe v. Wade, 410 U.S. 113, 120 (1973); Id. at 121.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None)
            names = [issue["rule_name"] for issue in result["reports"][0]["issues"]]

            self.assertIn("Id. context candidate", names)
            self.assertNotIn("Id. has no preceding full authority", names)

    def test_period_separated_id_uses_immediately_prior_full_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "period_id.docx"
            dst = tmp_path / "period_id_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe v. Wade, 410 U.S. 113, 120 (1973). Id. at 121.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None)
            report = result["reports"][0]
            names = [issue["rule_name"] for issue in report["issues"]]

            self.assertEqual([c["src_type"] for c in report["citations"]], ["case_domestic", "id"])
            self.assertIn("Id. context candidate", names)
            self.assertNotIn("Id. has no preceding full authority", names)

    def test_crossref_mismatch_is_not_high_confidence_verified(self):
        item = {
            "title": ["Completely Different Article"],
            "published": {"date-parts": [[2020]]},
            "container-title": ["J. Space L."],
            "author": [{"given": "Jane", "family": "Author"}],
            "DOI": "10.1234/example",
        }

        result = _crossref_item_result(
            "Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).",
            item,
            source_title="Space Law Today",
            doi=None,
        )

        self.assertIs(result.verified, False)
        self.assertLess(result.confidence, 0.85)

    def test_footnote_verified_false_if_any_citation_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "mixed_verify.docx"
            dst = tmp_path / "mixed_verify_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe v. Wade, 410 U.S. 113, 120 (1973); Brown v. Board of Education, 347 U.S. 483, 495 (1954).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            with mock.patch("run_pipeline.verify", side_effect=[
                VerificationResult(verified=False, confidence=0.2),
                VerificationResult(verified=True, confidence=0.95),
            ]):
                result = run_quiet(str(src), str(dst), False, False, None)

            self.assertIs(result["reports"][0]["verified"], False)

    def test_crossref_match_without_volume_page_is_not_fully_verified(self):
        item = {
            "title": ["Space Law Today"],
            "published": {"date-parts": [[2020]]},
            "container-title": ["Journal of Space Law"],
            "author": [{"given": "Jane", "family": "Author"}],
            "DOI": "10.1234/example",
        }

        result = _crossref_item_result(
            "Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).",
            item,
            source_title="Space Law Today",
            doi=None,
        )

        self.assertIsNone(result.verified)
        self.assertLess(result.confidence, 0.85)

    def test_crossref_author_mismatch_is_not_fully_verified(self):
        item = {
            "title": ["Space Law Today"],
            "published": {"date-parts": [[2020]]},
            "container-title": ["Journal of Space Law"],
            "volume": "4",
            "page": "1-30",
            "author": [{"given": "Different", "family": "Writer"}],
            "DOI": "10.1234/example",
        }

        result = _crossref_item_result(
            "Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).",
            item,
            source_title="Space Law Today",
            doi=None,
        )

        self.assertIsNone(result.verified)
        self.assertLess(result.confidence, 0.85)

    def test_offline_heuristic_is_not_source_verified(self):
        from core.verifier import verify

        result = verify("Roe v. Wade, 410 U.S. 113, 120 (1973).", SourceType.CASE_DOMESTIC, use_network=False)

        self.assertIsNone(result.verified)
        self.assertEqual(result.source, "heuristic")

    def test_openlibrary_title_author_without_year_is_not_fully_verified(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "docs": [{
                        "title": "Space Law Book",
                        "author_name": ["Jane Author"],
                        "isbn": ["1234567890"],
                    }]
                }).encode("utf-8")

        with mock.patch("core.verifier.urllib.request.urlopen", return_value=FakeResponse()):
            result = _openlibrary_verify("Jane Author, Space Law Book (2020).")

        self.assertIsNone(result.verified)
        self.assertLess(result.confidence, 0.85)
        self.assertIn("source year not available", result.note)

    def test_crossref_request_uses_current_user_agent(self):
        from core import verifier

        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "message": {
                        "items": [{
                            "score": 100,
                            "title": ["Space Law Today"],
                            "published": {"date-parts": [[2020]]},
                            "container-title": ["Journal of Space Law"],
                            "volume": "4",
                            "page": "1-30",
                            "author": [{"given": "Jane", "family": "Author"}],
                            "DOI": "10.1234/example",
                        }]
                    }
                }).encode("utf-8")

        def fake_urlopen(req, timeout=8):
            captured["user_agent"] = req.headers.get("User-agent")
            return FakeResponse()

        with mock.patch("core.verifier.time.sleep"):
            with mock.patch("core.verifier.urllib.request.urlopen", side_effect=fake_urlopen):
                verifier._crossref_verify("Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).")

        self.assertEqual(captured["user_agent"], verifier.USER_AGENT)

    def test_openlibrary_request_uses_current_user_agent(self):
        from core import verifier

        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "docs": [{
                        "title": "Space Law Book",
                        "author_name": ["Jane Author"],
                        "first_publish_year": 2020,
                        "isbn": ["1234567890"],
                    }]
                }).encode("utf-8")

        def fake_urlopen(req, timeout=8):
            captured["user_agent"] = req.headers.get("User-agent")
            return FakeResponse()

        with mock.patch("core.verifier.time.sleep"):
            with mock.patch("core.verifier.urllib.request.urlopen", side_effect=fake_urlopen):
                verifier._openlibrary_verify("Jane Author, Space Law Book (2020).")

        self.assertEqual(captured["user_agent"], verifier.USER_AGENT)

    def test_pipeline_marks_auto_applied_only_after_patcher_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "apply_status.docx"
            dst = tmp_path / "apply_status_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )
            failed_summary = {
                "text_changes": 0,
                "format_changes": 0,
                "text_change_results": [{
                    "index": 0,
                    "fn_id": 1,
                    "old_text": "Id",
                    "new_text": "Id.",
                    "label": "R4: Id. missing period",
                    "applied": False,
                    "reason": "target text not found in footnote paragraphs",
                }],
                "format_change_results": [],
            }

            with mock.patch("run_pipeline.verify", return_value=VerificationResult(verified=True, confidence=0.9)):
                with mock.patch("run_pipeline.apply_changes", return_value=failed_summary):
                    result = run_quiet(str(src), str(dst), False, False, None, apply_verified=True)
            id_issue = next(issue for issue in result["reports"][0]["issues"] if issue["rule_name"] == "Id. missing period")

            self.assertFalse(id_issue["auto_applied"])
            self.assertIn("tracked change was not applied", id_issue["manual_review_reason"])
            self.assertEqual(result["manual_review_count"], 1)

    def test_format_change_parser_keeps_colons_in_target(self):
        self.assertEqual(
            _parse_format_change("add:italic:Space Law: A New Frontier"),
            ("add", "italic", "Space Law: A New Frontier"),
        )

    def test_patcher_uses_revision_ids_after_existing_tracked_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "existing_rev.docx"
            dst = tmp_path / "existing_rev_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:ins w:id="42" w:author="A" w:date="2026-01-01T00:00:00Z"><w:r><w:t>Existing. </w:t></w:r></w:ins><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            summary = apply_changes(
                str(src),
                str(dst),
                [TextChange(fn_id=1, old_text="Id", new_text="Id.")],
                [],
            )
            out_xml = read_footnotes_xml(dst)

            self.assertEqual(summary["text_changes"], 1)
            self.assertIn('w:id="43"', out_xml)
            self.assertIn('w:id="44"', out_xml)

    def test_patcher_uses_revision_ids_after_document_level_tracked_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "document_rev.docx"
            dst = tmp_path / "document_rev_out.docx"
            make_full_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                footnote_reference_ids=[1],
                document_extra_xml=(
                    '<w:ins w:id="99" w:author="A" w:date="2026-01-01T00:00:00Z">'
                    "<w:r><w:t>Existing tracked document edit.</w:t></w:r>"
                    "</w:ins>"
                ),
            )

            summary = apply_changes(
                str(src),
                str(dst),
                [TextChange(fn_id=1, old_text="Id", new_text="Id.")],
                [],
            )
            out_xml = read_footnotes_xml(dst)

            self.assertEqual(summary["text_changes"], 1)
            self.assertIn('w:id="100"', out_xml)
            self.assertIn('w:id="101"', out_xml)

    def test_extractor_preserves_hyperlink_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "hyperlink.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:footnote w:id="1"><w:p><w:r><w:t>See </w:t></w:r><w:hyperlink r:id="rId1"><w:r><w:t>https://example.com</w:t></w:r></w:hyperlink><w:r><w:t> (last visited Jan. 1, 2026).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            footnotes = extract_footnotes(str(src))

            self.assertEqual(footnotes[0].full_text, "See https://example.com (last visited Jan. 1, 2026).")

    def test_extractor_appends_hyperlink_relationship_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "hyperlink_full.docx"
            make_full_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:footnote w:id="1"><w:p><w:r><w:t>See </w:t></w:r><w:hyperlink r:id="rIdLink"><w:r><w:t>agency page</w:t></w:r></w:hyperlink><w:r><w:t> (last visited Jan. 1, 2026).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/agency" TargetMode="External"/>
</Relationships>""",
            )

            footnotes = extract_footnotes(str(src))

            self.assertEqual(footnotes[0].full_text, "See agency page (https://example.com/agency) (last visited Jan. 1, 2026).")

    def test_patcher_refuses_replacement_across_hyperlink_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "hyperlink_boundary.docx"
            dst = tmp_path / "hyperlink_boundary_out.docx"
            make_full_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:footnote w:id="1"><w:p><w:r><w:t>See </w:t></w:r><w:hyperlink r:id="rIdLink"><w:r><w:t>agency page</w:t></w:r></w:hyperlink><w:r><w:t>.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/agency" TargetMode="External"/>
</Relationships>""",
            )

            summary = apply_changes(
                str(src),
                str(dst),
                [TextChange(fn_id=1, old_text="See agency page", new_text="Review agency page")],
                [],
            )

            self.assertEqual(summary["text_changes"], 0)
            self.assertFalse(summary["text_change_results"][0]["applied"])

    def test_id_short_form_reports_context_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "id_context.docx"
            dst = tmp_path / "id_context_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe v. Wade, 410 U.S. 113, 120 (1973).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Id. at 121.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, False, None)
            id_report = result["reports"][1]

            self.assertEqual(id_report["src_type"], "id")
            self.assertTrue(any(issue["rule_name"] == "Id. context candidate" for issue in id_report["issues"]))

    def test_existing_supra_self_forward_and_wrong_numbers_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "bad_supra.docx"
            dst = tmp_path / "bad_supra_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Author, supra note 2, at 7.</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="3"><w:p><w:r><w:t>Author, supra note 99, at 8.</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="4"><w:p><w:r><w:t>Author, supra note 2, at 9.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            result = run_quiet(str(src), str(dst), False, True, None)
            names = [issue["rule_name"] for report in result["reports"] for issue in report["issues"]]

            self.assertIn("Supra self-reference", names)
            self.assertIn("Supra forward reference", names)
            self.assertIn("Supra wrong note number", names)

    def test_existing_supra_matches_particle_last_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "particle_supra.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Frans von der Dunk, Space Law Today, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>von der Dunk, supra note 1, at 7.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            issues = validate_existing_supras(extract_footnotes(str(src)), bio_note_count=0)

            self.assertEqual(issues, [])

    def test_existing_supra_same_author_second_source_is_ambiguous_not_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "same_author_supra.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, First Space Article, 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Second Space Article, 5 J. Space L. 20, 25 (2021).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="3"><w:p><w:r><w:t>Author, supra note 2, at 28.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            issues = validate_existing_supras(extract_footnotes(str(src)), bio_note_count=0)
            names = [issue.rule_name for issue in issues]

            self.assertIn("Supra ambiguous label", names)
            self.assertNotIn("Supra wrong note number", names)

    def test_treaties_do_not_get_generic_supra_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "treaty_supra.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Treaty on Principles Governing the Activities of States in the Exploration and Use of Outer Space, opened for signature Jan. 27, 1967, 18 U.S.T. 2410, 610 U.N.T.S. 205.</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Treaty on Principles Governing the Activities of States in the Exploration and Use of Outer Space, opened for signature Jan. 27, 1967, 18 U.S.T. 2410, 610 U.N.T.S. 205.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            proposals = analyse_supras(extract_footnotes(str(src)), bio_note_count=0)

            self.assertEqual(proposals, [])

    def test_supra_dedupe_normalizes_vol_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "vol_supra.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Jane Author, Space Law Today, vol. 4 J. Space L. 1, 5 (2020).</w:t></w:r></w:p></w:footnote>
  <w:footnote w:id="2"><w:p><w:r><w:t>Jane Author, Space Law Today, 4 J. Space L. 1, 7 (2020).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            proposals = analyse_supras(extract_footnotes(str(src)), bio_note_count=0)

            self.assertEqual(len(proposals), 1)
            self.assertEqual(proposals[0].replacement, "Author, supra note 1, at 7.")

    def test_apply_verified_uses_rule_level_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "allowlist.docx"
            dst = tmp_path / "allowlist_out.docx"
            make_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Roe vs. Wade, 410 U.S. 113, 120 (1973).</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

            with mock.patch("run_pipeline.verify", return_value=VerificationResult(verified=True, confidence=0.95)):
                result = run_quiet(str(src), str(dst), False, False, None, apply_verified=True)
            reasons = [item["reason"] for item in result["manual_review"]]

            self.assertEqual(result["patch_summary"]["text_changes"], 0)
            self.assertTrue(any(reason == "rule is not in the safe auto-apply allowlist" for reason in reasons))

    def test_full_docx_package_is_preserved_after_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "full.docx"
            dst = tmp_path / "full_out.docx"
            rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/source" TargetMode="External"/>
</Relationships>"""
            make_full_docx(
                src,
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
                rels_xml,
            )

            apply_changes(str(src), str(dst), [TextChange(fn_id=1, old_text="Id", new_text="Id.")], [])

            with ZipFile(dst) as z:
                self.assertIn("[Content_Types].xml", z.namelist())
                self.assertIn("word/document.xml", z.namelist())
                self.assertIn("word/footnotes.xml", z.namelist())
                self.assertIn("word/_rels/footnotes.xml.rels", z.namelist())
                self.assertEqual(z.read("word/_rels/footnotes.xml.rels").decode("utf-8"), rels_xml)
                self.assertIn("<w:ins", z.read("word/footnotes.xml").decode("utf-8"))
                for name in z.namelist():
                    if name.endswith((".xml", ".rels")):
                        ET.fromstring(z.read(name))

    def test_broad_abbreviation_checks_are_report_only(self):
        issues = check_r8_abbreviations("Jane Author, International Space Law, 4 J. Space L. 1 (2020).")

        self.assertTrue(issues)
        self.assertTrue(all(issue.original == issue.corrected for issue in issues))

    def test_usc_year_parenthetical_is_warning_not_auto_fix(self):
        issues = check_r12_statutes("42 U.S.C. § 1983")
        year_issues = [issue for issue in issues if issue.rule_name == "Statute missing year parenthetical"]

        self.assertEqual(len(year_issues), 1)
        self.assertEqual(year_issues[0].severity, "warning")
        self.assertEqual(year_issues[0].original, year_issues[0].corrected)

    def test_treaty_date_examples_match_checker(self):
        issues = check_r21_treaties(
            "Treaty on Principles Governing the Activities of States in the Exploration and Use of Outer Space, "
            "opened for signature Jan. 27, 1967, 18 U.S.T. 2410, 610 U.N.T.S. 205."
        )

        self.assertFalse(any(issue.rule_name == "Treaty date format" for issue in issues))

    def test_agent_skill_compatibility_check_accepts_current_package(self):
        report = check_skill(ROOT)

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["metadata"]["name"], "law-citation-skill")
        self.assertLessEqual(report["metadata"]["description_length"], 1024)
        self.assertIn("claude", report["targets"])
        self.assertIn("openai", report["targets"])
        self.assertTrue((ROOT / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((ROOT / ".agents" / "plugins" / "marketplace.json").is_file())
        self.assertTrue((ROOT / "skills" / "law-citation-skill" / "SKILL.md").is_file())

    def test_repo_marketplace_points_to_plugin_root(self):
        marketplace = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        entry = next(item for item in marketplace["plugins"] if item["name"] == "law-citation-skill")

        self.assertEqual(entry["source"], {"source": "local", "path": "./"})
        self.assertTrue((ROOT / ".codex-plugin" / "plugin.json").is_file())

    def test_runtime_has_no_third_party_python_dependency(self):
        self.assertEqual((ROOT / "requirements.txt").read_text(encoding="utf-8").strip(), "")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("dependencies = []", pyproject)
        self.assertNotIn("lxml", pyproject)

    def test_agent_skill_zip_supports_folder_and_root_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            folder_zip = tmp_path / "law-citation-skill-folder.zip"
            root_zip = tmp_path / "law-citation-skill-root.zip"

            folder_result = build_skill_zip(ROOT, folder_zip, layout="folder")
            root_result = build_skill_zip(ROOT, root_zip, layout="root")

            self.assertEqual(folder_result["top_level"], "law-citation-skill")
            self.assertEqual(root_result["top_level"], None)
            self.assertEqual(folder_result["profile"], "raw")
            self.assertEqual(root_result["profile"], "raw")
            with ZipFile(folder_zip) as z:
                names = z.namelist()
                self.assertIn("law-citation-skill/SKILL.md", names)
                self.assertIn("law-citation-skill/agents/openai.yaml", names)
                self.assertIn("law-citation-skill/LICENSE", names)
                self.assertIn("law-citation-skill/requirements.txt", names)
                self.assertIn("law-citation-skill/scripts/run_pipeline.py", names)
                self.assertIn("law-citation-skill/references/bluebook_rules.md", names)
                self.assertNotIn("law-citation-skill/.agents/plugins/marketplace.json", names)
                self.assertNotIn("law-citation-skill/.codex-plugin/plugin.json", names)
                self.assertNotIn("law-citation-skill/README.md", names)
                self.assertFalse(any(name.startswith("law-citation-skill/.github/") for name in names))
                self.assertFalse(any(name.startswith("law-citation-skill/tests/") for name in names))
                self.assertFalse(any("/dist/" in name or "/build/" in name for name in names))
            with ZipFile(root_zip) as z:
                names = z.namelist()
                self.assertIn("SKILL.md", names)
                self.assertIn("agents/openai.yaml", names)
                self.assertIn("scripts/run_pipeline.py", names)
                self.assertIn("references/bluebook_rules.md", names)
                self.assertNotIn(".agents/plugins/marketplace.json", names)
                self.assertNotIn(".codex-plugin/plugin.json", names)
                self.assertNotIn("README.md", names)

    def test_plugin_profile_zip_includes_codex_distribution_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_zip = tmp_path / "law-citation-skill-plugin.zip"

            result = build_skill_zip(ROOT, plugin_zip, layout="folder", profile="plugin")

            self.assertEqual(result["top_level"], "law-citation-skill")
            self.assertEqual(result["profile"], "plugin")
            with ZipFile(plugin_zip) as z:
                names = z.namelist()
                self.assertIn("law-citation-skill/.agents/plugins/marketplace.json", names)
                self.assertIn("law-citation-skill/.codex-plugin/plugin.json", names)
                self.assertIn("law-citation-skill/skills/law-citation-skill/SKILL.md", names)
                self.assertIn("law-citation-skill/README.md", names)
                self.assertIn("law-citation-skill/PRIVACY.md", names)
                self.assertIn("law-citation-skill/SKILL.md", names)
                self.assertFalse(any(name.startswith("law-citation-skill/tests/") for name in names))


if __name__ == "__main__":
    unittest.main()
