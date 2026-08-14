"""Files browser API — list/upload/folder/move/delete for the personal file
library. The endpoints existed without direct coverage.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


PREFIX = "/api/v1/files"


async def _upload(client, headers, name="测试文件.md", content="# 内容"):
    r = await client.post(f"{PREFIX}/upload?folder=/", headers=headers,
                          files={"file": (name, content.encode(), "text/markdown")})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_upload_and_list_standalone(client, auth_headers):
    f = await _upload(client, auth_headers)
    assert f["name"] == "测试文件.md"
    assert f["folder_path"] == "/"
    r = await client.get(f"{PREFIX}/standalone?folder=/", headers=auth_headers)
    assert r.status_code == 200
    names = [x["name"] for x in r.json()]
    assert "测试文件.md" in names


async def test_upload_is_user_scoped(client, auth_headers, db):
    """Another user must not see my standalone files."""
    from app.core.security import create_token, hash_password
    from app.db.models.user import User
    await _upload(client, auth_headers)
    other = User(
        id=uuid.uuid4(), email="files-other@h.io", name="other",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(other)
    await db.flush()
    other_headers = {"Authorization": f"Bearer {create_token(str(other.id), 'access')[0]}"}
    r = await client.get(f"{PREFIX}/standalone?folder=/", headers=other_headers)
    assert all(x["name"] != "测试文件.md" for x in r.json())


async def test_raw_and_content(client, auth_headers):
    f = await _upload(client, auth_headers, content="# 你好内容")
    r = await client.get(f"{PREFIX}/{f['id']}/raw", headers=auth_headers)
    assert r.status_code == 200
    assert "你好内容" in r.text
    r = await client.get(f"{PREFIX}/{f['id']}/content", headers=auth_headers)
    assert r.status_code == 200
    assert "你好内容" in r.text


async def test_folder_create_list_and_move(client, auth_headers):
    f = await _upload(client, auth_headers)
    # Create folder
    r = await client.post(f"{PREFIX}/folder", params={"name": "文档夹", "parent": "/"},
                          headers=auth_headers)
    assert r.status_code == 201, r.text
    folder = r.json()
    assert folder["is_folder"] is True

    # Folders endpoint lists it
    r = await client.get(f"{PREFIX}/folders", headers=auth_headers)
    assert r.status_code == 200

    # Move file into the folder
    r = await client.put(f"{PREFIX}/{f['id']}/move", json={"target_folder": "/文档夹"},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    r = await client.get(f"{PREFIX}/standalone?folder=/文档夹", headers=auth_headers)
    assert any(x["id"] == f["id"] for x in r.json()), "file must appear in target folder"
    r = await client.get(f"{PREFIX}/standalone?folder=/", headers=auth_headers)
    assert all(x["id"] != f["id"] for x in r.json()), "file must leave root"


async def test_move_to_nonexistent_folder(client, auth_headers):
    f = await _upload(client, auth_headers)
    r = await client.put(f"{PREFIX}/{f['id']}/move",
                         json={"target_folder": "/不存在目录"}, headers=auth_headers)
    # Move to an arbitrary path is allowed (folder is a path string)
    assert r.status_code == 200, r.text


async def test_delete_file_and_folder(client, auth_headers):
    f = await _upload(client, auth_headers)
    r = await client.delete(f"{PREFIX}/{f['id']}", headers=auth_headers)
    assert r.status_code in (200, 204)
    r = await client.get(f"{PREFIX}/standalone?folder=/", headers=auth_headers)
    assert all(x["id"] != f["id"] for x in r.json())

    # Folder
    r = await client.post(f"{PREFIX}/folder", params={"name": "待删夹", "parent": "/"},
                          headers=auth_headers)
    folder = r.json()
    r = await client.delete(f"{PREFIX}/folder/{folder['id']}", headers=auth_headers)
    assert r.status_code in (200, 204)


async def test_file_access_requires_owner(client, auth_headers, db):
    """Raw access to another user's file must be denied."""
    from app.core.security import create_token, hash_password
    from app.db.models.user import User
    f = await _upload(client, auth_headers)
    other = User(
        id=uuid.uuid4(), email="files-raw@h.io", name="other",
        password_hash=hash_password("Test@1234"), is_active=True, role="member",
    )
    db.add(other)
    await db.flush()
    other_headers = {"Authorization": f"Bearer {create_token(str(other.id), 'access')[0]}"}
    r = await client.get(f"{PREFIX}/{f['id']}/raw", headers=other_headers)
    assert r.status_code in (403, 404)


async def test_empty_folder_list(client, auth_headers):
    r = await client.get(f"{PREFIX}/standalone?folder=/", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
