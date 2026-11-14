from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rulelens.cli import app
from rulelens.ir import load
from tests.conftest import FIXTURES

runner = CliRunner()


def test_validate_ok(fixture_paths: list[Path]) -> None:
    result = runner.invoke(app, ["validate", *map(str, fixture_paths)])
    assert result.exit_code == 0, result.output
    assert result.output.count("ok ") == 3
    assert "Coin-Player ×9" in result.output


def test_validate_reports_bad_file(tmp_path: Path, fixture_paths: list[Path]) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"t":"frame","tick":0,"ents":[]}\n', encoding="utf-8")
    result = runner.invoke(app, ["validate", str(bad), str(fixture_paths[0])])
    assert result.exit_code == 1
    assert "FAIL" in result.output and "first record must be the header" in result.output


def test_infer_then_eval(tmp_path: Path, fixture_paths: list[Path]) -> None:
    config = tmp_path / "rulelens.toml"
    config.write_text("[causal]\nmin_support = 2\n", encoding="utf-8")
    rules = tmp_path / "out" / "rules.yaml"
    result = runner.invoke(
        app, ["infer", *map(str, fixture_paths), "-o", str(rules), "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert "3 causal" in result.output
    assert len(load(rules).rules) == 9

    report = tmp_path / "eval.json"
    result = runner.invoke(
        app,
        ["eval", str(rules), "--truth", str(FIXTURES / "g1_mini.truth.yaml"), "-o", str(report)],
    )
    assert result.exit_code == 0, result.output
    assert "overall" in result.output
    scores = json.loads(report.read_text(encoding="utf-8"))
    assert scores["overall"]["recall"] == 1.0 and scores["overall"]["tp"] == 7


def test_infer_miner_subset(tmp_path: Path, fixture_paths: list[Path]) -> None:
    rules = tmp_path / "rules.yaml"
    args = ["infer", *map(str, fixture_paths), "-o", str(rules), "--miners"]
    assert runner.invoke(app, [*args, "invariants"]).exit_code == 0
    assert {r.kind for r in load(rules).rules} == {"invariant"}
    assert runner.invoke(app, [*args, "fsm"]).exit_code == 2
    assert runner.invoke(app, [*args, "nope"]).exit_code == 2
