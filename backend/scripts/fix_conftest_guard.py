#!/usr/bin/env python3
"""Insert the settings-singleton Redis guard into backend/tests/conftest.py."""
import pathlib

p = pathlib.Path("/Users/caotinghui/Downloads/hermes-python/backend/tests/conftest.py")
src = p.read_text(encoding="utf-8")

anchor = 'os.environ["STORAGE_BACKEND"] = "db"\n'
assert src.count(anchor) == 1, f"anchor count = {src.count(anchor)}"

guard = anchor + '''# ── Second guard: the app Settings singleton must ALSO target test Redis ──
# The 2026-08-12 production-dispatch incident: pytest tests/test_group_chat.py
# (test_group_roundtable_attachment_gets_content_blocks) dispatched a REAL
# roundtable session ("大家看看这份笔记" + notes.md fixture) to the production
# acp_stream because the ACP subprocess env (agent_runner/acp_client.py) and the
# test assertions (app/core/redis.py) read settings.redis_url — the pydantic
# Settings singleton — NOT os.environ["REDIS_URL"]. If that singleton is
# instantiated before the setdefault above (a higher-level conftest / plugin
# importing app.config early), it picks up backend/.env's production URL
# (127.0.0.1:1979) and the env-var guard above passes anyway. Validate the
# singleton itself; creating it here (env vars set) also pins it to 6380 for
# every subsequent import.
from app.config import get_settings as _get_settings  # noqa: E402

_sredis = _get_settings().redis_url
_sru = _urlparse(_sredis)
if (_sru.hostname, _sru.port) not in _ALLOWED_REDIS:
    raise RuntimeError(
        f"settings.redis_url points at an unapproved Redis "
        f"({_sru.hostname}:{_sru.port}). The Settings singleton must resolve to "
        "the dedicated test instance (127.0.0.1:6380) — see the 2026-08-12 "
        "production-dispatch note above. Something imported app.config before "
        "conftest pinned REDIS_URL; move that import after this file, or export "
        "REDIS_URL before pytest runs."
    )
'''

src = src.replace(anchor, guard, 1)
p.write_text(src, encoding="utf-8")
print("OK: guard inserted, new size =", len(src))
