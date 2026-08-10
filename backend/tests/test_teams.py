"""Tests for team endpoints."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_team(client: AsyncClient, admin_headers):
    """Test creating a team."""
    resp = await client.post("/api/v1/teams", json={
        "name": "Test Team",
        "handle": "test-team",
    }, headers=admin_headers)
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["name"] == "Test Team"


@pytest.mark.asyncio
async def test_list_teams(client: AsyncClient, admin_headers):
    """Test listing teams."""
    # Create one first
    await client.post("/api/v1/teams", json={
        "name": "My Team",
    }, headers=admin_headers)

    resp = await client.get("/api/v1/teams", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_create_team_no_auth(client: AsyncClient):
    """Test creating team without auth fails."""
    resp = await client.post("/api/v1/teams", json={
        "name": "No Auth Team",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_team_detail(client: AsyncClient, admin_headers):
    """Test getting team details."""
    # Create team
    create_resp = await client.post("/api/v1/teams", json={
        "name": "Detail Team",
    }, headers=admin_headers)
    team_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/teams/{team_id}", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Detail Team"
    assert "members" in data or "my_role" in data
