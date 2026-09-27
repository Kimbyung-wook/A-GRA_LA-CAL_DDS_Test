"""One OWP connection: state machine AWAIT_INIT -> READY -> CLOSED (OMSC-SPC-013 5.1.1.1)."""
from __future__ import annotations

import asyncio
import enum
import itertools
import json
import logging
import time
from typing import TYPE_CHECKING

from . import owp_codec as owp
from .graph import ServiceNode
from .omsjson import InvalidMessage
from .router import Envelope, Subscription

if TYPE_CHECKING:
    from .app import CalServer

log = logging.getLogger(__name__)
_ids = itertools.count(1)


class State(enum.Enum):
    AWAIT_INIT = 1
    READY = 2
    CLOSED = 3


class FailConnection(Exception):
    """INIT problems require failing the WebSocket connection (RFC 6455 7.1.7)."""

    def __init__(self, reply: str):
        super().__init__(reply)
        self.reply = reply


class Session:
    def __init__(self, server: "CalServer", remote_addr: str = ""):
        self.server = server
        self.remote_addr = remote_addr
        self.state = State.AWAIT_INIT
        self.verbose = True
        self.version: str | None = None
        self.schema: str | None = None
        self.service = None
        self.node_id = f"svc:{server.config.server_id}:{next(_ids)}"
        self.subs: dict[str, Subscription] = {}
        self.outbox: asyncio.Queue[str] = asyncio.Queue(server.config.outbound_queue)

    # ------------------------------------------------------------------ outbound
    def send(self, text: str) -> None:
        try:
            self.outbox.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("%s: outbound queue full, dropping", self.node_id)

    def deliver(self, sub: Subscription, message: str) -> bool:
        if self.state is not State.READY:
            return False
        try:
            self.outbox.put_nowait(owp.fmt_msg(sub.sub_id, message))
            return True
        except asyncio.QueueFull:
            return False

    # ------------------------------------------------------------------ inbound
    def handle(self, text: str) -> None:
        """Process one WebSocket message. Raises FailConnection to close the connection."""
        try:
            op = owp.parse_client(text)
            if self.state is State.AWAIT_INIT and op.name != "INIT":
                raise FailConnection(owp.fmt_err(owp.ILLEGAL_STATE, "INIT must be the first operation"))
            if op.name == "INIT":
                self._init(op.args[0])
                return
            getattr(self, "_" + op.name.lower())(*op.args)
            if self.verbose:
                self.send(owp.fmt_ok())
        except owp.OwpError as err:
            if self.state is State.AWAIT_INIT:
                raise FailConnection(owp.fmt_err(err.code, err.detail)) from err
            self.send(owp.fmt_err(err.code, err.detail))
        except FailConnection:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("internal error")
            self.send(owp.fmt_err(owp.INTERNAL_ERROR, type(exc).__name__))

    def _init(self, json_text: str) -> None:
        if self.state is not State.AWAIT_INIT:
            raise FailConnection(owp.fmt_err(owp.ILLEGAL_STATE, "INIT sent more than once"))
        try:
            req = owp.parse_json_object(json_text)
        except owp.OwpError as err:
            raise FailConnection(owp.fmt_err(err.code, err.detail)) from err
        cfg = self.server.config
        versions, schema, service_id = req.get("versions"), req.get("schema"), req.get("service_id")
        if (not isinstance(versions, list) or not all(isinstance(v, str) for v in versions)
                or not isinstance(schema, str) or not isinstance(service_id, str)
                or not isinstance(req.get("verbose", True), bool)):
            raise FailConnection(owp.fmt_err(owp.ILLEGAL_ARGUMENT, "INIT requires versions[], schema, service_id"))
        common = [v for v in cfg.owp_versions if v in versions]
        if not common:
            raise FailConnection(owp.fmt_err(owp.UNSUPPORTED_VERSION, f"server supports {cfg.owp_versions}"))
        if schema not in cfg.schema_versions:
            raise FailConnection(owp.fmt_err(owp.UNSUPPORTED_SCHEMA, f"server supports {cfg.schema_versions}"))
        service = cfg.service(service_id)
        if service is None:
            raise FailConnection(owp.fmt_err(owp.UNSUPPORTED_SERVICE, service_id))

        self.version = max(common, key=lambda v: tuple(int(x) for x in v.split(".")))
        self.schema, self.service, self.verbose = schema, service, req.get("verbose", True)
        self.state = State.READY
        uuids = {"system": cfg.system_uuid, "service": service.service}
        if service.subsystem:
            uuids["subsystem"] = service.subsystem
        if service.capabilities:
            uuids["capabilities"] = service.capabilities
        if service.components:
            uuids["components"] = service.components
        info = {"version": self.version, "server_id": cfg.server_id, "uuids": uuids,
                "system_label": service.system_label or cfg.system_label}
        if cfg.connect_urls:
            info["connect_urls"] = cfg.connect_urls
        if self.verbose:
            self.send(owp.fmt_ok())  # the first +OK precedes INFO on verbose connections
        self.send(owp.fmt_info(info))
        self.server.graph.add_service(ServiceNode(
            id=self.node_id, service_id=service_id, server_id=cfg.server_id,
            system_label=info["system_label"], uuids=uuids, connected_at=time.time(),
            remote_addr=self.remote_addr,
            declared={"pub": service.declared_pub, "sub": service.declared_sub}))
        log.info("%s INIT service_id=%s version=%s", self.node_id, service_id, self.version)

    def _pub(self, topic: str, message: str) -> None:
        if not self.server.config.topic_allowed(topic):
            raise owp.OwpError(owp.ILLEGAL_STATE, f"topic {topic} is not allowed")
        try:
            name, obj, _xml = self.server.codec.parse_message(message)
        except InvalidMessage as exc:
            self.server.graph.note_invalid(self.node_id)
            raise owp.OwpError(owp.INVALID_MESSAGE, str(exc)[:300]) from exc
        canonical = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        env = Envelope(message_name=name, cal_topic=topic, src_system=self.server.config.system_uuid,
                       src_service=self.service.service, server_id=self.server.config.server_id,
                       encoding=1, payload=canonical.encode("utf-8"))
        self.server.router.publish_local(self.node_id, env, canonical)

    def _sub(self, sub_id: str, message_name: str, topic: str, group: str | None = None) -> None:
        if self.server.codec.global_element(message_name) is None:
            raise owp.OwpError(owp.ILLEGAL_ARGUMENT, f"{message_name} is not a global element")
        if sub_id in self.subs:
            raise owp.OwpError(owp.ILLEGAL_STATE, f"subscription id {sub_id} already in use")
        if not self.server.config.topic_allowed(topic):
            raise owp.OwpError(owp.ILLEGAL_STATE, f"topic {topic} is not allowed")
        sub = Subscription(self, sub_id, message_name, topic, group)
        self.subs[sub_id] = sub
        self.server.router.add(sub)

    def _unsub(self, sub_id: str) -> None:
        sub = self.subs.pop(sub_id, None)
        if sub is None:
            raise owp.OwpError(owp.ILLEGAL_STATE, f"subscription id {sub_id} is not active")
        self.server.router.remove(sub)

    # ------------------------------------------------------------------ teardown
    def close(self) -> None:
        if self.state is State.CLOSED:
            return
        was_ready = self.state is State.READY
        self.state = State.CLOSED
        for sub in list(self.subs.values()):
            self.server.router.remove(sub)
        self.subs.clear()
        if was_ready:
            self.server.graph.remove_service(self.node_id)
        log.info("%s closed", self.node_id)
