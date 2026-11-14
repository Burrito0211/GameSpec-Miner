from __future__ import annotations

from rulelens.config import CausalConfig
from rulelens.io.trace import Trace
from rulelens.ir.model import Effect, Trigger
from rulelens.miners.causal import causal_candidates, mine_causal

# The fixtures hold 9 Coin pickups, 2 BigCoin pickups and 3 game overs.
LOW_SUPPORT = CausalConfig(min_support=2)


def test_expected_rules(traces: list[Trace]) -> None:
    rules = {r.trigger: r for r in mine_causal(traces, LOW_SUPPORT)}
    assert set(rules) == {
        Trigger("contact_enter", a="BigCoin", b="Player"),
        Trigger("contact_enter", a="Coin", b="Player"),
        Trigger("attr_reaches", entity="World", attr="timer", value=0),
    }
    coin = rules[Trigger("contact_enter", a="Coin", b="Player")]
    assert set(coin.effects) == {
        Effect("destroy", target="a"),
        Effect("attr_change", target="World", attr="score", op="add", value=10),
    }
    assert coin.window_ticks == 1
    assert coin.stats is not None
    assert (coin.stats.support, coin.stats.confidence) == (9, 1.0)
    assert coin.stats.lift is not None and coin.stats.lift > 2

    big = rules[Trigger("contact_enter", a="BigCoin", b="Player")]
    assert Effect("attr_change", target="World", attr="score", op="add", value=50) in big.effects

    over = rules[Trigger("attr_reaches", entity="World", attr="timer", value=0)]
    assert over.effects == (
        Effect("attr_change", target="World", attr="gameState", op="set", value="GameOver"),
    )
    assert over.window_ticks == 0


def test_evidence_points_at_trigger_ticks(traces: list[Trace]) -> None:
    coin = next(
        r for r in mine_causal(traces, LOW_SUPPORT)
        if r.trigger == Trigger("contact_enter", a="Coin", b="Player")
    )
    assert coin.evidence[0].trace == "g1_mini_s01.jsonl" and coin.evidence[0].tick == 3
    assert len({e.trace for e in coin.evidence}) == 3  # spread over sessions


def test_spec_default_support_is_respected(traces: list[Trace]) -> None:
    """With the SPEC default (support >= 10) nine pickups are not enough."""
    assert mine_causal(traces) == []


def test_clock_is_not_blamed_on_coincident_triggers(traces: list[Trace]) -> None:
    """Coins spawn on ticks where the timer also ticks; the timer must not become an effect."""
    for cand in causal_candidates(traces, LOW_SUPPORT):
        assert cand.effect.attr != "timer"


def test_no_rule_repeats_a_chained_effect(traces: list[Trace]) -> None:
    """destroy(Coin) always precedes score += 10, but only as part of the contact rule."""
    cands = causal_candidates(traces, LOW_SUPPORT)
    assert any(
        c.trigger == Trigger("destroy", type="Coin") and c.effect.attr == "score" for c in cands
    )
    triggers = {r.trigger for r in mine_causal(traces, LOW_SUPPORT)}
    assert Trigger("destroy", type="Coin") not in triggers


def test_initial_spawns_are_not_triggers(traces: list[Trace]) -> None:
    for cand in causal_candidates(traces, CausalConfig(min_support=1)):
        assert cand.trigger != Trigger("spawn", type="Player")
        assert cand.trigger != Trigger("spawn", type="World")
