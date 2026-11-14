# GameSpec-Miner (RuleLens)

RuleLens observes a running game, infers its rules from the recorded traces, and
generates a human-readable spec in which every rule carries statistics and evidence.

- [`SPEC.md`](SPEC.md) is the source of truth for the design.
- [`CLAUDE.md`](CLAUDE.md) holds the working rules for the repo.

## Status

Milestone M0 → M1 (SPEC §12). The Python side of SPEC §16 tasks 1–6 is in place:
trace schema and reader, Rule IR, eventizer, invariant and causal miners, eval
matcher, and the `validate` / `infer` / `eval` commands. The Unity project
(`unity/RuleLensGames/`) is still an empty skeleton.

## Quick start

```bash
cd inference
pip install -e ".[dev]"
pytest
ruff check . && mypy --strict rulelens
```

Try the pipeline on the fixture traces (a scripted miniature of G1):

```bash
rulelens validate tests/fixtures/*.jsonl
rulelens infer tests/fixtures/*.jsonl -o ../out/mini/rules.yaml
rulelens eval ../out/mini/rules.yaml --truth tests/fixtures/g1_mini.truth.yaml
```

The fixtures are small, so the causal miner needs a lower support threshold than
the SPEC default to report anything. Put this in `inference/rulelens.toml`:

```toml
[causal]
min_support = 2
```
