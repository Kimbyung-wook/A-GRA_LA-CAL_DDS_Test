"""Helpers shared by the demo nodes: UCI message envelopes and EncodedPayload handling."""
from __future__ import annotations

import datetime as dt
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lxml import etree  # noqa: E402

from server.omsjson import OmsJsonCodec  # noqa: E402

SCHEMA_VERSION = "006.0.ASK"
DEFAULT_URL = "ws://127.0.0.1:8765"
DMS_TOPIC = "ms.dms"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_uuid() -> str:
    return str(uuid.uuid4())


def uci_message(name: str, system_uuid: str, message_data: dict) -> dict:
    """Wrap MessageData with the mandatory SecurityInformation and MessageHeader."""
    return {name: {
        "SecurityInformation": {"Classification": "U", "OwnerProducer": [{"GovernmentIdentifier": "USA"}]},
        "MessageHeader": {"SystemID": {"UUID": system_uuid}, "Timestamp": now_iso(),
                          "SchemaVersion": SCHEMA_VERSION, "Mode": "LIVE"},
        "MessageData": message_data,
    }}


def body(msg: dict) -> dict:
    """MessageData of an OMS JSON message."""
    return next(iter(msg.values()))["MessageData"]


def message_type_enum(message_name: str) -> str:
    """SystemStatus -> SYSTEM_STATUS (MessageTypeEnum naming)."""
    name = message_name.removeprefix("MA_")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).upper()


def encode_payload(codec: OmsJsonCodec, msg: dict) -> str:
    """UCI message -> UTF-8 XML -> hexBinary (MS L1 Volume: EncodedPayload across the L1 boundary)."""
    return codec.to_xml_bytes(msg).hex().upper()


def decode_payload(codec: OmsJsonCodec, hex_text: str) -> dict:
    root = etree.fromstring(bytes.fromhex(hex_text))
    codec.validate_xml(root)
    return codec.xml_to_json(root)
