"""Message timing trace: one event per routing step, streamed to monitors (see docs/trace_api.md).

Kinds:
  connect / disconnect      a CAL Client finished INIT / went away
  sub / unsub               subscription added / removed
  pub                       a PUB was accepted from a local client (after XSD validation)
  deliver / drop            a MSG was queued for a subscriber / dropped (outbound queue full)
  dds_out / dds_in          the envelope left for / arrived from another CAL Server
  err                       a -ERR was returned to a client
"""
from __future__ import annotations

import asyncio
import itertools
from collections import deque
from typing import Any

from .clock import now

# MessageData fields worth showing next to a message in a monitor, in priority order.
_SUMMARY_FIELDS = ("CommandState", "CommandProcessingState", "MessageType", "SystemState",
                   "DestinationRouting")
_ID_FIELDS = ("CommandID", "RxDataPayloadID", "SystemID", "RequestID")


def summarize(obj: Any) -> dict:
    """Small, monitor-friendly digest of an OMS JSON message (never raises)."""
    try:
        body = next(iter(obj.values()))
        data = body.get("MessageData", {}) if isinstance(body, dict) else {}
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for f in _ID_FIELDS:
        v = data.get(f)
        if isinstance(v, dict) and isinstance(v.get("UUID"), str):
            out["id_field"], out["id"] = f, v["UUID"]
            break
    for f in _SUMMARY_FIELDS:
        v = data.get(f)
        if isinstance(v, str):
            out[f] = v
    ts = body.get("MessageHeader", {}).get("Timestamp") if isinstance(body, dict) else None
    if ts:
        out["header_ts"] = ts
    return out


class TraceBus:
    def __init__(self, server_id: str, backlog: int = 2000):
        self.server_id = server_id
        self._seq = itertools.count(1)
        self.recent: deque[dict] = deque(maxlen=backlog)
        self._listeners: set[asyncio.Queue] = set()

    def emit(self, kind: str, t: float | None = None, **fields) -> dict:
        """Record an event. Pass ``t`` (from clock.now()) when the moment of interest precedes the call."""
        ev = {"seq": next(self._seq), "t": now() if t is None else t, "server_id": self.server_id, "kind": kind}
        ev.update({k: v for k, v in fields.items() if v is not None})
        self.recent.append(ev)
        for q in list(self._listeners):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # a slow monitor loses events; it can resync with GET /trace
        return ev

    def listen(self, since: int = 0, maxsize: int = 5000) -> asyncio.Queue:
        """Subscribe; events with seq > since that are still in the backlog are queued first."""
        q: asyncio.Queue = asyncio.Queue(maxsize)
        for ev in self.recent:
            if ev["seq"] > since and not q.full():
                q.put_nowait(ev)
        self._listeners.add(q)
        return q

    def unlisten(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)
