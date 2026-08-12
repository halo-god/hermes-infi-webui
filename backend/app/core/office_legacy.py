"""Legacy Office formats (.doc/.xls/.ppt) + OpenDocument (.odt/.ods/.odp)
via LibreOffice headless — optional.

The OLE2 binary formats and OpenDocument family have no reliable
pure-Python reader in this pipeline, so we probe for `soffice`
(LibreOffice) at runtime. When present, uploads convert through it
(headless, isolated profile, hard timeout). When absent, the extractor
returns a clear "convert to docx/xlsx" hint instead of a silently
unreadable file.

Deliberately NOT wired into the fast path for docx/xlsx/pptx — the
pure-Python extractors are faster, lighter and cleaner for those.
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
import threading

logger = logging.getLogger(__name__)

_soffice_path: str | None = None
_soffice_checked = False

_SOFFICE_TIMEOUT = 45  # seconds; damaged files can hang the conversion
# Each soffice conversion peaks at 300-800MB RAM — cap concurrent conversions
# so parallel uploads can't stack processes on a memory-limited container.
# A threading lock (not asyncio) works here because extractors run in worker
# threads via asyncio.to_thread, and it also covers sync paths (content
# hydration, retry) that never touch the event loop.
_SOFFICE_SLOTS = threading.BoundedSemaphore(2)


def soffice_available() -> bool:
    """Probe for a usable LibreOffice binary (cached).

    The cache can be invalidated with HERMES_SOFFICE_RECHECK=1 (e.g. the
    container started before LibreOffice was installed) — otherwise the
    result is cached for the process lifetime.
    """
    global _soffice_path, _soffice_checked
    if not _soffice_checked or os.environ.get("HERMES_SOFFICE_RECHECK") == "1":
        _soffice_path = shutil.which("soffice") or shutil.which("libreoffice")
        _soffice_checked = True
    return _soffice_path is not None


def convert_to_pdf(raw: bytes, ext: str) -> bytes | None:
    """Convert any Office/ODF file to PDF bytes via soffice (preview pipeline).

    Returns the PDF bytes, or None when soffice is missing or the conversion
    fails (timeout, corrupt file, non-zero exit). Same isolation profile /
    hard timeout / concurrency slot as the HTML extractor above.
    """
    if not soffice_available():
        return None
    with tempfile.TemporaryDirectory(prefix="hermes-office-") as td:
        src = os.path.join(td, f"input.{ext}")
        with open(src, "wb") as fh:
            fh.write(raw)
        profile = f"file://{os.path.join(td, 'profile')}"
        try:
            with _SOFFICE_SLOTS:
                result = subprocess.run(
                    [
                        _soffice_path,
                        "--headless", "--norestore", "--nolockcheck",
                        "--convert-to", "pdf",
                        "--outdir", td,
                        f"-env:UserInstallation={profile}",
                        src,
                    ],
                    timeout=_SOFFICE_TIMEOUT,
                    capture_output=True,
                )
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("soffice pdf conversion timed out/failed for .%s", ext)
            return None
        if result.returncode != 0:
            logger.warning("soffice pdf conversion failed (rc=%s): %s", result.returncode, result.stderr[:200])
            return None
        out_path = os.path.join(td, "input.pdf")
        if not os.path.isfile(out_path):
            return None
        try:
            with open(out_path, "rb") as fh:
                return fh.read()
        except OSError:
            return None


def _legacy_hint(ext: str) -> str:
    pretty = {
        "doc": "Word 97-2003 (.doc)", "xls": "Excel 97-2003 (.xls)",
        "ppt": "PowerPoint 97-2003 (.ppt)",
        "odt": "OpenDocument 文本 (.odt)", "ods": "OpenDocument 表格 (.ods)",
        "odp": "OpenDocument 演示 (.odp)",
    }.get(ext, ext)
    return (
        f'<p><em style="color:#c0392b">⚠ 暂不支持 {pretty} 格式</em></p>'
        "<p>服务器未安装 LibreOffice（soffice），无法解析该旧版 Office 二进制格式。</p>"
        "<p>解决方法：请将文件另存为 <code>docx</code> / <code>xlsx</code> / <code>pptx</code> 后重新上传。</p>"
    )


def extract_legacy_doc_html(raw: bytes, ext: str) -> str | None:
    """Convert a legacy/OpenDocument office file via soffice to preview HTML.

    - .xls / .ods → CSV text, wrapped into an HTML table preview
    - .doc / .ppt / .odt / .odp → HTML (StarWriter) with embedded images
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
        # Spreadsheets → CSV (tabular text); documents/presentations → single
        # -file HTML with embedded images.
        if ext in ("xls", "ods"):
            # Character-set token (3rd) is 65001 = UTF-8. The old 76 was a
            # legacy code page that mangled CJK text into "?" before we ever
            # read the CSV back.
            out_filter = "csv:Text - txt - csv (StarCalc):44,34,65001,1,,0,false,true,true"
            out_name = "input.csv"
        else:
            out_filter = "html:HTML (StarWriter):EmbedImages"
            out_name = "input.html"

        try:
            with _SOFFICE_SLOTS:
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

        if ext in ("xls", "ods"):
            return _csv_to_table(content)
        return content.strip() or None


def _csv_to_table(csv_text: str) -> str:
    """Wrap soffice CSV output into a sanitized HTML table preview."""
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(csv_text)):
        rows.append([_html.escape(c) for c in row])
        if len(rows) >= 500:
            # Cap like extract_xlsx_html — an unbounded table would balloon
            # the DB row and freeze the preview.
            rows.append(["… 仅显示前 500 行，请下载原文件查看完整内容"])
            break
    if not rows:
        return "<p><em>(空表格)</em></p>"
    parts = ["<table>"]
    for i, row in enumerate(rows):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{c or ''}</{tag}>" for c in row)
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "\n".join(parts)
