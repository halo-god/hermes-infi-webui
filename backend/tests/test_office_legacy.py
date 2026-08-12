"""Legacy Office (.doc/.xls/.ppt) + OpenDocument (.odt/.ods/.odp) extractors.

These tests stub the soffice subprocess — the real LibreOffice binary ships
in the api Docker image (see docker/api.Dockerfile). Covers:

- registration: every legacy/ODF extractor is callable with a single arg
  (regression for the old two-arg extract_legacy_doc_html(raw, ext) which
  made every .doc/.xls/.ppt upload raise TypeError)
- conversion: .xls/.ods → CSV wrapped in an HTML table; .doc/.ppt/.odt/.odp
  → HTML passthrough
- failure paths: soffice missing → user-facing hint; soffice failure →
  None (caller keeps raw bytes); timeout → None
"""
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from app.core import files as files_core
from app.core import office_legacy

LEGACY_EXTS = ("doc", "xls", "ppt", "odt", "ods", "odp")


@pytest.mark.parametrize("ext", LEGACY_EXTS)
def test_legacy_extractors_registered_single_arg(ext):
    """Every legacy/ODF extractor must accept exactly one argument (the raw
    bytes) so all OFFICE_EXTRACTORS[ext](raw) call sites work. With soffice
    missing it must return the hint instead of raising TypeError."""
    extractor = files_core.OFFICE_EXTRACTORS[ext]
    assert callable(extractor)
    with mock.patch.object(office_legacy, "soffice_available", return_value=False):
        out = extractor(b"\xd0\xcf\x11\xe0" * 8)
    assert out is not None
    assert "暂不支持" in out and "另存为" in out


@pytest.mark.parametrize("ext,out_name", [
    ("xls", "input.csv"),
    ("ods", "input.csv"),
    ("doc", "input.html"),
    ("ppt", "input.html"),
    ("odt", "input.html"),
    ("odp", "input.html"),
])
def test_legacy_conversion_output_pipeline(ext, out_name):
    """A successful soffice run must produce the expected output file, and
    the extractor must return the wrapped HTML (table for spreadsheets,
    passthrough for documents/presentations)."""
    def fake_run(cmd, timeout, capture_output):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / out_name).write_text(
            "name,age\nAlice,30\n" if out_name.endswith(".csv") else "<h1>Legacy Doc</h1>",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    with mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         mock.patch.object(office_legacy.subprocess, "run", side_effect=fake_run):
        out = office_legacy.extract_legacy_doc_html(b"fake-content", ext)

    if out_name.endswith(".csv"):
        assert "<table>" in out and "<th>" in out and "Alice" in out
    else:
        assert out == "<h1>Legacy Doc</h1>"


def test_soffice_failure_returns_none():
    """Non-zero exit keeps the raw bytes for download (content stays unset)."""
    with mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         mock.patch.object(
             office_legacy.subprocess, "run",
             return_value=subprocess.CompletedProcess([], 1, b"", b"boom"),
         ):
        assert office_legacy.extract_legacy_doc_html(b"x", "doc") is None


def test_soffice_timeout_returns_none():
    """A hanging conversion must be cut off by the hard timeout, not block."""
    with mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         mock.patch.object(
             office_legacy.subprocess, "run",
             side_effect=subprocess.TimeoutExpired("soffice", 45),
         ):
        assert office_legacy.extract_legacy_doc_html(b"x", "xls") is None


def test_legacy_hint_mentions_each_format():
    for ext in LEGACY_EXTS:
        hint = office_legacy._legacy_hint(ext)  # noqa: SLF001
        assert "暂不支持" in hint


# ── convert_to_pdf (unified PDF preview pipeline) ──────────────────────


def test_convert_to_pdf_success():
    """A successful soffice run must return the produced PDF bytes."""
    def fake_run(cmd, timeout, capture_output):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "input.pdf").write_bytes(b"%PDF-1.4 fake")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    with mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         mock.patch.object(office_legacy.subprocess, "run", side_effect=fake_run):
        pdf = office_legacy.convert_to_pdf(b"fake-bytes", "docx")
    assert pdf == b"%PDF-1.4 fake"


def test_convert_to_pdf_missing_soffice():
    """No soffice -> None (caller degrades to HTML extraction / hint)."""
    with mock.patch.object(office_legacy, "soffice_available", return_value=False):
        assert office_legacy.convert_to_pdf(b"x", "docx") is None


def test_convert_to_pdf_failure_and_timeout():
    with mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         mock.patch.object(
             office_legacy.subprocess, "run",
             return_value=subprocess.CompletedProcess([], 2, b"", b"boom"),
         ):
        assert office_legacy.convert_to_pdf(b"x", "doc") is None
    with mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         mock.patch.object(
             office_legacy.subprocess, "run",
             side_effect=subprocess.TimeoutExpired("soffice", 45),
         ):
        assert office_legacy.convert_to_pdf(b"x", "doc") is None


# ── process_upload office branch (preview_pdf_key wiring) ──────────────


class _FakeStorage:
    """Sync object-storage stub (call sites wrap via asyncio.to_thread)."""

    def __init__(self):
        self.put_calls: list[tuple] = []

    def put(self, key, data, ctype):
        self.put_calls.append((key, data, ctype))

    def get(self, key):
        return b"raw"

    def delete(self, key):
        pass


@pytest.mark.asyncio
async def test_process_upload_office_builds_pdf_preview(monkeypatch):
    """Office uploads get a preview PDF (preview_pdf_key) via soffice while
    `content` keeps the per-format extraction (HTML table/text) — PDF text
    extraction is NEVER used for content (it would destroy table structure);
    `storage_key` keeps the ORIGINAL bytes (download/AI raw reads)."""
    import app.core as core_pkg
    from app.core import object_storage as _real  # noqa: F401 — gain attr first
    from app.core import file_validation, files as files_core

    storage = _FakeStorage()
    monkeypatch.setattr(core_pkg, "object_storage", storage)
    monkeypatch.setattr(file_validation, "validate_upload", lambda raw, ext: None)
    monkeypatch.setattr(
        "app.core.office_legacy.convert_to_pdf", lambda raw, ext: b"%PDF-1.4 fake",
    )
    monkeypatch.setattr(
        files_core, "_extract_doc_content",
        mock.AsyncMock(return_value="<table><tr><td>结构化表格</td></tr></table>"),
    )

    result = await files_core.process_upload(
        b"PK\x03\x04 fake-docx", "docx", "conversations/c1", "a.docx",
    )
    assert result.preview_pdf_key and result.preview_pdf_key.startswith(
        "conversations/c1/previews/"
    )
    assert result.storage_key and result.storage_key != result.preview_pdf_key
    # content = per-format extractor output, NOT PDF text.
    assert "结构化表格" in result.content
    assert len(storage.put_calls) == 2  # original bytes + preview pdf


@pytest.mark.asyncio
async def test_process_upload_csv_skips_pdf_conversion(monkeypatch):
    """csv/rtf never go through soffice: no preview_pdf_key, no soffice call,
    and content keeps the structured CSV table (not flattened PDF text)."""
    import app.core as core_pkg
    from app.core import object_storage as _real  # noqa: F401 — gain attr first
    from app.core import file_validation, files as files_core

    storage = _FakeStorage()
    monkeypatch.setattr(core_pkg, "object_storage", storage)
    monkeypatch.setattr(file_validation, "validate_upload", lambda raw, ext: None)
    convert_calls: list = []
    monkeypatch.setattr(
        "app.core.office_legacy.convert_to_pdf",
        lambda raw, ext: convert_calls.append(ext) or b"%PDF fake",
    )
    monkeypatch.setattr(
        files_core, "_extract_doc_content",
        mock.AsyncMock(return_value="<table><tr><th>名称</th></tr></table>"),
    )

    result = await files_core.process_upload(
        b"name,age\n", "csv", "conversations/c1", "a.csv",
    )
    assert result.preview_pdf_key is None
    assert convert_calls == [], "soffice must not run for csv"
    assert "<table>" in result.content
    assert len(storage.put_calls) == 1  # original bytes only


@pytest.mark.asyncio
async def test_process_upload_office_degrades_without_soffice(monkeypatch):
    """No soffice -> no preview_pdf_key; content falls back to the legacy
    HTML extraction so nothing regresses on bare-metal dev environments."""
    import app.core as core_pkg
    from app.core import object_storage as _real  # noqa: F401 — gain attr first
    from app.core import file_validation, files as files_core

    storage = _FakeStorage()
    monkeypatch.setattr(core_pkg, "object_storage", storage)
    monkeypatch.setattr(file_validation, "validate_upload", lambda raw, ext: None)
    monkeypatch.setattr("app.core.office_legacy.convert_to_pdf", lambda raw, ext: None)
    monkeypatch.setattr(
        files_core, "_extract_doc_content",
        mock.AsyncMock(return_value="<p>HTML FALLBACK</p>"),
    )

    result = await files_core.process_upload(
        b"PK\x03\x04 fake-docx", "docx", "conversations/c1", "a.docx",
    )
    assert result.preview_pdf_key is None
    assert result.content == "<p>HTML FALLBACK</p>"
    assert len(storage.put_calls) == 1  # original bytes only


def test_legacy_csv_filter_uses_utf8_codepage():
    """The soffice CSV export filter must use codepage 65001 (UTF-8) — the
    old 76 mangled CJK text into '?' before it was ever read back."""
    import subprocess as _sp
    from unittest import mock as _mock

    def fake_run(cmd, timeout, capture_output):
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        (outdir / "input.csv").write_text("产品,数量\n笔记本,3\n", encoding="utf-8")
        return _sp.CompletedProcess(cmd, 0, b"", b"")

    with _mock.patch.object(office_legacy, "soffice_available", return_value=True), \
         _mock.patch.object(office_legacy.subprocess, "run", side_effect=fake_run) as m:
        out = office_legacy.extract_legacy_doc_html(b"fake-xls", "xls")
    filter_arg = m.call_args.args[0][m.call_args.args[0].index("--convert-to") + 1]
    assert "65001" in filter_arg, filter_arg
    assert "笔记本" in out, "CJK text must survive the CSV export"
