"""Routes published CAL Messages to matching subscriptions (local) and to the backend (remote)."""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from .graph import GraphModel

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscription:
    owner: "Deliverer"
    sub_id: str
    message_name: str
    topic: str
    group: str | None = None


class Deliverer(Protocol):
    node_id: str

    def deliver(self, sub: Subscription, message: str) -> bool:
        """Queue a MSG for the client; return False if it had to be dropped."""


@dataclass
class Envelope:
    """What travels between CAL Servers (mirrors the DDS CalEnvelope IDL)."""
    message_name: str
    cal_topic: str
    src_system: str
    src_service: str
    server_id: str
    encoding: int      # 1 = OMS JSON (UTF-8), 2 = XML, 3 = EXI
    payload: bytes


class Backend(Protocol):
    async def start(self, router: "Router") -> None: ...
    def ensure_topic(self, cal_topic: str) -> None: ...
    def publish(self, env: Envelope) -> None: ...
    async def stop(self) -> None: ...


@dataclass(eq=False)
class Tap:
    """Admin-side observer (e.g. `lacal topic echo`); not part of the graph."""
    callback: Callable[[str, str, str, str], None]  # (topic, message_name, origin, message)
    topic: str | None = None
    message_name: str | None = None


class Router:
    def __init__(self, graph: "GraphModel", server_id: str):
        self.graph = graph
        self.server_id = server_id
        self.backend: Backend | None = None
        self._subs: dict[tuple[str, str], list[Subscription]] = {}
        self._rr: dict[tuple[str, str], itertools.count] = {}
        self.taps: set[Tap] = set()
        self.stats = {"published": 0, "delivered": 0, "dropped": 0, "remote_in": 0, "remote_out": 0}

    # ------------------------------------------------------------------ subscriptions
    def add(self, sub: Subscription) -> None:
        self._subs.setdefault((sub.message_name, sub.topic), []).append(sub)
        if self.backend is not None:
            self.backend.ensure_topic(sub.topic)
        self.graph.add_sub(sub.owner.node_id, sub.sub_id, sub.message_name, sub.topic, sub.group)

    def remove(self, sub: Subscription) -> None:
        lst = self._subs.get((sub.message_name, sub.topic), [])
        if sub in lst:
            lst.remove(sub)
            if not lst:
                del self._subs[(sub.message_name, sub.topic)]
        self.graph.remove_sub(sub.owner.node_id, sub.sub_id)

    def subscribed_keys(self) -> set[tuple[str, str]]:
        return set(self._subs)

    # ------------------------------------------------------------------ publishing
    def publish_local(self, origin_node_id: str, env: Envelope, message: str) -> None:
        """A PUB accepted from a local client: deliver locally and forward to the backend."""
        self.stats["published"] += 1
        self.graph.note_pub(origin_node_id, env.message_name, env.cal_topic, len(env.payload))
        self._deliver(env.message_name, env.cal_topic, message, origin_node_id)
        if self.backend is not None:
            self.backend.publish(env)
            self.stats["remote_out"] += 1

    def publish_remote(self, env: Envelope) -> None:
        """A message received from another CAL Server via the backend."""
        if env.server_id == self.server_id:
            return  # our own sample echoed back
        if env.encoding != 1:
            log.warning("dropping %s: unsupported payload encoding %d", env.message_name, env.encoding)
            return
        self.stats["remote_in"] += 1
        self._deliver(env.message_name, env.cal_topic, env.payload.decode("utf-8"),
                      f"remote:{env.server_id}:{env.src_service}")

    def _deliver(self, message_name: str, topic: str, message: str, origin: str) -> None:
        for tap in list(self.taps):
            if tap.topic in (None, topic) and tap.message_name in (None, message_name):
                try:
                    tap.callback(topic, message_name, origin, message)
                except Exception:  # pragma: no cover - observer bugs must not break routing
                    log.exception("tap failed")
        subs = self._subs.get((message_name, topic))
        if not subs:
            return
        # Ungrouped subscriptions all receive the message; each subscription group
        # (same group name + CAL topic, OMSC-SPC-013 4.8) gets it exactly once.
        groups: dict[str, list[Subscription]] = {}
        targets: list[Subscription] = []
        for s in subs:
            if s.group is None:
                targets.append(s)
            else:
                groups.setdefault(s.group, []).append(s)
        for gname, members in groups.items():
            counter = self._rr.setdefault((topic, gname), itertools.count())
            targets.append(members[next(counter) % len(members)])
        nbytes = len(message.encode("utf-8"))
        for s in targets:
            ok = s.owner.deliver(s, message)
            self.stats["delivered" if ok else "dropped"] += 1
            self.graph.note_delivery(s.owner.node_id, s.sub_id, nbytes, dropped=not ok)
