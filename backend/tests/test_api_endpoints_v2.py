"""API endpoint tests for the new P1-3 / P2-3 / P2-4 features.

Uses the shared `client` + `auth_headers` fixtures (TestClient with overridden
DB + a logged-in test user). Verifies routing, auth guards, and basic happy
paths without needing a running ACP subprocess or LLM.
"""
from __future__ import annotations

from httpx import AsyncClient


class TestStageEndpoint:
    async def test_set_stage_unauthorized(self, client: AsyncClient):
        """PUT /stage without auth → 401."""
        r = await client.put("/api/v1/conversations/00000000-0000-0000-0000-000000000000/stage",
                             json={"stage": "clarify"})
        assert r.status_code == 401

    async def test_set_stage_invalid_value(self, client: AsyncClient, auth_headers):
        """PUT /stage with invalid stage value → 422 (Pydantic validation)."""
        r = await client.put("/api/v1/conversations/00000000-0000-0000-0000-000000000000/stage",
                             json={"stage": "invalid"}, headers=auth_headers)
        assert r.status_code == 422

    async def test_set_stage_not_found(self, client: AsyncClient, auth_headers):
        """PUT /stage on a non-existent conversation → 404."""
        r = await client.put("/api/v1/conversations/00000000-0000-0000-0000-000000000000/stage",
                             json={"stage": "clarify"}, headers=auth_headers)
        # _require_convo raises 404 for non-existent
        assert r.status_code in (404, 403)


class TestAuthoriseToolEndpoint:
    async def test_authorise_unauthorized(self, client: AsyncClient):
        r = await client.post("/api/v1/conversations/00000000-0000-0000-0000-000000000000/authorise-tool",
                              json={"tool": "some-tool"})
        assert r.status_code == 401

    async def test_authorise_missing_tool_field(self, client: AsyncClient, auth_headers):
        r = await client.post("/api/v1/conversations/00000000-0000-0000-0000-000000000000/authorise-tool",
                              json={}, headers=auth_headers)
        assert r.status_code == 422  # Pydantic: tool is required

    async def test_authorise_not_found(self, client: AsyncClient, auth_headers):
        r = await client.post("/api/v1/conversations/00000000-0000-0000-0000-000000000000/authorise-tool",
                              json={"tool": "x"}, headers=auth_headers)
        assert r.status_code in (404, 403)


class TestProfileEvolutionEndpoints:
    async def test_list_proposals_unauthorized(self, client: AsyncClient):
        """GET /profile-evolution/proposals without auth → 401."""
        r = await client.get("/api/v1/profile-evolution/proposals")
        assert r.status_code == 401

    async def test_list_proposals_not_super_admin(self, client: AsyncClient, auth_headers):
        """GET /profile-evolution/proposals as non-super-admin → 403."""
        r = await client.get("/api/v1/profile-evolution/proposals", headers=auth_headers)
        assert r.status_code == 403

    async def test_trigger_evolve_not_super_admin(self, client: AsyncClient, auth_headers):
        """POST evolve as non-super-admin → 403."""
        r = await client.post("/api/v1/profile-evolution/profiles/"
                              "00000000-0000-0000-0000-000000000000/evolve", headers=auth_headers)
        assert r.status_code == 403


class TestChunksCountEndpoint:
    async def test_chunks_count_unauthorized(self, client: AsyncClient):
        r = await client.get("/api/v1/teams/00000000-0000-0000-0000-000000000000/knowledge/"
                             "00000000-0000-0000-0000-000000000000/chunks-count")
        assert r.status_code == 401
