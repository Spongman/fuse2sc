"""The geometry route: a design with no timeline (direct modelling, imports) comes in as Fusion's
STEP: the exact shape, without history."""

from __future__ import annotations

from pathlib import Path

from ..solidcraft import Remote, open_session
from .results import BuildResult


async def import_step(
	cli: Path, step: Path, out: Path, *, name: str, document: str, sc_version: str, remote: Remote | None = None
) -> BuildResult:
	result = BuildResult(document=document, solidcraft_version=sc_version, commands=1)
	log = out.with_suffix(".solidcraft.log")
	async with open_session(cli, [out.parent, step.parent], log_path=log, remote=remote) as sc:
		reply = await sc.execute("file.insert_step", {"path": str(step), "name": name, "names": "auto"})
		if not reply.ok:
			result.failed.append(f"importing {step.name} failed: {str(reply.body)[:300]}")
			return result
		saved = await sc.save(out)
		result.saved = saved.ok
		if not saved.ok:
			result.failed.append(f"save: {saved.body}")
	result.out = str(out) if result.saved else None
	result.category = "geometry" if result.saved else "failed"
	return result
