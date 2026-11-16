"""SolidCraft: finding solidcraft-cli, its MCP session (local or a remote app), and one-shot runs."""

from .locate import CliNotFound, cli_version, find_cli, in_range
from .remote import Remote, RemoteError
from .session import Reply, SessionError, SessionTimeout, SolidCraft, open_session

__all__ = [
	"CliNotFound",
	"Remote",
	"RemoteError",
	"Reply",
	"SessionError",
	"SessionTimeout",
	"SolidCraft",
	"cli_version",
	"find_cli",
	"in_range",
	"open_session",
]
