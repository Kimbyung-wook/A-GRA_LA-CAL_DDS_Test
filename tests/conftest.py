import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "nodes"))

from server.omsjson import OmsJsonCodec  # noqa: E402


@pytest.fixture(scope="session")
def mini_codec():
    return OmsJsonCodec(ROOT / "tests" / "fixtures" / "mini.xsd")


@pytest.fixture(scope="session")
def agra_codec():
    return OmsJsonCodec()
