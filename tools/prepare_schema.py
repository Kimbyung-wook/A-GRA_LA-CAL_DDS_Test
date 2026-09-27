"""Copy the A-GRA XSDs into ./schema and fix the SecurityMarkings include filename.

The upstream MessageDefinitions XSD includes "A-GRA_SecurityMarkings_v6_0.xsd" but the
file shipped in the ASK is named "A-GRA_SecurityMarkings_v6_0_a.xsd".
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / "OpenArsenalGit" / "A-GRA_standard" / "Schema"
DST = ROOT / "schema"

MSG = "A-GRA_MessageDefinitions_v6_0_a.xsd"
SEC = "A-GRA_SecurityMarkings_v6_0_a.xsd"


def main() -> None:
    DST.mkdir(exist_ok=True)
    shutil.copyfile(SRC / SEC, DST / SEC)
    text = (SRC / MSG).read_text(encoding="utf-8")
    fixed = text.replace('schemaLocation="A-GRA_SecurityMarkings_v6_0.xsd"', f'schemaLocation="{SEC}"')
    if fixed == text:
        print("warning: include line not found (already fixed upstream?)")
    (DST / MSG).write_text(fixed, encoding="utf-8")
    print(f"schema written to {DST}")


if __name__ == "__main__":
    main()
