"""Run miners over a batch of traces and assemble the Rule IR (``rulelens infer``)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from rulelens.config import Config, config_hash
from rulelens.io.trace import Trace
from rulelens.ir.model import Rule, RuleSet
from rulelens.miners.causal import mine_causal
from rulelens.miners.invariants import mine_invariants

Miner = Callable[[Sequence[Trace], Config], list[Rule]]

# Order here is the order of rules in the output.
MINERS: dict[str, Miner] = {
    "causal": lambda traces, cfg: mine_causal(traces, cfg.causal),
    "invariants": lambda traces, cfg: mine_invariants(traces, cfg.invariants),
}
PLANNED_MINERS = ("input_effect", "fsm", "timing", "stochastic")


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, cwd=Path(__file__).parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def game_prefix(game: str) -> str:
    """``g1_coin`` -> ``g1`` (used in rule ids such as ``R-g1-002``)."""
    return game.split("_", 1)[0]


def infer(
    traces: Sequence[Trace], config: Config | None = None, miners: Sequence[str] | None = None
) -> RuleSet:
    if not traces:
        raise ValueError("no traces given")
    cfg = config or Config()
    games = sorted({t.header.game for t in traces})
    if len(games) != 1:
        raise ValueError(f"traces come from different games: {games}")
    selected = list(miners) if miners is not None else list(MINERS)
    for name in selected:
        if name in PLANNED_MINERS:
            raise ValueError(f"miner {name!r} is not implemented yet")
        if name not in MINERS:
            raise ValueError(f"unknown miner {name!r}; available: {sorted(MINERS)}")

    rules: list[Rule] = []
    for name in MINERS:
        if name in selected:
            rules.extend(MINERS[name](traces, cfg))
    prefix = game_prefix(games[0])
    numbered = tuple(replace(r, id=f"R-{prefix}-{i:03d}") for i, r in enumerate(rules, start=1))

    return RuleSet(
        game=games[0],
        rules=numbered,
        generated_from={
            "traces": len(traces),
            "ticks": sum(len(t.frames) for t in traces),
            "observability": "/".join(sorted({t.header.observability for t in traces})),
            "miners": [name for name in MINERS if name in selected],
            "git_commit": _git_commit(),
            "config_hash": config_hash(cfg),
            "trace_files": [t.name for t in traces],
        },
    )
