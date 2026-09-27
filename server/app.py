"""CAL Server: OWP WebSocket endpoint + admin/graph API + backend wiring."""
from __future__ import annotations

import asyncio
import json
import logging
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .config import ServerConfig
from .graph import GraphModel
from .omsjson import OmsJsonCodec
from .router import Router, Tap
from .session import FailConnection, Session

log = logging.getLogger(__name__)

OWP_SUBPROTOCOL = "owp"
STATS_INTERVAL_S = 1.0


class CalServer:
    def __init__(self, config: ServerConfig, codec: OmsJsonCodec | None = None):
        self.config = config
        self.codec = codec or OmsJsonCodec(config.schema_path)
        self.graph = GraphModel(config.server_id, config.system_label, config.system_uuid,
                                config.graph_inactive_after_s)
        self.router = Router(self.graph, config.server_id)
        self.sessions: set[Session] = set()
        self._servers = []
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if self.config.backend == "dds":
            from .backend_dds import DdsBackend
            self.router.backend = DdsBackend(self.config, self.graph)
            await self.router.backend.start(self.router)
        # websockets rejects a handshake with HTTP 400 when the client offers no subprotocol
        # or none we support (CERT LACAL-000002..005).
        self._servers.append(await serve(self._owp_handler, self.config.host, self.config.port,
                                         subprotocols=[OWP_SUBPROTOCOL]))
        self._servers.append(await serve(self._admin_handler, self.config.admin_host, self.config.admin_port,
                                         process_request=self._admin_http))
        self._tasks.append(asyncio.create_task(self._stats_loop()))
        log.info("CAL Server %s: OWP ws://%s:%d  admin http://%s:%d", self.config.server_id,
                 self.config.host, self.port, self.config.admin_host, self.admin_port)

    @property
    def port(self) -> int:
        return self._servers[0].sockets[0].getsockname()[1]

    @property
    def admin_port(self) -> int:
        return self._servers[1].sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for s in self._servers:
            s.close()
            await s.wait_closed()
        if self.router.backend is not None:
            await self.router.backend.stop()

    # ------------------------------------------------------------------ OWP
    async def _owp_handler(self, ws: ServerConnection) -> None:
        addr = ws.remote_address
        session = Session(self, f"{addr[0]}:{addr[1]}" if addr else "")
        self.sessions.add(session)
        writer = asyncio.create_task(self._writer(ws, session))
        try:
            async for raw in ws:
                if isinstance(raw, bytes):  # OWP is text-only
                    raw = raw.decode("utf-8", "replace")
                try:
                    session.handle(raw)
                except FailConnection as fail:
                    await self._flush(ws, session)
                    await ws.send(fail.reply)
                    await ws.close(1008, fail.reply[:120])
                    break
        except ConnectionClosed:
            pass
        finally:
            session.close()
            self.sessions.discard(session)
            writer.cancel()

    @staticmethod
    async def _writer(ws: ServerConnection, session: Session) -> None:
        try:
            while True:
                await ws.send(await session.outbox.get())
        except (ConnectionClosed, asyncio.CancelledError):
            pass

    @staticmethod
    async def _flush(ws: ServerConnection, session: Session) -> None:
        while not session.outbox.empty():
            await ws.send(session.outbox.get_nowait())

    # ------------------------------------------------------------------ admin API
    def _admin_http(self, conn: ServerConnection, request: Request) -> Response | None:
        url = urlsplit(request.path)
        if url.path in ("/graph/events", "/tap"):
            if "upgrade" not in request.headers.get("Connection", "").lower():
                return conn.respond(HTTPStatus.UPGRADE_REQUIRED, "WebSocket endpoint\n")
            return None  # continue with the WebSocket handshake
        routes = {
            "/graph": lambda: _json(self.graph.snapshot()),
            "/graph/local": lambda: _json(self.graph.local_snapshot()),
            "/graph.dot": lambda: (self.graph.to_dot(), "text/vnd.graphviz"),
            "/services": lambda: _json([n for n in self.graph.snapshot()["nodes"] if n["kind"] == "service"]),
            "/topics": lambda: _json([n for n in self.graph.snapshot()["nodes"] if n["kind"] == "topic"]),
            "/stats": lambda: _json({"server_id": self.config.server_id, "sessions": len(self.sessions),
                                     **self.router.stats}),
            "/healthz": lambda: ("ok\n", "text/plain"),
        }
        handler = routes.get(url.path)
        if handler is None:
            return conn.respond(HTTPStatus.NOT_FOUND, "not found\n")
        body, ctype = handler()
        resp = conn.respond(HTTPStatus.OK, body)
        del resp.headers["Content-Type"]  # Headers is multi-valued: replace, don't append
        resp.headers["Content-Type"] = f"{ctype}; charset=utf-8"
        resp.headers["Access-Control-Allow-Origin"] = "*"  # for a browser-based graph viewer
        return resp

    async def _admin_handler(self, ws: ServerConnection) -> None:
        url = urlsplit(ws.request.path)
        if url.path == "/graph/events":
            await self._graph_events(ws)
        elif url.path == "/tap":
            q = parse_qs(url.query)
            await self._tap(ws, q.get("topic", [None])[0], q.get("msg", [None])[0])

    async def _graph_events(self, ws: ServerConnection) -> None:
        queue = self.graph.listen()
        try:
            await ws.send(json.dumps({"type": "snapshot", "graph": self.graph.snapshot()}))
            await _pump(ws, queue)
        finally:
            self.graph.unlisten(queue)

    async def _tap(self, ws: ServerConnection, topic: str | None, msg: str | None) -> None:
        queue: asyncio.Queue = asyncio.Queue(1000)

        def on_msg(t: str, name: str, origin: str, message: str) -> None:
            try:
                queue.put_nowait({"topic": t, "message_name": name, "origin": origin, "message": json.loads(message)})
            except asyncio.QueueFull:
                pass

        tap = Tap(on_msg, topic, msg)
        self.router.taps.add(tap)
        try:
            await _pump(ws, queue)
        finally:
            self.router.taps.discard(tap)

    async def _stats_loop(self) -> None:
        """Periodic rate/count update for live graph viewers."""
        while True:
            await asyncio.sleep(STATS_INTERVAL_S)
            snap = self.graph.local_snapshot()
            self.graph._emit({"type": "stats", "edges": [
                {k: e[k] for k in ("id", "msg_count", "byte_count", "rate_hz", "drop_count", "active")}
                for e in snap["edges"]]}, structural=False)


async def _pump(ws: ServerConnection, queue: asyncio.Queue) -> None:
    """Forward queued events as JSON until the peer (or the server) closes the connection.

    Waiting on the queue alone would never notice a closed connection, which would keep
    server shutdown waiting on this handler forever.
    """
    closed = asyncio.ensure_future(ws.wait_closed())
    try:
        while True:
            get = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({get, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed in done:
                get.cancel()
                return
            await ws.send(json.dumps(get.result()))
    except ConnectionClosed:
        pass
    finally:
        closed.cancel()


def _json(obj) -> tuple[str, str]:
    return json.dumps(obj, indent=1), "application/json"
