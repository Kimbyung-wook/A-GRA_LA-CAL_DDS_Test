"""OMS WebSocket Protocol (OWP 1.0) operation parsing and formatting (OMSC-SPC-013 5.1.1.1).

Fields are separated by one or more spaces/tabs. Because a CAL Message (OMS JSON) may itself
contain spaces, the *last* field of INIT, PUB and MSG is taken as the remainder of the
WebSocket message after the preceding fixed fields; this is the only reading under which the
protocol can carry arbitrary JSON text.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")
_DELIM = re.compile(r"[ \t]+")

CLIENT_OPS = {"INIT", "PUB", "SUB", "UNSUB"}
SERVER_OPS = {"INFO", "MSG", "+OK", "-ERR"}

# Table 5.1-7 Errors
UNSUPPORTED_VERSION = "Unsupported-Version"
UNSUPPORTED_SCHEMA = "Unsupported-Schema"
UNSUPPORTED_SERVICE = "Unsupported-Service"
ILLEGAL_OPERATION = "Illegal-Operation"
ILLEGAL_ARGUMENT = "Illegal-Argument"
ILLEGAL_STATE = "Illegal-State"
INTERNAL_ERROR = "Internal-Error"
INVALID_MESSAGE = "Invalid-Message"


class OwpError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code} {detail}".strip())
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Operation:
    name: str
    args: tuple[str, ...]


def _split(text: str, maxsplit: int = 0) -> list[str]:
    return _DELIM.split(text.strip(" \t"), maxsplit=maxsplit)


def parse_client(text: str) -> Operation:
    """Parse a client protocol operation, raising OwpError on malformed input."""
    head = _split(text, 1)[0] if text.strip(" \t") else ""
    if head not in CLIENT_OPS:
        raise OwpError(ILLEGAL_OPERATION, f"unknown client operation {head[:32]!r}")
    if head == "INIT":
        parts = _split(text, 1)
        if len(parts) != 2:
            raise OwpError(ILLEGAL_ARGUMENT, "INIT <JSON text>")
    elif head == "PUB":
        parts = _split(text, 2)
        if len(parts) != 3:
            raise OwpError(ILLEGAL_ARGUMENT, "PUB <topic> <message>")
        _name(parts[1], "topic")
    elif head == "SUB":
        parts = _split(text)
        if len(parts) not in (4, 5):
            raise OwpError(ILLEGAL_ARGUMENT, "SUB <subscription id> <message name> <topic> [group]")
        _name(parts[1], "subscription id")
        _name(parts[3], "topic")
        if len(parts) == 5:
            _name(parts[4], "group")
    else:  # UNSUB
        parts = _split(text)
        if len(parts) != 2:
            raise OwpError(ILLEGAL_ARGUMENT, "UNSUB <subscription id>")
        _name(parts[1], "subscription id")
    return Operation(head, tuple(parts[1:]))


def _name(value: str, what: str) -> None:
    if not NAME_RE.match(value):
        raise OwpError(ILLEGAL_ARGUMENT, f"{what} does not match ^[A-Za-z0-9_\\-.]+$")


def parse_json_object(text: str) -> dict:
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise OwpError(ILLEGAL_ARGUMENT, f"not a valid JSON text: {exc}") from exc
    if not isinstance(obj, dict):
        raise OwpError(ILLEGAL_ARGUMENT, "JSON text must be an object")
    return obj


# ---------------------------------------------------------------- server operations
def fmt_info(info: dict) -> str:
    return "INFO " + json.dumps(info, separators=(",", ":"))


def fmt_msg(sub_id: str, message: str) -> str:
    return f"MSG {sub_id} {message}"


def fmt_ok() -> str:
    return "+OK"


def fmt_err(code: str, detail: str = "") -> str:
    detail = " ".join(detail.split())  # keep it on one line
    return f"-ERR {code} {detail}".rstrip()


# ---------------------------------------------------------------- client-side helpers
def parse_server(text: str) -> Operation:
    head = _split(text, 1)[0] if text.strip(" \t") else ""
    if head in ("INFO",):
        return Operation(head, tuple(_split(text, 1)[1:]))
    if head == "MSG":
        return Operation(head, tuple(_split(text, 2)[1:]))
    if head == "+OK":
        return Operation(head, ())
    if head == "-ERR":
        return Operation(head, tuple(_split(text, 2)[1:]))
    raise OwpError(ILLEGAL_OPERATION, f"unknown server operation {head[:32]!r}")
