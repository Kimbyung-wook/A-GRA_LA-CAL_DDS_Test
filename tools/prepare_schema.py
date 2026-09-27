"""Refresh ./schema from the original A-GRA ASK XSDs and fix the SecurityMarkings include filename.

The repository already ships the patched schema in ./schema, so this is only needed to re-import
the XSDs (for example from a newer ASK release).

Sources, in order:
  --download      fetch from the Open Arsenal A-GRA repository on GitLab (--ref selects branch/tag/commit)
  --src DIR       a local checkout's Schema directory
  (default)       ../OpenArsenalGit/A-GRA_standard/Schema, if present
When no local source is found the script keeps the bundled schema and suggests the --download command.

The upstream MessageDefinitions XSD includes "A-GRA_SecurityMarkings_v6_0.xsd" but the file shipped
in the ASK is named "A-GRA_SecurityMarkings_v6_0_a.xsd"; the copy written here points at the real name.

Usage:
    python tools/prepare_schema.py --download [--ref main]
    python tools/prepare_schema.py --src <ASK>/A-GRA_standard/Schema
"""
import argparse
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT.parent / "OpenArsenalGit" / "A-GRA_standard" / "Schema"
DST = ROOT / "schema"

REPO = "https://gitlab.com/open-arsenal/a-gra/standard"
RAW = REPO + "/-/raw/{ref}/Schema/{name}"

MSG = "A-GRA_MessageDefinitions_v6_0_a.xsd"
SEC = "A-GRA_SecurityMarkings_v6_0_a.xsd"


def download(ref: str, dest: Path) -> None:
    for name in (MSG, SEC):
        url = RAW.format(ref=ref, name=name)
        print(f"downloading {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(dest / name, "wb") as out:
                shutil.copyfileobj(resp, out)
        except urllib.error.URLError as exc:
            raise SystemExit(f"error: download failed for {url}: {exc}") from exc
        try:  # a login page or error page would not parse as a schema
            root = etree.parse(str(dest / name)).getroot()
        except etree.XMLSyntaxError as exc:
            raise SystemExit(f"error: {url} is not valid XML: {exc}") from exc
        if root.tag != "{http://www.w3.org/2001/XMLSchema}schema":
            raise SystemExit(f"error: {url} is not an XML Schema (root element {root.tag})")


def install(src: Path) -> None:
    DST.mkdir(exist_ok=True)
    # Text round-trip for both files so line endings come out the same whether the source is a
    # local checkout (CRLF on Windows) or the GitLab raw download (LF).
    (DST / SEC).write_text((src / SEC).read_text(encoding="utf-8"), encoding="utf-8")
    text = (src / MSG).read_text(encoding="utf-8")
    fixed = text.replace('schemaLocation="A-GRA_SecurityMarkings_v6_0.xsd"', f'schemaLocation="{SEC}"')
    if fixed == text:
        print("warning: include line not found (already fixed upstream?)")
    (DST / MSG).write_text(fixed, encoding="utf-8")
    # The UCI primitive cache is derived from the XSD; drop it so it is rebuilt on next use.
    (DST / "primitives.json").unlink(missing_ok=True)
    print(f"schema written to {DST}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, help="directory holding the original ASK XSDs")
    ap.add_argument("--download", action="store_true", help=f"fetch the XSDs from {REPO}")
    ap.add_argument("--ref", default="main", help="branch, tag or commit for --download (default: main)")
    args = ap.parse_args()

    if args.download:
        with tempfile.TemporaryDirectory() as tmp:
            download(args.ref, Path(tmp))
            install(Path(tmp))
        return 0

    src = args.src or DEFAULT_SRC
    missing = [name for name in (MSG, SEC) if not (src / name).is_file()]
    if not missing:
        install(src)
        return 0

    suggestion = (f"  python tools/prepare_schema.py --download            # from {REPO}\n"
                  f"  python tools/prepare_schema.py --src <dir>           # from a local checkout's Schema/")
    if args.src is None and (DST / MSG).is_file() and (DST / SEC).is_file():
        print(f"no local ASK schema found at {src}; keeping the schema already included in {DST}.\n"
              f"To re-import it:\n{suggestion}")
        return 0
    print(f"error: {', '.join(missing)} not found in {src}.\nTry:\n{suggestion}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
