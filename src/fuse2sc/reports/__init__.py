"""Reports: per design (report.txt, build.json), per workspace (scorecard, index), and the 3D
viewer, rendered from jinja2 templates in reports/templates."""

from __future__ import annotations

import base64
import csv
import io
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from ..replay.results import BuildResult, StepResult
from ..settings import Workspace

env = Environment(
	loader=PackageLoader("fuse2sc.reports"),
	autoescape=select_autoescape(["html"]),
	trim_blocks=True,
	lstrip_blocks=True,
	keep_trailing_newline=True,
)

CATEGORY = {
	"full": ("✅", "every step rebuilt with history, and the result matches Fusion"),
	"partial": ("🟡", "history kept; some steps came in as Fusion's result (no history for those bodies up to there)"),
	"mismatch": ("🔶", "built, but the result does not match Fusion everywhere"),
	"geometry": ("🟠", "Fusion's shape only, without history"),
	"empty": ("⚪", "nothing to convert: Fusion has no bodies"),
	"failed": ("❌", "no design was written"),
	"not built": ("·", "not built yet"),
}
ORDER = list(CATEGORY)
STATUS = {
	"exact": "rebuilt",
	"adjusted": "rebuilt (adjusted)",
	"substituted": "FUSION RESULT",
	"mismatch": "DIFFERS",
	"failed": "FAILED",
	"skipped": "skipped",
}
env.globals.update(CATEGORY=CATEGORY, STATUS=STATUS)


def write_design(result: BuildResult, folder: Path, stem: str) -> tuple[Path, Path]:
	"""<stem>.build.json and <stem>.report.txt."""
	folder.mkdir(parents=True, exist_ok=True)
	jpath, tpath = folder / f"{stem}.build.json", folder / f"{stem}.report.txt"
	jpath.write_text(result.model_dump_json(indent=1), encoding="utf-8")
	tpath.write_text(env.get_template("report.txt.j2").render(r=result), encoding="utf-8")
	return jpath, tpath


@dataclass
class Row:
	design: str
	folder: Path
	category: str
	result: BuildResult | None = None

	@property
	def live(self) -> list[StepResult]:
		return [s for s in self.result.steps if s.status != "skipped"] if self.result else []

	@property
	def kept(self) -> int:
		return sum(1 for s in self.live if s.status in ("exact", "adjusted"))

	@property
	def substituted(self) -> int:
		return sum(1 for s in self.live if s.status == "substituted")

	@property
	def failed(self) -> int:
		return sum(1 for s in self.live if s.status in ("failed", "mismatch"))

	@property
	def blocker(self) -> StepResult | None:
		return self.result.first_blocker() if self.result else None

	@property
	def verify_text(self) -> str:
		v = self.result.verify if self.result else None
		return "" if v is None else ("ok" if v.ok else "; ".join(v.notes)[:200])

	def link(self, reports: Path) -> str | None:
		for suffix in (".viewer.html", ".report.txt"):
			if hits := sorted(self.folder.glob(f"*{suffix}")):
				return Path(os.path.relpath(hits[0], reports)).as_posix()
		return None


def collect(ws: Workspace) -> list[Row]:
	rows = []
	for d in ws.design_dirs():
		name = d.relative_to(ws.designs).as_posix()
		builds = sorted(d.glob("*.build.json"))
		if not builds:
			rows.append(Row(name, d, "not built"))
			continue
		r = BuildResult.model_validate_json(builds[0].read_text(encoding="utf-8"))
		rows.append(Row(name, d, r.category, r))
	rows.sort(key=lambda r: (ORDER.index(r.category) if r.category in ORDER else 99, r.design))
	return rows


def write_workspace(ws: Workspace, header: str = "") -> dict[str, Path]:
	rows = collect(ws)
	ws.reports.mkdir(parents=True, exist_ok=True)
	blockers = Counter(r.blocker.kind for r in rows if r.blocker)
	affected = Counter(s.kind for r in rows for s in r.live if s.status in ("substituted", "failed", "mismatch"))
	ctx = {
		"rows": rows,
		"header": header,
		"categories": Counter(r.category for r in rows),
		"steps": sum(len(r.live) for r in rows),
		"kept": sum(r.kept for r in rows),
		"blockers": blockers.most_common(),
		"affected": affected,
		"order": ORDER,
		"reports": ws.reports,
	}
	out = {"md": ws.reports / "scorecard.md", "csv": ws.reports / "scorecard.csv", "html": ws.reports / "index.html"}
	out["md"].write_text(env.get_template("scorecard.md.j2").render(**ctx), encoding="utf-8")
	out["html"].write_text(env.get_template("index.html.j2").render(**ctx), encoding="utf-8")
	buf = io.StringIO()
	w = csv.writer(buf, lineterminator="\n")
	w.writerow(
		["design", "category", "steps", "kept", "substituted", "failed", "first_blocker", "verify", "solidcraft"]
	)
	for r in rows:
		b = r.blocker
		w.writerow(
			[
				r.design,
				r.category,
				len(r.live) if r.result else "",
				r.kept if r.result else "",
				r.substituted if r.result else "",
				r.failed if r.result else "",
				f"{b.kind} ({b.name})" if b else "",
				r.verify_text,
				r.result.solidcraft_version if r.result else "",
			]
		)
	out["csv"].write_text(buf.getvalue(), encoding="utf-8")
	return out


def write_viewer(path: Path, title: str, glb: bytes | None, fusion_stl: bytes | None) -> Path:
	def b64(data: bytes | None) -> str:
		return base64.b64encode(data).decode("ascii") if data else ""

	path.write_text(
		env.get_template("viewer.html.j2").render(title=title, glb=b64(glb), stl=b64(fusion_stl)), encoding="utf-8"
	)
	return path
