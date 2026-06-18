"""ACP read-loop resilience against oversized stdout lines.

Regression test for the large-attachment bug: when the agent streams a single
JSON-RPC line bigger than the StreamReader limit, ``readline()`` raises
``ValueError``. The read loop must drop that line and keep the session alive
instead of dying and surfacing a bare "ValueError" to the user.
"""
import asyncio
import sys

import pytest

import agent_runner.acp_client as acp


@pytest.mark.asyncio
async def test_read_loop_survives_oversized_line(monkeypatch):
    # Shrink the limit so a moderate line trips the overflow path fast.
    monkeypatch.setattr(acp, "STDIO_LIMIT", 4096)

    child = (
        "import sys, time;"
        "sys.stdout.write('A' * 20000 + '\\n');"  # one line > limit -> ValueError
        "sys.stdout.write('{\"jsonrpc\":\"2.0\",\"method\":\"session/update\","
        "\"params\":{\"update\":{\"sessionUpdate\":\"ok\"}}}\\n');"
        "sys.stdout.flush();"
        "time.sleep(2)"
    )

    got = asyncio.Event()
    received: list[dict] = []

    async def on_update(update: dict) -> None:
        received.append(update)
        got.set()

    client = acp.ACPClient(
        command=[sys.executable, "-c", child], cwd="/tmp", on_update=on_update,
    )
    await client.start()
    try:
        await asyncio.wait_for(got.wait(), timeout=8)
    finally:
        await client.stop()

    # The valid notification after the oversized line must still be dispatched.
    assert received == [{"sessionUpdate": "ok"}]
