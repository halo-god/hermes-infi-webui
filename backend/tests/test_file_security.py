"""P2-file: tests for the file-processing security & enhancement layer.

Covers magic-number validation, archive extraction, charset detection, and
EXIF stripping — the pure-logic helpers in app.core.files /
app.core.file_validation that don't need a DB or LLM.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from app.core.file_validation import detect_mime, validate_upload
from app.core.files import _decode_text, _extract_archive, _strip_exif


# ── magic-number validation ──

class TestMagicValidation:
    def test_detect_pdf(self):
        assert detect_mime(b"%PDF-1.4 some content") == "application/pdf"

    def test_detect_exe(self):
        # PE/EXE magic header MZ
        mime = detect_mime(b"MZ\x90\x00" + b"\x00" * 100)
        assert mime is not None and "dosexec" in mime

    def test_valid_pdf_passes(self):
        result = validate_upload(b"%PDF-1.4 content here", "pdf")
        assert result == "application/pdf"

    def test_exe_disguised_as_pdf_rejected(self):
        with pytest.raises(Exception) as exc_info:
            validate_upload(b"MZ\x90\x00" + b"\x00" * 100, "pdf")
        # Should be an HTTPException with a helpful detail
        assert hasattr(exc_info.value, "detail")
        assert "pdf" in exc_info.value.detail.lower() or "不符" in exc_info.value.detail

    def test_text_file_passes(self):
        result = validate_upload(b"hello world python code", "py")
        assert result is not None

    def test_zip_disguised_as_png_rejected(self):
        with pytest.raises(Exception):
            validate_upload(b"PK\x03\x04" + b"\x00" * 50, "png")

    def test_valid_png_passes(self):
        # Use a real minimal PNG so libmagic detects it as image/png, not text.
        from PIL import Image
        import io as _io
        img = Image.new("RGB", (2, 2), "blue")
        buf = _io.BytesIO()
        img.save(buf, "PNG")
        result = validate_upload(buf.getvalue(), "png")
        assert result is not None


# ── charset detection ──

class TestCharsetDetection:
    def test_utf8(self):
        assert _decode_text("hello 你好".encode("utf-8")) == "hello 你好"

    def test_gbk_long_text(self):
        # charset-normalizer is reliable on longer text
        original = "人工智能是计算机科学的一个分支，研究模拟人类智能。" * 3
        decoded = _decode_text(original.encode("gbk"))
        assert "人工智能" in decoded

    def test_big5(self):
        original = "這是一個繁體中文的測試內容。" * 3
        decoded = _decode_text(original.encode("big5"))
        assert "繁體中文" in decoded

    def test_fallback_utf8_ignore_on_garbage(self):
        # Pure binary garbage — should not crash, returns something
        result = _decode_text(b"\xff\xfe\x00\x01\x02")
        assert isinstance(result, str)


# ── archive extraction ──

class TestArchiveExtraction:
    def test_zip_with_text_files(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "说明文件内容")
            zf.writestr("data.json", '{"key": "value"}')
        result = _extract_archive(buf.getvalue(), "zip")
        assert result is not None
        assert "readme.txt" in result
        assert "说明文件内容" in result
        assert '"key": "value"' in result

    def test_zip_with_binary_skipped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("note.md", "# Title")
            zf.writestr("image.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        result = _extract_archive(buf.getvalue(), "zip")
        assert result is not None
        assert "note.md" in result
        # binary png content should not appear
        assert "\\x89" not in result

    def test_path_traversal_member_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../etc/passwd", "malicious")
            zf.writestr("safe.txt", "ok")
        result = _extract_archive(buf.getvalue(), "zip")
        assert result is not None
        assert "safe.txt" in result
        assert "malicious" not in result or "passwd" not in result

    def test_corrupt_zip_returns_none(self):
        assert _extract_archive(b"not a zip file at all", "zip") is None

    def test_empty_zip_returns_none(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass  # no members
        assert _extract_archive(buf.getvalue(), "zip") is None


# ── EXIF stripping ──

class TestExifStripping:
    def test_strip_jpeg(self):
        from PIL import Image
        img = Image.new("RGB", (20, 20), "red")
        buf = io.BytesIO()
        img.save(buf, "JPEG")
        original = buf.getvalue()
        cleaned = _strip_exif(original, "jpg")
        # Should be valid image bytes (possibly smaller or same without EXIF)
        assert len(cleaned) > 0
        # Verify it's still a valid JPEG
        Image.open(io.BytesIO(cleaned)).verify()

    def test_non_image_returns_original(self):
        original = b"plain text content"
        assert _strip_exif(original, "txt") == original

    def test_png_handled(self):
        from PIL import Image
        img = Image.new("RGB", (10, 10), "blue")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        cleaned = _strip_exif(buf.getvalue(), "png")
        assert len(cleaned) > 0
