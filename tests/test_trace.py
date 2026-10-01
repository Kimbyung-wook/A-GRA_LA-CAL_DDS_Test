"""Message timing trace: event sequence, admin endpoints and the monitor page."""
import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from client.lacal_client import CalError, LaCalClient
from helpers import http_get, start_server
from server.clock import now
from server.trace import TraceBus, summarize
from tools.json_skeleton import SkeletonBuilder
from tools.lacal_cli import format_trace


def test_summarize_command_fields():
    msg = {"MA_TxDataPayloadCommandStatus": {"MessageHeader": {"Timestamp": "2026-01-01T00:00:00Z"},
           "MessageData": {"CommandID": {"UUID": "3c428ddc-96be-4802-9c11-aa94e6adf650"},
                           "CommandProcessingState": "ACCEPTED"}}}
    assert summarize(msg) == {"id_field": "CommandID", "id": "3c428ddc-96be-4802-9c11-aa94e6adf650",
                              "CommandProcessingState": "ACCEPTED", "header_ts": "2026-01-01T00:00:00Z"}
    assert summarize("garbage") == {}


async def test_bus_backlog_and_since():
    bus = TraceBus("s1", backlog=3)
    for i in range(5):
        bus.emit("pub", message=f"M{i}")
    assert [e["seq"] for e in bus.recent] == [3, 4, 5]
    q = bus.listen(since=4)
    assert q.get_nowait()["seq"] == 5 and q.empty()
    bus.emit("pub", message="live")
    assert q.get_nowait()["message"] == "live"


@pytest.fixture
async def server(agra_codec):
    srv = await start_server(agra_codec)
    yield srv
    await srv.stop()


async def test_event_sequence_and_timing(server, agra_codec):
    msg = SkeletonBuilder(agra_codec).message("MA_TxDataPayloadCommand")
    url = f"ws://127.0.0.1:{server.port}"
    async with LaCalClient(url, "svc_a") as a, LaCalClient(url, "svc_b") as b:
        got = asyncio.Event()
        await b.subscribe("MA_TxDataPayloadCommand", "t.x", lambda n, m: got.set())
        await a.publish("t.x", msg)
        await asyncio.wait_for(got.wait(), 3)
        with pytest.raises(CalError):
            await a.publish("t.x", {"MA_TxDataPayloadCommand": {}})
    await asyncio.sleep(0.1)
    evs = list(server.trace.recent)
    kinds = [e["kind"] for e in evs]
    assert kinds[:6] == ["connect", "connect", "sub", "pub", "deliver", "err"]
    assert sorted(kinds[6:]) == ["disconnect", "disconnect"]
    pub = next(e for e in evs if e["kind"] == "pub")
    deliver = next(e for e in evs if e["kind"] == "deliver")
    assert (pub["service"], pub["message"], pub["topic"]) == ("svc_a", "MA_TxDataPayloadCommand", "t.x")
    assert pub["summary"]["id_field"] == "CommandID" and pub["summary"]["CommandState"] == "NEW"
    assert pub["validate_ms"] >= 0 and pub["bytes"] > 0
    assert (deliver["service"], deliver["origin"], deliver["sub_id"]) == ("svc_b", "svc_a", "s1")
    assert pub["seq"] < deliver["seq"] and pub["t"] <= deliver["t"]
    err = next(e for e in evs if e["kind"] == "err")
    assert (err["op"], err["code"], err["service"]) == ("PUB", "Invalid-Message", "svc_a")
    line = format_trace(pub, None)
    assert "PUB" in line and "svc_a -> MA_TxDataPayloadCommand@t.x" in line and "CommandID=" in line


async def test_trace_endpoints_and_monitor(server, agra_codec):
    msg = SkeletonBuilder(agra_codec).message("SystemStatus")
    async with LaCalClient(f"ws://127.0.0.1:{server.port}", "svc_a") as a:
        await a.publish("t.x", msg)                                   # goes to the backlog
        async with connect(f"ws://127.0.0.1:{server.admin_port}/trace/events") as ws:
            hello = json.loads(await ws.recv())
            assert hello["kind"] == "hello" and hello["server_id"] == "calsrv-test"
            backlog = [json.loads(await ws.recv()) for _ in range(2)]
            assert [e["kind"] for e in backlog] == ["connect", "pub"]
            await a.publish("t.x", msg)                               # live event
            live = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert live["kind"] == "pub" and live["seq"] > backlog[-1]["seq"]
    _, body = await http_get(server.admin_port, "/trace")
    assert [e["kind"] for e in json.loads(body)][:3] == ["connect", "pub", "pub"]
    ctype, html = await http_get(server.admin_port, "/monitor")
    assert ctype.startswith("text/html") and "LA-CAL Live Monitor" in html and "/trace/events" in html


def test_clock_resolves_sub_millisecond():
    """time.time() on Windows only advances every 0.5-15.6 ms; trace timestamps must not."""
    import time
    samples = [now() for _ in range(2000)]
    steps = [b - a for a, b in zip(samples, samples[1:]) if b != a]
    assert len(set(samples)) > 1000          # almost every call yields a new value
    assert min(steps) < 1e-4                 # well below a millisecond
    assert abs(now() - time.time()) < 0.05   # same wall clock as time.time()
