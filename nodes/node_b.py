"""node_b: Mission Systems DMS mock (CAL Client, service "ms_dms").

Answers the DMS Minimum Message Set commands from MA:
  MA_MessagingSubscriptionSettingCommand -> MA_MessagingSubscriptionSettingCommandStatus
  MA_TxDataPayloadCommand                -> MA_TxDataPayloadCommandStatus (payload XML is validated)
and simulates the offboard link by looping each transmitted payload back as MA_RxDataPayload
when its message type has a topic subscription. Publishes MA_MessagingSubscriptionStatus at 1 Hz.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from common import (DEFAULT_URL, DMS_TOPIC, OmsJsonCodec, body, decode_payload, new_uuid, now_iso,
                    uci_message)

from client.lacal_client import LaCalClient
from server.omsjson import InvalidMessage

log = logging.getLogger("node_b")


class DmsMock:
    def __init__(self, client: LaCalClient, codec: OmsJsonCodec, topic: str, peer_system: str):
        self.client, self.codec, self.topic, self.peer_system = client, codec, topic, peer_system
        self.subscriptions: dict[str, dict] = {}   # Topic (message name) -> MA_TopicSubscriptionType

    async def setup(self) -> None:
        await self.client.subscribe("MA_MessagingSubscriptionSettingCommand", self.topic, self.on_setting)
        await self.client.subscribe("MA_TxDataPayloadCommand", self.topic, self.on_tx)

    async def status(self, name: str, command_id: str, state: str) -> None:
        await self.client.publish(self.topic, uci_message(name, self.client.system_uuid, {
            "CommandID": {"UUID": command_id}, "CommandProcessingState": state}))

    async def on_setting(self, name: str, msg: dict) -> None:
        data = body(msg)
        for sub in data.get("AddTopicSubscription", []):
            self.subscriptions[sub["Topic"]] = sub
            log.info("subscription added: %s", sub["Topic"])
        await self.status("MA_MessagingSubscriptionSettingCommandStatus", data["CommandID"]["UUID"], "ACCEPTED")

    async def on_tx(self, name: str, msg: dict) -> None:
        data = body(msg)
        try:
            inner = decode_payload(self.codec, data["EncodedPayload"])
            state = "ACCEPTED"
        except (InvalidMessage, ValueError) as exc:
            log.warning("rejecting payload: %s", exc)
            inner, state = None, "REJECTED"
        await self.status("MA_TxDataPayloadCommandStatus", data["CommandID"]["UUID"], state)
        if inner is None:
            return
        inner_name = next(iter(inner))
        log.info("TX %s (%d bytes XML) -> %s", inner_name, len(data["EncodedPayload"]) // 2, state)
        if inner_name in self.subscriptions:  # pretend a peer sent it back over the air
            await self.client.publish(self.topic, uci_message("MA_RxDataPayload", self.client.system_uuid, {
                "RxDataPayloadID": {"UUID": new_uuid()},
                "DataPayloadOriginatorID": {"UUID": self.peer_system},
                "EncodedPayload": data["EncodedPayload"],
                "Timestamp": now_iso(),
                "MessageType": data["MessageType"],
                "DestinationRouting": data["DestinationRouting"]}))

    async def status_loop(self) -> None:
        while True:
            topics = [{k: v for k, v in s.items() if k in ("Topic", "SubscriptionID")}
                      for s in self.subscriptions.values()]
            await self.client.publish(self.topic, uci_message(
                "MA_MessagingSubscriptionStatus", self.client.system_uuid,
                {"TopicSubscription": topics} if topics else {}))
            await asyncio.sleep(1.0)


async def run(args) -> None:
    codec = OmsJsonCodec()
    async with LaCalClient(args.url, args.service) as client:
        log.info("connected: server=%s system=%s service=%s", client.info["server_id"],
                 client.system_uuid, client.service_uuid)
        dms = DmsMock(client, codec, args.topic, args.peer_system)
        await dms.setup()
        log.info("DMS mock ready on topic %s", args.topic)
        await dms.status_loop()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--service", default="ms_dms")
    ap.add_argument("--topic", default=DMS_TOPIC)
    ap.add_argument("--peer-system", default="e3231a4d-da56-4b6f-a0a7-36364541cd4b",
                    help="SystemID reported as originator of looped-back payloads")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
