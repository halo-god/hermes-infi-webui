"""Admin 会话日志 (session log) — CallCollector unit tests + API tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from agent_runner.call_log import CallCollector


# ── CallCollector (pure, no DB) ──
def test_tool_call_dedup_by_id_and_duration():
    t0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    c = CallCollector(model_name="hermes", turn_started_at=t0)
    c.on_tool_call(title="Bash", status="pending", tool_call_id="t1", now=t0)
    c.on_tool_call(title="Bash", status="running", tool_call_id="t1", now=t0 + timedelta(seconds=1))
    c.on_tool_call(title="Bash", status="completed", tool_call_id="t1", now=t0 + timedelta(seconds=5))
    recs = c.records()
    assert len(recs) == 1
    assert recs[0]["kind"] == "tool"
    assert recs[0]["name"] == "Bash"
    assert recs[0]["status"] == "completed"
    assert recs[0]["duration_ms"] == 5000


def test_tool_call_repeat_title_without_id_is_separate():
    t0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    c = CallCollector(model_name="hermes", turn_started_at=t0)
    c.on_tool_call(title="Bash", status="completed", now=t0)
    c.on_tool_call(title="Bash", status="completed", now=t0 + timedelta(seconds=2))
    recs = c.records()
    assert len(recs) == 2  # single-event calls never merge across titles
    # each event started+finished at its own timestamp → duration 0
    assert [r["duration_ms"] for r in recs] == [0, 0]


def test_tool_call_begin_end_transitions():
    t0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    c = CallCollector(model_name="hermes", turn_started_at=t0)
    c.on_tool_call(title="Read", status="running", tool_kind="read", now=t0)
    c.on_tool_call(title="Read", status="completed", tool_kind="read", now=t0 + timedelta(milliseconds=250))
    recs = c.records()
    assert len(recs) == 1
    assert recs[0]["duration_ms"] == 250
    assert recs[0]["tool_kind"] == "read"


def test_tool_call_null_status_treated_as_completed():
    t0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    c = CallCollector(model_name="hermes", turn_started_at=t0)
    # hermes emits tool_call events without a status field
    c.on_tool_call(title="Bash", status=None, now=t0 + timedelta(seconds=2))
    recs = c.records()
    assert len(recs) == 1
    assert recs[0]["status"] == "completed"
    assert recs[0]["ended_at"] is not None
    assert recs[0]["duration_ms"] == 0


def test_usage_records_model_calls_with_tokens():
    t0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    c = CallCollector(model_name="coder", turn_started_at=t0)
    c.on_usage(input_tokens=100, output_tokens=50, now=t0 + timedelta(seconds=3))
    c.on_usage(input_tokens=10, output_tokens=5, now=t0 + timedelta(seconds=6))
    recs = c.records()
    assert len(recs) == 2
    m1, m2 = recs
    assert m1["kind"] == "model" and m1["name"] == "coder"
    assert m1["tokens_in"] == 100 and m1["tokens_out"] == 50
    assert m1["duration_ms"] == 3000
    assert m2["duration_ms"] == 3000  # gap since previous event


# ── API (needs PostgreSQL/Redis) ──
@pytest.mark.asyncio
async def test_session_logs_api(client, db):
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    from app.core.security import create_token, hash_password
    from app.db.models.conversation import Conversation, Message
    from app.db.models.session_log import SessionCallLog
    from app.db.models.user import User

    uniq = uuid.uuid4().hex[:8]
    admin = User(id=uuid.uuid4(), email=f"sl-admin-{uniq}@hermes.io", name="日志管理员",
                 password_hash=hash_password("Admin@1234"), is_active=True, role="admin")
    member = User(id=uuid.uuid4(), email=f"sl-member-{uniq}@hermes.io", name="普通成员",
                  password_hash=hash_password("Member@1234"), is_active=True, role="member")
    db.add_all([admin, member])
    await db.flush()
    admin_headers = {"Authorization": f"Bearer {create_token(str(admin.id), 'access')[0]}"}
    member_headers = {"Authorization": f"Bearer {create_token(str(member.id), 'access')[0]}"}

    owner_id = admin.id
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def mk_convo(*, ctype="personal", status="complete", with_calls=True, title="会话A"):
        c = Conversation(id=uuid.uuid4(), title=title, type=ctype, owner_id=owner_id,
                         primary_agent_id="hermes", profile_id="p1")
        db.add(c)
        u = Message(id=uuid.uuid4(), conversation_id=c.id, owner_id=owner_id, role="user",
                    content={"text": "帮我写一个冒泡排序"}, status="complete",
                    created_at=base)
        a = Message(id=uuid.uuid4(), conversation_id=c.id, owner_id=owner_id, role="agent",
                    agent_id="hermes", content={"text": "好的，已生成。"}, status=status,
                    created_at=base + timedelta(minutes=1),
                    updated_at=base + timedelta(minutes=2))
        db.add_all([u, a])
        # FK-dependent rows must be flushed before session_call_logs can
        # reference message ids (UOW doesn't sort mappers without relationships)
        await db.flush()
        if with_calls:
            db.add_all([
                SessionCallLog(conversation_id=c.id, message_id=a.id, kind="model",
                               name="hermes", status="completed", duration_ms=1200,
                               tokens_in=100, tokens_out=50,
                               started_at=base + timedelta(seconds=5),
                               ended_at=base + timedelta(seconds=6.2)),
                SessionCallLog(conversation_id=c.id, message_id=a.id, kind="tool",
                               name="Bash", tool_kind="execute", status="completed",
                               duration_ms=800, started_at=base + timedelta(seconds=7),
                               ended_at=base + timedelta(seconds=7.8)),
            ])
        return c

    ok_convo = await mk_convo(title="排序助手")
    fail_convo = await mk_convo(status="error", title="失败会话", with_calls=False)
    await db.flush()

    # list
    r = await client.get("/api/v1/admin/session-logs", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 2
    item = body["items"][0]
    assert item["user_name"] == "日志管理员"
    assert {"first_input", "last_output", "turn_count", "model_calls",
            "tool_calls", "duration_ms", "status"} <= set(item.keys())

    # status filter: only the failed conversation
    r = await client.get("/api/v1/admin/session-logs", params={"status": "fail"}, headers=admin_headers)
    assert r.status_code == 200
    assert all(i["status"] == "fail" for i in r.json()["items"])
    ids = {i["id"] for i in r.json()["items"]}
    assert str(fail_convo.id) in ids and str(ok_convo.id) not in ids

    # source filter
    r = await client.get("/api/v1/admin/session-logs", params={"source": "personal"}, headers=admin_headers)
    assert r.status_code == 200
    assert all(i["source"] == "personal" for i in r.json()["items"])

    # search by first input
    r = await client.get("/api/v1/admin/session-logs", params={"q": "冒泡排序"}, headers=admin_headers)
    assert r.status_code == 200
    assert any(i["id"] == str(ok_convo.id) for i in r.json()["items"])

    # pagination
    r = await client.get("/api/v1/admin/session-logs", params={"page": 1, "page_size": 1}, headers=admin_headers)
    assert r.status_code == 200
    p1 = r.json()
    assert p1["page_size"] == 1 and len(p1["items"]) == 1 and p1["total"] >= 2

    # detail with call overview
    r = await client.get(f"/api/v1/admin/session-logs/{ok_convo.id}", headers=admin_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["model_calls"] == 1 and d["tool_calls"] == 1
    assert d["turn_count"] == 1
    assert len(d["turns"]) == 1
    turn = d["turns"][0]
    assert turn["user_text"] == "帮我写一个冒泡排序"
    assert turn["agent_text"] == "好的，已生成。"
    assert turn["thinking"] is None
    assert {c["kind"] for c in turn["calls"]} == {"model", "tool"}

    # detail: unknown id → 404
    r = await client.get(f"/api/v1/admin/session-logs/{uuid.uuid4()}", headers=admin_headers)
    assert r.status_code == 404

    # export CSV
    r = await client.get("/api/v1/admin/session-logs/export", headers=admin_headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    csv_text = r.text.lstrip("\ufeff")
    assert csv_text.startswith("最新时间,用户,来源,状态")
    assert "冒泡排序" in csv_text

    # non-admin blocked
    r = await client.get("/api/v1/admin/session-logs", headers=member_headers)
    assert r.status_code == 403
