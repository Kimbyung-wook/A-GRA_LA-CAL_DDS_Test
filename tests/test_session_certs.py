"""Conformance tests mapped to OMSC-SPC-013 RevB CERTs (LACAL-000001 .. LACAL-000014)."""
import asyncio
import json

import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from helpers import Raw, start_server
from tools.json_skeleton import SkeletonBuilder


@pytest.fixture
async def server(agra_codec):
    srv = await start_server(agra_codec)
    yield srv
    await srv.stop()


@pytest.fixture(scope="session")
def status_msg(agra_codec):
    return json.dumps(SkeletonBuilder(agra_codec).message("SystemStatus"), separators=(",", ":"))


# ---------------------------------------------------------------- 001-005 WebSocket + subprotocol
async def test_lacal_000003_subprotocol_selected(server):
    c = await Raw.open(server)
    assert c.ws.subprotocol == "owp"
    await c.close()


@pytest.mark.parametrize("protos", [(), ("chat",)])
async def test_lacal_000004_missing_or_unknown_subprotocol_is_400(server, protos):
    with pytest.raises(InvalidStatus) as ei:
        await Raw.open(server, protos)
    assert ei.value.response.status_code == 400


# ---------------------------------------------------------------- 007 INIT / 011 INFO / 013 +OK
async def test_lacal_000007_011_init_and_info(server):
    c = await Raw.open(server)
    info = await c.init("svc_a")
    assert info["version"] == "1.0" and info["server_id"] == "calsrv-test" and info["system_label"] == "TEST"
    assert info["uuids"] == {"system": "8159a223-cf0f-4f82-a154-1584fe401e28",
                             "service": "3f14303d-b1c6-40f4-a096-c0660b1bb634",
                             "components": {"c1": "07119c48-d946-425d-85c2-e8649b79a841"}}
    await c.close()


async def test_lacal_000007_non_verbose_has_no_ok(server):
    c = await Raw.open(server)
    await c.init("svc_b", verbose=False)
    await c.send("SUB s1 SystemStatus t.a")
    await c.expect_none()
    await c.close()


@pytest.mark.parametrize("init,code", [
    ({"versions": ["9.9"], "schema": "006.0.ASK", "service_id": "svc_a"}, "Unsupported-Version"),
    ({"versions": ["1.0"], "schema": "005.0", "service_id": "svc_a"}, "Unsupported-Schema"),
    ({"versions": ["1.0"], "schema": "006.0.ASK", "service_id": "nobody"}, "Unsupported-Service"),
    ({"versions": "1.0", "schema": "006.0.ASK", "service_id": "svc_a"}, "Illegal-Argument"),
])
async def test_lacal_000007_init_failures_fail_the_connection(server, init, code):
    c = await Raw.open(server)
    await c.send("INIT " + json.dumps(init))
    assert (await c.recv()).startswith(f"-ERR {code}")
    with pytest.raises(ConnectionClosed):
        await c.recv()


async def test_lacal_000007_init_invalid_json(server):
    c = await Raw.open(server)
    await c.send("INIT {not json")
    assert (await c.recv()).startswith("-ERR Illegal-Argument")
    with pytest.raises(ConnectionClosed):
        await c.recv()


async def test_lacal_000007_init_must_be_first(server):
    c = await Raw.open(server)
    await c.send("SUB s1 SystemStatus t.a")
    assert (await c.recv()).startswith("-ERR Illegal-State")
    with pytest.raises(ConnectionClosed):
        await c.recv()


async def test_lacal_000007_init_only_once(server):
    c = await Raw.open(server)
    await c.init()
    await c.send('INIT {"versions":["1.0"],"schema":"006.0.ASK","service_id":"svc_a"}')
    assert (await c.recv()).startswith("-ERR Illegal-State")
    with pytest.raises(ConnectionClosed):
        await c.recv()


# ---------------------------------------------------------------- 008 PUB / 009 SUB / 012 MSG
async def test_lacal_000008_009_012_pub_sub_msg(server, status_msg):
    a, b = await Raw.open(server), await Raw.open(server)
    await a.init("svc_a")
    await b.init("svc_b")
    await b.send("SUB sub-1 SystemStatus t.status")
    assert await b.recv() == "+OK"
    await a.send(f"PUB t.status {status_msg}")
    assert await a.recv() == "+OK"
    frame = await b.recv()
    assert frame.startswith("MSG sub-1 ")
    assert json.loads(frame[len("MSG sub-1 "):]) == json.loads(status_msg)
    await a.send(f"PUB t.other {status_msg}")        # different topic: not delivered
    assert await a.recv() == "+OK"
    await b.expect_none()
    await a.close()
    await b.close()


async def test_lacal_000008_pub_pretty_printed_json(server, status_msg):
    a = await Raw.open(server)
    await a.init()
    await a.send("PUB t.x " + json.dumps(json.loads(status_msg), indent=2))
    assert await a.recv() == "+OK"
    await a.close()


@pytest.mark.parametrize("op,code", [
    ("PUB t/x {}", "Illegal-Argument"),
    ("PUB forbidden.topic {MSG}", "Illegal-State"),
    ('PUB t.x {"SystemStatus": {}}', "Invalid-Message"),
    ('PUB t.x {"NotAMessage": {}}', "Invalid-Message"),
    ("PUB t.x [1,2]", "Invalid-Message"),
    ("SUB s1 NotAMessage t.x", "Illegal-Argument"),
    ("SUB s1 SystemStatus forbidden.topic", "Illegal-State"),
    ("UNSUB s-none", "Illegal-State"),
    ("BOGUS", "Illegal-Operation"),
    ("INFO {}", "Illegal-Operation"),
])
async def test_lacal_000008_009_010_014_errors(server, status_msg, op, code):
    c = await Raw.open(server)
    await c.init()
    await c.send(op.replace("{MSG}", status_msg))
    assert (await c.recv()).startswith(f"-ERR {code}")
    await c.send("SUB still-alive SystemStatus t.x")  # errors after INIT keep the connection open
    assert await c.recv() == "+OK"
    await c.close()


async def test_lacal_000009_duplicate_subscription_id(server):
    c = await Raw.open(server)
    await c.init()
    await c.send("SUB s1 SystemStatus t.x")
    assert await c.recv() == "+OK"
    await c.send("SUB s1 SystemStatus t.y")
    assert (await c.recv()).startswith("-ERR Illegal-State")
    await c.close()


async def test_lacal_000014_err_on_non_verbose(server):
    c = await Raw.open(server)
    await c.init(verbose=False)
    await c.send('PUB t.x {"SystemStatus": {}}')
    assert (await c.recv()).startswith("-ERR Invalid-Message")
    await c.close()


# ---------------------------------------------------------------- 010 UNSUB, groups, close
async def test_lacal_000010_unsub_stops_delivery(server, status_msg):
    a, b = await Raw.open(server), await Raw.open(server)
    await a.init("svc_a")
    await b.init("svc_b")
    await b.send("SUB s1 SystemStatus t.x")
    assert await b.recv() == "+OK"
    await b.send("UNSUB s1")
    assert await b.recv() == "+OK"
    await a.send(f"PUB t.x {status_msg}")
    assert await a.recv() == "+OK"
    await b.expect_none()
    await a.close()
    await b.close()


async def test_subscription_group_delivers_once(server, status_msg):
    pub, g1, g2, solo = [await Raw.open(server) for _ in range(4)]
    await pub.init("svc_a")
    for c, sid in ((g1, "svc_b"), (g2, "svc_c"), (solo, "svc_b")):
        await c.init(sid)
    await g1.send("SUB s SystemStatus t.x workers")
    await g2.send("SUB s SystemStatus t.x workers")
    await solo.send("SUB s SystemStatus t.x")
    for c in (g1, g2, solo):
        assert await c.recv() == "+OK"
    for _ in range(4):
        await pub.send(f"PUB t.x {status_msg}")
        assert await pub.recv() == "+OK"

    async def drain(c):
        n = 0
        while True:
            try:
                await c.recv(0.3)
                n += 1
            except Exception:
                return n
    assert (await drain(g1), await drain(g2), await drain(solo)) == (2, 2, 4)
    for c in (pub, g1, g2, solo):
        await c.close()


async def test_close_disposes_subscriptions(server):
    c = await Raw.open(server)
    await c.init()
    await c.send("SUB s1 SystemStatus t.x")
    assert await c.recv() == "+OK"
    assert server.router.subscribed_keys() == {("SystemStatus", "t.x")}
    await c.close()
    await c.ws.wait_closed()
    for _ in range(20):
        if not server.router.subscribed_keys():
            break
        await asyncio.sleep(0.05)
    assert server.router.subscribed_keys() == set()
