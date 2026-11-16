"""What the build needs from SolidCraft: five calls.

`fuse2sc.solidcraft.SolidCraft` (the MCP session) satisfies this protocol; tests use an in-memory
fake (tests/unit/fake_port.py), so the build's logic is tested without SolidCraft.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..solidcraft import Reply


class SolidCraftPort(Protocol):
	async def execute(
		self, command: str, params: dict[str, Any] | None = None, timeout: float | None = None
	) -> Reply: ...

	async def tool(self, name: str, args: dict[str, Any] | None = None, timeout: float | None = None) -> Reply: ...

	async def bodies(self) -> dict[str, dict[str, Any]]: ...

	async def undo(self) -> bool: ...

	async def save(self, path: Path) -> Reply: ...
