"""MCP servers must actually reach the ACP `mcpServers` session param.

Admin-registered MCP servers (system_settings.data['mcp_servers']) were
previously dead config — no caller ever passed them into
ACPClient.new_session/resume_session/fork_session. This wires them through a
Profile-level allowlist (Profile.mcp_server_names).
"""
from __future__ import annotations

import uuid

from app.db.models.agent import Profile
from app.services import conversation_service as svc
from app.services import settings_service


async def _mk_profile(db, mcp_server_names: list[str]) -> Profile:
    p = Profile(
        id=uuid.uuid4(), name="工具助手", handle=f"tool-{uuid.uuid4().hex[:8]}",
        default_agent_id="hermes", is_active=True, mcp_server_names=mcp_server_names,
    )
    db.add(p)
    await db.flush()
    return p


async def _register_servers(db, servers: list[dict]) -> None:
    settings_row = await settings_service.get(db)
    data = dict(settings_row.data or {})
    data["mcp_servers"] = servers
    await settings_service.update(db, data)


async def test_no_profile_resolves_empty(db):
    assert await svc._resolve_mcp_servers(db, None) == []


async def test_profile_without_enabled_servers_resolves_empty(db):
    profile = await _mk_profile(db, [])
    await _register_servers(db, [
        {"name": "fs", "transport": "stdio", "command": "npx -y srv", "env": {}},
    ])
    assert await svc._resolve_mcp_servers(db, profile) == []


async def test_profile_filters_catalog_by_enabled_names(db):
    await _register_servers(db, [
        {"name": "fs", "transport": "stdio", "command": "npx -y fs-server", "env": {"A": "1"}},
        {"name": "web", "transport": "http", "url": "https://example.com/mcp", "env": None},
        {"name": "unused", "transport": "stdio", "command": "unused-cmd", "env": {}},
    ])
    profile = await _mk_profile(db, ["fs", "web"])

    resolved = await svc._resolve_mcp_servers(db, profile)

    names = {e["name"] for e in resolved}
    assert names == {"fs", "web"}
    fs_entry = next(e for e in resolved if e["name"] == "fs")
    assert fs_entry == {"name": "fs", "command": "npx", "args": ["-y", "fs-server"], "env": {"A": "1"}}
    web_entry = next(e for e in resolved if e["name"] == "web")
    assert web_entry == {"name": "web", "type": "sse", "url": "https://example.com/mcp", "headers": {}}


async def test_stdio_entry_without_command_is_skipped(db):
    await _register_servers(db, [{"name": "broken", "transport": "stdio", "command": "", "env": {}}])
    profile = await _mk_profile(db, ["broken"])
    assert await svc._resolve_mcp_servers(db, profile) == []


async def test_http_entry_without_url_is_skipped(db):
    await _register_servers(db, [{"name": "broken", "transport": "http", "url": None, "env": {}}])
    profile = await _mk_profile(db, ["broken"])
    assert await svc._resolve_mcp_servers(db, profile) == []
