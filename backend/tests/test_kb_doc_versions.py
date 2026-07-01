"""Team knowledge + project doc version history (new feature)."""
import uuid

import pytest

from app.db.models.team import Project, Team, TeamKnowledge
from app.db.models.user import User
from app.schemas.team import KnowledgeUpdate
from app.services import team_service as svc


async def _mk_user(db, email: str) -> User:
    from app.core.security import hash_password
    u = User(
        id=uuid.uuid4(), email=email, name=email.split("@")[0],
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(u)
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_knowledge_versioning_roundtrip(db):
    await _mk_user(db, "kb-owner@h.io")
    team = Team(id=uuid.uuid4(), name="KB Team")
    db.add(team)
    await db.flush()
    k = TeamKnowledge(team_id=team.id, name="doc.md", kind="doc", content="v1", size_bytes=2)
    db.add(k)
    await db.commit()
    await db.refresh(k)

    # First edit: v1 -> v2, snapshots v1 into history.
    updated = await svc.update_knowledge(
        db, team.id, k.id, KnowledgeUpdate(content="v2"), author="alice"
    )
    assert updated.current_version == 2

    # Second edit: v2 -> v3, snapshots v2.
    updated = await svc.update_knowledge(
        db, team.id, k.id, KnowledgeUpdate(content="v3"), author="alice"
    )
    assert updated.current_version == 3

    versions = await svc.list_knowledge_versions(db, k.id)
    assert [v.version_num for v in versions] == [2, 1]
    assert {v.content for v in versions} == {"v1", "v2"}
    assert versions[0].author == "alice"

    # Restoring v1 doesn't destroy history — it snapshots the current (v3)
    # content as a new version, then applies v1's content.
    restored = await svc.restore_knowledge_version(db, team.id, k.id, 1, author="bob")
    assert restored.content == "v1"
    assert restored.current_version == 4
    versions_after = await svc.list_knowledge_versions(db, k.id)
    assert len(versions_after) == 3


@pytest.mark.asyncio
async def test_knowledge_version_cap_keeps_latest_ten(db):
    await _mk_user(db, "kb-cap@h.io")
    team = Team(id=uuid.uuid4(), name="KB Cap Team")
    db.add(team)
    await db.flush()
    k = TeamKnowledge(team_id=team.id, name="cap.md", kind="doc", content="v0", size_bytes=2)
    db.add(k)
    await db.commit()
    await db.refresh(k)

    for i in range(1, 13):
        await svc.update_knowledge(db, team.id, k.id, KnowledgeUpdate(content=f"v{i}"))

    versions = await svc.list_knowledge_versions(db, k.id)
    assert len(versions) == 10


@pytest.mark.asyncio
async def test_project_doc_versioning_roundtrip(db):
    await _mk_user(db, "doc-owner@h.io")
    team = Team(id=uuid.uuid4(), name="Doc Team")
    db.add(team)
    await db.flush()
    project = Project(team_id=team.id, name="Doc Project")
    db.add(project)
    await db.flush()

    from app.db.models.team import ProjectDoc
    d = ProjectDoc(project_id=project.id, name="notes.md", kind="doc", content="v1", size_bytes=2)
    db.add(d)
    await db.commit()
    await db.refresh(d)

    updated = await svc.update_doc_content(db, project.id, d.id, "v2", author="alice")
    assert updated.current_version == 2

    versions = await svc.list_doc_versions(db, d.id)
    assert len(versions) == 1
    assert versions[0].content == "v1"

    restored = await svc.restore_doc_version(db, project.id, d.id, 1, author="bob")
    assert restored.content == "v1"
    assert restored.current_version == 3
