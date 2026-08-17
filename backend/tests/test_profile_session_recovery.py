"""Profile session recovery logic (commit 1f2a625) — targeted unit tests.

Verifies the cross-layer contract that makes profile session RECOVERY work:
runner._set_session_id writes `acp_session:{conv}:{profile}[:stage]` into Redis,
and conversation_service._has_acp_session probes that exact key to decide
first-turn vs follow-up. If the two key formats drift, every profile turn
looks like the first turn (clarify preamble re-injected every time) — the bug
class 1f2a625 fixed for row-column sessions but must hold for Redis-scoped ones.

Runs against the dedicated test Redis (127.0.0.1:6380) enforced by conftest.
"""
import uuid

import pytest

from app.core import redis as redis_core
from app.services.conversation_service import _has_acp_session


def _key(conversation_id, profile_id, stage=None):
    suffix = f":{stage}" if stage else ""
    return f"acp_session:{conversation_id}:{profile_id}{suffix}"


@pytest.mark.asyncio
async def test_no_profile_id_is_not_first_turn_probe():
    """Without a profile_id, _has_acp_session must return False (never blocks
    the clarify preamble on profile-less conversations — legacy behavior)."""
    assert await _has_acp_session(uuid.uuid4(), None, None) is False
    assert await _has_acp_session(uuid.uuid4(), "", None) is False


@pytest.mark.asyncio
async def test_key_format_matches_runner_set_session_id():
    """The probe key must byte-for-byte match what runner._set_session_id writes:
    acp_session:{conv}:{profile} (and :{stage} suffix when staged)."""
    conv = uuid.uuid4()
    pid = uuid.uuid4()
    r = redis_core.get_redis()
    # Simulate runner._set_session_id writing a session for this profile turn.
    key = _key(conv, pid)
    await r.set(key, "acp_session_token_abc", ex=3600 * 24 * 7)
    try:
        assert await _has_acp_session(conv, str(pid), None) is True, (
            "probe must find session written by runner (same key)"
        )
    finally:
        await r.delete(key)


@pytest.mark.asyncio
async def test_stage_suffix_isolation():
    """A staged session lives under acp_session:{conv}:{profile}:{stage} — the
    base (unstaged) probe must NOT see it, and the staged probe MUST."""
    conv = uuid.uuid4()
    pid = uuid.uuid4()
    r = redis_core.get_redis()
    await r.set(_key(conv, pid, "rag"), "token_staged", ex=3600)
    try:
        assert await _has_acp_session(conv, str(pid), None) is False, (
            "unstaged probe must not match a staged-only session"
        )
        assert await _has_acp_session(conv, str(pid), "rag") is True
        # And the reverse: runner writes base key, staged probe must miss.
        await r.set(_key(conv, pid), "token_base", ex=3600)
        try:
            assert await _has_acp_session(conv, str(pid), "rag") is True, (
                "staged probe must fall through to base key (stage key missing)"
            )
        finally:
            await r.delete(_key(conv, pid))
    finally:
        await r.delete(_key(conv, pid, "rag"))


@pytest.mark.asyncio
async def test_redis_hickup_falls_back_to_first_turn():
    """On Redis failure the probe must return False (first-turn semantics) —
    never crash the send path with an unhandled exception."""
    import app.services.conversation_service as cs

    class _Boom:
        async def exists(self, key):
            raise ConnectionError("redis down")

    orig = redis_core.get_redis
    redis_core.get_redis = lambda: _Boom()
    try:
        assert await _has_acp_session(uuid.uuid4(), uuid.uuid4(), None) is False
    finally:
        redis_core.get_redis = orig


@pytest.mark.asyncio
async def test_profile_scoped_recovery_end_to_end_semantics():
    """The actual recovery scenario: runner holds a profile session in Redis →
    the next turn is NOT first-turn (clarify preamble skipped)."""
    conv = uuid.uuid4()
    pid = uuid.uuid4()
    r = redis_core.get_redis()
    # First turn: no session anywhere → first turn.
    assert await _has_acp_session(conv, str(pid), None) is False
    # Runner completes turn 1 and stores the profile-scoped session in Redis.
    await r.set(_key(conv, pid), "hermes_session_12345", ex=3600 * 24 * 7)
    try:
        # Turn 2: session recovered from Redis → follow-up turn.
        assert await _has_acp_session(conv, str(pid), None) is True, (
            "turn 2 must be a follow-up once runner stored the profile session"
        )
        # Expiry → back to first-turn semantics (fresh session).
        await r.expire(_key(conv, pid), 1)
        import asyncio as _aio
        await _aio.sleep(1.2)
        assert await _has_acp_session(conv, str(pid), None) is False, (
            "expired session must fall back to first-turn semantics"
        )
    finally:
        await r.delete(_key(conv, pid))
