"""Unit tests for the security-hardening helpers (no DB/Redis needed)."""
from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile

from app.config import Settings
from app.core.files import confine_to_dir, read_upload_capped, safe_relative_path


# ── Path traversal containment ──

@pytest.mark.parametrize("raw, expected", [
    ("src/main.py", "src/main.py"),
    ("./notes.md", "notes.md"),
    ("/etc/passwd", "etc/passwd"),
    ("../../etc/passwd", "etc/passwd"),
    ("a/../../b.txt", "b.txt"),
    ("..\\..\\win.ini", "win.ini"),
    ("", "untitled.txt"),
])
def test_safe_relative_path_contains(raw, expected):
    assert safe_relative_path(raw) == expected


def test_confine_to_dir_allows_inside(tmp_path):
    target = confine_to_dir(str(tmp_path), "sub/dir/file.txt")
    assert target.startswith(str(tmp_path.resolve()))


def test_confine_to_dir_rejects_escape(tmp_path):
    # A pre-normalized path can't escape, but confine_to_dir is the last guard:
    with pytest.raises(HTTPException) as ei:
        confine_to_dir(str(tmp_path), "../../../etc/passwd")
    assert ei.value.status_code == 400


# ── Upload size cap ──

def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="f.bin", file=io.BytesIO(data))


@pytest.mark.asyncio
async def test_read_upload_capped_ok():
    up = _upload(b"x" * 100)
    assert await read_upload_capped(up, 1000) == b"x" * 100


@pytest.mark.asyncio
async def test_read_upload_capped_rejects_oversize():
    up = _upload(b"x" * (3 * 1024 * 1024))
    with pytest.raises(HTTPException) as ei:
        await read_upload_capped(up, 1 * 1024 * 1024)
    assert ei.value.status_code == 413


# ── Production config validation ──

def test_validate_for_production_flags_defaults():
    s = Settings(secret_key="change-me-in-production-please-32+chars",
                 first_admin_password="Hermes@2026")
    problems = s.validate_for_production()
    assert any("SECRET_KEY" in p for p in problems)
    assert any("ADMIN" in p.upper() for p in problems)


def test_validate_for_production_clean():
    s = Settings(secret_key="x" * 48, first_admin_password="some-strong-secret",
                 storage_backend="db")
    assert s.validate_for_production() == []
