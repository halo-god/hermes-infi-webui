"""MCP connector extensions — enable/disable, descriptions, icons, permissions.

MCP server catalog entries live in system_settings.data['mcp_servers'] (a JSONB
array), so the new fields (enabled/description/icon/permissions) need no DDL —
JSONB accepts arbitrary keys. This migration backfills the new keys onto every
existing entry so the runner and UI can rely on them being present, and
normalises the runtime connection-state key name `mcp_connections`.

Revision ID: 0073
Revises: 0072
Create Date: 2026-07-29
"""
import json

from alembic import op
from sqlalchemy import text

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None

_DEFAULTS = {"enabled": True, "description": "", "icon": "cube", "permissions": []}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, data FROM system_settings")).fetchall()
    for row in rows:
        data = row[1]
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            continue
        changed = False
        servers = data.get("mcp_servers")
        if isinstance(servers, list):
            for entry in servers:
                if not isinstance(entry, dict):
                    continue
                for key, default in _DEFAULTS.items():
                    if key not in entry:
                        entry[key] = default
                        changed = True
        # Ensure the runtime connection-state key exists.
        if "mcp_connections" not in data:
            data["mcp_connections"] = {}
            changed = True
        if changed:
            conn.execute(
                text("UPDATE system_settings SET data = CAST(:d AS jsonb) WHERE id = :id"),
                {"d": json.dumps(data), "id": row[0]},
            )


def downgrade() -> None:
    # Best-effort: strip the new keys. We can't recover original values, so we
    # only remove keys that were absent before this migration.
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, data FROM system_settings")).fetchall()
    for row in rows:
        data = row[1]
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            continue
        changed = False
        servers = data.get("mcp_servers")
        if isinstance(servers, list):
            for entry in servers:
                if isinstance(entry, dict):
                    for key in _DEFAULTS:
                        if key in entry:
                            entry.pop(key)
                            changed = True
        if "mcp_connections" in data:
            data.pop("mcp_connections")
            changed = True
        if changed:
            conn.execute(
                text("UPDATE system_settings SET data = CAST(:d AS jsonb) WHERE id = :id"),
                {"d": json.dumps(data), "id": row[0]},
            )
