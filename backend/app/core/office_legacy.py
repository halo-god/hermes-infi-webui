"""Legacy Office formats (.doc/.xls/.ppt) via LibreOffice headless — optional.

These OLE2 binary formats have no reliable pure-Python reader, so we probe for
`soffice` (LibreOffice) at runtime. When present, uploads convert through it
(headless, isolated profile, hard timeout). When absent, the extractor returns
a clear "convert to docx/xlsx" hint instead of a silently unreadable file.

Deliberately NOT wired into the fast path for docx/xlsx/pptx — the pure-Python
extractors are faster, lighter and cleaner for those.
"""
from __future__ import annotations

import csv
import html as _html
import io
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_soffice_path: str | None = None
_soffice_checked = False

_SOFFICE_TIMEOUT = 45  # seconds; damaged files can hang the conversion


def soffice_available() -> bool:
    """Probe for a usable LibreOffice binary (cached)."""
    global _soffice_path, _soffice_checked
    if not _soffice_checked:
        _soffice_path = shutil.which("soffice") or shutil.which("libreoffice")
        _soffice_checked = True
    return _soffice_path is not None


def _legacy_hint(ext: str) -> str:
    pretty = {"doc": "Word 97-2003 (.doc)", "xls": "Excel 97-2003 (.xls)", "ppt": "PowerPoint 97-2003 (.ppt)"}.get(ext, ext)
    return (
        f'<p><em style="color:#c0392b">⚠ 暂不支持 {pretty} 格式</em></p>'
        "<p>服务器未安装 LibreOffice（soffice），无法解析该旧版 Office 二进制格式。</p>"
        "<p>解决方法：请将文件另存为 <code>docx</code> / <code>xlsx</code> / <code>pptx</code> 后重新上传。</p>"
    )


def extract_legacy_doc_html(raw: bytes, ext: str) -> str | None:
    """Convert a legacy office file via soffice to preview HTML.

    - .xls → CSV text, wrapped into an HTML table preview
    - .doc / .ppt → HTML (StarWriter) with embedded images
    Each conversion uses an isolated temporary profile and a hard timeout;
    failures return None (the caller keeps the raw bytes for download).
    """
    if not soffice_available():
        return _legacy_hint(ext)

    with tempfile.TemporaryDirectory(prefix="hermes-office-") as td:
        src = os.path.join(td, f"input.{ext}")
        with open(src, "wb") as fh:
            fh.write(raw)
        profile = f"file://{os.path.join(td, 'profile')}"
        # .xls → CSV (tabular text); .doc/.ppt → single-file HTML with images
        if ext == "xls":
            out_filter = "csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,true"
            out_name = "input.csv"
        else:
            out_filter = "html:HTML (StarWriter):EmbedImages"
            out_name = "input.html"

        try:
            result = subprocess.run(
                [
                    _soffice_path,
                    "--headless", "--norestore", "--nolockcheck",
                    "--convert-to", out_filter,
                    "--outdir", td,
                    f"-env:UserInstallation={profile}",
                    src,
                ],
                timeout=_SOFFICE_TIMEOUT,
                capture_output=True,
            )
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("soffice conversion timed out/failed for .%s", ext)
            return None
        if result.returncode != 0:
            logger.warning("soffice conversion failed (rc=%s): %s", result.returncode, result.stderr[:200])
            return None

        out_path = os.path.join(td, out_name)
        if not os.path.isfile(out_path):
            return None
        try:
            with open(out_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            return None

        if ext == "xls":
            return _csv_to_table(content)
        return content.strip() or None


def _csv_to_table(csv_text: str) -> str:
    """Wrap soffice CSV output into a sanitized HTML table preview."""
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(csv_text)):
        rows.append([_html.escape(c) for c in row])
    if not rows:
        return "<p><em>(空表格)</em></p>"
    parts = ["<table>"]
    for i, row in enumerate(rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{c or ''}</{tag}>" for c in row)
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "\n".join(parts)
