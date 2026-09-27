"""lacal: ros2-style command line for a CAL Server's graph/admin API.

    python tools/lacal_cli.py graph [--dot]
    python tools/lacal_cli.py service list
    python tools/lacal_cli.py topic list
    python tools/lacal_cli.py topic info <topic> <message>
    python tools/lacal_cli.py topic hz   <topic> <message>
    python tools/lacal_cli.py topic echo [--topic T] [--msg M]
    python tools/lacal_cli.py events
    python tools/lacal_cli.py pub <topic> <file.json|-> [--url ws://...] [--service test_client]

Global option --admin selects the admin endpoint (default http://127.0.0.1:8766).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def http_json(admin: str, path: str):
    with urllib.request.urlopen(admin.rstrip("/") + path, timeout=5) as r:
        body = r.read().decode()
    return body if path.endswith(".dot") else json.loads(body)


def ws_url(admin: str, path: str) -> str:
    u = urlsplit(admin)
    return f"ws://{u.netloc}{path}"


def cmd_graph(args) -> None:
    if args.dot:
        print(http_json(args.admin, "/graph.dot"), end="")
        return
    g = http_json(args.admin, "/graph")
    nodes = {n["id"]: n for n in g["nodes"]}
    print("servers: " + ", ".join(f'{s["server_id"]}{"" if s["local"] else " (remote)"}' for s in g["servers"]))
    for t in (n for n in g["nodes"] if n["kind"] == "topic"):
        pubs = [e for e in g["edges"] if e["kind"] == "pub" and e["target"] == t["id"]]
        subs = [e for e in g["edges"] if e["kind"] == "sub" and e["source"] == t["id"]]
        print(f'\n{t["label"]}  [{t.get("primitive") or "?"}]')
        for e in pubs:
            n = nodes.get(e["source"], {})
            state = "" if e.get("active", True) else "  (inactive)"
            print(f'  pub  {n.get("label", e["source"])}@{n.get("server_id", "?")}  {e.get("rate_hz", 0)} Hz  '
                  f'{e.get("msg_count", 0)} msgs{state}')
        for e in subs:
            n = nodes.get(e["target"], {})
            grp = f'  group={e["group"]}' if e.get("group") else ""
            print(f'  sub  {n.get("label", e["target"])}@{n.get("server_id", "?")}  id={e.get("sub_id")}{grp}')


def cmd_service_list(args) -> None:
    for s in http_json(args.admin, "/services"):
        print(f'{s["service_id"]:<20} {s["server_id"]:<12} service={s["uuids"].get("service")}  '
              f'{s.get("remote_addr", "")}')


def cmd_topic_list(args) -> None:
    for t in http_json(args.admin, "/topics"):
        print(f'{t["topic"]:<16} {t["message_name"]:<50} {t.get("primitive") or ""}')


def _topic_edges(args):
    g = http_json(args.admin, "/graph")
    tid = f"topic:{args.topic}/{args.message}"
    return g, tid, [e for e in g["edges"] if tid in (e["source"], e["target"])]


def cmd_topic_info(args) -> None:
    g, tid, edges = _topic_edges(args)
    nodes = {n["id"]: n for n in g["nodes"]}
    if tid not in nodes:
        sys.exit(f"no such topic node: {args.message}@{args.topic}")
    print(f'Type: {args.message}  Topic: {args.topic}  Primitive: {nodes[tid].get("primitive")}')
    print(f'Publisher count: {sum(e["kind"] == "pub" for e in edges)}')
    print(f'Subscription count: {sum(e["kind"] == "sub" for e in edges)}')


def cmd_topic_hz(args) -> None:
    try:
        while True:
            _, _, edges = _topic_edges(args)
            rates = [e.get("rate_hz", 0) for e in edges if e["kind"] == "pub"]
            print(f"average rate: {sum(rates):.3f} Hz ({len(rates)} publisher(s))")
            time.sleep(1)
    except KeyboardInterrupt:
        pass


async def _stream(url: str) -> None:
    from websockets.asyncio.client import connect
    async with connect(url) as ws:
        async for frame in ws:
            print(json.dumps(json.loads(frame), indent=1), flush=True)


def cmd_topic_echo(args) -> None:
    q = urlencode({k: v for k, v in (("topic", args.topic), ("msg", args.msg)) if v})
    _run(_stream(ws_url(args.admin, "/tap" + (f"?{q}" if q else ""))))


def cmd_events(args) -> None:
    _run(_stream(ws_url(args.admin, "/graph/events")))


def cmd_pub(args) -> None:
    from client.lacal_client import LaCalClient
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")

    async def go():
        async with LaCalClient(args.url, args.service) as c:
            await c.publish(args.topic, json.loads(text))
            print("+OK")
    _run(go())


def _run(coro) -> None:
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(prog="lacal", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin", default="http://127.0.0.1:8766")
    sp = ap.add_subparsers(dest="cmd", required=True)
    g = sp.add_parser("graph")
    g.add_argument("--dot", action="store_true")
    g.set_defaults(fn=cmd_graph)
    s = sp.add_parser("service").add_subparsers(dest="sub", required=True)
    s.add_parser("list").set_defaults(fn=cmd_service_list)
    t = sp.add_parser("topic").add_subparsers(dest="sub", required=True)
    t.add_parser("list").set_defaults(fn=cmd_topic_list)
    for name, fn in (("info", cmd_topic_info), ("hz", cmd_topic_hz)):
        p = t.add_parser(name)
        p.add_argument("topic")
        p.add_argument("message")
        p.set_defaults(fn=fn)
    e = t.add_parser("echo")
    e.add_argument("--topic")
    e.add_argument("--msg")
    e.set_defaults(fn=cmd_topic_echo)
    sp.add_parser("events").set_defaults(fn=cmd_events)
    p = sp.add_parser("pub")
    p.add_argument("topic")
    p.add_argument("file")
    p.add_argument("--url", default="ws://127.0.0.1:8765")
    p.add_argument("--service", default="test_client")
    p.set_defaults(fn=cmd_pub)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
