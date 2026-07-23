"""Tool discovery service: intelligently select the most relevant MCP tools
based on the user's query, rather than injecting all tool schemas (which
causes token bloat when there are many tools).

Inspired by ai-agent-book's `active-tool-discovery` experiment: embed-based
retrieval finds 3-5 most relevant tools per query instead of injecting 120+.
"""
from __future__ import annotations



def _count_tools_in_catalog(catalog: list[dict], server_names: set[str]) -> int:
    """Count total tools across the profile's bound MCP servers."""
    total = 0
    for server in catalog:
        if server.get("name") in server_names:
            tools = server.get("discovered_tools") or server.get("tools") or []
            total += len(tools) if isinstance(tools, list) else 0
    return total


def select_relevant_tools(
    catalog: list[dict],
    server_names: set[str],
    user_query: str,
    max_tools: int = 8,
) -> list[dict]:
    """Select the most relevant tools from the profile's MCP servers based on
    the user's query text using simple keyword matching.

    Falls back to returning all tools when:
    - Total tool count <= max_tools (no optimization needed)
    - No user_query provided
    - No tools have descriptions/names to match against

    Returns the filtered catalog (same shape as input catalog) with only
    the servers containing relevant tools.
    """
    if not user_query or not user_query.strip():
        return [
            s for s in catalog if s.get("name") in server_names
        ]

    # Count total tools - if small enough, don't optimize
    total = _count_tools_in_catalog(catalog, server_names)
    if total <= max_tools:
        return [s for s in catalog if s.get("name") in server_names]

    query_lower = user_query.lower()
    query_words = set(query_lower.split())

    # Score each tool by keyword overlap with the query
    scored_tools: list[tuple[float, str, dict, dict]] = []  # (score, server_name, server, tool)
    for server in catalog:
        if server.get("name") not in server_names:
            continue
        tools = server.get("discovered_tools") or server.get("tools") or []
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_name = (tool.get("name") or "").lower()
            tool_desc = (tool.get("description") or "").lower()
            # Score: name exact match = 5, name partial = 2, desc word overlap = 1 each
            score = 0.0
            if tool_name and tool_name in query_lower:
                score += 5
            for word in query_words:
                if word in tool_name:
                    score += 2
                if word in tool_desc:
                    score += 1
            # Always include tools with non-zero score; also keep a small baseline
            # so unmatched tools aren't completely excluded (score 0.1)
            score = max(score, 0.1)
            scored_tools.append((score, server.get("name", ""), server, tool))

    if not scored_tools:
        return [s for s in catalog if s.get("name") in server_names]

    # Sort by score desc, take top N
    scored_tools.sort(key=lambda x: x[0], reverse=True)
    selected = scored_tools[:max_tools]

    # Group selected tools back by server
    by_server: dict[str, dict] = {}
    for _score, server_name, server, tool in selected:
        if server_name not in by_server:
            # Shallow copy server with empty tools list
            s = dict(server)
            s["discovered_tools"] = []
            s["tools"] = []
            by_server[server_name] = s
        by_server[server_name].setdefault("discovered_tools", []).append(tool)
        by_server[server_name].setdefault("tools", []).append(tool)

    return list(by_server.values())
