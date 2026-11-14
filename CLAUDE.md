# CLAUDE.md — RuleLens

RuleLens infers game rules from gameplay traces and generates a human-readable spec. **Read `SPEC.md` first. It is the source of truth.** If a task conflicts with SPEC.md, ask before deviating, and update SPEC.md when a decision changes.

## Layout
- `unity/RuleLensGames/`: Unity 6 LTS project (C#). Contains the test games G1–G3, the Recorder SDK, AgentBridge and auto-players.
- `inference/`: Python 3.11 package `rulelens` (eventizer, features, miners, IR, report, eval, CLI).
- `groundtruth/`: hand-written ground-truth Rule IR per game.
- `traces/`, `out/`: generated, gitignored.

## Contracts (do not change without updating SPEC.md and both sides)
- Trace format `rulelens.trace/1`: `inference/rulelens/io/trace.schema.json` (SPEC §5.2)
- Rule IR `rulelens.ir/1`: `inference/rulelens/ir/` (SPEC §7.1)

## Hard rules
- The Recorder SDK logs **raw state and low-level events only**. It contains no game-specific logic and never states *why* something happened. Its only integration point is `ObservedEntity` + `[Observe]`.
- Miners must not read `groundtruth/`. Only `rulelens.eval` may read it.
- An LLM may only rephrase rules that are already in the IR (`--polish`, off by default). It must never create rules.
- No work on online games, anti-cheat, or injection into commercial binaries.

## Commands
```bash
cd inference
pip install -e ".[dev]"
pytest
ruff check . && mypy --strict rulelens
rulelens validate ../traces/g1/*.jsonl
rulelens infer ../traces/g1/*.jsonl -o ../out/g1/rules.yaml
rulelens report ../out/g1/rules.yaml -o ../out/g1/SPEC_g1.md
rulelens eval ../out/g1/rules.yaml --truth ../groundtruth/g1.truth.yaml
```

## Conventions
- Python: type hints everywhere, dataclasses for IR, pure functions in miners (`traces -> list[Rule]`), and config defaults taken from SPEC.
- Every miner ships with fixture-based unit tests in which the expected rule is known.
- C#: the Recorder lives in its own `.asmdef`; game code must not reference miner concepts.
- Keep fixture traces small, under 200 ticks, and hand-readable.

## Current milestone
M0 → M1. Follow SPEC §16 "First tasks" in order.
