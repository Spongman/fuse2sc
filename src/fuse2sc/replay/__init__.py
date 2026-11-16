"""Replay a plan in SolidCraft with per-step checks and fallbacks.

engine.py        orchestration: replay() step by step; Engine adds the session, saving, results
runner.py        one command: preparation, variants, retry strategies, the sketch guard
checker.py       after a step: compare with Fusion, rename, substitute, track divergence
substitution.py  Fusion's own result in place of SolidCraft's bodies
naming.py        body identity: matching and safe renames
strategies.py    retries for refusals, scoped to SolidCraft versions
checks.py        comparisons and every tolerance
port.py          the five calls the build needs from SolidCraft
"""

from .checks import Tolerances
from .engine import Engine, build, categorize, replay
from .results import BuildResult, StepResult, Verification
from .sources import ExportedStep, Sources, StepsFolder, StepUnavailable
from .state import BuildState, Options

__all__ = [
	"BuildResult",
	"BuildState",
	"Engine",
	"ExportedStep",
	"Options",
	"Sources",
	"StepResult",
	"StepUnavailable",
	"StepsFolder",
	"Tolerances",
	"Verification",
	"build",
	"categorize",
	"replay",
]
