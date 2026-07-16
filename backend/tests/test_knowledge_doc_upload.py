"""Team knowledge base + project doc uploads share the same process_upload()
pipeline as conversation attachments now (large/small file handling unified).

Covers: small files still inline in Postgres (no storage_key); large files
offload to object storage while still extracting text content for prompt
injection; the raw-download route serves offloaded bytes back correctly.
"""
import uuid

import pytest

from app.db.models.team import Project, Team, TeamMember
from app.db.models.user import User


async def _mk_team(db, owner: User) -> Team:
    team = Team(id=uuid.uuid4(), name="Upload Team")
    db.add(team)
    await db.flush()
    db.add(TeamMember(team_id=team.id, user_id=owner.id, role="owner"))
    await db.flush()
    return team


async def _mk_project(db, team: Team) -> Project:
    project = Project(team_id=team.id, name="Upload Project")
    db.add(project)
    await db.flush()
    return project


@pytest.mark.asyncio
async def test_small_knowledge_upload_inlines_content(client, auth_headers, test_user, db):
    team = await _mk_team(db, test_user)
    await db.commit()

    r = await client.post(
        f"/api/v1/teams/{team.id}/knowledge/upload",
        files={"file": ("notes.md", b"# Hello\nsmall file", "text/markdown")},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["kind"] == "md"

    from app.db.models.team import TeamKnowledge
    k = await db.get(TeamKnowledge, uuid.UUID(data["id"]))
    assert k.storage_key is None
    assert k.content == "# Hello\nsmall file"


@pytest.mark.asyncio
async def test_large_knowledge_upload_offloads_to_object_storage(client, auth_headers, test_user, db):
    pytest.importorskip("moto")
    from moto import mock_aws
    from app.config import settings
    from app.core import object_storage

    team = await _mk_team(db, test_user)
    await db.commit()

    body = ("x" * 300_000).encode("utf-8")  # > file_offload_threshold_kb (256KB)

    prev_endpoint, prev_bucket = settings.minio_endpoint, settings.minio_bucket
    settings.minio_endpoint = ""
    settings.minio_bucket = "hermes-test-bucket"
    try:
        with mock_aws():
            object_storage.reset_client()
            r = await client.post(
                f"/api/v1/teams/{team.id}/knowledge/upload",
                files={"file": ("big.txt", body, "text/plain")},
                headers=auth_headers,
            )
            assert r.status_code == 201, r.text
            data = r.json()

            from app.db.models.team import TeamKnowledge
            k = await db.get(TeamKnowledge, uuid.UUID(data["id"]))
            assert k.storage_key is not None  # offloaded, not base64-inlined
            assert k.content == body.decode("utf-8")  # text still extracted for prompt injection

            raw = await client.get(
                f"/api/v1/teams/{team.id}/knowledge/{k.id}/raw",
                params={"ticket": None},
                headers=auth_headers,
            )
            # raw route accepts either a ticket or the bearer header via user_from_ticket_or_header
            assert raw.status_code == 200, raw.text
            assert raw.content == body
    finally:
        settings.minio_endpoint, settings.minio_bucket = prev_endpoint, prev_bucket
        object_storage.reset_client()


@pytest.mark.asyncio
async def test_large_project_doc_upload_offloads_to_object_storage(client, auth_headers, test_user, db):
    pytest.importorskip("moto")
    from moto import mock_aws
    from app.config import settings
    from app.core import object_storage

    team = await _mk_team(db, test_user)
    project = await _mk_project(db, team)
    await db.commit()

    body = ("y" * 300_000).encode("utf-8")

    prev_endpoint, prev_bucket = settings.minio_endpoint, settings.minio_bucket
    settings.minio_endpoint = ""
    settings.minio_bucket = "hermes-test-bucket"
    try:
        with mock_aws():
            object_storage.reset_client()
            r = await client.post(
                f"/api/v1/projects/{project.id}/docs/upload",
                files={"file": ("big.txt", body, "text/plain")},
                headers=auth_headers,
            )
            assert r.status_code == 201, r.text
            data = r.json()

            from app.db.models.team import ProjectDoc
            d = await db.get(ProjectDoc, uuid.UUID(data["id"]))
            assert d.storage_key is not None
            assert d.content == body.decode("utf-8")

            detail = await client.get(f"/api/v1/projects/docs/{d.id}", headers=auth_headers)
            assert detail.status_code == 200
            assert detail.json()["content"] == body.decode("utf-8")
    finally:
        settings.minio_endpoint, settings.minio_bucket = prev_endpoint, prev_bucket
        object_storage.reset_client()
