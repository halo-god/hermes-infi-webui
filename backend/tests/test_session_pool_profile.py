"""Profile-scoped HERMES_HOME in the ACP session pool.

The pool must spawn the agent with HERMES_HOME pointing at the selected
profile's directory, reuse the subprocess only while the profile is unchanged,
and respawn when a conversation switches profile mid-stream.
"""
from __future__ import annotations

import pytest

from agent_runner import session_pool as sp
from agent_runner.acp_client import profile_env


class FakeProc:
    returncode = None
    pid = 4242


class FakeACPClient:
    instances: list["FakeACPClient"] = []

    def __init__(
        self, command, cwd, *, protocol_version=1, on_update=None, on_fs_write=None, env=None,
    ):
        self.command = command
        self.cwd = cwd
        self.on_update = on_update
        self.on_fs_write = on_fs_write
        self.env = env
        self._closed = False
        self._proc = FakeProc()
        self.stopped = False
        FakeACPClient.instances.append(self)

    async def start(self):
        pass

    async def initialize(self):
        return {}

    async def authenticate(self, method):
        pass

    async def new_session(self, cwd):
        return f"sess-{len(FakeACPClient.instances)}"

    async def resume_session(self, session_id, cwd):
        raise RuntimeError("unknown session in this profile's store")

    async def stop(self):
        self.stopped = True
        self._closed = True


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(sp, "ACPClient", FakeACPClient)
    FakeACPClient.instances.clear()
    return sp.SessionPool()


async def _noop_update(_u: dict) -> None:
    return None


async def _noop_fs(_p: str, _c: str) -> None:
    return None


async def test_profile_dir_sets_hermes_home(pool, tmp_path):
    prof = tmp_path / "profiles" / "coder"
    prof.mkdir(parents=True)
    c, sid = await pool.get("conv1", ["hermes", "acp"], "/tmp", _noop_update, _noop_fs,
                            profile_dir=str(prof))
    assert c.env == {"HERMES_HOME": str(prof)}
    assert sid is not None


async def test_no_profile_inherits_default_env(pool):
    c, _ = await pool.get("conv1", ["hermes", "acp"], "/tmp", _noop_update, _noop_fs)
    assert c.env is None


async def test_same_profile_reuses_client(pool, tmp_path):
    prof = tmp_path / "coder"
    prof.mkdir()
    c1, _ = await pool.get("conv1", ["hermes", "acp"], "/tmp", _noop_update, _noop_fs,
                           profile_dir=str(prof))
    c2, sid2 = await pool.get("conv1", ["hermes", "acp"], "/tmp", _noop_update, _noop_fs,
                              profile_dir=str(prof))
    assert c2 is c1
    assert sid2 is None  # reused, no new session


async def test_profile_switch_respawns(pool, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    c1, _ = await pool.get("conv1", ["hermes", "acp"], "/tmp", _noop_update, _noop_fs,
                           profile_dir=str(a))
    c2, sid2 = await pool.get("conv1", ["hermes", "acp"], "/tmp", _noop_update, _noop_fs,
                              profile_dir=str(b))
    assert c2 is not c1
    assert c1.stopped
    assert c2.env == {"HERMES_HOME": str(b)}
    assert sid2 is not None  # fresh session in the new profile


def test_profile_env_missing_dir_falls_back(tmp_path):
    assert profile_env(None) is None
    assert profile_env(str(tmp_path / "nope")) is None
    assert profile_env(str(tmp_path)) == {"HERMES_HOME": str(tmp_path)}
