"""Unit tests for the project closed-loop helpers that need no DB/Redis.

DB-backed flows (consolidate_message, move_task_status, notify_project_members)
require Postgres + Redis and are exercised via the manual walkthrough in the plan.
These cover the pure logic: task-line parsing and the new permission defaults.
"""
from __future__ import annotations

from app.core import governance as gov
from app.services.conversation_service import _parse_task_lines


def test_parse_task_lines_bullets_and_numbers():
    text = (
        "这是开头说明\n"
        "1. 设计登录页面\n"
        "2) 实现后端鉴权\n"
        "- 编写单元测试\n"
        "• 部署到测试环境\n"
        "普通段落不应被解析\n"
    )
    tasks = _parse_task_lines(text)
    assert tasks == [
        "设计登录页面",
        "实现后端鉴权",
        "编写单元测试",
        "部署到测试环境",
    ]


def test_parse_task_lines_dedup_and_length_bounds():
    text = "- 重复项\n- 重复项\n- a\n" + "- " + ("x" * 200) + "\n"
    tasks = _parse_task_lines(text)
    assert tasks == ["重复项"]  # too-short "a" and too-long line dropped, dup collapsed


def test_parse_task_lines_caps_at_20():
    text = "\n".join(f"- 任务{i:03d}" for i in range(40))
    assert len(_parse_task_lines(text)) == 20


def test_new_permissions_registered():
    for pid in ("task.move", "task.derive", "conversation.consolidate"):
        assert pid in gov.PERMISSION_IDS


def test_permission_defaults_by_role():
    # owner always passes; member can drive the loop; viewer cannot.
    for pid in ("task.move", "task.derive", "conversation.consolidate"):
        assert gov.can(None, pid, "owner") is True
        assert gov.can(None, pid, "admin") is True
        assert gov.can(None, pid, "member") is True
        assert gov.can(None, pid, "viewer") is False


def test_existing_team_policy_backfills_new_perms():
    # A pre-existing policy JSON without the new perms still resolves via defaults.
    legacy = {"project.create": {"owner": True, "admin": True, "member": True, "viewer": False}}
    assert gov.can(legacy, "task.move", "member") is True
    assert gov.can(legacy, "conversation.consolidate", "viewer") is False
