"""Unified tool-governance pipeline for all runner executors.

Historically the iteration-cap circuit breaker and the high-risk tool guard
lived inline in handle_single only — roundtable / chain / subagent / scheduled
executors ran tool calls with NO governance at all. This module centralizes
the decision logic as a pure, synchronous core (structured title matching +
threshold checks) so every executor can share it; the async side effects
(Redis authorisation lookup, session cancel, event publish) stay with the
caller via the returned decision.

Layered after the waterfall pattern popularized by agent frameworks like
deepseek-harness: visibility (which MCP servers a session gets) is decided at
session creation; this pipeline is the per-call governance layer:

    check() ──► Decision
                 ├─ deny(reason="iteration_cap")          → cancel session, iteration_warning
                 ├─ risk_hit=<server name>                → caller checks authorisation,
                 │                                          unauthorised → cancel + tool_blocked
                 └─ allowed, risk_hit=None                 → proceed

ACP has no per-call interception channel (only request_permission for fs
writes), so "deny" keeps the established cancel-the-turn semantics: the user
authorises via the UI and re-runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GovernanceDecision:
    allowed: bool
    reason: str | None = None  # "iteration_cap" | "high_risk_unauthorised"
    risk_hit: str | None = None  # matched high-risk server name
    title: str | None = None
    extra: dict = field(default_factory=dict)  # e.g. {"tool_calls", "limit"}


class ToolGovernancePipeline:
    """Pure decision core — no I/O, trivially unit-testable."""

    def __init__(self, high_risk_names: set[str] | frozenset[str] = frozenset()):
        self.high_risk_names = {n for n in high_risk_names if n}

    def high_risk_hit(self, title: str) -> str | None:
        """Match high-risk server names against a tool title (substring).

        Deliberately substring, not word-boundary: MCP tool titles follow the
        ``mcp__<server>__<tool>`` convention where the server name is glued
        with underscores, and users write ``db-migrate:prod``-style suffixes
        too — boundary matching MISSES those, and a miss is a security hole
        while a false positive only costs one extra authorisation prompt
        (cached per conversation for 7 days).
        """
        if not title:
            return None
        for name in self.high_risk_names:
            if name in title:
                return name
        return None

    def check(
        self,
        *,
        title: str,
        tool_calls: int,
        max_iterations: int = 0,
        iter_capped: bool = False,
    ) -> GovernanceDecision:
        # Cap first — matches the original handle_single ordering.
        if max_iterations and not iter_capped and tool_calls >= max_iterations:
            return GovernanceDecision(
                allowed=False,
                reason="iteration_cap",
                title=title,
                extra={"tool_calls": tool_calls, "limit": max_iterations},
            )
        hit = self.high_risk_hit(title)
        if hit:
            # Caller resolves authorisation (Redis-cached) for the hit name.
            return GovernanceDecision(allowed=True, risk_hit=hit, title=title)
        return GovernanceDecision(allowed=True, title=title)
