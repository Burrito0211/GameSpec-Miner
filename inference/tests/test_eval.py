from __future__ import annotations

from dataclasses import replace

from rulelens.config import CausalConfig, Config, EvalConfig
from rulelens.eval import compare_rules, evaluate
from rulelens.io.trace import Trace
from rulelens.ir import load, loads
from rulelens.ir.model import Rule, RuleSet
from rulelens.pipeline import infer
from tests.conftest import FIXTURES

TOL = EvalConfig()


def rule(body: str) -> Rule:
    return loads(f"schema: rulelens.ir/1\ngame: g\nrules:\n  - {body}").rules[0]


COIN = rule(
    "{id: T, kind: causal, trigger: {event: contact_enter, a: Player, b: Coin}, effects: ["
    "{event: destroy, target: b},"
    "{event: attr_change, target: World, attr: score, op: add, value: 10}]}"
)


def test_roles_are_resolved_before_comparing() -> None:
    swapped = rule(
        "{id: R, kind: causal, trigger: {event: contact_enter, a: Coin, b: Player}, effects: ["
        "{event: attr_change, target: World, attr: score, op: add, value: 10},"
        "{event: destroy, target: a}]}"
    )
    cmp = compare_rules(COIN, swapped, TOL)
    assert cmp is not None and not cmp.partial
    wrong_victim = rule(
        "{id: R, kind: causal, trigger: {event: contact_enter, a: Coin, b: Player}, effects: ["
        "{event: attr_change, target: World, attr: score, op: add, value: 10},"
        "{event: destroy, target: b}]}"
    )
    assert compare_rules(COIN, wrong_victim, TOL) is None


def test_numeric_tolerance() -> None:
    """±5% relative or ±0.1 absolute, whichever is larger (SPEC §8.1)."""
    def guard(value: float) -> Rule:
        return rule(
            "{id: R, kind: fsm_transition, entity: Guard, from: Patrol, to: Chase, guard: ["
            f"{{feature: dist, target: Player, op: '<', value: {value}}}]}}"
        )

    assert compare_rules(guard(5.0), guard(4.76), TOL) is not None  # within 5%
    assert compare_rules(guard(5.0), guard(4.70), TOL) is None
    assert compare_rules(guard(1.0), guard(1.09), TOL) is not None  # within 0.1 absolute
    assert compare_rules(guard(1.0), guard(1.2), TOL) is None
    cmp = compare_rules(guard(5.0), guard(4.9), TOL)
    assert cmp is not None and abs(cmp.rel_errors[0] - 0.02) < 1e-9


def test_extra_effects_or_predicates_make_a_partial_match() -> None:
    extra = replace(COIN, effects=(*COIN.effects, replace(COIN.effects[1], attr="combo")))
    cmp = compare_rules(COIN, extra, TOL)
    assert cmp is not None and cmp.partial
    assert compare_rules(extra, COIN, TOL) is None  # a truth effect is missing


def test_kind_and_names_must_be_equal() -> None:
    assert compare_rules(COIN, replace(COIN, kind="stochastic"), TOL) is None
    other = rule(
        "{id: R, kind: causal, trigger: {event: contact_enter, a: Player, b: BigCoin}, effects: ["
        "{event: destroy, target: b},"
        "{event: attr_change, target: World, attr: score, op: add, value: 10}]}"
    )
    assert compare_rules(COIN, other, TOL) is None


def test_invariants_compare_only_what_truth_states() -> None:
    truth = rule("{id: T, kind: invariant, entity: World, attr: score, "
                 "invariant: {template: range, min: 0}}")
    inferred = rule("{id: R, kind: invariant, entity: World, attr: score, "
                    "invariant: {template: range, min: 0, max: 340}}")
    assert compare_rules(truth, inferred, TOL) is not None
    assert compare_rules(replace(truth, attr="timer"), inferred, TOL) is None
    mod10 = rule("{id: T, kind: invariant, entity: World, attr: score, "
                 "invariant: {template: modulo, k: 10}}")
    mod5 = rule("{id: R, kind: invariant, entity: World, attr: score, "
                "invariant: {template: modulo, k: 5}}")
    assert compare_rules(mod10, mod5, TOL) is None


def test_params_rules() -> None:
    truth = rule("{id: T, kind: timing, params: {template: periodic_spawn, type: Coin, "
                 "period_s: 3.0, max_alive: 8}}")
    close = replace(truth, params={**truth.params, "period_s": 3.04})
    assert compare_rules(truth, close, TOL) is not None
    assert compare_rules(truth, replace(truth, params={"template": "periodic_spawn"}), TOL) is None


def test_scores() -> None:
    truth = RuleSet(game="g", rules=(COIN, replace(COIN, id="T2", kind="stochastic")))
    inferred = RuleSet(
        game="g",
        rules=(replace(COIN, id="R1"), replace(COIN, id="R2"), replace(COIN, id="R3")),
    )
    result = evaluate(inferred, truth)
    assert (result.overall.tp, result.overall.fp, result.overall.fn) == (1, 2, 1)
    assert abs(result.overall.precision - 1 / 3) < 1e-9
    assert result.overall.recall == 0.5
    assert abs(result.overall.f1 - 0.4) < 1e-9
    assert result.missed == ("T2",) and result.spurious == ("R2", "R3")
    assert result.per_kind["causal"].fp == 2 and result.per_kind["stochastic"].fn == 1
    assert evaluate(RuleSet(game="g"), RuleSet(game="g")).overall.f1 == 0.0


def test_end_to_end_on_fixtures(traces: list[Trace]) -> None:
    """traces -> miners -> IR -> eval, against the fixture truth."""
    inferred = infer(traces, Config(causal=CausalConfig(min_support=2)))
    assert [r.id for r in inferred.rules][:2] == ["R-g1-001", "R-g1-002"]
    assert inferred.generated_from["traces"] == 3
    assert inferred.generated_from["ticks"] == 243

    result = evaluate(inferred, load(FIXTURES / "g1_mini.truth.yaml"))
    assert result.missed == ()
    assert result.overall.recall == 1.0
    assert result.mean_rel_error == 0.0
    # true but unlisted: timer never increases, gameState in {Playing, GameOver}
    spurious = {r.id: r for r in inferred.rules if r.id in result.spurious}
    assert sorted(str(r.attr) for r in spurious.values()) == ["gameState", "timer"]
