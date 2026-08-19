"""Tool-governance pipeline: structured risk matching + iteration cap."""
from __future__ import annotations

from agent_runner.tool_governance import ToolGovernancePipeline


def _pipeline(*names: str) -> ToolGovernancePipeline:
    return ToolGovernancePipeline(set(names))


class TestHighRiskMatching:
    def test_substring_semantics_false_positives_are_accepted(self):
        # Deliberate trade-off (see high_risk_hit docstring): a false
        # positive costs one authorisation prompt; a miss is a security hole.
        g = _pipeline("git")
        assert g.high_risk_hit("digital-git-sync 工具") == "git"

    def test_standalone_and_mcp_style_names_match(self):
        g = _pipeline("git")
        assert g.high_risk_hit("git commit 变更") == "git"
        # The MCP naming convention glues the server with underscores —
        # word-boundary matching misses this, substring does not.
        assert g.high_risk_hit("mcp__git__commit") == "git"

    def test_multi_word_server_names_with_suffixes(self):
        g = _pipeline("db-migrate")
        assert g.high_risk_hit("run db-migrate:prod") == "db-migrate"

    def test_empty_title_and_empty_names(self):
        g = _pipeline("", "git")
        assert "" not in g.high_risk_names
        assert g.high_risk_hit("") is None
        assert _pipeline().high_risk_hit("anything") is None


class TestCheck:
    def test_cap_fires_once_then_stays_silent(self):
        g = _pipeline()
        d1 = g.check(title="t", tool_calls=5, max_iterations=5, iter_capped=False)
        assert not d1.allowed and d1.reason == "iteration_cap"
        assert d1.extra == {"tool_calls": 5, "limit": 5}
        # After the executor latches iter_capped, further calls are quiet.
        d2 = g.check(title="t", tool_calls=6, max_iterations=5, iter_capped=True)
        assert d2.allowed and d2.risk_hit is None

    def test_cap_disabled_when_zero(self):
        g = _pipeline()
        d = g.check(title="t", tool_calls=999, max_iterations=0, iter_capped=False)
        assert d.allowed

    def test_risk_hit_reported_for_authorisation(self):
        g = _pipeline("k8s")
        d = g.check(title="k8s rollout restart", tool_calls=1, max_iterations=50)
        assert d.allowed  # the pipeline reports the hit; caller checks auth
        assert d.risk_hit == "k8s"
        assert d.title == "k8s rollout restart"

    def test_cap_takes_precedence_over_risk(self):
        g = _pipeline("k8s")
        d = g.check(title="k8s delete", tool_calls=50, max_iterations=50, iter_capped=False)
        assert not d.allowed and d.reason == "iteration_cap"
