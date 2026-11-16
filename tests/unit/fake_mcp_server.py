"""A stand-in for `solidcraft-cli mcp`, built on the MCP SDK's own server, that misbehaves on
demand (argv[1]): normal, hang (never answers a tool call), pending (first answer is "still
running:"), crash (exits during a tool call)."""

import json
import os
import sys
import time
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
server = Server("fake-solidcraft")
state = {"pending": MODE == "pending"}
TOOLS = ("execute", "measure", "undo", "pending", "save", "new_design")


@server.list_tools()
async def list_tools() -> list[Tool]:
	return [Tool(name=n, description=n, inputSchema={"type": "object"}) for n in TOOLS]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
	if MODE == "hang":
		time.sleep(30)
	if MODE == "crash":
		os._exit(3)
	if state["pending"] and name != "pending":
		state["pending"] = False
		raise RuntimeError("still running: execute")
	if name == "measure":
		return [TextContent(type="text", text=json.dumps({"bodies": [{"name": "B", "volume_mm3": 1.0}]}))]
	if name == "execute" and arguments.get("command") == "bad":
		raise RuntimeError("refused")
	return [TextContent(type="text", text=json.dumps({"tool": name, "arguments": arguments}))]


async def main() -> None:
	async with stdio_server() as (read, write):
		await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
	import anyio

	anyio.run(main)
