"""ACP fs/read_text_file handler.

Regression test for the "empty response" bug: the client advertised
fs.readTextFile but never served it, so the agent's read_file on a referenced
attachment returned null and it produced no text. The handler must read files
inside cwd (with line/limit paging) and reject path escapes.
"""
import asyncio
import os
import sys
import tempfile

import pytest

import agent_runner.acp_client as acp


# Fake agent: fires three fs/read_text_file requests, echoes each response back
# as a session/update so the test can capture it.
_CHILD = r'''
import sys, json
def send(o): sys.stdout.write(json.dumps(o)+"\n"); sys.stdout.flush()
send({"jsonrpc":"2.0","id":1,"method":"fs/read_text_file","params":{"path":"doc.txt"}})
send({"jsonrpc":"2.0","id":2,"method":"fs/read_text_file","params":{"path":"doc.txt","line":2,"limit":2}})
send({"jsonrpc":"2.0","id":3,"method":"fs/read_text_file","params":{"path":"../../../etc/passwd"}})
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    msg=json.loads(line)
    rid=msg.get("id")
    if rid in (1,2,3):
        send({"jsonrpc":"2.0","method":"session/update","params":{"update":
              {"echo":{"got":rid,"result":msg.get("result"),"error":msg.get("error")}}}})
        if rid==3:
            sys.exit(0)
'''


@pytest.mark.asyncio
async def test_fs_read_text_file_serves_and_confines():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "doc.txt"), "w", encoding="utf-8") as f:
        f.write("line1\nline2\nline3\nline4\n")

    echoes: dict[int, dict] = {}
    done = asyncio.Event()

    async def on_update(u: dict) -> None:
        if "echo" in u:
            e = u["echo"]
            echoes[e["got"]] = e
            if e["got"] == 3:
                done.set()

    client = acp.ACPClient(command=[sys.executable, "-c", _CHILD], cwd=d, on_update=on_update)
    await client.start()
    try:
        await asyncio.wait_for(done.wait(), timeout=8)
    finally:
        await client.stop()

    # Full read returns the whole file.
    assert echoes[1]["result"]["content"] == "line1\nline2\nline3\nline4\n"
    # line=2, limit=2 returns a 2-line window.
    assert echoes[2]["result"]["content"] == "line2\nline3\n"
    # Path escape is rejected with an error, no content.
    assert echoes[3]["result"] is None
    assert echoes[3]["error"] and "escapes workspace" in echoes[3]["error"]["message"]
