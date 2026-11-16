"""A SolidCraft app on another computer, driven through its control channel.

The desktop app opens a control channel (port 27878 by default) when it is started with
`--control PORT` or allowed to in Preferences › AI. `solidcraft-cli mcp --connect HOST:PORT` turns
that channel into the MCP server fuse2sc drives, so a remote build runs a local solidcraft-cli
(the same version as the app) that talks to the app over TCP.

The channel asks for the app's control token. fuse2sc reads it from $SOLIDCRAFT_CONTROL_TOKEN and
hands it to solidcraft-cli in its environment, never on its command line or in a settings file.

Files: commands that name files (Fusion's STEP results, the saved design) are opened by the app,
on its own computer. The two computers must share the design folders, and `paths` says what the
remote computer calls each shared local folder; a path under none of them is sent unchanged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import anyio

DEFAULT_PORT = 27878
TOKEN_ENV = "SOLIDCRAFT_CONTROL_TOKEN"
_ADDRESS = re.compile(r"^(?:\[(?P<v6>[^\]]+)\]|(?P<host>[^:\[\]]+))(?::(?P<port>\d+))?$")
_WINDOWS = re.compile(r"^(?:[A-Za-z]:|\\\\)|\\")


class RemoteError(RuntimeError):
	pass


@dataclass(frozen=True)
class Remote:
	host: str
	port: int = DEFAULT_PORT
	token: str = field(default="", repr=False)
	paths: tuple[tuple[Path, str], ...] = ()  # (local folder, the remote computer's name for it)

	@classmethod
	def parse(cls, address: str, *, token: str | None = None, paths: dict[str, str] | None = None) -> Remote:
		"""`host`, `host:port` or `[ipv6]:port`; the token defaults to $SOLIDCRAFT_CONTROL_TOKEN."""
		m = _ADDRESS.match(address.strip())
		port = int(m["port"]) if m and m["port"] else DEFAULT_PORT
		if not m or not 0 < port < 65536:
			raise RemoteError(f"not a SolidCraft address: {address!r} (expected host, host:port or [ipv6]:port)")
		mapped = sorted(
			((Path(local).resolve(), remote) for local, remote in (paths or {}).items()),
			key=lambda pair: len(pair[0].parts),
			reverse=True,  # the most specific folder wins
		)
		return cls(m["v6"] or m["host"], port, os.environ.get(TOKEN_ENV, "") if token is None else token, tuple(mapped))

	@property
	def address(self) -> str:
		return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"

	def remote_path(self, local: Path | str) -> str:
		"""What the remote computer calls a local file: mapped through `paths`, else unchanged."""
		p = Path(local).resolve()
		for folder, remote in self.paths:
			if p.is_relative_to(folder):
				base: PurePath = PureWindowsPath(remote) if _WINDOWS.search(remote) else PurePosixPath(remote)
				return str(base.joinpath(*p.relative_to(folder).parts))
		return str(p)

	async def check_reachable(self, timeout: float = 10.0) -> None:
		"""Fail early, and clearly, when nothing listens at the address (solidcraft-cli itself
		would start anyway and fail on the first tool call)."""
		if not self.token:
			raise RemoteError(f"no control token for {self.address}: set {TOKEN_ENV} to the app's token")
		try:
			with anyio.fail_after(timeout):
				stream = await anyio.connect_tcp(self.host, self.port)
			await stream.aclose()
		except (OSError, TimeoutError) as e:
			raise RemoteError(
				f"no SolidCraft at {self.address} ({e or 'timed out'}): is the app running with its control "
				"channel open and reachable from here?"
			) from e
