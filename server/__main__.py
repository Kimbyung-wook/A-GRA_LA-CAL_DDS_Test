"""uv run python -m server --config config/server_a.yaml"""
import argparse
import asyncio
import faulthandler
import logging
import os
import sys

from .app import CalServer
from .config import load


async def _run(path: str) -> None:
    server = CalServer(load(path))
    await server.start()
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m server", description="OMS Language-Agnostic CAL Server (OWP 1.0)")
    ap.add_argument("--config", required=True)
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.environ.get("LACAL_STACKDUMP_S"):  # debugging aid: periodic thread stack dumps
        faulthandler.dump_traceback_later(float(os.environ["LACAL_STACKDUMP_S"]), repeat=True, file=sys.stderr)
    if sys.platform == "win32":
        # The Proactor loop closes a listening socket after one failed accept (WinError 64 when a
        # client aborts during the handshake), silently taking the server offline.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(_run(args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
