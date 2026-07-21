"""File-safety helpers: bounded upload reads + path-traversal confinement."""
from __future__ import annotations

import asyncio
import base64
import os
import uuid
from dataclasses import dataclass

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


_DOCX_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
}


def _extract_images_from_drawing(
    drawing_elem,
    image_map: dict[str, str],
    text_runs: list[str],
) -> None:
    """Find all <a:blip r:embed="rId..."> inside a <w:drawing> and append
    base64 <img> tags to text_runs."""
    for blip in drawing_elem.iter():
        tag = blip.tag.split("}")[-1] if "}" in blip.tag else blip.tag
        if tag != "blip":
            continue
        embed_attr = None
        for attr_name, attr_val in blip.attrib.items():
            if attr_name.endswith("}embed") or attr_name == "embed":
                embed_attr = attr_val
                break
        if embed_attr and embed_attr in image_map:
            text_runs.append(
                f'<img src="{image_map[embed_attr]}" style="max-width:100%;height:auto;border-radius:6px;margin:6px 0" />'
            )


def extract_docx_html(raw: bytes) -> str | None:
    """Convert a .docx file to sanitized preview HTML.

    Iterates the document body in true document order (paragraphs AND tables
    interleaved via XML iteration, not separate loops). Extracts embedded
    images as base64 data URIs. Every text node is escaped before being
    wrapped in a tag - document content can never inject raw HTML/scripts.
    """
    try:
        import io
        import base64
        from html import escape

        from docx import Document
        from docx.oxml.ns import qn

        doc = Document(io.BytesIO(raw))
        body = doc.element.body

        # Collect image relationship IDs -> base64 data URIs
        image_map: dict[str, str] = {}
        for rel_id, rel in doc.part.rels.items():
            if "image" in rel.reltype:
                try:
                    image_data = rel.target_part.blob
                    content_type = rel.target_part.content_type
                    b64 = base64.b64encode(image_data).decode("ascii")
                    image_map[rel_id] = f"data:{content_type};base64,{b64}"
                except Exception:
                    pass

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

        def process_paragraph(p_elem) -> None:
            """Process a single <w:p> element, extracting text runs + images."""
            # Find the corresponding python-docx Paragraph object
            para = None
            for dp in doc.paragraphs:
                if dp._element is p_elem:
                    para = dp
                    break
            if para is None:
                return

            style_name = (para.style.name if para.style else "") or ""
            text_runs = []

            # Walk all runs, extracting both text and inline images
            for run in para.runs:
                # Check for inline images in this run's XML
                for drawing in run._element.findall(qn("w:drawing")):
                    _extract_images_from_drawing(drawing, image_map, text_runs)
                t = escape(run.text or "")
                if t:
                    if run.bold:
                        t = f"<strong>{t}</strong>"
                    if run.italic:
                        t = f"<em>{t}</em>"
                    if run.underline:
                        t = f"<u>{t}</u>"
                    text_runs.append(t)

            text = "".join(text_runs) or escape(para.text or "")
            if not text.strip():
                return

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

        def process_table(tbl_elem) -> None:
            """Process a <w:tbl> element into an HTML table."""
            flush_list()
            rows_html = []
            for i, row in enumerate(tbl_elem.findall(qn("w:tr"))):
                cell_tag = "th" if i == 0 else "td"
                cells_xml = row.findall(qn("w:tc"))
                cell_parts = []
                for tc in cells_xml:
                    cell_text_parts = []
                    # Extract text from all paragraphs in the cell
                    for p in tc.findall(qn("w:p")):
                        p_texts = []
                        for r in p.findall(qn("w:r")):
                            for t in r.findall(qn("w:t")):
                                if t.text:
                                    p_texts.append(escape(t.text))
                        if p_texts:
                            cell_text_parts.append(" ".join(p_texts))
                    cell_parts.append(f"<{cell_tag}>{'<br/>'.join(cell_text_parts) or ''}</{cell_tag}>")
                rows_html.append(f"<tr>{''.join(cell_parts)}</tr>")
            if rows_html:
                parts.append("<table>" + "".join(rows_html) + "</table>")

        # Iterate body children in document order (paragraphs + tables interleaved)
        for child in body:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "p":
                process_paragraph(child)
            elif tag == "tbl":
                process_table(child)
        flush_list()

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

        # Validate: .xlsx must be a ZIP archive (PK\x03\x04 magic).
        if len(raw) < 4 or raw[:4] != b"PK\x03\x04":
            # If it looks like HTML/text, the agent likely wrote preview markup
            # into a .xlsx filename instead of the real binary. Surface this
            # clearly so the user / agent knows what went wrong.
            preview = raw[:200].decode("utf-8", "ignore").strip()
            return (
                '<p><em style="color:#c0392b">⚠ 文件格式错误：该文件不是有效的 .xlsx 工作簿。</em></p>'
                '<p>可能原因：AI 助手在生成文件时将 HTML 预览写入了 <code>.xlsx</code> 扩展名，'
                '而非真正的 Excel 二进制内容。</p>'
                '<p>解决方法：请使用 <code>write_file</code> 工具生成 <code>.md</code> 或 <code>.txt</code> 文件，'
                '或者上传真实的 .xlsx 文件。</p>'
                f'<pre style="background:#f8f9fa;padding:8px;border-radius:4px;font-size:12px">{escape(preview)}</pre>'
            )

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


PLAIN_TEXT_EXTS = frozenset({
    "md", "txt", "json", "csv", "html", "htm", "js", "ts", "py", "go", "rs",
    "yaml", "yml", "toml", "sh", "bash", "log", "xml", "css", "diff", "patch",
})


def is_text_extractable(kind: str) -> bool:
    """Return True for file kinds we can extract human-readable text from."""
    kind = kind.lower()
    return kind in PLAIN_TEXT_EXTS or kind == "pdf" or kind in OFFICE_EXTRACTORS


@dataclass
class ProcessedUpload:
    content: str | None
    storage_key: str | None
    size_bytes: int


async def process_upload(
    raw: bytes,
    ext: str,
    storage_key_prefix: str,
    name: str,
    content_type: str | None = None,
) -> ProcessedUpload:
    """Decide how to store + extract an uploaded file's bytes.

    Single source of truth for "large vs. small file" handling, shared by
    every upload endpoint (conversation attachments, personal file storage,
    team knowledge base, project docs) so they no longer each reinvent (and
    subtly diverge on) this decision:

    - Office docs (docx/xlsx/pptx/csv/rtf): `content` holds extracted preview
      HTML, not the raw bytes, so the raw bytes always go to object storage
      regardless of size — otherwise the "download original" route would
      have nothing but HTML to serve back.
    - Anything else bigger than settings.file_offload_threshold_kb, or when
      the storage backend is minio: raw bytes go to object storage; text or
      PDF content is still extracted (best-effort) for prompt injection.
    - Everything else (small, non-office): inlined directly — text types
      decoded as-is, PDFs text-extracted, everything else base64.

    `storage_key_prefix` is the caller's namespace (e.g. "conversations/{id}"
    or "team-knowledge/{id}"); the object key becomes
    "{storage_key_prefix}/{uuid}/{name}" so the original filename/extension
    stays visible when browsing the bucket directly.
    """
    from app.config import settings
    from app.core import object_storage

    ext = ext.lower()
    ctype = content_type or "application/octet-stream"
    threshold_bytes = settings.file_offload_threshold_kb * 1024
    storage_key: str | None = None
    content: str | None = None

    if ext in OFFICE_EXTRACTORS:
        storage_key = f"{storage_key_prefix}/{uuid.uuid4().hex}/{name}"
        try:
            await asyncio.to_thread(object_storage.put, storage_key, raw, ctype)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="文件预览服务不可用，请检查对象存储配置"
            ) from exc
        # Office extraction (zipfile + XML parse + base64 images) is CPU-bound;
        # offload to a thread so it doesn't block the event loop and stall
        # concurrent SSE token streams.
        content = await asyncio.to_thread(OFFICE_EXTRACTORS[ext], raw) or "<p><em>(无法解析文档内容)</em></p>"
    elif len(raw) > threshold_bytes or settings.storage_backend == "minio":
        storage_key = f"{storage_key_prefix}/{uuid.uuid4().hex}/{name}"
        await asyncio.to_thread(object_storage.put, storage_key, raw, ctype)
        if ext == "pdf":
            content = await asyncio.to_thread(extract_pdf_text, raw)
        elif ext in PLAIN_TEXT_EXTS:
            content = raw.decode("utf-8", "ignore")
    else:
        if ext in PLAIN_TEXT_EXTS:
            content = raw.decode("utf-8", "ignore")
        elif ext == "pdf":
            content = await asyncio.to_thread(extract_pdf_text, raw)
        else:
            content = base64.b64encode(raw).decode("ascii")

    return ProcessedUpload(content=content, storage_key=storage_key, size_bytes=len(raw))


async def hydrate_stored_content(
    kind: str | None,
    storage_key: str | None,
    inline_content: str | None,
) -> str | None:
    """Resolve knowledge/doc content from inline text or object storage.

    Consolidates the "if content is None and storage_key: fetch from storage
    and extract" pattern that was duplicated across teams.py and
    conversation_service.py. Returns ``None`` if no content is available.
    """
    if inline_content is not None:
        return inline_content
    if not storage_key or not kind:
        return None

    from app.core import object_storage

    data = await asyncio.to_thread(object_storage.get, storage_key)
    ext = kind.lower()
    if ext in OFFICE_EXTRACTORS:
        return OFFICE_EXTRACTORS[ext](data) or None
    if is_text_extractable(ext):
        return data.decode("utf-8", "ignore")
    return None


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
