"""Pub/sub graph of CAL clients, in the spirit of ROS rqt_graph.

OWP has no introspection operation and DDS discovery only sees CAL Servers, so the CAL Server
itself is the source of truth: it records which Service publishes / subscribes to which
(message name, CAL topic) pair. See docs/graph_api.md for the JSON contract.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from . import primitives

GRAPH_API_VERSION = 1
RATE_WINDOW_S = 5.0


def topic_node_id(topic: str, message_name: str) -> str:
    return f"topic:{topic}/{message_name}"


@dataclass
class ServiceNode:
    id: str
    service_id: str
    server_id: str
    system_label: str
    uuids: dict[str, Any]
    connected_at: float
    remote_addr: str = ""
    declared: dict[str, list[str]] = field(default_factory=lambda: {"pub": [], "sub": []})
    kind: str = "service"

    @property
    def label(self) -> str:
        return self.service_id


@dataclass
class TopicNode:
    id: str
    topic: str
    message_name: str
    primitive: str | None
    kind: str = "topic"

    @property
    def label(self) -> str:
        return f"{self.message_name}@{self.topic}"


@dataclass
class Edge:
    id: str
    kind: str          # "pub" (service -> topic) | "sub" (topic -> service)
    source: str
    target: str
    server_id: str
    sub_id: str | None = None
    group: str | None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    msg_count: int = 0
    byte_count: int = 0
    invalid_count: int = 0
    drop_count: int = 0
    _stamps: deque = field(default_factory=lambda: deque(maxlen=2000), repr=False)

    def touch(self, nbytes: int, now: float) -> None:
        self.msg_count += 1
        self.byte_count += nbytes
        self.last_seen = now
        self._stamps.append(now)

    def rate_hz(self, now: float) -> float:
        while self._stamps and now - self._stamps[0] > RATE_WINDOW_S:
            self._stamps.popleft()
        return round(len(self._stamps) / RATE_WINDOW_S, 3)

    def to_json(self, now: float, inactive_after: float) -> dict:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        d["rate_hz"] = self.rate_hz(now)
        last = self.last_seen or self.first_seen
        d["active"] = self.kind == "sub" or (now - last) <= inactive_after
        return d


class GraphModel:
    """Local graph plus graphs merged from remote CAL Servers (via the DDS backend)."""

    def __init__(self, server_id: str, system_label: str, system_uuid: str, inactive_after_s: float = 10.0):
        self.server_id = server_id
        self.system_label = system_label
        self.system_uuid = system_uuid
        self.inactive_after = inactive_after_s
        self.services: dict[str, ServiceNode] = {}
        self.topics: dict[str, TopicNode] = {}
        self.edges: dict[str, Edge] = {}
        self.remote: dict[str, dict] = {}          # server_id -> snapshot
        self._listeners: set[asyncio.Queue] = set()
        self.version = 0                           # bumps on structural change

    # ---------------------------------------------------------------- events
    def listen(self, maxsize: int = 1000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize)
        self._listeners.add(q)
        return q

    def unlisten(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)

    def _emit(self, event: dict, structural: bool = True) -> None:
        if structural:
            self.version += 1
        event.setdefault("server_id", self.server_id)
        event["t"] = time.time()
        for q in list(self._listeners):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow observer; it can resync with GET /graph

    # ---------------------------------------------------------------- mutations
    def add_service(self, node: ServiceNode) -> None:
        self.services[node.id] = node
        self._emit({"type": "node_added", "node": self._service_json(node)})

    def remove_service(self, node_id: str) -> None:
        node = self.services.pop(node_id, None)
        if node is None:
            return
        for e in [e for e in self.edges.values() if node_id in (e.source, e.target)]:
            self._remove_edge(e.id)
        self._emit({"type": "node_removed", "id": node_id})

    def _topic(self, topic: str, message_name: str) -> TopicNode:
        tid = topic_node_id(topic, message_name)
        node = self.topics.get(tid)
        if node is None:
            node = TopicNode(tid, topic, message_name, primitives.primitive_of(message_name))
            self.topics[tid] = node
            self._emit({"type": "node_added", "node": self._topic_json(node)})
        return node

    def add_sub(self, service_node_id: str, sub_id: str, message_name: str, topic: str, group: str | None) -> None:
        t = self._topic(topic, message_name)
        eid = f"sub:{service_node_id}:{sub_id}"
        e = Edge(eid, "sub", t.id, service_node_id, self.server_id, sub_id=sub_id, group=group, first_seen=time.time())
        self.edges[eid] = e
        self._emit({"type": "edge_added", "edge": e.to_json(time.time(), self.inactive_after)})

    def remove_sub(self, service_node_id: str, sub_id: str) -> None:
        self._remove_edge(f"sub:{service_node_id}:{sub_id}")

    def note_pub(self, service_node_id: str, message_name: str, topic: str, nbytes: int) -> None:
        """Called for every accepted PUB; the first one creates the pub edge."""
        now = time.time()
        t = self._topic(topic, message_name)
        eid = f"pub:{service_node_id}:{t.id}"
        e = self.edges.get(eid)
        if e is None:
            e = Edge(eid, "pub", service_node_id, t.id, self.server_id, first_seen=now)
            self.edges[eid] = e
            e.touch(nbytes, now)
            self._emit({"type": "edge_added", "edge": e.to_json(now, self.inactive_after)})
        else:
            e.touch(nbytes, now)

    def note_invalid(self, service_node_id: str) -> None:
        node = self.services.get(service_node_id)
        if node is not None:
            node.__dict__.setdefault("invalid_count", 0)
            node.__dict__["invalid_count"] += 1

    def note_delivery(self, service_node_id: str, sub_id: str, nbytes: int, dropped: bool = False) -> None:
        e = self.edges.get(f"sub:{service_node_id}:{sub_id}")
        if e is None:
            return
        if dropped:
            e.drop_count += 1
        else:
            e.touch(nbytes, time.time())

    def _remove_edge(self, eid: str) -> None:
        e = self.edges.pop(eid, None)
        if e is None:
            return
        self._emit({"type": "edge_removed", "id": eid})
        # Drop topic nodes nobody references any more.
        for tid in (e.source, e.target):
            if tid in self.topics and not any(tid in (x.source, x.target) for x in self.edges.values()):
                del self.topics[tid]
                self._emit({"type": "node_removed", "id": tid})

    # ---------------------------------------------------------------- remote graphs
    def merge_remote(self, snapshot: dict) -> None:
        sid = snapshot.get("server_id")
        if not sid or sid == self.server_id:
            return
        self.remote[sid] = snapshot
        self._emit({"type": "remote_updated", "remote_server_id": sid})

    def remove_remote(self, server_id: str) -> None:
        if self.remote.pop(server_id, None) is not None:
            self._emit({"type": "remote_removed", "remote_server_id": server_id})

    # ---------------------------------------------------------------- views
    def _service_json(self, n: ServiceNode) -> dict:
        d = asdict(n)
        d["label"] = n.label
        d["invalid_count"] = n.__dict__.get("invalid_count", 0)
        return d

    @staticmethod
    def _topic_json(n: TopicNode) -> dict:
        d = asdict(n)
        d["label"] = n.label
        return d

    def local_snapshot(self) -> dict:
        now = time.time()
        return {
            "api_version": GRAPH_API_VERSION,
            "server_id": self.server_id,
            "system_label": self.system_label,
            "system_uuid": self.system_uuid,
            "generated_at": now,
            "version": self.version,
            "nodes": [self._service_json(n) for n in self.services.values()]
                     + [self._topic_json(n) for n in self.topics.values()],
            "edges": [e.to_json(now, self.inactive_after) for e in self.edges.values()],
        }

    def snapshot(self, include_remote: bool = True) -> dict:
        """Merged view: topic nodes with the same (topic, message) are shared across servers."""
        local = self.local_snapshot()
        servers = [{"server_id": self.server_id, "system_label": self.system_label,
                    "system_uuid": self.system_uuid, "local": True}]
        nodes = {n["id"]: n for n in local["nodes"]}
        edges = {e["id"]: e for e in local["edges"]}
        if include_remote:
            for snap in self.remote.values():
                servers.append({"server_id": snap["server_id"], "system_label": snap.get("system_label"),
                                "system_uuid": snap.get("system_uuid"), "local": False})
                for n in snap.get("nodes", []):
                    nodes.setdefault(n["id"], n)
                for e in snap.get("edges", []):
                    edges.setdefault(e["id"], e)
        return {"api_version": GRAPH_API_VERSION, "generated_at": local["generated_at"],
                "servers": servers, "nodes": list(nodes.values()), "edges": list(edges.values())}

    def to_dot(self) -> str:
        snap = self.snapshot()
        lines = ["digraph lacal {", '  rankdir=LR;', '  node [fontname="Helvetica"];']
        by_server: dict[str, list[dict]] = {}
        for n in snap["nodes"]:
            if n["kind"] == "service":
                by_server.setdefault(n["server_id"], []).append(n)
        for i, (sid, nodes) in enumerate(by_server.items()):
            lines.append(f'  subgraph cluster_{i} {{ label="{_esc(sid)}"; style=dashed;')
            for n in nodes:
                lines.append(f'    "{_esc(n["id"])}" [label="{_esc(n["label"])}", shape=ellipse];')
            lines.append("  }")
        for n in snap["nodes"]:
            if n["kind"] == "topic":
                lines.append(f'  "{_esc(n["id"])}" [label="{_esc(n["label"])}", shape=box];')
        for e in snap["edges"]:
            attrs = [f'label="{e.get("rate_hz", 0)} Hz"'] if e["kind"] == "pub" else []
            if e.get("group"):
                attrs.append(f'taillabel="[{_esc(e["group"])}]"')
            if not e.get("active", True):
                attrs.append("color=gray")
            lines.append(f'  "{_esc(e["source"])}" -> "{_esc(e["target"])}" [{", ".join(attrs)}];')
        lines.append("}")
        return "\n".join(lines) + "\n"


def _esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')
