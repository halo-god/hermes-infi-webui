"""Analytics usage endpoint — aggregation correctness + shape."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _mk_convo_with_messages(db, owner_id, n_msgs: int, role: str = "user"):
    from datetime import datetime, timezone
    from app.db.models.conversation import Conversation, Message
    c = Conversation(owner_id=owner_id, title="用量会话", primary_agent_id="hermes")
    db.add(c)
    await db.flush()
    now = datetime.now(timezone.utc)
    for i in range(n_msgs):
        db.add(Message(
            conversation_id=c.id, owner_id=owner_id, role=role,
            content={"text": f"消息{i}"}, status="complete", created_at=now,
        ))
    await db.flush()
    return c


async def test_usage_shape(client, auth_headers, db):
    r = await client.get("/api/v1/analytics/usage", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert {"total_messages", "total_conversations", "tokens_total",
            "messages_by_day", "messages_by_role", "top_agents"} <= set(data.keys())


async def test_usage_counts_own_messages(client, auth_headers, db, test_user):
    """Messages created in this test must be counted; another user's must not."""
    await _mk_convo_with_messages(db, test_user.id, 5, role="user")
    await _mk_convo_with_messages(db, test_user.id, 2, role="agent")

    from app.core.security import hash_password
    from app.db.models.user import User
    other = User(
        id=uuid.uuid4(), email="usage-other@h.io", name="other",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(other)
    await db.flush()
    await _mk_convo_with_messages(db, other.id, 99, role="user")

    r = await client.get("/api/v1/analytics/usage", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total_messages"] >= 7, f"own messages must be counted, got {data}"
    assert data["messages_by_role"].get("user", 0) >= 5
    assert data["messages_by_role"].get("agent", 0) >= 2
    assert data["total_messages"] < 7 + 99, "other user's messages leaked into count"
