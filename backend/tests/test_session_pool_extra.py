"""session_pool empty-pool paths — pool_stats/evict/drop/close on a fresh
pool with no live clients (no ACP subprocesses needed).
"""
from __future__ import annotations

import pytest

from agent_runner.session_pool import SessionPool

pytestmark = pytest.mark.asyncio


async def test_empty_pool_stats_and_gauges():
    pool = SessionPool()
    stats = pool.pool_stats()
    assert stats["target"] >= 0
    assert "default" in stats["per_profile"] or stats["per_profile"] == {}


async def test_evict_idle_on_empty_pool():
    pool = SessionPool()
    await pool.evict_idle()  # must not raise


async def test_drop_missing_key_noop():
    pool = SessionPool()
    await pool.drop("nonexistent-conv")  # must not raise


async def test_close_all_empty():
    pool = SessionPool()
    await pool.close_all()  # must not raise


async def test_refill_warm_pool_without_agents():
    """With no registered agents, warm-pool refill must be a no-op."""
    pool = SessionPool()
    await pool._refill_warm_pool()  # no agents configured → early return
