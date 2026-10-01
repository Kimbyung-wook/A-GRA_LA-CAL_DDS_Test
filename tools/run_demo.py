"""Launch CAL Server(s) and both nodes, run the scenario, print the graph, clean up.

    python tools/run_demo.py            # one CAL Server, local routing
    python tools/run_demo.py --dds      # two CAL Servers bridged by Cyclone DDS
    python tools/run_demo.py --dds --live   # keep everything running and open the live monitor
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def wait_http(url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except OSError:
            time.sleep(0.3)
    raise TimeoutError(url)


def get(url: str) -> str:
    return urllib.request.urlopen(url, timeout=5).read().decode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dds", action="store_true", help="two servers bridged by DDS")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--live", action="store_true",
                    help="run node_a continuously and open the live monitor until Ctrl+C")
    ap.add_argument("--interval", type=float, default=1.0, help="--live: seconds between payload commands")
    ap.add_argument("--duration", type=float, default=0, help="--live: stop after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--no-browser", action="store_true", help="--live: do not open a browser")
    ap.add_argument("--logs", default=str(Path(tempfile.gettempdir()) / "lacal_demo"))
    args = ap.parse_args()
    logs = Path(args.logs)
    logs.mkdir(parents=True, exist_ok=True)

    if args.dds:
        servers = [("config/server_a_dds.yaml", 8766), ("config/server_b_dds.yaml", 8776)]
        url_a, url_b = "ws://127.0.0.1:8765", "ws://127.0.0.1:8775"
    else:
        servers = [("config/server_a.yaml", 8766)]
        url_a = url_b = "ws://127.0.0.1:8765"
    admin_last = servers[-1][1]  # node_b's server; with DDS it also sees node_a via graph sync

    procs: list[subprocess.Popen] = []

    def spawn(name: str, *cmd: str) -> subprocess.Popen:
        out = open(logs / f"{name}.log", "w", encoding="utf-8")
        p = subprocess.Popen([PY, *cmd], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
        procs.append(p)
        return p

    try:
        for i, (cfg, _) in enumerate(servers):
            spawn(f"server{i}", "-m", "server", "--config", cfg)
        for _, admin in servers:
            wait_http(f"http://127.0.0.1:{admin}/healthz", 60)
        node_b = spawn("node_b", "nodes/node_b.py", "--url", url_b)
        # node_b loads the schema (~5 s) before it subscribes; wait until its subscriptions show up.
        deadline = time.time() + 60
        while time.time() < deadline and node_b.poll() is None:
            graph = json.loads(get(f"http://127.0.0.1:{admin_last}/graph"))
            if sum(e["kind"] == "sub" for e in graph["edges"]) >= 2:
                break
            time.sleep(0.3)
        if args.dds:
            time.sleep(2.0)  # DDS discovery between the two servers

        if args.live:
            return live(args, spawn, url_a, servers)

        a = subprocess.Popen([PY, "nodes/node_a.py", "--url", url_a, "--count", str(args.count), "--linger", "6"],
                             cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # Snapshot the graph while node_a is still connected (it lingers after the scenario):
        # wait until ma_core's pub edge to MA_TxDataPayloadCommand is visible.
        dot = None
        deadline = time.time() + 90
        while time.time() < deadline and a.poll() is None:
            graph = json.loads(get(f"http://127.0.0.1:{admin_last}/graph"))
            if any(e["kind"] == "pub" and e["target"].endswith("/MA_TxDataPayloadCommand") for e in graph["edges"]):
                time.sleep(3.0)  # remaining edges + DDS graph sync (debounced, 2 s period)
                dot = get(f"http://127.0.0.1:{admin_last}/graph.dot")
                break
            time.sleep(0.3)
        out, _ = a.communicate(timeout=120)
        print("\n".join(line for line in out.splitlines() if not line[:4].isdigit()))  # summary lines
        print(f"\n# graph seen from {servers[-1][0]} while node_a was connected")
        print(dot or "(not captured)")
        for _, admin in servers:
            print(get(f"http://127.0.0.1:{admin}/stats").replace("\n", ""))
        print(f"\nlogs: {logs}")
        return a.returncode
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


def live(args, spawn, url_a: str, servers) -> int:
    spawn("node_a", "nodes/node_a.py", "--url", url_a, "--count", "0", "--interval", str(args.interval))
    admins = ",".join(f"http://127.0.0.1:{admin}" for _, admin in servers)
    url = f"http://127.0.0.1:{servers[0][1]}/monitor" + (f"?admin={admins}" if len(servers) > 1 else "")
    print(f"live monitor: {url}")
    print(f"terminal trace: python tools/lacal_cli.py --admin http://127.0.0.1:{servers[0][1]} trace")
    print("press Ctrl+C to stop")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        deadline = time.time() + args.duration if args.duration else None
        while deadline is None or time.time() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
