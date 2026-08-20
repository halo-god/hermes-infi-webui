from __future__ import annotations

import pytest



@pytest.mark.asyncio
async def test_roundtable_finalize_persists_merge_message_files(db):
    """Merge-phase files must land on the roundtable message (content.files)
    so the workspace chips render — regression for the 2026-08 '综合方观点'
    file-tag/handling gap."""
    from agent_runner.runner_roundtable import _finalize_roundtable

    # Build a roundtable message via the ORM directly (use real commits: the
    # db fixture's outer transaction is invisible to the async_session_maker
    # connection _finalize_roundtable uses).
    from app.db.base import async_session_maker
    from app.db.models.conversation import Conversation, Message
    from app.db.models.user import User
    from app.core.security import hash_password
    import uuid as _uuid

    async with async_session_maker() as s:
        user = User(
            id=_uuid.uuid4(), email=f"rt-{_uuid.uuid4().hex[:8]}@h.io", name="r",
            password_hash=hash_password("Test@1234"), is_active=True, role="member",
        )
        s.add(user)
        await s.commit()
    async with async_session_maker() as s:
        convo = Conversation(
            id=_uuid.uuid4(), owner_id=user.id, title="t", type="personal",
            primary_agent_id="hermes",
        )
        s.add(convo)
        await s.commit()
    async with async_session_maker() as s:
        msg = Message(
            id=_uuid.uuid4(), conversation_id=convo.id, owner_id=user.id,
            role="roundtable", agent_id="hermes", content={"text": ""},
            status="complete",
        )
        s.add(msg)
        await s.commit()
        mid = str(msg.id)

    targets = [{"agent_id": "hermes", "profile_id": None}]
    await _finalize_roundtable(
        mid, targets, ["回答A"], ["complete"], [""], [[]],
        "综合各方观点的结论", "complete", moa=False,
        message_files=[{"id": "f1", "name": "LED简报.md", "kind": "md", "version": 1}],
    )

    from sqlalchemy import select
    async with async_session_maker() as s:
        row = (await s.execute(
            select(Message).where(Message.id == _uuid.UUID(mid))
        )).scalar_one()
        assert row.content["files"] == [
            {"id": "f1", "name": "LED简报.md", "kind": "md", "version": 1},
        ]
        assert row.content["merged"]["text"] == "综合各方观点的结论"
        assert row.content["replies"][0]["files"] == []
