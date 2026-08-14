"""settings_service + identity_service coverage — the tenant settings access
patterns and identity-provider mapping logic.
"""
from __future__ import annotations

import uuid

import pytest

from app.services import settings_service as ssvc

pytestmark = pytest.mark.asyncio


# ── settings_service ──

async def test_settings_get_creates_default(db):
    s = await ssvc.get(db)
    assert s.id == 1
    assert isinstance(s.data, dict)
    assert "rag" in s.data or s.data  # non-empty defaults


async def test_settings_update_and_rag_flag(db):
    await ssvc.update(db, {"rag": {"enabled": False}, "branding": {"name": "X"}})
    s = await ssvc.get(db)
    assert s.data["branding"]["name"] == "X"
    assert ssvc.rag_enabled(db) is not None  # resolves override or env fallback


async def test_rag_flag_db_override_wins(db):
    await ssvc.update(db, {"rag": {"enabled": True}})
    flag = await ssvc.rag_flag(db, "enabled", default=False)
    assert flag is True


async def test_rag_flag_falls_back_to_default(db):
    await ssvc.update(db, {"rag": {}})
    flag = await ssvc.rag_flag(db, "enabled", default=False)
    assert flag is False


# ── identity_service: mappings + matching ──

async def test_identity_mapping_crud(db):
    from app.services import identity_service as isvc
    from app.db.models.identity import IdentityProvider

    prov = IdentityProvider(id="test-ldap", label="测试LDAP", config={"url": "ldap://x"})
    db.add(prov)
    await db.flush()

    m = await isvc.add_mapping(db, "test-ldap", {
        "match_basis": "dept", "source_value": "研发部",
        "dept": "研发部", "default_role": "member",
        "auto_join_team_id": str(uuid.uuid4()),
    })
    assert m.provider_id == "test-ldap"

    rows = await isvc.list_mappings(db, "test-ldap")
    assert any(r.id == m.id for r in rows)

    await isvc.delete_mapping(db, m.id)
    rows = await isvc.list_mappings(db, "test-ldap")
    assert all(r.id != m.id for r in rows)

    assert await isvc.get_provider(db, "test-ldap") is not None
    assert await isvc.get_provider(db, "nope") is None


async def test_identity_match_routes_dept(db):
    from app.services import identity_service as isvc
    from app.db.models.identity import DeptTeamMapping, IdentityProvider

    prov = IdentityProvider(id="test-match", label="M", config={})
    db.add(prov)
    await db.flush()
    tid = uuid.uuid4()
    db.add(DeptTeamMapping(
        provider_id="test-match", match_basis="dept", source_value="研发部",
        dept="研发部", auto_join_team_id=tid,
    ))
    await db.flush()

    from app.services.identity_service import IdentityInfo, _match
    info = IdentityInfo(name="张三", email="z@h.io", external_id="z3", source="ldap", department="研发部")
    hit = _match(info, await isvc.list_mappings(db, "test-match"))
    assert hit is not None and hit.auto_join_team_id == tid

    info2 = IdentityInfo(name="李四", email="l@h.io", external_id="l4", source="ldap", department="市场部")
    miss = _match(info2, await isvc.list_mappings(db, "test-match"))
    assert miss is None


async def test_provision_user_creates_user(db):
    from app.services import identity_service as isvc
    from app.db.models.identity import IdentityProvider

    db.add(IdentityProvider(id="test-prov", label="P", config={}))
    await db.flush()
    user = await isvc.provision_user(
        db,
        info=isvc.IdentityInfo(name="外部用户", email="ext@h.io", external_id="e1", source="ldap", department="研发部"),
        mappings=[],
    )
    assert user.email == "ext@h.io"
    assert user.is_active
