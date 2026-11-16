"""The intermediate format shared by the Fusion add-in and the host tools (stdlib only).

This package is the single source of truth; tools/sync_addin.py copies it into
fusion_addin/Fuse2SC/fuse2sc_ir, and a test fails if the copy drifts.
"""

from . import keys, units
from .schema import SCHEMA_VERSION, IRError, dump, final_bodies, load, new_document, upgrade, validate

__all__ = [
	"SCHEMA_VERSION",
	"IRError",
	"dump",
	"final_bodies",
	"keys",
	"load",
	"new_document",
	"units",
	"upgrade",
	"validate",
]
