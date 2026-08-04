"""ZIP 技能包导入 —— 正常导入 + zip-slip 防护."""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


SKILL_MD = """---
name: test-skill
description: A test skill imported from a ZIP package.
metadata:
  hermes:
    tags: [test, demo]
---

# Test Skill

Body content of the test skill.
"""


async def _auth_headers(client, db) -> dict:
    from app.core.security import create_token, hash_password
    from app.db.models.user import User

    uniq = uuid.uuid4().hex[:8]
    user = User(id=uuid.uuid4(), email=f"si-user-{uniq}@hermes.io", name="技能用户",
                password_hash=hash_password("Pass@1234"), is_active=True, role="member")
    db.add(user)
    await db.flush()
    return {"Authorization": f"Bearer {create_token(str(user.id), 'access')[0]}"}


@pytest.mark.asyncio
async def test_import_skill_zip(client, db, monkeypatch):
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    import tempfile
    from pathlib import Path

    tmp = tempfile.mkdtemp(prefix="hermes-skills-test-")
    monkeypatch.setattr("app.config.settings.hermes_home", tmp)

    headers = await _auth_headers(client, db)
    zipped = _make_zip({
        "SKILL.md": SKILL_MD.encode(),
        "scripts/run.sh": b"#!/bin/sh\necho hi\n",
        "references/notes.md": b"reference notes\n",
    })
    r = await client.post(
        "/api/v1/memory/skills/import",
        headers=headers,
        files={"file": ("test-skill.zip", zipped, "application/zip")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "test-skill"
    assert body["description"].startswith("A test skill imported")
    assert "Test Skill" in body["content"]
    assert body["trigger_conditions"].get("keywords") == ["test", "demo"]

    # 资源文件解压到 HERMES_HOME/skills/test-skill/
    dest = Path(tmp) / "skills" / "test-skill"
    assert (dest / "scripts" / "run.sh").read_bytes() == b"#!/bin/sh\necho hi\n"
    assert (dest / "references" / "notes.md").read_bytes() == b"reference notes\n"
    assert (dest / "SKILL.md").exists()  # Direction-A sync wrote it


@pytest.mark.asyncio
async def test_import_zip_slip_rejected(client, db):
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    headers = await _auth_headers(client, db)
    evil = _make_zip({
        "SKILL.md": SKILL_MD.encode(),
        "../evil.txt": b"pwned",
    })
    r = await client.post(
        "/api/v1/memory/skills/import",
        headers=headers,
        files={"file": ("evil.zip", evil, "application/zip")},
    )
    assert r.status_code == 400, r.text
    assert "非法路径" in r.json()["detail"]


@pytest.mark.asyncio
async def test_import_missing_skillmd_rejected(client, db):
    from sqlalchemy import text
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("PostgreSQL not reachable")

    headers = await _auth_headers(client, db)
    bad = _make_zip({"readme.txt": b"no skill here"})
    r = await client.post(
        "/api/v1/memory/skills/import",
        headers=headers,
        files={"file": ("bad.zip", bad, "application/zip")},
    )
    assert r.status_code == 400, r.text
    assert "SKILL.md" in r.json()["detail"]
