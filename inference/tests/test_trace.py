from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rulelens.io.trace import Trace, TraceError, load_schema, read_trace, validate_trace

HEADER: dict[str, Any] = {
    "t": "header", "schema": "rulelens.trace/1", "game": "g1_coin", "build": "0.1.0",
    "observability": "whitebox", "tick_hz": 50, "seed": 1234, "session": "s01", "agent": "random",
    "entity_types": {
        "World": {"attrs": {"score": "int", "gameState": "enum:Playing|GameOver"}},
        "Player": {"attrs": {"pos": "vec2"}},
    },
    "static": {"walls": [[0, 0, 20, 1]]},
}


def frame(tick: int, score: Any = 0, state: str = "Playing") -> dict[str, Any]:
    return {"t": "frame", "tick": tick, "ents": [
        {"id": 0, "type": "World", "a": {"score": score, "gameState": state}},
        {"id": 1, "type": "Player", "a": {"pos": [2.0, 3.0]}},
    ]}


FOOTER = {"t": "footer", "ticks": 2, "end_reason": "GameOver"}


def write(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    path = tmp_path / "trace.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_fixtures_are_valid(fixture_paths: list[Path]) -> None:
    assert len(fixture_paths) == 3
    for path in fixture_paths:
        assert validate_trace(path) == []


def test_fixtures_are_small(traces: list[Trace]) -> None:
    assert all(len(t.frames) < 200 for t in traces)


def test_read_parses_records(traces: list[Trace]) -> None:
    trace = traces[0]
    assert trace.name == "g1_mini_s01.jsonl"
    assert trace.header.tick_hz == 10
    assert trace.header.entity_types["World"]["gameState"] == "enum:Playing|GameOver"
    assert trace.header.walls[0] == (0.0, 0.0, 10.0, 1.0)
    assert trace.frames[0].tick == 0 and trace.frames[-1].tick == 80
    world = trace.frames[0].ents[0]
    assert world.type == "World" and world.attrs == {
        "score": 0, "timer": 8, "gameState": "Playing"
    }
    assert trace.frames[0].ents[1].attrs["pos"] == (2.0, 2.0)
    assert trace.inputs[0].action == "Move" and trace.inputs[0].value == (1.0, 0.0)
    assert trace.footer.end_reason == "GameOver"


def test_spec_example_records_match_schema() -> None:
    """The records shown in SPEC §5.2 must be accepted by the schema."""
    from jsonschema import Draft202012Validator

    validator = Draft202012Validator(load_schema())
    examples: list[dict[str, Any]] = [
        HEADER,
        frame(0),
        {"t": "input", "tick": 12, "action": "Move", "phase": "value", "value": [1, 0]},
        {"t": "input", "tick": 30, "action": "Attack", "phase": "down"},
        {"t": "contact", "tick": 40, "phase": "enter", "a": 1, "b": 7},
        {"t": "spawn", "tick": 150, "id": 9, "type": "Coin"},
        {"t": "destroy", "tick": 41, "id": 7},
        {"t": "footer", "ticks": 3000, "end_reason": "GameOver"},
    ]
    for record in examples:
        validator.validate(record)


def test_minimal_trace_is_valid(tmp_path: Path) -> None:
    assert validate_trace(write(tmp_path, [HEADER, frame(0), frame(1), FOOTER])) == []


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([frame(0), frame(1), FOOTER], "first record must be the header"),
        ([HEADER, frame(0), frame(1)], "last record must be the footer"),
        ([HEADER, frame(0), frame(2), FOOTER], "does not follow"),
        ([HEADER, frame(0), frame(1, score="ten"), FOOTER], "is not a int"),
        ([HEADER, frame(0), frame(1, score=1.5), FOOTER], "is not a int"),
        ([HEADER, frame(0), frame(1, state="Paused"), FOOTER], "is not a enum"),
        ([HEADER, frame(0), frame(1), {**FOOTER, "ticks": 9}], "footer says 9 ticks"),
        ([{**HEADER, "schema": "rulelens.trace/2"}, frame(0), frame(1), FOOTER], "was expected"),
        ([{**HEADER, "observability": "xray"}, frame(0), frame(1), FOOTER], "is not one of"),
        ([HEADER, frame(0), frame(1), {"t": "why", "tick": 1}, FOOTER], "unknown record kind"),
        (
            [HEADER, frame(0), frame(1),
             {"t": "input", "tick": 1, "action": "Move", "phase": "value"}, FOOTER],
            "'value' is a required property",
        ),
        (
            [HEADER, frame(0), frame(1),
             {"t": "contact", "tick": 1, "phase": "enter", "a": 1, "b": 99}, FOOTER],
            "unknown entity id 99",
        ),
        (
            [HEADER, frame(0), frame(1), {"t": "destroy", "tick": 7, "id": 1}, FOOTER],
            "outside frames",
        ),
        (
            [HEADER, frame(0),
             {"t": "frame", "tick": 1, "ents": [{"id": 5, "type": "Ghost", "a": {}}]}, FOOTER],
            "undeclared entity type",
        ),
        (
            [HEADER, frame(0),
             {"t": "frame", "tick": 1, "ents": [{"id": 1, "type": "Player", "a": {}}]}, FOOTER],
            "attributes do not match header",
        ),
    ],
)
def test_invalid_traces_are_rejected(
    tmp_path: Path, records: list[dict[str, Any]], expected: str
) -> None:
    path = write(tmp_path, records)
    errors = validate_trace(path)
    assert any(expected in e for e in errors), errors
    with pytest.raises(TraceError):
        read_trace(path)


def test_invalid_json_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(HEADER) + "\n{not json\n", encoding="utf-8")
    assert any("invalid JSON" in e for e in validate_trace(path))
