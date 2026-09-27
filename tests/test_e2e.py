"""End-to-end: CAL Clients on one server, and on two servers bridged by Cyclone DDS."""
import asyncio
import json
import os
import random

import pytest

from client.lacal_client import CalError, LaCalClient
from common import decode_payload, encode_payload, uci_message
from helpers import start_server
from tools.json_skeleton import SkeletonBuilder


async def wait_for(predicate, timeout=10.0, step=0.05):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


async def test_single_server_pub_sub_and_errors(agra_codec):
    srv = await start_server(agra_codec)
    try:
        url = f"ws://127.0.0.1:{srv.port}"
        got = []
        async with LaCalClient(url, "svc_a") as a, LaCalClient(url, "svc_b") as b:
            assert a.info["uuids"]["service"] == "3f14303d-b1c6-40f4-a096-c0660b1bb634"
            await b.subscribe("SystemStatus", "t.x", lambda name, msg: got.append(msg))
            msg = SkeletonBuilder(agra_codec).message("SystemStatus")
            await a.publish("t.x", msg)
            assert await wait_for(lambda: got)
            assert got[0] == json.loads(json.dumps(msg))
            with pytest.raises(CalError) as ei:
                await a.publish("t.x", {"SystemStatus": {}})
            assert ei.value.code == "Invalid-Message"
    finally:
        await srv.stop()


async def test_encoded_payload_helpers(agra_codec):
    inner = uci_message("SystemStatus", "8159a223-cf0f-4f82-a154-1584fe401e28", {
        "SystemID": {"UUID": "8159a223-cf0f-4f82-a154-1584fe401e28"}, "SystemState": "OPERATIONAL",
        "Source": "ACTUAL", "Communications": {"MissionCommunicationsState": "ACTIVE"}})
    hex_text = encode_payload(agra_codec, inner)
    assert bytes.fromhex(hex_text).startswith(b"<?xml")
    assert decode_payload(agra_codec, hex_text) == inner


@pytest.mark.skipif(os.environ.get("LACAL_SKIP_DDS") == "1", reason="DDS disabled")
async def test_two_servers_over_dds(agra_codec):
    domain = random.randint(100, 200)  # isolate from any other DDS traffic on the host
    a_srv = await start_server(agra_codec, server_id="dds-a", backend="dds", dds_domain=domain)
    b_srv = await start_server(agra_codec, server_id="dds-b", backend="dds", dds_domain=domain)
    try:
        got_status, got_cmd = [], []
        builder = SkeletonBuilder(agra_codec)
        async with LaCalClient(f"ws://127.0.0.1:{a_srv.port}", "svc_a") as a, \
                LaCalClient(f"ws://127.0.0.1:{b_srv.port}", "svc_b") as b:
            await b.subscribe("SystemStatus", "t.x", lambda n, m: got_status.append(m))            # BEST_EFFORT
            await b.subscribe("MA_TxDataPayloadCommand", "t.x", lambda n, m: got_cmd.append(m))    # RELIABLE
            status = builder.message("SystemStatus")
            cmd = builder.message("MA_TxDataPayloadCommand")
            # Discovery takes a moment; best-effort samples sent before matching are lost by design.
            for _ in range(50):
                await a.publish("t.x", status)
                if await wait_for(lambda: got_status, timeout=0.2):
                    break
            await a.publish("t.x", cmd)
            assert got_status and got_status[0] == json.loads(json.dumps(status))
            assert await wait_for(lambda: got_cmd)
            assert got_cmd[0] == json.loads(json.dumps(cmd))
            # Graph sync: server A sees svc_b (on B) subscribed to the shared topic node.
            assert await wait_for(lambda: any(
                n.get("service_id") == "svc_b" for n in a_srv.graph.snapshot()["nodes"]), timeout=10)
            snap = a_srv.graph.snapshot()
            assert {s["server_id"] for s in snap["servers"]} == {"dds-a", "dds-b"}
            assert a_srv.router.stats["remote_out"] >= 2 and b_srv.router.stats["remote_in"] >= 2
        # When B goes away (dispose on stop / liveliness loss), A removes B's subgraph.
        await b_srv.stop()
        assert await wait_for(lambda: "dds-b" not in a_srv.graph.remote, timeout=10)
    finally:
        await a_srv.stop()
        if b_srv.router.backend._running:
            await b_srv.stop()
