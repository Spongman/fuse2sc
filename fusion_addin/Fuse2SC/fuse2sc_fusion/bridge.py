"""A small local server so a build running outside Fusion can ask Fusion for its result at a step.

- Listens on 127.0.0.1 only, on a free port.
- Every request carries the token (X-Fuse2SC-Token), compared in constant time.
- Fixed operations only: `ping` and `export_step`. It never runs code it is sent.
- Fusion's API may only be used on Fusion's main thread: the HTTP thread queues the request and
  fires a custom event; Fusion runs it when idle and hands the answer back.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import adsk.core

from .export import export_at_step
from .reader import design_of

if TYPE_CHECKING:
	from .records import Record

EVENT_ID = "Fuse2SC_BridgeRequest"
MAX_BODY = 1 << 20
OPERATIONS = ("ping", "export_step")


@dataclass
class Job:
	op: str
	payload: Record
	done: threading.Event = field(default_factory=threading.Event)
	result: Record | None = None


class Bridge:
	def __init__(self, app: adsk.core.Application, steps_folder: Path, keep_alive: list[Any]) -> None:
		self.app = app
		self.steps_folder = Path(steps_folder)
		self.token = secrets.token_urlsafe(32)
		self.jobs: dict[str, Job] = {}
		self.lock = threading.Lock()
		self.keep_alive = keep_alive  # Fusion drops event handlers that nothing references
		self.server: ThreadingHTTPServer | None = None

	@property
	def url(self) -> str:
		assert self.server is not None, "start() the bridge first"
		return f"http://127.0.0.1:{self.server.server_address[1]}"

	def start(self) -> "Bridge":
		event = self.app.registerCustomEvent(EVENT_ID)
		handler = _OnMainThread(self)
		event.add(handler)
		self.keep_alive.append(handler)
		self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
		threading.Thread(target=self.server.serve_forever, name="fuse2sc-bridge", daemon=True).start()
		return self

	def call(self, op: str, payload: Record, timeout: float = 900) -> Record:
		jid, job = uuid.uuid4().hex, Job(op, payload)
		with self.lock:
			self.jobs[jid] = job
		self.app.fireCustomEvent(EVENT_ID, jid)
		finished = job.done.wait(timeout)
		with self.lock:
			self.jobs.pop(jid, None)
		if not finished:
			return {"ok": False, "error": "Fusion did not answer in time"}
		return job.result or {"ok": False, "error": "Fusion gave no answer"}

	def run_job(self, jid: str) -> None:
		with self.lock:
			job = self.jobs.get(jid)
		if job is None:
			return
		try:
			job.result = self._do(job.op, job.payload)
		except Exception:
			job.result = {"ok": False, "error": traceback.format_exc().strip().splitlines()[-1]}
		finally:
			job.done.set()

	def _do(self, op: str, payload: Record) -> Record:
		doc = self.app.activeDocument
		if op == "ping":
			return {"ok": True, "document": doc.name if doc else None, "steps_folder": str(self.steps_folder)}
		wanted = payload.get("document_id")
		if wanted and (doc is None or doc.dataFile is None or doc.dataFile.id != wanted):
			return {"ok": False, "error": "that design is not the active document in Fusion"}
		files = export_at_step(
			design_of(doc), int(payload["index"]), list(payload.get("components") or []), self.steps_folder
		)
		return {"ok": True, "files": files}

	def write_connection_file(self, path: Path) -> None:
		"""The address and token for `fuse2sc build --bridge` in a terminal, readable by this user only."""
		path.parent.mkdir(parents=True, exist_ok=True)
		fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
		with os.fdopen(fd, "w", encoding="utf-8") as f:
			json.dump({"url": self.url, "token": self.token}, f)

	def stop(self) -> None:
		try:
			if self.server:
				self.server.shutdown()
				self.server.server_close()
		finally:
			try:
				self.app.unregisterCustomEvent(EVENT_ID)
			except Exception:
				pass


def _handler_for(bridge: Bridge) -> type[BaseHTTPRequestHandler]:
	class Handler(BaseHTTPRequestHandler):
		def log_message(self, *args: Any) -> None:
			pass

		def do_GET(self) -> None:
			self.send_error(405)

		def do_POST(self) -> None:
			if not hmac.compare_digest(self.headers.get("X-Fuse2SC-Token", ""), bridge.token):
				self._reply(403, {"ok": False, "error": "bad token"})
				return
			length = int(self.headers.get("Content-Length") or 0)
			if length > MAX_BODY:
				self._reply(413, {"ok": False, "error": "too large"})
				return
			try:
				payload = json.loads(self.rfile.read(length) or b"{}")
			except ValueError:
				self._reply(400, {"ok": False, "error": "bad json"})
				return
			op = self.path.strip("/")
			if op not in OPERATIONS:
				self._reply(404, {"ok": False, "error": "unknown operation"})
				return
			self._reply(200, bridge.call(op, payload))

		def _reply(self, code: int, data: Record) -> None:
			body = json.dumps(data).encode("utf-8")
			self.send_response(code)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(body)))
			self.end_headers()
			self.wfile.write(body)

	return Handler


class _OnMainThread(adsk.core.CustomEventHandler):
	def __init__(self, bridge: Bridge) -> None:
		super().__init__()
		self.bridge = bridge

	def notify(self, args: adsk.core.CustomEventArgs) -> None:
		self.bridge.run_job(args.additionalInfo)
