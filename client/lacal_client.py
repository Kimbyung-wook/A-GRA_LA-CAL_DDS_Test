"""asyncio CAL Client for the OMS WebSocket Protocol (OWP 1.0)."""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections import deque
from typing import Awaitable, Callable, Union

from websockets.asyncio.client import ClientConnection, connect

from server import owp_codec as owp

log = logging.getLogger(__name__)

MessageCallback = Callable[[str, dict], Union[None, Awaitable[None]]]  # (message_name, OMS JSON object)


class CalError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class LaCalClient:
    def __init__(self, url: str, service_id: str, schema: str = "006.0.ASK",
                 versions: tuple[str, ...] = ("1.0",), verbose: bool = True):
        self.url = url
        self.service_id = service_id
        self.schema = schema
        self.versions = list(versions)
        self.verbose = verbose
        self.info: dict = {}
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._pending: deque[asyncio.Future] = deque()   # FIFO: one +OK/-ERR per operation
        self._callbacks: dict[str, tuple[str, MessageCallback]] = {}
        self._ids = itertools.count(1)
        self.errors: list[CalError] = []                  # -ERR seen on non-verbose connections

    # ------------------------------------------------------------------ connection
    async def connect(self) -> dict:
        self._ws = await connect(self.url, subprotocols=[owp_subprotocol()], max_size=16 * 2**20)
        init = {"versions": self.versions, "schema": self.schema, "service_id": self.service_id,
                "verbose": self.verbose}
        await self._ws.send("INIT " + json.dumps(init))
        while True:
            op = owp.parse_server(await self._ws.recv())
            if op.name == "INFO":
                self.info = json.loads(op.args[0])
                break
            if op.name == "-ERR":
                raise CalError(*op.args)
        self._reader = asyncio.create_task(self._read_loop())
        return self.info

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()

    async def __aenter__(self) -> "LaCalClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    @property
    def system_uuid(self) -> str:
        return self.info["uuids"]["system"]

    @property
    def service_uuid(self) -> str:
        return self.info["uuids"]["service"]

    # ------------------------------------------------------------------ operations
    async def publish(self, topic: str, message: dict) -> None:
        await self._op(f"PUB {topic} " + json.dumps(message, separators=(",", ":")))

    async def subscribe(self, message_name: str, topic: str, callback: MessageCallback,
                        group: str | None = None, sub_id: str | None = None) -> str:
        sub_id = sub_id or f"s{next(self._ids)}"
        self._callbacks[sub_id] = (message_name, callback)
        try:
            await self._op(" ".join(filter(None, ["SUB", sub_id, message_name, topic, group])))
        except CalError:
            self._callbacks.pop(sub_id, None)
            raise
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        await self._op(f"UNSUB {sub_id}")
        self._callbacks.pop(sub_id, None)

    async def _op(self, text: str) -> None:
        assert self._ws is not None, "connect() first"
        if not self.verbose:
            await self._ws.send(text)
            return
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending.append(fut)
        await self._ws.send(text)
        await fut

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                op = owp.parse_server(raw)
            except owp.OwpError:
                log.warning("unexpected server frame: %.80s", raw)
                continue
            if op.name == "MSG":
                sub_id, message = op.args
                entry = self._callbacks.get(sub_id)
                if entry:
                    obj = json.loads(message)
                    res = entry[1](next(iter(obj)), obj)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
            elif op.name == "+OK":
                if self._pending:
                    self._pending.popleft().set_result(None)
            elif op.name == "-ERR":
                err = CalError(*op.args)
                if self._pending:
                    self._pending.popleft().set_exception(err)
                else:
                    self.errors.append(err)
                    log.warning("server error: %s", err)
            elif op.name == "INFO":
                self.info = json.loads(op.args[0])


def owp_subprotocol():
    from websockets.typing import Subprotocol
    return Subprotocol("owp")
