from __future__ import annotations

from dataclasses import replace

from rulelens.config import InvariantConfig
from rulelens.io.trace import EntityState, Frame, Trace
from rulelens.ir.model import Invariant
from rulelens.miners.invariants import mine_invariants


def found(
    traces: list[Trace], config: InvariantConfig | None = None
) -> set[tuple[str | None, str | None, Invariant]]:
    out: set[tuple[str | None, str | None, Invariant]] = set()
    for rule in mine_invariants(traces, config):
        assert rule.kind == "invariant" and rule.invariant is not None
        out.add((rule.entity, rule.attr, rule.invariant))
    return out


def test_expected_invariants_with_spec_defaults(traces: list[Trace]) -> None:
    """3 sessions x 81 ticks = 243 samples of World, enough for the default thresholds."""
    assert found(traces) == {
        ("World", "score", Invariant("range", min=0, max=80)),
        ("World", "score", Invariant("modulo", k=10)),
        ("World", "score", Invariant("monotonic", direction="non_decreasing")),
        ("World", "timer", Invariant("range", min=0, max=8)),
        ("World", "timer", Invariant("monotonic", direction="non_increasing")),
        ("World", "gameState", Invariant("enum_set", values=("GameOver", "Playing"))),
    }


def test_stats_and_evidence(traces: list[Trace]) -> None:
    rules = mine_invariants(traces)
    timer_range = next(
        r for r in rules
        if r.attr == "timer" and r.invariant is not None and r.invariant.template == "range"
    )
    assert timer_range.stats is not None
    assert (timer_range.stats.support, timer_range.stats.sessions) == (243, 3)
    assert timer_range.stats.confidence == 1.0
    # evidence points at where the maximum (first tick) and the minimum (tick 80) were seen
    assert {e.tick for e in timer_range.evidence} == {0, 80}


def test_needs_three_sessions(traces: list[Trace]) -> None:
    assert mine_invariants(traces[:2]) == []


def test_needs_enough_ticks(traces: list[Trace]) -> None:
    assert mine_invariants(traces, InvariantConfig(min_support_ticks=244)) == []


def test_no_pairwise_relation_between_score_and_timer(traces: list[Trace]) -> None:
    """score starts below timer and ends above it, so neither x <= y nor x == y holds."""
    low_bar = InvariantConfig(min_support_ticks=1, min_sessions=1)
    assert not any(inv.template == "pair" for _, _, inv in found(traces, low_bar))


def test_pairwise_relations_are_found(traces: list[Trace]) -> None:
    """hp <= max_hp on every tick, and shield == armor on every tick."""
    header = replace(
        traces[0].header,
        entity_types={"Unit": {"hp": "int", "max_hp": "int", "shield": "int", "armor": "int"}},
    )
    samples = [(10, 10, 20), (7, 10, 2), (7, 12, 20), (12, 12, 2)]
    frames = tuple(
        Frame(tick, (EntityState(1, "Unit", {"hp": hp, "max_hp": cap, "shield": s, "armor": s}),))
        for tick, (hp, cap, s) in enumerate(samples)
    )
    trace = replace(traces[0], header=header, frames=frames)
    low_bar = InvariantConfig(min_support_ticks=1, min_sessions=1)
    pairs = {(attr, inv) for _, attr, inv in found([trace], low_bar) if inv.template == "pair"}
    assert pairs == {
        ("hp", Invariant("pair", op="<=", other="max_hp")),
        ("armor", Invariant("pair", op="==", other="shield")),
    }
