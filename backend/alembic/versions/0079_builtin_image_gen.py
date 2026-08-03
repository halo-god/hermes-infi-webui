"""Register the built-in image-generation MCP server (P1-8).

The `image-gen` catalog entry points at the bundled stdio MCP server
(agent_runner/mcp_image_gen.py). It ships disabled by default — the admin
enables it and fills ARK_API_KEY in the connector's env (the env map is
injected into the ACP child at session start). Idempotent: existing entries
with that name are left untouched.

Revision ID: 0079
Revises: 0078
Create Date: 2026-07-23
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None

_ENTRY = {
    "name": "image-gen",
    "transport": "stdio",
    "command": "python -m agent_runner.mcp_image_gen",
    "env": {},
    "risk_level": "write",
    "enabled": False,
    "description": "内置图像生成（火山方舟 seedream）。启用后请编辑环境变量填写 ARK_API_KEY。",
    "icon": "sparkle",
    "permissions": [],
    "builtin": True,
}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, data FROM system_settings")).fetchall()
    for row in rows:
        data = row[1]
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            continue
        servers = data.get("mcp_servers")
        if not isinstance(servers, list):
            servers = []
            data["mcp_servers"] = servers
        if any(isinstance(s, dict) and s.get("name") == _ENTRY["name"] for s in servers):
            continue
        servers.append(dict(_ENTRY))
        conn.execute(
            text("UPDATE system_settings SET data = CAST(:d AS jsonb) WHERE id = :id"),
            {"d": json.dumps(data), "id": row[0]},
        )


def downgrade() -> None:
    """Best-effort: drop built-in image-gen entries (keep admin-added configs
    that happen to share the name — only builtin-flagged ones are removed)."""
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, data FROM system_settings")).fetchall()
    for row in rows:
        data = row[1]
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            continue
        servers = data.get("mcp_servers")
        if not isinstance(servers, list):
            continue
        kept = [s for s in servers if not (isinstance(s, dict) and s.get("builtin") and s.get("name") == "image-gen")]
        if len(kept) != len(servers):
            data["mcp_servers"] = kept
            conn.execute(
                text("UPDATE system_settings SET data = CAST(:d AS jsonb) WHERE id = :id"),
                {"d": json.dumps(data), "id": row[0]},
            )
