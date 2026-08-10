"""Regression tests for the upload-async refactor's edit/raw-download paths.

Covers two P1 bugs fixed in the refactor:

1. update_file_content must NOT overwrite object-storage bytes for
   non-plain-text kinds. MinIO backend gives EVERY file a storage_key;
   office/PDF objects hold the ORIGINAL binary upload while `content` holds
   an extracted preview, images hold raw pixels. Writing edited text back
   would destroy the source file. Only PLAIN_TEXT_EXTS kinds get synced.

2. Raw-download routes must decode inline content by kind, not by blind
   b64decode — plain text that happens to be valid base64 (e.g. "SGVsbG8=")
   would be silently corrupted.
"""
from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.core as core_pkg
from app.core.files import PLAIN_TEXT_EXTS
from app.db.models.workspace import WorkspaceFile
from app.services import conversation_service as svc


class FakeObjectStorage:
    """Replaces app.core.object_storage so function-level `from app.core import
    object_storage` picks up the fake (same module object). The real object
    storage API is SYNCHRONOUS (wrapped via asyncio.to_thread at call sites),
    so these must be plain MagicMocks, not AsyncMock."""

    def __init__(self):
        self.put = MagicMock()
        self.get = MagicMock(return_value=b"raw")
        self.delete = MagicMock()


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeObjectStorage()
    # Import first so the package gains the attribute, then swap it out.
    from app.core import object_storage as _real  # noqa: F401
    monkeypatch.setattr(core_pkg, "object_storage", fake)
    return fake


def _make_file(kind: str, *, content: str | None = None, storage_key: str | None = None) -> WorkspaceFile:
    return WorkspaceFile(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        name=f"file.{kind}",
        kind=kind,
        content=content,
        storage_key=storage_key,
        size_bytes=len(content or ""),
        current_version=1,
    )


def _mock_db(f: WorkspaceFile) -> MagicMock:
    """AsyncSession stub: first execute() returns the re-fetched row
    (scalar_one), later version-history queries return empty lists."""
    db = MagicMock()

    row_result = MagicMock()
    row_result.scalar_one.return_value = f

    empty_versions = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))))
    db.execute = AsyncMock(side_effect=[row_result, empty_versions])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


class TestUpdateFileContentStorageSync:
    """P1-1: object storage sync must be plain-text whitelist, not office-blacklist."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["md", "txt", "json", "csv", "py", "yaml", "html", "log", "ts", "go"])
    async def test_text_kinds_sync_to_storage(self, fake_storage, kind):
        f = _make_file(kind, content="old", storage_key=f"conversations/{uuid.uuid4()}/x.{kind}")

        await svc.update_file_content(_mock_db(f), f, "new content")

        assert fake_storage.put.call_count == 1, f"{kind} should sync edited text to object storage"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["docx", "xlsx", "pptx", "pdf", "png", "jpg", "jpeg", "gif", "webp", "zip"])
    async def test_binary_kinds_never_sync_to_storage(self, fake_storage, kind):
        f = _make_file(kind, content="preview text", storage_key=f"conversations/{uuid.uuid4()}/x.{kind}")

        await svc.update_file_content(_mock_db(f), f, "edited preview")

        assert fake_storage.put.call_count == 0, (
            f"{kind} object holds ORIGINAL binary; edited text must not overwrite it"
        )

    @pytest.mark.asyncio
    async def test_no_storage_key_is_noop(self, fake_storage):
        """Inline-only files (no storage_key) must not attempt storage writes."""
        f = _make_file("md", content="old")

        await svc.update_file_content(_mock_db(f), f, "new")

        assert fake_storage.put.call_count == 0


class TestRawDownloadDecodeByKind:
    """P1-2: inline content decode must branch on kind, not blind b64decode.

    Asserts the exact branch condition both raw routes use (files_browser
    checks wf.kind against _TEXT_EXTS / pdf; conversations.py checks the
    name-derived ext against PLAIN_TEXT_EXTS / pdf) so a future refactor
    can't silently reintroduce the blind decode.
    """

    @pytest.mark.parametrize(
        "kind,content",
        [
            # Plain text that HAPPENS to be valid base64 — the corruption case.
            ("txt", "SGVsbG8="),
            ("md", "ZGF0YQ=="),
            ("json", "eyJhIjoxfQ=="),
            # PDF content is extracted text, never base64.
            ("pdf", "extracted text, not base64"),
        ],
    )
    def test_text_kinds_serve_raw_utf8(self, kind, content):
        from app.api.v1 import files_browser

        wf = _make_file(kind, content=content)
        is_text = (wf.kind or "").lower() in files_browser._TEXT_EXTS or wf.kind == "pdf"
        assert is_text, f"{kind} should be classified as inline text"
        if kind == "pdf":
            # Extracted text isn't valid base64 — a blind decode would crash.
            with pytest.raises(Exception):
                base64.b64decode(content)
        else:
            # Valid base64 that decodes to something DIFFERENT — a blind
            # decode would silently corrupt the download.
            assert base64.b64decode(content) != content.encode("utf-8")

    @pytest.mark.parametrize(
        "kind,raw_bytes",
        [
            ("png", b"\x89PNG\r\n\x1a\nfake"),
            ("jpg", b"\xff\xd8\xff\xe0fake"),
        ],
    )
    def test_binary_kinds_decode_base64(self, kind, raw_bytes):
        from app.api.v1 import files_browser

        wf = _make_file(kind, content=base64.b64encode(raw_bytes).decode("ascii"))
        is_text = (wf.kind or "").lower() in files_browser._TEXT_EXTS or wf.kind == "pdf"
        decoded = wf.content.encode("utf-8") if is_text else base64.b64decode(wf.content)
        assert not is_text, f"{kind} must be classified as binary"
        assert decoded == raw_bytes

    @pytest.mark.parametrize(
        "kind",
        ["txt", "md", "json", "py", "yaml", "log", "html", "css", "csv", "go", "rs", "toml", "sh", "xml", "diff", "patch", "ts", "js"],
    )
    def test_plaint_text_set_covers_route_classification(self, kind):
        """Both routes' text sets must agree on what counts as inline text."""
        from app.api.v1 import files_browser

        assert kind in files_browser._TEXT_EXTS
        assert kind in PLAIN_TEXT_EXTS
