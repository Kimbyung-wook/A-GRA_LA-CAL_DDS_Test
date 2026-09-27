"""Graph introspection: model behaviour and the admin API (rqt_graph-style data source)."""
import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from client.lacal_client import LaCalClient
from helpers import http_get, start_server
from server.graph import GraphModel, ServiceNode
from tools.json_skeleton import SkeletonBuilder


def svc(node_id, service_id="svc"):
    return ServiceNode(id=node_id, service_id=service_id, server_id="s1", system_label="L",
                       uuids={"system": "x", "service": "y"}, connected_at=0.0)


def ids(snap, kind=None):
    return {n["id"] for n in snap["nodes"] if kind in (None, n["kind"])}


def test_model_lifecycle():
    g = GraphModel("s1", "L", "sys", inactive_after_s=10)
    q = g.listen()
    g.add_service(svc("a"))
    g.add_service(svc("b"))
    g.add_sub("b", "s1", "SystemStatus", "t.x", None)
    g.note_pub("a", "SystemStatus", "t.x", 100)
    g.note_pub("a", "SystemStatus", "t.x", 100)
    snap = g.snapshot()
    assert ids(snap, "topic") == {"topic:t.x/SystemStatus"}
    pub = next(e for e in snap["edges"] if e["kind"] == "pub")
    assert (pub["source"], pub["target"], pub["msg_count"], pub["byte_count"]) == ("a", "topic:t.x/SystemStatus", 2, 200)
    assert pub["active"] and pub["rate_hz"] > 0
    sub = next(e for e in snap["edges"] if e["kind"] == "sub")
    assert (sub["source"], sub["target"], sub["sub_id"]) == ("topic:t.x/SystemStatus", "b", "s1")
    g.remove_service("a")                      # pub edge goes; topic stays because b subscribes
    assert ids(g.snapshot(), "topic") == {"topic:t.x/SystemStatus"}
    g.remove_sub("b", "s1")                    # last reference: topic node disappears
    assert ids(g.snapshot(), "topic") == set()
    types = []
    while not q.empty():
        types.append(q.get_nowait()["type"])
    assert types == ["node_added", "node_added", "node_added", "edge_added", "edge_added",
                     "edge_removed", "node_removed", "edge_removed", "node_removed"]


def test_inactive_publisher():
    g = GraphModel("s1", "L", "sys", inactive_after_s=0.0)
    g.add_service(svc("a"))
    g.note_pub("a", "SystemStatus", "t.x", 1)
    import time
    time.sleep(0.01)
    assert not next(e for e in g.snapshot()["edges"] if e["kind"] == "pub")["active"]


def test_remote_merge_and_dot():
    g = GraphModel("s1", "L", "sys")
    g.add_service(svc("a", "ma_core"))
    g.note_pub("a", "SystemStatus", "t.x", 1)
    g.merge_remote({"server_id": "s2", "system_label": "R", "system_uuid": "u2", "nodes": [
        {"id": "svc:s2:1", "kind": "service", "service_id": "ms_dms", "server_id": "s2", "label": "ms_dms"},
        {"id": "topic:t.x/SystemStatus", "kind": "topic", "topic": "t.x", "message_name": "SystemStatus",
         "label": "SystemStatus@t.x"}],
        "edges": [{"id": "sub:svc:s2:1:q", "kind": "sub", "source": "topic:t.x/SystemStatus", "target": "svc:s2:1"}]})
    snap = g.snapshot()
    assert [s["server_id"] for s in snap["servers"]] == ["s1", "s2"]
    assert ids(snap) == {"a", "svc:s2:1", "topic:t.x/SystemStatus"}  # topic node shared across servers
    dot = g.to_dot()
    assert 'cluster_0 { label="s1"' in dot and 'cluster_1 { label="s2"' in dot
    assert '"topic:t.x/SystemStatus" -> "svc:s2:1"' in dot
    g.remove_remote("s2")
    assert "svc:s2:1" not in ids(g.snapshot())


def test_graph_listener_overflow_does_not_block():
    g = GraphModel("s1", "L", "sys")
    g.listen(maxsize=1)
    for i in range(5):
        g.add_service(svc(f"n{i}"))
    assert len(g.services) == 5


@pytest.fixture
async def server(agra_codec):
    srv = await start_server(agra_codec)
    yield srv
    await srv.stop()


async def test_admin_api_reflects_clients(server, agra_codec):
    msg = SkeletonBuilder(agra_codec).message("SystemStatus")
    url = f"ws://127.0.0.1:{server.port}"
    async with connect(f"ws://127.0.0.1:{server.admin_port}/graph/events") as events:
        first = json.loads(await events.recv())
        assert first["type"] == "snapshot" and first["graph"]["nodes"] == []
        async with LaCalClient(url, "svc_a") as a, LaCalClient(url, "svc_b") as b:
            await b.subscribe("SystemStatus", "t.status", lambda n, m: None, group="g1")
            await a.publish("t.status", msg)
            ctype, body = await http_get(server.admin_port, "/graph")
            assert ctype.startswith("application/json")
            snap = json.loads(body)
            labels = {n["label"] for n in snap["nodes"]}
            assert labels == {"svc_a", "svc_b", "SystemStatus@t.status"}
            kinds = {(e["kind"], e.get("group")) for e in snap["edges"]}
            assert kinds == {("pub", None), ("sub", "g1")}
            _, dot = await http_get(server.admin_port, "/graph.dot")
            assert dot.startswith("digraph lacal")
            _, services = await http_get(server.admin_port, "/services")
            assert {s["service_id"] for s in json.loads(services)} == {"svc_a", "svc_b"}
        seen = []
        while True:
            ev = json.loads(await asyncio.wait_for(events.recv(), 3))
            seen.append(ev["type"])
            if seen.count("node_removed") >= 3:  # 2 services + the topic node
                break
        assert {"node_added", "edge_added", "edge_removed", "node_removed"} <= set(seen)


async def test_tap_echo(server, agra_codec):
    msg = SkeletonBuilder(agra_codec).message("SystemStatus")
    async with connect(f"ws://127.0.0.1:{server.admin_port}/tap?topic=t.status") as tap:
        async with LaCalClient(f"ws://127.0.0.1:{server.port}", "svc_a") as a:
            await a.publish("t.status", msg)
            await a.publish("t.other", msg)
            got = json.loads(await asyncio.wait_for(tap.recv(), 3))
            assert (got["topic"], got["message_name"]) == ("t.status", "SystemStatus")
            assert got["message"] == json.loads(json.dumps(msg))
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(tap.recv(), 0.3)
    for _ in range(40):  # the server notices the closed tap asynchronously
        if not server.router.taps:
            break
        await asyncio.sleep(0.05)
    assert server.router.taps == set()


async def test_admin_404(server):
    import urllib.error
    with pytest.raises(urllib.error.HTTPError):
        await http_get(server.admin_port, "/nope")
