"""An in-memory SolidCraft for testing the build's logic without SolidCraft.

Bodies are {name: {volume_mm3, center_of_mass}}. Commands do nothing unless a test teaches them
(`on(command, handler)`); a handler gets the params and the fake and returns a Reply, and may change
`fake.model`. Renames, removals and undo work out of the box. Every call is recorded in `calls`.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fuse2sc.solidcraft import Reply

Handler = Callable[[dict[str, Any], "FakeSolidCraft"], Reply]


def body(volume: float, com: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> dict[str, Any]:
	return {"volume_mm3": float(volume), "center_of_mass": list(com)}


class FakeSolidCraft:
	def __init__(self) -> None:
		self.model: dict[str, dict[str, Any]] = {}
		self.calls: list[tuple[str, dict[str, Any]]] = []
		self.undone = 0
		self._history: list[dict[str, dict[str, Any]]] = []
		self._handlers: dict[str, Handler] = {"body.rename": _rename, "solid.remove": _remove}

	def on(self, command: str, handler: Handler) -> FakeSolidCraft:
		self._handlers[command] = handler
		return self

	def commands(self, name: str | None = None) -> list[tuple[str, dict[str, Any]]]:
		return [c for c in self.calls if name is None or c[0] == name]

	# --- the port ---------------------------------------------------------------------------------

	async def execute(self, command: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Reply:
		params = params or {}
		self.calls.append((command, params))
		before = copy.deepcopy(self.model)
		reply = self._handlers.get(command, lambda p, f: Reply(True, {}))(params, self)
		if reply.ok and command not in ("sketch.inspect",):
			self._history.append(before)
		else:
			self.model = before
		return reply

	async def tool(self, name: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> Reply:
		self.calls.append((f"tool:{name}", args or {}))
		return Reply(True, {})

	async def bodies(self) -> dict[str, dict[str, Any]]:
		return {k: dict(v, name=k) for k, v in copy.deepcopy(self.model).items()}

	async def undo(self) -> bool:
		if not self._history:
			return False
		self.model = self._history.pop()
		self.undone += 1
		return True

	async def save(self, path: Path) -> Reply:
		return Reply(True, {"path": str(path)})


def _rename(params: dict[str, Any], fake: FakeSolidCraft) -> Reply:
	if params["body"] not in fake.model or params["name"] in fake.model:
		return Reply(False, "no such body, or the name is taken")
	fake.model[params["name"]] = fake.model.pop(params["body"])
	return Reply(True, {})


def _remove(params: dict[str, Any], fake: FakeSolidCraft) -> Reply:
	for b in params["bodies"]:
		fake.model.pop(b, None)
	return Reply(True, {})


def creates(name: str, volume: float, com: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Handler:
	"""A handler for a command that makes one new body."""

	def handler(params: dict[str, Any], fake: FakeSolidCraft) -> Reply:
		fake.model[name] = body(volume, com)
		return Reply(True, {"created_bodies": [name]})

	return handler


def refuses(message: str = "refused") -> Handler:
	return lambda params, fake: Reply(False, message)
