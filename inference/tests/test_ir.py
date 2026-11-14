from __future__ import annotations

import pytest

from rulelens.ir import IRError, dumps, load, loads
from rulelens.ir.model import RULE_KINDS, Evidence, Predicate, Rule, RuleSet, Stats, Trigger
from tests.conftest import REPO_ROOT

# The example from SPEC §7.1 (evidence list filled in).
SPEC_EXAMPLE = """
schema: rulelens.ir/1
game: g2_stealth
generated_from: {traces: 50, ticks: 750000, observability: whitebox}
rules:
  - id: R-g1-002
    kind: causal
    trigger: {event: contact_enter, a: Player, b: Coin}
    effects:
      - {event: destroy, target: b}
      - {event: attr_change, target: World, attr: score, op: add, value: 10}
    window_ticks: 1
    stats: {support: 143, confidence: 1.0, lift: 41.2}
    evidence: [{trace: s01.jsonl, tick: 812}, {trace: s07.jsonl, tick: 233}]

  - id: R-g2-003
    kind: fsm_transition
    entity: Guard
    from: Patrol
    to: Chase
    guard:
      - {feature: dist, target: Player, op: "<", value: 4.97, ci: [4.90, 5.05]}
      - {feature: rel_angle, target: Player, op: "<", value: 45.3}
      - {feature: los, target: Player, op: "==", value: true}
    stats: {support: 88, confidence: 0.98}

  - id: R-g3-003
    kind: stochastic
    trigger: {event: destroy, type: Slime}
    effects: [{event: spawn, type: Potion}]
    probability: {p: 0.247, ci: [0.21, 0.29], n: 520}
"""


def test_spec_example_parses() -> None:
    ruleset = loads(SPEC_EXAMPLE)
    assert ruleset.game == "g2_stealth"
    assert ruleset.generated_from["ticks"] == 750000
    coin, chase, drop = ruleset.rules
    assert coin.trigger == Trigger("contact_enter", a="Player", b="Coin")
    assert coin.effects[1].value == 10 and coin.window_ticks == 1
    assert coin.stats == Stats(support=143, confidence=1.0, lift=41.2)
    assert coin.evidence == (Evidence("s01.jsonl", 812), Evidence("s07.jsonl", 233))
    assert (chase.from_, chase.to) == ("Patrol", "Chase")
    assert chase.guard[0] == Predicate("dist", "<", 4.97, target="Player", ci=(4.90, 5.05))
    assert chase.guard[2].value is True
    assert drop.probability is not None and drop.probability.ci == (0.21, 0.29)


def test_round_trip_is_lossless() -> None:
    ruleset = loads(SPEC_EXAMPLE)
    text = dumps(ruleset)
    assert loads(text) == ruleset
    assert "from: Patrol" in text and "from_" not in text
    assert dumps(loads(text)) == text


def test_empty_fields_are_omitted() -> None:
    text = dumps(RuleSet(game="g", rules=(Rule(id="R-1", kind="invariant", entity="World"),)))
    assert "trigger" not in text and "effects" not in text and "params" not in text


@pytest.mark.parametrize(
    "bad",
    [
        "schema: rulelens.ir/2\ngame: g\nrules: []",
        "schema: rulelens.ir/1\nrules: []",
        "schema: rulelens.ir/1\ngame: g\nrules: [{id: R-1, kind: magic}]",
        "schema: rulelens.ir/1\ngame: g\nrules: [{id: R-1, kind: causal, triger: {}}]",
        "schema: rulelens.ir/1\ngame: g\nrules: [{id: R-1, kind: causal, trigger: {evnt: x}}]",
        "schema: rulelens.ir/1\ngame: g\nrules: [{kind: causal}]",
        "schema: rulelens.ir/1\ngame: g\nrules: [{id: A, kind: causal}, {id: A, kind: causal}]",
        "schema: rulelens.ir/1\ngame: g\n"
        "rules: [{id: R-1, kind: invariant, invariant: {template: vibes}}]",
        "rules: [",
    ],
)
def test_invalid_documents_are_rejected(bad: str) -> None:
    with pytest.raises(IRError):
        loads(bad)


def test_g1_ground_truth_loads() -> None:
    truth = load(REPO_ROOT / "groundtruth" / "g1.truth.yaml")
    assert truth.game == "g1_coin"
    assert len(truth.rules) == 13
    assert {r.kind for r in truth.rules} <= set(RULE_KINDS)
    by_id = {r.id: r for r in truth.rules}
    assert by_id["T-g1-02"].effects[1].value == 10
    assert by_id["T-g1-03"].effects[1].value == 50
    assert by_id["T-g1-04a"].params == {
        "template": "periodic_spawn", "type": "Coin", "period_s": 3.0, "max_alive": 8
    }
    inv = by_id["T-g1-07c"].invariant
    assert inv is not None and (inv.min, inv.max) == (0, 60)
    assert loads(dumps(truth)) == truth
