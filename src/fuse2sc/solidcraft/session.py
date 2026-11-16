"""SolidCraft, driven through its MCP server with the official `mcp` SDK.

    async with open_session(cli, allow=[folder]) as sc:
        reply = await sc.execute("sketch.create", {"plane": "XY"})
        if reply.ok: ...

By default the session is a headless SolidCraft of its own (`solidcraft-cli mcp --headless`).
With `remote`, it drives an app on another computer through its control channel
(`solidcraft-cli mcp --connect`, see remote.py), starting from a new, empty design there; file
paths in `file.*` commands and in `save` are then translated to the remote computer's names.

Every call has a timeout (a SolidCraft that hangs raises SessionTimeout rather than hanging the
build), a reply SolidCraft marks "still running:" is awaited through its `pending` tool, and
SolidCraft's stderr goes to a log file whose tail is attached to errors.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from .remote import TOKEN_ENV, Remote, RemoteError

log = logging.getLogger(__name__)
STILL_RUNNING = "still running:"


class SessionError(RuntimeError):
	pass


class SessionTimeout(SessionError):
	pass


@dataclass(frozen=True, slots=True)
class Reply:
	ok: bool
	body: Any

	@property
	def error(self) -> str:
		return "" if self.ok else str(self.body)

	def get(self, key: str, default: Any = None) -> Any:
		return self.body.get(key, default) if isinstance(self.body, dict) else default


@dataclass
class SolidCraft:
	session: ClientSession
	timeout: float
	log_path: Path
	server: dict[str, Any] = field(default_factory=dict)
	remote: Remote | None = None

	def path(self, local: Path | str) -> str:
		"""A local file's path as SolidCraft (on this computer or the remote one) names it."""
		return self.remote.remote_path(local) if self.remote else str(Path(local).resolve())

	async def tool(self, name: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> Reply:
		reply = await self._call(name, args or {}, timeout)
		if not reply.ok and isinstance(reply.body, str) and reply.body.startswith(STILL_RUNNING):
			reply = await self._wait_pending(timeout)
		return reply

	async def _call(self, name: str, args: dict[str, Any], timeout: float | None) -> Reply:
		t = self.timeout if timeout is None else timeout
		try:
			result = await self.session.call_tool(name, args, read_timeout_seconds=timedelta(seconds=t))
		except McpError as e:
			if "timed out" in str(e).lower() or getattr(e.error, "code", None) == 408:
				raise SessionTimeout(f"{name} got no reply in {t:.0f} s{self.stderr_tail()}") from e
			raise SessionError(f"{name}: {e}{self.stderr_tail()}") from e
		except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream) as e:
			raise SessionError(f"SolidCraft went away during {name}{self.stderr_tail()}") from e
		text = "".join(getattr(c, "text", "") for c in result.content)
		try:
			body = json.loads(text) if text else {}
		except ValueError:
			body = text
		return Reply(not result.isError, body)

	async def _wait_pending(self, timeout: float | None) -> Reply:
		with anyio.fail_after(self.timeout if timeout is None else timeout):
			while True:
				reply = await self._call("pending", {}, 120)
				if not (not reply.ok and isinstance(reply.body, str) and reply.body.startswith(STILL_RUNNING)):
					return reply

	async def execute(self, command: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Reply:
		params = dict(params or {})
		if command.startswith("file.") and isinstance(params.get("path"), str):
			params["path"] = self.path(params["path"])
		return await self.tool("execute", {"command": command, "params": params}, timeout)

	async def bodies(self) -> dict[str, dict[str, Any]]:
		"""Every body by name: {name: {volume_mm3, area_mm2, center_of_mass, bbox, closed, ...}}."""
		reply = await self.tool("measure")
		if not reply.ok or not isinstance(reply.body, dict):
			raise SessionError(f"measure failed: {reply.body}")
		return {b["name"]: b for b in reply.body.get("bodies", [])}

	async def undo(self) -> bool:
		return (await self.tool("undo")).ok

	async def save(self, path: Path) -> Reply:
		return await self.tool("save", {"path": self.path(path)})

	def stderr_tail(self, n: int = 8) -> str:
		try:
			with self.log_path.open(encoding="utf-8", errors="replace") as f:
				tail = deque(f, maxlen=n)
		except OSError:
			return ""
		return ("\n  solidcraft stderr: " + "  ".join(tail).strip()) if tail else ""


@asynccontextmanager
async def open_session(
	cli: Path,
	allow: list[Path] | None = None,
	*,
	timeout: float = 300.0,
	log_path: Path | None = None,
	extra_args: list[str] | None = None,
	remote: Remote | None = None,
) -> AsyncIterator[SolidCraft]:
	"""Start `solidcraft-cli mcp --headless`, allowed to read and write the given folders, or,
	with `remote`, connect to the app there (see the module docstring)."""
	args, env = server_command(allow or [], remote)
	args += extra_args or []
	if remote:
		try:
			await remote.check_reachable(min(timeout, 30))
		except RemoteError as e:
			raise SessionError(str(e)) from e
	log_path = Path(log_path) if log_path else Path(tempfile.gettempdir()) / "fuse2sc-solidcraft.log"
	log_path.parent.mkdir(parents=True, exist_ok=True)
	with log_path.open("a", encoding="utf-8", errors="replace") as errlog:
		params = StdioServerParameters(command=str(cli), args=args, env=env, encoding_error_handler="replace")
		try:
			async with stdio_client(params, errlog=errlog) as (read, write), ClientSession(read, write) as session:
				with anyio.fail_after(min(timeout, 120)):
					init = await session.initialize()
				sc = SolidCraft(session, timeout, log_path, server=init.serverInfo.model_dump(), remote=remote)
				log.debug("connected to %s%s", sc.server, f" at {remote.address}" if remote else "")
				if remote:
					# The app keeps whatever design it has open; a build starts from nothing.
					fresh = await sc.tool("new_design", {})
					if not fresh.ok:
						raise SessionError(f"could not start a new design at {remote.address}: {fresh.error}")
				yield sc
		except BaseExceptionGroup as group:
			# The SDK's task groups wrap what went wrong; callers should see our own error.
			raise _first_leaf(group) from group


def server_command(allow: list[Path], remote: Remote | None) -> tuple[list[str], dict[str, str] | None]:
	"""solidcraft-cli's arguments and extra environment for a local or a remote session.

	Remote: the folders are the app's to allow, on its computer; the local server's own check
	would test the remote paths against this computer's disk, so it is lifted (--allow-any-path)."""
	if remote:
		return ["mcp", "--connect", remote.address, "--allow-any-path"], {TOKEN_ENV: remote.token}
	args = ["mcp", "--headless"]
	for p in allow:
		args += ["--allow-path", str(Path(p).resolve())]
	return args, None


def _first_leaf(group: BaseExceptionGroup) -> BaseException:
	leaves = []
	stack: list[BaseException] = [group]
	while stack:
		e = stack.pop()
		if isinstance(e, BaseExceptionGroup):
			stack.extend(e.exceptions)
		else:
			leaves.append(e)
	ours = [e for e in leaves if isinstance(e, SessionError)]
	if ours:
		return ours[0]
	other = next((e for e in leaves if not isinstance(e, anyio.get_cancelled_exc_class())), None)
	return SessionError(f"SolidCraft session failed: {other!r}") if other else group
