"""Platform-level RBAC: role hierarchy + FastAPI guards.

Mirrors the frontend roles in hermes-data.js / hermes-governance.js.
Team-scoped content permissions (the per-team matrix) are handled separately
in services/governance_service.py during P3.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status

# Platform roles, most → least privileged.
ROLE_ORDER: dict[str, int] = {
    "super_admin": 100,
    "admin": 80,
    "team_admin": 60,
    "member": 40,
    "viewer": 20,
}

ADMIN_ROLES = {"super_admin", "admin"}

# ── Platform role metadata (mirrors the prototype hermes-data.js ROLES) ──
# `system` roles cannot be deleted; descriptions surface in the admin console.
ROLE_META: list[dict] = [
    {"id": "super_admin", "name": "超级管理员", "system": True,
     "desc": "可访问所有功能，包含后台、计费、连接器配置。"},
    {"id": "admin", "name": "管理员", "system": True,
     "desc": "管理用户、团队、助手与知识库。不可修改连接器与计费。"},
    {"id": "team_admin", "name": "团队管理员", "system": False,
     "desc": "在自己所属团队内拥有完整管理权限。"},
    {"id": "member", "name": "成员", "system": True,
     "desc": "使用助手、参与团队对话、上传知识，不可邀请。"},
    {"id": "viewer", "name": "只读", "system": False,
     "desc": "仅可查看分享给自己的内容与历史，不可发送消息。"},
]

# ── Platform permission matrix (mirrors the prototype hermes-data.js PERMISSIONS) ──
# Which platform roles hold each capability. Drives the admin「权限管理」matrix.
PERMISSION_CATALOG: list[dict] = [
    {"group": "会话", "items": [
        {"id": "chat.create", "name": "创建会话", "roles": ["super_admin", "admin", "team_admin", "member"]},
        {"id": "chat.delete_own", "name": "删除自己的会话", "roles": ["super_admin", "admin", "team_admin", "member"]},
        {"id": "chat.delete_any", "name": "删除任意会话", "roles": ["super_admin", "admin"]},
        {"id": "chat.share", "name": "分享会话", "roles": ["super_admin", "admin", "team_admin", "member"]},
    ]},
    {"group": "团队 & 项目", "items": [
        {"id": "team.create", "name": "创建团队", "roles": ["super_admin", "admin"]},
        {"id": "team.invite", "name": "邀请成员", "roles": ["super_admin", "admin", "team_admin"]},
        {"id": "project.create", "name": "创建项目", "roles": ["super_admin", "admin", "team_admin", "member"]},
        {"id": "project.archive", "name": "归档项目", "roles": ["super_admin", "admin", "team_admin"]},
    ]},
    {"group": "助手 & 知识", "items": [
        {"id": "agent.publish", "name": "发布团队助手", "roles": ["super_admin", "admin", "team_admin"]},
        {"id": "agent.system_prompt", "name": "修改系统提示", "roles": ["super_admin", "admin"]},
        {"id": "kb.upload", "name": "上传知识库", "roles": ["super_admin", "admin", "team_admin", "member"]},
        {"id": "kb.delete", "name": "删除知识条目", "roles": ["super_admin", "admin", "team_admin"]},
    ]},
    {"group": "后台管理", "items": [
        {"id": "admin.users", "name": "用户管理", "roles": ["super_admin", "admin"]},
        {"id": "admin.roles", "name": "权限管理", "roles": ["super_admin"]},
        {"id": "admin.identity", "name": "身份与连接器", "roles": ["super_admin"]},
        {"id": "admin.billing", "name": "查看计费", "roles": ["super_admin"]},
        {"id": "admin.audit", "name": "审计日志", "roles": ["super_admin", "admin"]},
    ]},
]


def has_permission(role: str, perm_id: str) -> bool:
    """True if the platform `role` holds capability `perm_id`."""
    for group in PERMISSION_CATALOG:
        for item in group["items"]:
            if item["id"] == perm_id:
                return role in item["roles"]
    return False


def role_rank(role: str) -> int:
    return ROLE_ORDER.get(role, 0)


def has_at_least(role: str, required: str) -> bool:
    return role_rank(role) >= role_rank(required)


def require_role(required: str):
    """Dependency factory: ensures the current user has >= required role.

    NOTE: This function is kept for backward compatibility. New code should
    use app.core.guards.require_role() instead to avoid circular imports.
    """
    from app.core.guards import require_role as _require_role
    return _require_role(required)


def require_admin():
    """Dependency: requires admin or super_admin role."""
    from app.core.guards import require_admin as _require_admin
    return _require_admin()
