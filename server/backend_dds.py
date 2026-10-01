"""Cyclone DDS backend: connects CAL Servers into one ASB over RTPS.

On the wire every CAL Message travels as a CDR-encoded ``lacal::CalEnvelope`` whose ``payload``
octet sequence carries the OMS JSON text (encoding = 1). One CAL topic maps to two DDS topics
because DDS reliability is a per-topic QoS while A-GRA assigns it per UCI primitive
(MS L1 Interface Volume 1.2.5.5):

    cal/r/<topic>   RELIABLE      Command-2, ActionRequest-2, DataRequest-2, DataRecord-1, notifications
    cal/b/<topic>   BEST_EFFORT   Data-1, Status-1 (and anything else)

Graph introspection: each server publishes its local graph on ``lacal/graph`` (keyed by
server_id, RELIABLE + TRANSIENT_LOCAL + KEEP_LAST 1) so late joiners get the full picture;
a server that disappears is removed via dispose / liveliness loss.

Threading: no DDS listeners are used. A listener runs Python code on a Cyclone thread that holds
internal locks while it waits for the GIL, and deadlocks against DataWriter.write(), which holds
the GIL while taking those locks. Instead one receive thread blocks in a WaitSet (the ctypes call
releases the GIL), takes the samples and hands them to the asyncio loop.
"""
# No "from __future__ import annotations": Cyclone resolves IdlStruct field types at runtime.
import asyncio
import json
import logging
import threading
import uuid
from dataclasses import dataclass

from cyclonedds.core import (GuardCondition, InstanceState, Policy, Qos, ReadCondition, SampleState,
                             ViewState, WaitSet)
from cyclonedds.domain import DomainParticipant
from cyclonedds.idl import IdlStruct
from cyclonedds.idl.annotations import key
from cyclonedds.idl.types import sequence, uint8
from cyclonedds.pub import DataWriter
from cyclonedds.sub import DataReader
from cyclonedds.topic import Topic
from cyclonedds.util import duration

from . import primitives
from .config import ServerConfig
from .graph import GraphModel
from .router import Envelope, Router

log = logging.getLogger(__name__)

GRAPH_TOPIC = "lacal/graph"
GRAPH_PERIOD_S = 2.0
GRAPH_DEBOUNCE_S = 0.2
LIVELINESS_LEASE_NS = 3_000_000_000


@dataclass
class CalEnvelope(IdlStruct, typename="lacal::CalEnvelope"):
    message_name: str
    cal_topic: str
    src_system: str
    src_service: str
    server_id: str
    encoding: uint8
    payload: sequence[uint8]


@dataclass
class GraphState(IdlStruct, typename="lacal::GraphState"):
    server_id: str
    snapshot: str      # JSON, see docs/graph_api_en.md
    key("server_id")


def dds_topic_name(cal_topic: str, reliable: bool) -> str:
    """Reversible mapping of ^[A-Za-z0-9_\\-.]+$ onto conservative DDS topic characters."""
    escaped = cal_topic.replace("_", "__").replace("-", "_h").replace(".", "/")
    return f"cal/{'r' if reliable else 'b'}/{escaped}"


class DdsBackend:
    def __init__(self, config: ServerConfig, graph: GraphModel):
        self.config = config
        self.graph = graph
        self.router: Router | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.dp: DomainParticipant | None = None
        self._writers: dict[str, DataWriter] = {}
        self._readers: dict[str, DataReader] = {}
        self._topics: dict[str, Topic] = {}
        self._tasks: list[asyncio.Task] = []
        self._graph_dirty = asyncio.Event()
        self._rx_lock = threading.Lock()
        self._rx: list[tuple[DataReader, object]] = []   # (reader, handler) polled by the rx thread
        self._running = False
        self._graph_instances: dict[int, str] = {}       # DDS instance handle -> server_id
        uid = uuid.UUID(config.system_uuid).bytes
        # MS L1 Volume 1.2.5: participants/readers/writers carry the SystemID UUID as UserData.
        self._userdata = Policy.Userdata(uid)
        self._reliable = Qos(Policy.Reliability.Reliable(1_000_000_000), Policy.History.KeepLast(100),
                             self._userdata)
        self._best_effort = Qos(Policy.Reliability.BestEffort, Policy.History.KeepLast(10), self._userdata)

    # ------------------------------------------------------------------ lifecycle
    async def start(self, router: Router) -> None:
        self.router = router
        self.loop = asyncio.get_running_loop()
        self.dp = DomainParticipant(self.config.dds_domain, qos=Qos(self._userdata))
        gqos = Qos(Policy.Reliability.Reliable(1_000_000_000), Policy.Durability.TransientLocal,
                   Policy.History.KeepLast(1), Policy.Liveliness.Automatic(LIVELINESS_LEASE_NS),
                   self._userdata)
        gtopic = Topic(self.dp, GRAPH_TOPIC, GraphState, qos=gqos)
        self._graph_writer = DataWriter(self.dp, gtopic, qos=gqos)
        self._waitset = WaitSet(self.dp)
        self._wakeup = GuardCondition(self.dp)
        self._waitset.attach(self._wakeup)
        self._add_reader(DataReader(self.dp, gtopic, qos=gqos), self._on_graph)
        self._graph_queue = self.graph.listen()
        self._running = True
        self._thread = threading.Thread(target=self._rx_loop, name="dds-rx", daemon=True)
        self._thread.start()
        self._tasks += [asyncio.create_task(self._graph_publisher()),
                        asyncio.create_task(self._graph_watch())]
        log.info("DDS backend on domain %d", self.config.dds_domain)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._running = False
        self._wakeup.set(True)
        await asyncio.to_thread(self._thread.join, 2.0)
        try:
            self._graph_writer.dispose(GraphState(self.config.server_id, ""))
        except Exception:  # pragma: no cover
            pass
        self.graph.unlisten(self._graph_queue)
        self._readers.clear()
        self._writers.clear()
        self.dp = None  # entities are deleted when garbage collected

    # ------------------------------------------------------------------ data path
    def _topic(self, name: str) -> Topic:
        t = self._topics.get(name)
        if t is None:
            t = self._topics[name] = Topic(self.dp, name, CalEnvelope)
        return t

    def ensure_topic(self, cal_topic: str) -> None:
        """Create readers for a CAL topic once a local client subscribes to it."""
        for reliable in (True, False):
            name = dds_topic_name(cal_topic, reliable)
            if name not in self._readers:
                self._readers[name] = DataReader(
                    self.dp, self._topic(name), qos=self._reliable if reliable else self._best_effort)
                self._add_reader(self._readers[name], self._on_data)

    def publish(self, env: Envelope) -> None:
        reliable = primitives.is_reliable(env.message_name)
        name = dds_topic_name(env.cal_topic, reliable)
        w = self._writers.get(name)
        if w is None:
            w = self._writers[name] = DataWriter(self.dp, self._topic(name),
                                                 qos=self._reliable if reliable else self._best_effort)
        w.write(CalEnvelope(env.message_name, env.cal_topic, env.src_system, env.src_service,
                            env.server_id, env.encoding, env.payload))

    # ------------------------------------------------------------------ receive thread
    def _add_reader(self, reader: DataReader, handler) -> None:
        mask = SampleState.Any | ViewState.Any | InstanceState.Any
        cond = ReadCondition(reader, mask)
        with self._rx_lock:
            self._rx.append((reader, handler))
            self._conds = getattr(self, "_conds", []) + [cond]  # keep conditions alive
        self._waitset.attach(cond)

    def _rx_loop(self) -> None:
        while self._running:
            self._waitset.wait(duration(milliseconds=200))
            self._wakeup.set(False)
            with self._rx_lock:
                pairs = list(self._rx)
            for reader, handler in pairs:
                try:
                    handler(reader)
                except Exception:  # pragma: no cover - keep the thread alive
                    log.exception("DDS receive handler failed")

    def _on_data(self, reader: DataReader) -> None:  # dds-rx thread
        for s in reader.take(64):
            if not s.sample_info.valid_data:
                continue
            env = Envelope(s.message_name, s.cal_topic, s.src_system, s.src_service, s.server_id,
                           s.encoding, bytes(s.payload))
            self.loop.call_soon_threadsafe(self.router.publish_remote, env)

    # ------------------------------------------------------------------ graph sharing
    def _on_graph(self, reader: DataReader) -> None:  # dds-rx thread
        for s in reader.take(16):
            info = s.sample_info
            if info.valid_data and s.snapshot:
                self._graph_instances[info.instance_handle] = s.server_id
                self.loop.call_soon_threadsafe(self.graph.merge_remote, json.loads(s.snapshot))
            elif info.instance_state != InstanceState.Alive:
                # Dispose / no-writers notifications arrive as InvalidSample without key fields,
                # so the server is identified through the instance handle seen earlier.
                sid = self._graph_instances.pop(info.instance_handle, None)
                if sid is not None:
                    self.loop.call_soon_threadsafe(self.graph.remove_remote, sid)

    async def _graph_watch(self) -> None:
        while True:
            ev = await self._graph_queue.get()
            if ev.get("type") in ("node_added", "node_removed", "edge_added", "edge_removed"):
                self._graph_dirty.set()

    async def _graph_publisher(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._graph_dirty.wait(), GRAPH_PERIOD_S)
                await asyncio.sleep(GRAPH_DEBOUNCE_S)
            except asyncio.TimeoutError:
                pass
            self._graph_dirty.clear()
            snap = self.graph.local_snapshot()
            self._graph_writer.write(GraphState(self.config.server_id, json.dumps(snap, separators=(",", ":"))))
