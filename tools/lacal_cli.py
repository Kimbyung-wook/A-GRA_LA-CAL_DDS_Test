"""lacal: ros2-style command line for a CAL Server's graph/admin API.

    uv run python tools/lacal_cli.py graph [--dot]
    uv run python tools/lacal_cli.py service list
    uv run python tools/lacal_cli.py topic list
    uv run python tools/lacal_cli.py topic info <topic> <message>
    uv run python tools/lacal_cli.py topic hz   <topic> <message>
    uv run python tools/lacal_cli.py topic echo [--topic T] [--msg M]
    uv run python tools/lacal_cli.py events
    uv run python tools/lacal_cli.py trace [--kinds pub,deliver,dds_in,dds_out,err] [--filter TEXT]
    uv run python tools/lacal_cli.py pub <topic> <file.json|-> [--url ws://...] [--service test_client]

Global option --admin selects the admin endpoint (default http://127.0.0.1:8766).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
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


def format_trace(ev: dict, prev_t: float | None) -> str:
    """One line per trace event: time, delta, server, kind, who -> what, size, state/id, timing."""
    t = time.localtime(ev["t"])
    stamp = time.strftime("%H:%M:%S", t) + f".{int(ev['t'] * 1e6) % 1000000:06d}"
    dms = None if prev_t is None else (ev["t"] - prev_t) * 1000
    delta = "" if dms is None else f"+{dms:.3f}ms" if dms < 10 else f"+{dms:.1f}ms"
    kind = ev["kind"]
    s = ev.get("summary") or {}
    what = f'{ev.get("message", "")}@{ev.get("topic", "")}' if ev.get("message") else ""
    if kind == "pub":
        who = f'{ev.get("service")} -> {what}'
    elif kind in ("deliver", "drop"):
        who = f'{what} -> {ev.get("service")} ({ev.get("sub_id")}, from {ev.get("origin")})'
    elif kind == "dds_in":
        who = f'{ev.get("from_server")} => {what} [{ev.get("qos")}]'
    elif kind == "dds_out":
        who = f'{what} => DDS [{ev.get("qos")}]'
    elif kind == "err":
        who = f'{ev.get("service")} {ev.get("op")} -ERR {ev.get("code")} {ev.get("detail", "")}'
    else:
        who = f'{ev.get("service", "")} {ev.get("sub_id", "")} {what}'.strip()
    extra = [f'{ev["bytes"]}B'] if ev.get("bytes") else []
    state = s.get("CommandProcessingState") or s.get("CommandState") or s.get("SystemState")
    if state:
        extra.append(state)
    if s.get("id"):
        extra.append(f'{s.get("id_field", "id")}={s["id"][:8]}')
    if ev.get("validate_ms") is not None:
        extra.append(f'validate {ev["validate_ms"]}ms')
    color = {"pub": "1", "err": "31", "dds_in": "34", "dds_out": "34", "drop": "31"}.get(kind, "0")
    head = f"{stamp} {delta:>10} {ev['server_id']:<10} \033[{color}m{kind.upper():<10}\033[0m"
    return f"{head} {who}  {'  '.join(extra)}".rstrip()


def cmd_trace(args) -> None:
    from websockets.asyncio.client import connect
    kinds = set(args.kinds.split(",")) if args.kinds else None
    needle = (args.filter or "").lower()
    if sys.platform == "win32":
        os.system("")  # enable ANSI colours in the Windows console

    async def go():
        prev = None
        async with connect(ws_url(args.admin, f"/trace/events?since={0 if args.backlog else 10**12}")) as ws:
            async for frame in ws:
                ev = json.loads(frame)
                if ev["kind"] == "hello":
                    print(f"# tracing {ev['server_id']} ({ev.get('system_label', '')}) via {args.admin}")
                    continue
                if kinds and ev["kind"] not in kinds:
                    continue
                if needle and needle not in json.dumps(ev).lower():
                    continue
                print(format_trace(ev, prev), flush=True)
                prev = ev["t"]
    _run(go())


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
    tr = sp.add_parser("trace", help="live message timing, one line per routing step")
    tr.add_argument("--kinds", help="comma list: connect,disconnect,sub,unsub,pub,deliver,drop,dds_out,dds_in,err")
    tr.add_argument("--filter", help="only events whose JSON contains this text")
    tr.add_argument("--backlog", action="store_true", help="print the server's recent history first")
    tr.set_defaults(fn=cmd_trace)
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
