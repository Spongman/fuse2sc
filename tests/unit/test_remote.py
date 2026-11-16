"""Building in a SolidCraft app on another computer: addresses, shared-folder paths, and the
session's arguments, token and path translation (against the fake MCP server)."""

import sys
from pathlib import Path

import anyio
import pytest
from anyio.abc import SocketAttribute

from fuse2sc.solidcraft import Remote, RemoteError, SessionError, session
from fuse2sc.solidcraft.remote import DEFAULT_PORT, TOKEN_ENV

FAKE = Path(__file__).with_name("fake_mcp_server.py")


@pytest.mark.parametrize(
	("address", "host", "port", "shown"),
	[
		("192.0.2.10", "192.0.2.10", DEFAULT_PORT, "192.0.2.10:27878"),
		("192.0.2.10:27900", "192.0.2.10", 27900, "192.0.2.10:27900"),
		(" sandbox.lan:1 ", "sandbox.lan", 1, "sandbox.lan:1"),
		("[fe80::1]:27878", "fe80::1", 27878, "[fe80::1]:27878"),
		("[::1]", "::1", DEFAULT_PORT, "[::1]:27878"),
	],
)
def test_addresses(address, host, port, shown):
	r = Remote.parse(address, token="t")
	assert (r.host, r.port, r.address) == (host, port, shown)


@pytest.mark.parametrize("address", ["", "host:", "host:0", "host:70000", "a:b:c", "[::1", "host:port"])
def test_bad_addresses(address):
	with pytest.raises(RemoteError):
		Remote.parse(address, token="t")


def test_the_token_comes_from_the_environment_and_is_never_shown(monkeypatch):
	monkeypatch.setenv(TOKEN_ENV, "s3cret")
	r = Remote.parse("h")
	assert r.token == "s3cret" and "s3cret" not in repr(r)


def test_shared_folders_map_to_the_remote_names(tmp_path):
	designs, deeper = tmp_path / "designs", tmp_path / "designs" / "special"
	r = Remote.parse(
		"h", token="t", paths={str(designs): "/mnt/designs", str(deeper): r"D:\special", str(tmp_path / "x"): "/x"}
	)
	assert r.remote_path(designs / "a" / "b.step") == "/mnt/designs/a/b.step"
	assert r.remote_path(deeper / "c.sc") == r"D:\special\c.sc"  # the most specific folder wins
	assert r.remote_path(designs / "special2" / "d.sc") == "/mnt/designs/special2/d.sc"  # not a prefix match
	elsewhere = tmp_path / "elsewhere" / "e.sc"
	assert r.remote_path(elsewhere) == str(elsewhere.resolve())  # unmapped: unchanged


def test_server_command_local_and_remote(tmp_path):
	args, env = session.server_command([tmp_path], None)
	assert args == ["mcp", "--headless", "--allow-path", str(tmp_path.resolve())] and env is None
	args, env = session.server_command([tmp_path], Remote.parse("192.0.2.10", token="tok"))
	assert args == ["mcp", "--connect", "192.0.2.10:27878", "--allow-any-path"]
	assert env == {TOKEN_ENV: "tok"}  # the token travels in the environment, not on the command line


@pytest.fixture
async def listening():
	"""A local TCP port that accepts connections, standing in for the app's control channel."""
	async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
		port = listener.extra(SocketAttribute.local_port)
		async with anyio.create_task_group() as tg:
			tg.start_soon(listener.serve, lambda stream: stream.aclose())
			yield port
			tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_a_remote_session_starts_fresh_and_translates_paths(monkeypatch, tmp_path, listening):
	seen: dict = {}
	real = session.StdioServerParameters

	def make(**kw):
		seen.update(kw)
		return real(command=sys.executable, args=[str(FAKE), "normal"], encoding_error_handler="replace")

	monkeypatch.setattr(session, "StdioServerParameters", make)
	remote = Remote.parse(f"127.0.0.1:{listening}", token="tok", paths={str(tmp_path): "/mnt/w"})
	async with session.open_session(Path("cli"), [tmp_path], log_path=tmp_path / "sc.log", remote=remote) as sc:
		assert seen["args"][:2] == ["mcp", "--connect"] and seen["env"] == {TOKEN_ENV: "tok"}
		step = await sc.execute("file.insert_step", {"path": str(tmp_path / "steps" / "3.step"), "name": "x"})
		assert step.body["arguments"]["params"] == {"path": "/mnt/w/steps/3.step", "name": "x"}
		other = await sc.execute("sketch.create", {"path": "not a file"})
		assert other.body["arguments"]["params"]["path"] == "not a file"  # only file commands name files
		saved = await sc.save(tmp_path / "d.sc")
		assert saved.body["arguments"] == {"path": "/mnt/w/d.sc"}


@pytest.mark.anyio
async def test_an_unreachable_app_is_a_clear_error(tmp_path):
	async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
		port = listener.extra(SocketAttribute.local_port)
	remote = Remote.parse(f"127.0.0.1:{port}", token="tok")  # nothing listens there any more
	with pytest.raises(SessionError, match=r"no SolidCraft at 127\.0\.0\.1"):
		async with session.open_session(Path("cli"), [], log_path=tmp_path / "sc.log", remote=remote, timeout=5):
			pass


@pytest.mark.anyio
async def test_no_token_is_a_clear_error(tmp_path, listening):
	with pytest.raises(SessionError, match=TOKEN_ENV):
		async with session.open_session(
			Path("cli"), [], log_path=tmp_path / "sc.log", remote=Remote.parse(f"127.0.0.1:{listening}", token="")
		):
			pass


def test_the_cli_combines_the_workspace_and_command_line(monkeypatch, tmp_path):
	from fuse2sc import cli
	from fuse2sc.settings import RemoteSettings, Settings, Workspace

	ws = Workspace(tmp_path, Settings(solidcraft_remote=RemoteSettings(address="sandbox", paths={"designs": "/mnt/d"})))
	monkeypatch.setattr(cli, "state", cli.State())
	assert cli.solidcraft_remote(None) is None  # nothing configured: build here
	cli.state.path_map = [f"{tmp_path / 'extra'}=/mnt/e"]
	r = cli.solidcraft_remote(ws)
	assert r is not None and r.address == "sandbox:27878"
	assert r.remote_path(tmp_path / "designs" / "a.sc") == "/mnt/d/a.sc"  # relative to the workspace
	assert r.remote_path(tmp_path / "extra" / "b.step") == "/mnt/e/b.step"
	cli.state.remote, cli.state.path_map = "other:1", ["no-equals-sign"]
	with pytest.raises(cli.typer.Exit):
		cli.solidcraft_remote(ws)
