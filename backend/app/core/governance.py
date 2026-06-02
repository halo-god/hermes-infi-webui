"""Team content-permission matrix — Python port of hermes-governance.js.

Two layers of authorization exist in the product:
  1. Platform RBAC (super_admin…viewer)         → app/core/rbac.py
  2. Per-team content permissions (this module)  → stored on Team.policy (JSONB)

A team policy is { perm_id: { role_key: bool } }. Owners always pass (their
column is fixed-on). Members/viewers are governed by the (editable) matrix.
"""
from __future__ import annotations

from typing import Iterable

# Team-scoped roles, most → least privileged.
TEAM_ROLES = ["owner", "admin", "member", "viewer"]

# Permission catalog (id, group, label) — mirrors the prototype's Settings matrix.
PERMISSIONS: list[dict] = [
    {"id": "project.create", "group": "项目", "label": "新建项目"},
    {"id": "project.edit", "group": "项目", "label": "编辑项目"},
    {"id": "project.delete", "group": "项目", "label": "归档 / 删除项目"},
    {"id": "knowledge.upload", "group": "知识库", "label": "上传条目"},
    {"id": "knowledge.edit", "group": "知识库", "label": "编辑条目"},
    {"id": "knowledge.delete", "group": "知识库", "label": "删除条目"},
    {"id": "conversation.pin", "group": "会话与助手", "label": "置顶会话"},
    {"id": "agent.manage", "group": "会话与助手", "label": "管理共享助手"},
    {"id": "channel.clear", "group": "会话与助手", "label": "清空群聊记录"},
    {"id": "channel.manage_agents", "group": "会话与助手", "label": "管理群聊助手"},
    {"id": "member.invite", "group": "成员", "label": "邀请成员"},
    {"id": "member.role", "group": "成员", "label": "调整角色"},
    {"id": "member.remove", "group": "成员", "label": "移除成员"},
]

PERMISSION_IDS = [p["id"] for p in PERMISSIONS]

# Default policy by role. owner is implicitly all-true (not stored as togglable).
_DEFAULTS: dict[str, dict[str, bool]] = {
    "admin": {pid: True for pid in PERMISSION_IDS} | {"member.remove": False},
    "member": {
        "project.create": True,
        "project.edit": True,
        "project.delete": False,
        "knowledge.upload": True,
        "knowledge.edit": True,
        "knowledge.delete": False,
        "conversation.pin": True,
        "agent.manage": False,
        "channel.clear": False,
        "channel.manage_agents": False,
        "member.invite": False,
        "member.role": False,
        "member.remove": False,
    },
    "viewer": {pid: False for pid in PERMISSION_IDS},
}


def default_policy() -> dict:
    """Full policy object: { perm_id: { role_key: bool } } including owner=True."""
    policy: dict[str, dict[str, bool]] = {}
    for pid in PERMISSION_IDS:
        policy[pid] = {
            "owner": True,
            "admin": _DEFAULTS["admin"][pid],
            "member": _DEFAULTS["member"][pid],
            "viewer": _DEFAULTS["viewer"][pid],
        }
    return policy


def ensure_policy(policy: dict | None) -> dict:
    """Return a complete policy, filling any missing perms/roles from defaults."""
    base = default_policy()
    if not policy:
        return base
    for pid in PERMISSION_IDS:
        row = policy.get(pid) or {}
        merged = base[pid] | {k: bool(v) for k, v in row.items() if k in TEAM_ROLES}
        merged["owner"] = True  # owner column is always on
        base[pid] = merged
    return base


def can(policy: dict | None, perm_id: str, role_key: str) -> bool:
    if role_key == "owner":
        return True
    pol = ensure_policy(policy)
    return bool(pol.get(perm_id, {}).get(role_key, False))


def toggle(policy: dict, perm_id: str, role_key: str) -> dict:
    pol = ensure_policy(policy)
    if role_key == "owner" or perm_id not in PERMISSION_IDS:
        return pol  # owner is locked-on
    pol[perm_id][role_key] = not pol[perm_id].get(role_key, False)
    return pol


def grouped_permissions() -> list[dict]:
    """Permissions grouped for the Settings matrix UI."""
    groups: dict[str, list[dict]] = {}
    for p in PERMISSIONS:
        groups.setdefault(p["group"], []).append(p)
    return [{"group": g, "permissions": items} for g, items in groups.items()]


def assert_known(perm_ids: Iterable[str]) -> None:
    for pid in perm_ids:
        if pid not in PERMISSION_IDS:
            raise ValueError(f"unknown permission: {pid}")
