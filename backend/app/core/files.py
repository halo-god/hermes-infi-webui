"""File-safety helpers: bounded upload reads + path-traversal confinement."""
from __future__ import annotations

import os

from fastapi import HTTPException, UploadFile

_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB


def extract_pdf_text(data: bytes) -> str | None:
    """Extract plain text from PDF bytes using PyMuPDF (fitz)."""
    try:
        import pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        parts: list[str] = []
        for page in doc:
            txt = page.get_text()
            if txt:
                parts.append(txt)
        return "\n\n".join(parts) if parts else None
    except Exception:
        return None


def extract_docx_html(raw: bytes) -> str | None:
    """Convert a .docx file's paragraphs/tables to sanitized preview HTML.

    Every text node is escaped before being wrapped in a tag — document
    content can never inject raw HTML/scripts into what's ultimately
    rendered via v-html, matching markdown-it's html:false posture.
    """
    try:
        import io
        from html import escape

        from docx import Document

        doc = Document(io.BytesIO(raw))
        parts: list[str] = []
        list_buf: list[str] = []
        list_tag: str | None = None

        def flush_list() -> None:
            nonlocal list_buf, list_tag
            if list_buf:
                parts.append(f"<{list_tag}>" + "".join(list_buf) + f"</{list_tag}>")
                list_buf = []
                list_tag = None

        heading_map = {f"Heading {i}": f"h{min(i, 6)}" for i in range(1, 7)}

        for para in doc.paragraphs:
            style_name = (para.style.name if para.style else "") or ""
            text_runs = []
            for run in para.runs:
                t = escape(run.text or "")
                if not t:
                    continue
                if run.bold:
                    t = f"<strong>{t}</strong>"
                if run.italic:
                    t = f"<em>{t}</em>"
                if run.underline:
                    t = f"<u>{t}</u>"
                text_runs.append(t)
            text = "".join(text_runs) or escape(para.text or "")
            if not text.strip():
                continue

            if style_name in heading_map:
                flush_list()
                tag = heading_map[style_name]
                parts.append(f"<{tag}>{text}</{tag}>")
            elif style_name.startswith("List Bullet"):
                if list_tag != "ul":
                    flush_list()
                    list_tag = "ul"
                list_buf.append(f"<li>{text}</li>")
            elif style_name.startswith("List Number"):
                if list_tag != "ol":
                    flush_list()
                    list_tag = "ol"
                list_buf.append(f"<li>{text}</li>")
            else:
                flush_list()
                parts.append(f"<p>{text}</p>")
        flush_list()

        for table in doc.tables:
            rows_html = []
            for i, row in enumerate(table.rows):
                cell_tag = "th" if i == 0 else "td"
                cells = "".join(f"<{cell_tag}>{escape(c.text)}</{cell_tag}>" for c in row.cells)
                rows_html.append(f"<tr>{cells}</tr>")
            parts.append("<table>" + "".join(rows_html) + "</table>")

        return "\n".join(parts) if parts else "<p><em>(空文档)</em></p>"
    except Exception:
        return None


_XLSX_MAX_ROWS = 500
_XLSX_MAX_COLS = 50


def extract_xlsx_html(raw: bytes) -> str | None:
    """Convert an .xlsx workbook to sanitized per-sheet preview HTML tables."""
    try:
        import io
        from html import escape

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        parts: list[str] = []
        for ws in wb.worksheets:
            parts.append(f"<h3>{escape(ws.title)}</h3>")
            rows_html = []
            truncated_cols = False
            row_count = 0
            for row in ws.iter_rows():
                if row_count >= _XLSX_MAX_ROWS:
                    parts.append(f"<p><em>(仅显示前 {_XLSX_MAX_ROWS} 行，已截断)</em></p>")
                    break
                cells = row[:_XLSX_MAX_COLS]
                if len(row) > _XLSX_MAX_COLS:
                    truncated_cols = True
                cell_html = "".join(
                    f"<td>{escape(str(c.value)) if c.value is not None else ''}</td>" for c in cells
                )
                rows_html.append(f"<tr>{cell_html}</tr>")
                row_count += 1
            parts.append("<table>" + "".join(rows_html) + "</table>")
            if truncated_cols:
                parts.append(f"<p><em>(仅显示前 {_XLSX_MAX_COLS} 列，已截断)</em></p>")
        wb.close()
        return "\n".join(parts) if parts else "<p><em>(空工作簿)</em></p>"
    except Exception:
        return None


def extract_pptx_html(raw: bytes) -> str | None:
    """Convert a .pptx presentation's slide text to sanitized preview HTML."""
    try:
        import io
        from html import escape

        from pptx import Presentation

        prs = Presentation(io.BytesIO(raw))
        parts: list[str] = []
        for i, slide in enumerate(prs.slides, start=1):
            parts.append(f'<div class="slide"><h4>Slide {i}</h4>')
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                for para in shape.text_frame.paragraphs:
                    text = "".join(escape(run.text or "") for run in para.runs) or escape(para.text or "")
                    if text.strip():
                        parts.append(f"<p>{text}</p>")
            parts.append("</div>")
        return "\n".join(parts) if parts else "<p><em>(空演示文稿)</em></p>"
    except Exception:
        return None


def extract_csv_html(raw: bytes) -> str | None:
    """Convert CSV bytes to an HTML table preview (first 200 rows)."""
    try:
        import io
        import csv
        from html import escape

        text = raw.decode("utf-8", "ignore")
        reader = csv.reader(io.StringIO(text))
        parts: list[str] = ["<table>"]
        row_count = 0
        for row in reader:
            if row_count >= 200:
                parts.append("<p><em>(仅显示前 200 行，已截断)</em></p>")
                break
            tag = "th" if row_count == 0 else "td"
            cells = "".join(f"<{tag}>{escape(c)}</{tag}>" for c in row)
            parts.append(f"<tr>{cells}</tr>")
            row_count += 1
        parts.append("</table>")
        return "\n".join(parts) if row_count > 0 else None
    except Exception:
        return None


def extract_rtf_html(raw: bytes) -> str | None:
    """Extract plain text from RTF and wrap in <p> tags."""
    try:
        import re
        text = raw.decode("utf-8", "ignore")
        # Strip RTF control words and groups
        text = re.sub(r"\\'[0-9a-fA-F]{2}", "", text)
        text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", "", text)
        text = re.sub(r"[{}]", "", text)
        text = re.sub(r"\\\*", "", text)
        text = re.sub(r"\\\n", "\n", text)
        text = text.strip()
        if not text:
            return None
        from html import escape
        paragraphs = [f"<p>{escape(p)}</p>" for p in text.split("\n\n") if p.strip()]
        return "\n".join(paragraphs) if paragraphs else None
    except Exception:
        return None


OFFICE_EXTRACTORS = {
    "docx": extract_docx_html,
    "xlsx": extract_xlsx_html,
    "pptx": extract_pptx_html,
    "csv": extract_csv_html,
    "rtf": extract_rtf_html,
}


def is_text_extractable(kind: str) -> bool:
    """Return True for file kinds we can extract human-readable text from."""
    return kind.lower() in {
        "md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
        "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch", "pdf",
        "docx", "xlsx", "pptx", "rtf",
    }


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile fully, but abort with HTTP 413 once it exceeds max_bytes.

    Reads in chunks so an oversized upload can't balloon memory before the
    limit is hit (``await file.read()`` would buffer the whole body first).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大，上限 {max_bytes // (1024 * 1024)}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def safe_relative_path(name: str, fallback: str = "untitled.txt") -> str:
    """Normalize a user/agent-supplied path to a contained relative path.

    Anchors the path at root before normalizing so ``../`` segments can never
    climb above it (``a/../../b`` → ``b``, ``../../etc/passwd`` → ``etc/passwd``),
    then strips the leading separator. Never raises — preserves valid nested
    paths like ``src/main.py`` for folder support.
    """
    candidate = (name or "").replace("\\", "/").strip()
    normalized = os.path.normpath("/" + candidate).lstrip("/")
    return normalized.replace(os.sep, "/") or fallback


def confine_to_dir(base_dir: str, relative: str) -> str:
    """Join base_dir + a (pre-normalized) relative path and assert containment.

    Defense in depth after ``safe_relative_path``: resolves symlinks and rejects
    any result that escapes base_dir. Raises HTTP 400 on escape.
    """
    base_real = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_real, relative))
    if target != base_real and not target.startswith(base_real + os.sep):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return target
