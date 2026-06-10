#!/usr/bin/env python3
"""Bundled mock ACP agent — a real JSON-RPC-over-stdio ACP *server*.

Lets the full conversation loop (send → stream → produce file → done) run and
be verified before the real `hermes` CLI is installed. Supports a --persona so
roundtable shows distinct repliers. Pure stdlib, synchronous, deterministic.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

PERSONAS = {
    "hermes": {"name": "Hermes", "stance": "综合协调", "lead": "我来统筹："},
    "cowork": {"name": "Cowork", "stance": "提出方案", "lead": "我的方案是："},
    "critic": {"name": "Critic", "stance": "提出风险", "lead": "需要注意的风险："},
}


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read() -> dict | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return {}
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def _update(session_id: str, update: dict) -> None:
    _send({"jsonrpc": "2.0", "method": "session/update",
           "params": {"sessionId": session_id, "update": update}})


def _msg_chunk(session_id: str, text: str) -> None:
    _update(session_id, {"sessionUpdate": "agent_message_chunk",
                         "content": {"type": "text", "text": text}})


def _handle_prompt(session_id: str, params: dict, req_id, persona: dict, emit_file: bool) -> None:
    import uuid as _uuid
    prompt_text = ""
    for part in params.get("prompt", []):
        if part.get("type") == "text":
            prompt_text += part.get("text", "")
    prompt_text = prompt_text.strip()

    # Memory consolidation task: reply with deterministic JSON so the dream-time
    # consolidation flow is verifiable end-to-end without the real CLI.
    if "【记忆整理任务】" in prompt_text:
        _msg_chunk(session_id, json.dumps({
            "user_profile": "全栈工程师，偏好简洁直接的回答（mock 整理结果）",
            "soul": "以有条理的技术顾问角色对话",
            "notes": "正在测试做梦整理记忆功能",
        }, ensure_ascii=False))
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}})
        return

    # Emit confirmation_request for very short or question messages to demo the flow
    if len(prompt_text) < 15 or prompt_text.endswith("?") or prompt_text.endswith("？"):
        _update(session_id, {
            "sessionUpdate": "confirmation_request",
            "request_id": str(_uuid.uuid4()),
            "question": f"你想了解「{prompt_text or '...'}」？请确认操作方向：",
            "options": ["继续回答", "重新提问", "跳过"],
        })
        time.sleep(0.1)

    chunks = [
        persona["lead"], "围绕「", prompt_text or "你的请求", "」，\n\n",
        "1. 明确目标与约束\n",
        "2. 给出关键步骤\n",
        "3. 标注产出与负责人\n\n",
        f"> 立场：{persona['stance']}（经 ACP 会话作答）",
    ]
    for c in chunks:
        _msg_chunk(session_id, c)
        time.sleep(0.03)

    _update(session_id, {"sessionUpdate": "tool_call", "toolCallId": "tc-1",
                         "title": "整理要点", "status": "completed"})

    if emit_file:
        file_md = (
            f"# 会议纪要\n\n**主题**：{prompt_text or '未命名'}\n\n"
            "## 决议\n- [x] 目标确认\n- [ ] 下一步排期\n\n"
            "## 负责人\n| 事项 | 负责人 |\n|---|---|\n| 起草 | Hermes |\n"
        )
        _send({"jsonrpc": "2.0", "id": "fs-1", "method": "fs/write_text_file",
               "params": {"sessionId": session_id, "path": "会议纪要.md", "content": file_md}})
        for _ in range(20):
            resp = _read()
            if resp is None or resp.get("id") == "fs-1" or resp.get("method") == "session/cancel":
                break

    _send({"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="hermes")
    ap.add_argument("--no-file", action="store_true", help="don't emit a workspace file")
    args = ap.parse_args()
    persona = PERSONAS.get(args.persona, PERSONAS["hermes"])
    emit_file = not args.no_file

    session_counter = 0
    while True:
        msg = _read()
        if msg is None:
            break
        if not msg:
            continue
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": params.get("protocolVersion", 1),
                "agentCapabilities": {"promptCapabilities": {"image": False}},
                "agentInfo": {"name": persona["name"], "version": "mock-0.1",
                              "stance": persona["stance"]},
            }})
        elif method == "session/new":
            session_counter += 1
            _send({"jsonrpc": "2.0", "id": req_id,
                   "result": {"sessionId": f"sess-mock-{session_counter}"}})
        elif method == "session/prompt":
            _handle_prompt(params.get("sessionId", "sess-mock"), params, req_id, persona, emit_file)
        elif method == "session/cancel":
            pass
        elif req_id is not None:
            _send({"jsonrpc": "2.0", "id": req_id, "result": None})


if __name__ == "__main__":
    main()
