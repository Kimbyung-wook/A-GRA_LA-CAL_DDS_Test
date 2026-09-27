"""UCI primitive per message, read from the XSD annotations ("UCI_PRIMITIVE: Data-1.").

The primitive drives the default DDS reliability (MS L1 Interface Volume 1.2.5.5):
RELIABLE for Command-2, ActionRequest-2, DataRequest-2, DataRecord-1 and the operator/system
notification messages; BEST_EFFORT for Data-1 and Status-1.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .omsjson import DEFAULT_SCHEMA

CACHE = DEFAULT_SCHEMA.with_name("primitives.json")

_ELEMENT = re.compile(r'^\t<xs:element name="([A-Za-z0-9_]+)"', re.M)
_PRIMITIVE = re.compile(r"UCI_PRIMITIVE:\s*([A-Za-z]+-\d+)")

RELIABLE_PRIMITIVES = {"Command-2", "ActionRequest-2", "DataRequest-2", "DataRecord-1"}
RELIABLE_MESSAGES = {"MA_SystemNotification", "OperatorNotification", "OperatorNotificationAck"}


def extract(schema_path: Path = DEFAULT_SCHEMA) -> dict[str, str]:
    text = schema_path.read_text(encoding="utf-8")
    starts = [(m.start(), m.group(1)) for m in _ELEMENT.finditer(text)]
    table: dict[str, str] = {}
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        m = _PRIMITIVE.search(text, pos, min(end, pos + 20000))
        if m:
            table[name] = m.group(1)
    return table


@lru_cache(maxsize=1)
def table() -> dict[str, str]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    data = extract()
    CACHE.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    return data


def primitive_of(message_name: str) -> str | None:
    return table().get(message_name)


def is_reliable(message_name: str) -> bool:
    return message_name in RELIABLE_MESSAGES or primitive_of(message_name) in RELIABLE_PRIMITIVES
