"""Scheduled-task conversation lifecycle: a deleted dedicated conversation
must be recreated on the next trigger, not silently reused (which would make
the runner write results into a nonexistent conversation — FK error, lost
output, user never sees the result).
"""
import uuid

import pytest

from app.db.models.conversation import Conversation
from app.db.models.scheduled import ScheduledTask
from app.db.models.user import User


async def _mk_user(db, email: str) -> User:
    from app.core.security import hash_password
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


async def _mk_task(db, owner: User) -> ScheduledTask:
    t = ScheduledTask(
        id=uuid.uuid4(), owner_id=owner.id, name="每日日报", agent_id="hermes",
        prompt="写日报", cron="0 9 * * *", enabled=True,
    )
    db.add(t)
    await db.flush()
    return t


@pytest.mark.asyncio
async def test_get_or_create_conversation_recreates_after_delete(db):
    """When the task's dedicated conversation was deleted (user cleaned it
    up), the next trigger must create a NEW conversation and repoint the
    task — otherwise results are written into a nonexistent conversation."""
    from agent_runner.runner_scheduled import _get_or_create_conversation

    owner = await _mk_user(db, "sched1@h.io")
    task = await _mk_task(db, owner)

    # First run: creates the dedicated conversation.
    conv_id = await _get_or_create_conversation(db, task, str(owner.id))
    await db.commit()
    assert conv_id is not None
    conv = await db.get(Conversation, conv_id)
    assert conv is not None and conv.type == "scheduled"
    assert task.conversation_id == conv_id

    # User deletes the conversation (e.g. from the chat list).
    await db.delete(conv)
    await db.commit()
    assert await db.get(Conversation, conv_id) is None

    # Next trigger must NOT return the dead id.
    new_conv_id = await _get_or_create_conversation(db, task, str(owner.id))
    await db.commit()
    assert new_conv_id != conv_id, "must create a fresh conversation, not reuse the deleted id"
    new_conv = await db.get(Conversation, new_conv_id)
    assert new_conv is not None and new_conv.type == "scheduled"
    assert task.conversation_id == new_conv_id, "task must be repointed to the new conversation"
