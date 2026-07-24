"""Tests for the 3 file-handling bug fixes: watcher attachments/ ignore,
process_upload fast_mode, and save_file user-file protection.
"""
from __future__ import annotations



class TestWatcherIgnoresAttachments:
    """问题3: WorkspaceWatcher must ignore the attachments/ directory."""

    def test_attachments_path_ignored(self):
        from agent_runner.workspace_watcher import _should_ignore
        assert _should_ignore("attachments/report.pdf") is True
        assert _should_ignore("attachments/sub/deep.txt") is True

    def test_attachments_absolute_path_ignored(self):
        """watchdog gives absolute paths — must still match attachments/."""
        from agent_runner.workspace_watcher import _should_ignore
        assert _should_ignore("/tmp/workspace/conv123/attachments/report.pdf") is True
        assert _should_ignore("/var/work/c/attachments/a/b/c.txt") is True

    def test_normal_files_not_ignored(self):
        from agent_runner.workspace_watcher import _should_ignore
        assert _should_ignore("output.md") is False
        assert _should_ignore("src/main.py") is False
        assert _should_ignore("notes.txt") is False

    def test_temp_files_still_ignored(self):
        from agent_runner.workspace_watcher import _should_ignore
        assert _should_ignore("file.tmp") is True
        assert _should_ignore(".hidden") is True


class TestProcessUploadFastMode:
    """问题1: fast_mode skips Docling for chat attachments."""

    async def test_fast_mode_skips_docling(self, monkeypatch):
        """In fast_mode, _extract_doc_content should NOT call Docling."""
        from app.core import files
        from app.core import file_validation
        from app.config import settings

        docling_called = []

        async def fake_extract(raw, ext, *, prefer_docling=True):
            docling_called.append(prefer_docling)
            return "fast text"

        monkeypatch.setattr(files, "_extract_doc_content", fake_extract)
        monkeypatch.setattr(file_validation, "validate_upload", lambda *a, **kw: None)
        monkeypatch.setattr(settings, "storage_backend", "db")
        monkeypatch.setattr(settings, "strip_exif_enabled", False)

        await files.process_upload(b"fake pdf", "pdf", "test", "f.pdf", fast_mode=True)
        assert docling_called == [False], f"Expected [False], got {docling_called}"

    async def test_normal_mode_uses_docling(self, monkeypatch):
        """Without fast_mode, Docling is preferred (for knowledge base)."""
        from app.core import files
        from app.core import file_validation
        from app.config import settings

        docling_called = []
        async def fake_extract(raw, ext, *, prefer_docling=True):
            docling_called.append(prefer_docling)
            return "docling text"

        monkeypatch.setattr(files, "_extract_doc_content", fake_extract)
        monkeypatch.setattr(file_validation, "validate_upload", lambda *a, **kw: None)
        monkeypatch.setattr(settings, "storage_backend", "db")
        monkeypatch.setattr(settings, "strip_exif_enabled", False)

        await files.process_upload(b"fake pdf", "pdf", "test", "f.pdf")
        assert docling_called == [True], f"Expected [True], got {docling_called}"


class TestSaveFileUserProtection:
    """问题2: agent writing a user-uploaded file creates an _edited copy."""

    async def test_agent_creates_copy_for_user_file(self):
        """When an agent writes a file that was uploaded by a user
        (created_by_agent=None), it should create an _edited copy."""
        from agent_runner import storage
        from app.db.base import async_session_maker
        from app.db.models.workspace import WorkspaceFile
        from app.db.models.conversation import Conversation
        from app.db.models.user import User
        from app.core.security import hash_password
        from sqlalchemy import delete
        import uuid as _uuid

        uid = _uuid.uuid4()
        cid = _uuid.uuid4()
        async with async_session_maker() as s:
            u = User(id=uid, email=f"fp{_uuid.uuid4().hex[:6]}@t.com", name="fp",
                     password_hash=hash_password("Test@1234"), is_active=True, role="member")
            s.add(u)
            await s.flush()
            c = Conversation(id=cid, title="fp", owner_id=u.id,
                             primary_agent_id="hermes", active_agent_ids=["hermes"])
            s.add(c)
            await s.flush()
            user_file = WorkspaceFile(
                conversation_id=cid, name="report.pdf", kind="pdf",
                content="original user content", size_bytes=22,
                created_by_agent=None, current_version=1,
            )
            s.add(user_file)
            await s.commit()

        # Agent writes same file
        result = await storage.save_file(cid, "report.pdf", "agent modified content", "hermes")

        assert "_edited" in result.name
        assert result.created_by_agent == "hermes"

        # Verify original unchanged
        async with async_session_maker() as s:
            orig = await s.get(WorkspaceFile, user_file.id)
            assert orig.content == "original user content"
            assert orig.current_version == 1

        # Cleanup
        async with async_session_maker() as s:
            await s.execute(delete(WorkspaceFile).where(WorkspaceFile.conversation_id == cid))
            await s.execute(delete(Conversation).where(Conversation.id == cid))
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()

    async def test_agent_can_overwrite_own_file(self):
        """When an agent writes a file it previously created, version
        increments normally (no _edited copy)."""
        from agent_runner import storage
        from app.db.base import async_session_maker
        from app.db.models.workspace import WorkspaceFile
        from app.db.models.conversation import Conversation
        from app.db.models.user import User
        from app.core.security import hash_password
        from sqlalchemy import delete
        import uuid as _uuid

        uid = _uuid.uuid4()
        cid = _uuid.uuid4()
        async with async_session_maker() as s:
            u = User(id=uid, email=f"fp2{_uuid.uuid4().hex[:6]}@t.com", name="fp2",
                     password_hash=hash_password("Test@1234"), is_active=True, role="member")
            s.add(u)
            await s.flush()
            c = Conversation(id=cid, title="fp2", owner_id=u.id,
                             primary_agent_id="hermes", active_agent_ids=["hermes"])
            s.add(c)
            await s.flush()
            agent_file = WorkspaceFile(
                conversation_id=cid, name="summary.md", kind="md",
                content="v1 content", size_bytes=10,
                created_by_agent="hermes", current_version=1,
            )
            s.add(agent_file)
            await s.commit()

        result = await storage.save_file(cid, "summary.md", "v2 content", "hermes")
        assert result.name == "summary.md"
        assert result.current_version == 2

        # Cleanup
        async with async_session_maker() as s:
            await s.execute(delete(WorkspaceFile).where(WorkspaceFile.conversation_id == cid))
            await s.execute(delete(Conversation).where(Conversation.id == cid))
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()
