from __future__ import annotations

from pathlib import Path

import pytest

from rulelens.io.trace import Trace, read_trace

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def fixture_paths() -> list[Path]:
    return sorted(FIXTURES.glob("g1_mini_s*.jsonl"))


@pytest.fixture(scope="session")
def traces(fixture_paths: list[Path]) -> list[Trace]:
    return [read_trace(p) for p in fixture_paths]
