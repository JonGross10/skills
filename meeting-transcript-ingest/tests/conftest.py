"""Load ingest_meeting_transcript.py by path — the skill scripts are standalone
files, not an importable package."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ingest_meeting_transcript.py"


def _load():
    spec = importlib.util.spec_from_file_location("ingest_meeting_transcript", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def mti():
    return _load()
