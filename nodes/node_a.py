"""node_a: Mission Autonomy component (CAL Client, service "ma_core").

Scenario (MS L1 Interface Volume 1.2.5, DMS Minimum Message Set):
  1. MA_MessagingSubscriptionSettingCommand  -> wait MA_MessagingSubscriptionSettingCommandStatus
  2. N x MA_TxDataPayloadCommand (EncodedPayload = hex UTF-8 XML of a SystemStatus)
                                             -> wait MA_TxDataPayloadCommandStatus, measure RTT
  3. count MA_RxDataPayload looped back by the DMS mock and MA_MessagingSubscriptionStatus.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import sys
import time

from common import (DEFAULT_URL, DMS_TOPIC, OmsJsonCodec, body, decode_payload, encode_payload,
                    message_type_enum, new_uuid, now_iso, uci_message)

from client.lacal_client import LaCalClient

log = logging.getLogger("node_a")


class MaNode:
    def __init__(self, client: LaCalClient, codec: OmsJsonCodec, topic: str):
        self.client, self.codec, self.topic = client, codec, topic
        self.waiting: dict[str, tuple[asyncio.Future, float]] = {}
        self.rtts: list[float] = []
        self.rx_payloads = 0
        self.sub_status = 0

    async def setup(self) -> None:
        for name in ("MA_MessagingSubscriptionSettingCommandStatus", "MA_TxDataPayloadCommandStatus"):
            await self.client.subscribe(name, self.topic, self.on_status)
        await self.client.subscribe("MA_RxDataPayload", self.topic, self.on_rx)
        await self.client.subscribe("MA_MessagingSubscriptionStatus", self.topic, self.on_sub_status)

    def on_status(self, name: str, msg: dict) -> None:
        data = body(msg)
        cid = data["CommandID"]["UUID"]
        entry = self.waiting.pop(cid, None)
        if entry is None:
            return
        fut, t0 = entry
        rtt = (time.perf_counter() - t0) * 1000
        self.rtts.append(rtt)
        log.info("<- %s %s (%.2f ms)", name, data["CommandProcessingState"], rtt)
        fut.set_result(data["CommandProcessingState"])

    def on_rx(self, name: str, msg: dict) -> None:
        inner = decode_payload(self.codec, body(msg)["EncodedPayload"])
        self.rx_payloads += 1
        log.info("<- %s carrying %s", name, next(iter(inner)))

    def on_sub_status(self, name: str, msg: dict) -> None:
        self.sub_status += 1

    async def command(self, name: str, data: dict, timeout: float) -> str:
        cid = new_uuid()
        data = {"CommandID": {"UUID": cid}, "CommandState": "NEW", **data}
        fut = asyncio.get_running_loop().create_future()
        self.waiting[cid] = (fut, time.perf_counter())
        await self.client.publish(self.topic, uci_message(name, self.client.system_uuid, data))
        log.info("-> %s", name)
        return await asyncio.wait_for(fut, timeout)

    def system_status(self) -> dict:
        return uci_message("SystemStatus", self.client.system_uuid, {
            "SystemID": {"UUID": self.client.system_uuid}, "SystemState": "OPERATIONAL",
            "Source": "ACTUAL", "Communications": {"MissionCommunicationsState": "ACTIVE"}})


async def run(args) -> int:
    codec = OmsJsonCodec()
    async with LaCalClient(args.url, args.service) as client:
        log.info("connected: server=%s system=%s service=%s", client.info["server_id"],
                 client.system_uuid, client.service_uuid)
        node = MaNode(client, codec, args.topic)
        await node.setup()
        ok = 0
        state = await node.command("MA_MessagingSubscriptionSettingCommand", {
            "AddTopicSubscription": [{"Topic": "SystemStatus", "SubscriptionID": {"UUID": new_uuid()}}]},
            args.timeout)
        ok += state == "ACCEPTED"
        for _ in range(args.count):
            inner = node.system_status()
            state = await node.command("MA_TxDataPayloadCommand", {
                "EncodedPayload": encode_payload(codec, inner),
                "MessageType": message_type_enum("SystemStatus"),
                "Priority": {"Priority": 1, "PrecedenceWithinPriority": 0},
                "Timestamp": now_iso(),
                "DestinationRouting": "TOPIC_AND_SPECIFIC_DESTINATION"}, args.timeout)
            ok += state == "ACCEPTED"
            await asyncio.sleep(args.interval)
        await asyncio.sleep(0.5)  # let looped-back MA_RxDataPayload arrive
        expected = args.count + 1
        print(f"commands accepted: {ok}/{expected}  RxDataPayload: {node.rx_payloads}  "
              f"SubscriptionStatus: {node.sub_status}")
        if node.rtts:
            print(f"RTT ms: min {min(node.rtts):.2f}  median {statistics.median(node.rtts):.2f}  "
                  f"max {max(node.rtts):.2f}")
        if args.linger:
            log.info("lingering %.0fs (graph stays visible)", args.linger)
            await asyncio.sleep(args.linger)
        return 0 if ok == expected and node.rx_payloads == args.count else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--service", default="ma_core")
    ap.add_argument("--topic", default=DMS_TOPIC)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--linger", type=float, default=0.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
