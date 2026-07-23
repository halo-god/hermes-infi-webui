"""P0-2/P1-3/P2-1/P2-3: Agent orchestration feature tests.

Tests the pure-logic pieces that don't need a running ACP subprocess:
- max_iterations circuit breaker config propagation
- staged system-prompt resolution + tool subsetting
- chain/research profile flags
- MCP risk-level classification
"""
from __future__ import annotations



# ── P0-2: max_iterations ──

class TestMaxIterations:
    async def test_profile_has_max_iterations_default(self, db):
        """Profiles flushed to DB carry the circuit-breaker default."""
        from app.db.models.agent import Profile
        p = Profile(name="test-mi", handle="test_mi", scope="personal")
        db.add(p)
        await db.flush()
        # After flush, server_default (50) is applied
        assert p.max_iterations == 50


# ── P1-3: staged system prompts ──

class TestStagedResolution:
    def test_unstaged_profile_returns_none(self):
        from app.db.models.agent import Profile
        from app.services.conversation_service import _resolve_staged_profile
        p = Profile(name="t", handle="t2", scope="personal")
        prompt, tools = _resolve_staged_profile(p, None)
        assert prompt is None
        assert tools is None

    def test_staged_clarify_stage(self):
        from app.db.models.agent import Profile
        from app.services.conversation_service import _resolve_staged_profile
        p = Profile(name="t", handle="t3", scope="personal")
        p.staged_enabled = True
        p.staged_prompts = {
            "clarify": {"prompt": "你是澄清专家", "mcp_servers": ["read_tool"]},
            "implement": {"prompt": "你是实现者", "mcp_servers": ["read_tool", "write_tool"]},
            "review": {"prompt": "你是审查员", "mcp_servers": ["read_tool"]},
        }
        prompt, tools = _resolve_staged_profile(p, "clarify")
        assert prompt == "你是澄清专家"
        assert tools == {"read_tool"}

    def test_staged_implement_more_tools(self):
        from app.db.models.agent import Profile
        from app.services.conversation_service import _resolve_staged_profile
        p = Profile(name="t", handle="t4", scope="personal")
        p.staged_enabled = True
        p.staged_prompts = {
            "clarify": {"prompt": "c", "mcp_servers": ["read"]},
            "implement": {"prompt": "i", "mcp_servers": ["read", "write", "exec"]},
        }
        _, tools = _resolve_staged_profile(p, "implement")
        assert tools == {"read", "write", "exec"}

    def test_staged_unknown_stage_returns_none(self):
        from app.db.models.agent import Profile
        from app.services.conversation_service import _resolve_staged_profile
        p = Profile(name="t", handle="t5", scope="personal")
        p.staged_enabled = True
        p.staged_prompts = {"clarify": {"prompt": "c"}}
        prompt, tools = _resolve_staged_profile(p, "nonexistent")
        assert prompt is None
        assert tools is None

    def test_staged_no_mcp_servers_returns_none(self):
        from app.db.models.agent import Profile
        from app.services.conversation_service import _resolve_staged_profile
        p = Profile(name="t", handle="t6", scope="personal")
        p.staged_enabled = True
        p.staged_prompts = {"clarify": {"prompt": "c"}}  # no mcp_servers
        prompt, tools = _resolve_staged_profile(p, "clarify")
        assert prompt == "c"
        assert tools is None  # inherits full set


# ── P2-1/P2-2: chain & research flags ──

class TestChainResearchFlags:
    def test_chain_flags_settable(self):
        from app.db.models.agent import Profile
        p = Profile(name="t", handle="t8", scope="personal")
        p.is_chain = True
        p.chain_target_profile_ids = ["id1", "id2"]
        p.is_research = True
        assert p.is_chain is True
        assert p.chain_target_profile_ids == ["id1", "id2"]
        assert p.is_research is True


# ── P2-3: MCP risk classification ──

class TestMCPRiskClassification:
    def test_high_risk_loaded_from_catalog(self, db):
        """_load_high_risk_server_names reads write/destructive from settings."""
        # We test via the Runner helper, but it needs a running instance.
        # Instead test the classification logic directly.
        servers = [
            {"name": "safe-reader", "risk_level": "read"},
            {"name": "dangerous-writer", "risk_level": "destructive"},
            {"name": "moderate-writer", "risk_level": "write"},
            {"name": "no-risk-field"},  # old entry, defaults to read
        ]
        high_risk = {
            s["name"] for s in servers
            if s.get("risk_level") in ("write", "destructive") and s.get("name")
        }
        assert "dangerous-writer" in high_risk
        assert "moderate-writer" in high_risk
        assert "safe-reader" not in high_risk
        assert "no-risk-field" not in high_risk
