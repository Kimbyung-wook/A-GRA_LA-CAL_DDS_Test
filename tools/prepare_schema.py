"""Refresh ./schema from the original A-GRA ASK XSDs and fix the SecurityMarkings include filename.

The repository already ships the patched schema in ./schema, so this is only needed to re-import
the XSDs from an ASK distribution (for example a newer release).

The upstream MessageDefinitions XSD includes "A-GRA_SecurityMarkings_v6_0.xsd" but the file shipped
in the ASK is named "A-GRA_SecurityMarkings_v6_0_a.xsd"; the copy written here points at the real name.

Usage:
    python tools/prepare_schema.py [--src <ASK>/A-GRA_standard/Schema]
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT.parent / "OpenArsenalGit" / "A-GRA_standard" / "Schema"
DST = ROOT / "schema"

MSG = "A-GRA_MessageDefinitions_v6_0_a.xsd"
SEC = "A-GRA_SecurityMarkings_v6_0_a.xsd"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="directory holding the original ASK XSDs")
    args = ap.parse_args()

    missing = [name for name in (MSG, SEC) if not (args.src / name).is_file()]
    if missing:
        if (DST / MSG).is_file() and (DST / SEC).is_file():
            print(f"original XSDs not found in {args.src} ({', '.join(missing)}); "
                  f"keeping the schema already included in {DST}")
            return 0
        print(f"error: {', '.join(missing)} not found in {args.src} and no schema in {DST}; "
              f"pass --src <ASK>/A-GRA_standard/Schema", file=sys.stderr)
        return 1

    DST.mkdir(exist_ok=True)
    shutil.copyfile(args.src / SEC, DST / SEC)
    text = (args.src / MSG).read_text(encoding="utf-8")
    fixed = text.replace('schemaLocation="A-GRA_SecurityMarkings_v6_0.xsd"', f'schemaLocation="{SEC}"')
    if fixed == text:
        print("warning: include line not found (already fixed upstream?)")
    (DST / MSG).write_text(fixed, encoding="utf-8")
    # The UCI primitive cache is derived from the XSD; drop it so it is rebuilt on next use.
    (DST / "primitives.json").unlink(missing_ok=True)
    print(f"schema written to {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
