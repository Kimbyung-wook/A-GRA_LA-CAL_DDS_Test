import asyncio
import json
import urllib.request

from websockets.asyncio.client import connect

from server.app import CalServer
from server.config import ServerConfig, ServiceEntry

SYSTEM = "8159a223-cf0f-4f82-a154-1584fe401e28"


def make_config(server_id="calsrv-test", **kw) -> ServerConfig:
    return ServerConfig(
        server_id=server_id, system_uuid=SYSTEM, system_label="TEST", port=0, admin_port=0,
        allowed_topics=["t.*"],
        services={
            "svc_a": ServiceEntry("svc_a", "3f14303d-b1c6-40f4-a096-c0660b1bb634",
                                  components={"c1": "07119c48-d946-425d-85c2-e8649b79a841"}),
            "svc_b": ServiceEntry("svc_b", "13766ed8-477b-4da3-92ae-b6ecc6be71ed",
                                  subsystem="531e0a1e-cad0-4b33-baac-02a6f5a8ed61"),
            "svc_c": ServiceEntry("svc_c", "8692549c-55c3-43d0-96a1-e338303ccfe5"),
        },
        **kw)


async def start_server(codec, **kw) -> CalServer:
    server = CalServer(make_config(**kw), codec)
    await server.start()
    return server


class Raw:
    """Thin OWP test client that exposes every frame."""

    def __init__(self, ws):
        self.ws = ws

    @classmethod
    async def open(cls, server: CalServer, subprotocols=("owp",)):
        ws = await connect(f"ws://127.0.0.1:{server.port}", subprotocols=list(subprotocols) or None)
        return cls(ws)

    async def send(self, text: str) -> None:
        await self.ws.send(text)

    async def recv(self, timeout: float = 2.0) -> str:
        return await asyncio.wait_for(self.ws.recv(), timeout)

    async def init(self, service_id="svc_a", verbose=True, **extra) -> dict:
        req = {"versions": ["1.0"], "schema": "006.0.ASK", "service_id": service_id, "verbose": verbose, **extra}
        await self.send("INIT " + json.dumps(req))
        if verbose:
            assert await self.recv() == "+OK"
        frame = await self.recv()
        assert frame.startswith("INFO ")
        return json.loads(frame[5:])

    async def expect_none(self, timeout: float = 0.3) -> None:
        try:
            frame = await asyncio.wait_for(self.ws.recv(), timeout)
        except asyncio.TimeoutError:
            return
        raise AssertionError(f"unexpected frame {frame[:120]}")

    async def close(self) -> None:
        await self.ws.close()


async def http_get(port: int, path: str):
    def get():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.headers.get("Content-Type"), r.read().decode()
    return await asyncio.to_thread(get)
