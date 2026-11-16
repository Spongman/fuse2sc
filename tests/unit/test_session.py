"""The SolidCraft session against a fake server that misbehaves (no SolidCraft needed)."""

import sys
from pathlib import Path

import pytest

from fuse2sc.solidcraft import SessionError, SessionTimeout, session

FAKE = Path(__file__).with_name("fake_mcp_server.py")
pytestmark = pytest.mark.anyio


@pytest.fixture
def fake(monkeypatch, tmp_path):
	"""open_session() that starts the fake server in a given mode instead of solidcraft-cli."""
	real = session.StdioServerParameters

	def params(mode):
		def make(**kw):
			return real(command=sys.executable, args=[str(FAKE), mode], encoding_error_handler="replace")

		return make

	def open_fake(mode, **kw):
		monkeypatch.setattr(session, "StdioServerParameters", params(mode))
		return session.open_session(Path("ignored"), [tmp_path], log_path=tmp_path / "sc.log", **kw)

	return open_fake


async def test_tool_calls_and_errors(fake):
	async with fake("normal", timeout=20) as sc:
		reply = await sc.execute("sketch.create", {"plane": "XY"})
		assert reply.ok and reply.body["arguments"] == {"command": "sketch.create", "params": {"plane": "XY"}}
		assert await sc.bodies() == {"B": {"name": "B", "volume_mm3": 1.0}}
		bad = await sc.execute("bad")
		assert not bad.ok and "refused" in bad.error


async def test_a_hang_times_out(fake):
	async with fake("hang", timeout=20) as sc:
		with pytest.raises(SessionTimeout):
			await sc.tool("execute", {"command": "x", "params": {}}, timeout=1.0)


async def test_still_running_is_waited_for(fake):
	async with fake("pending", timeout=20) as sc:
		reply = await sc.execute("solid.fillet")
		assert reply.ok and reply.body["tool"] == "pending"


async def test_a_crash_is_an_error(fake):
	with pytest.raises(SessionError):
		async with fake("crash", timeout=5) as sc:
			await sc.execute("x")
