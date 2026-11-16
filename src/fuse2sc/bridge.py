"""Asking a running Fusion for its result at a timeline step, through the add-in's bridge.

The bridge listens on 127.0.0.1 only, runs a fixed set of operations (never code it is sent),
and requires a random token. When the add-in starts a build it passes the address and token in
the environment (FUSE2SC_BRIDGE_URL, FUSE2SC_BRIDGE_TOKEN); when it is switched on for terminal use, it
writes them to <user config>/fuse2sc/bridge.json, readable only by the user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .replay.sources import ExportedStep, StepUnavailable
from .settings import user_config_dir


@dataclass
class Bridge:
	url: str
	token: str
	document_id: str | None = None
	timeout: float = 900.0

	async def _post(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
		try:
			async with httpx.AsyncClient(
				base_url=self.url, timeout=self.timeout, headers={"X-Fuse2SC-Token": self.token}
			) as client:
				r = await client.post(f"/{op}", json=payload)
				r.raise_for_status()
				return r.json()
		except httpx.HTTPStatusError as e:
			raise StepUnavailable(f"Fusion bridge: HTTP {e.response.status_code} {e.response.text[:300]}") from e
		except (httpx.HTTPError, ValueError) as e:
			raise StepUnavailable(f"Fusion bridge unreachable: {e}") from e

	async def ping(self) -> dict[str, Any]:
		return await self._post("ping", {})

	async def export(self, index: int, components: list[str]) -> list[ExportedStep]:
		r = await self._post("export_step", {"index": index, "components": components, "document_id": self.document_id})
		if not r.get("ok"):
			raise StepUnavailable(f"Fusion bridge: {r.get('error', 'export failed')}")
		return [ExportedStep(f["component"], Path(f["path"]), f.get("matrix")) for f in r["files"]]


def connection_file() -> Path:
	return user_config_dir() / "bridge.json"


def from_environment(document_id: str | None = None, use_file: bool = False) -> Bridge | None:
	"""The bridge the add-in started this build with, or (with use_file) the one switched on in
	Fusion for terminal use."""
	url, token = os.environ.get("FUSE2SC_BRIDGE_URL"), os.environ.get("FUSE2SC_BRIDGE_TOKEN")
	if url and token:
		return Bridge(url, token, document_id)
	if use_file:
		try:
			d = json.loads(connection_file().read_text(encoding="utf-8"))
			return Bridge(d["url"], d["token"], document_id)
		except (OSError, ValueError, KeyError):
			return None
	return None
