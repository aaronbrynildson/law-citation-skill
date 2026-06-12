from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def main() -> int:
    cli = shutil.which("citation-checker")
    if cli is None:
        candidate = Path(sys.executable).with_name("citation-checker")
        cli = str(candidate) if candidate.exists() else "citation-checker"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "smoke.docx"
        dst = tmp_path / "smoke_out.docx"
        with ZipFile(src, "w", ZIP_DEFLATED) as z:
            z.writestr(
                "word/footnotes.xml",
                f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{W}">
  <w:footnote w:id="1"><w:p><w:r><w:t>Id at 45.</w:t></w:r></w:p></w:footnote>
</w:footnotes>""",
            )

        proc = subprocess.run(
            [cli, "--input", str(src), "--output", str(dst)],
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            return proc.returncode
        payload = json.loads(proc.stdout)
        assert payload["footnote_count"] == 1
        assert payload["patch_summary"]["text_changes"] == 0
        assert payload["manual_review_count"] >= 1
        assert dst.exists()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
