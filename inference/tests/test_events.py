from __future__ import annotations

from rulelens.events import Event, coverage, eventize
from rulelens.io.trace import Trace


def test_coin_pickup_sequence(traces: list[Trace]) -> None:
    """s01: the player touches Coin#2 at tick 3; one tick later it is gone and score is 10."""
    events = eventize(traces[0])
    window = [e for e in events if 3 <= e.tick <= 4]
    assert window == [
        Event("contact_enter", 3, a_id=1, a_type="Player", b_id=2, b_type="Coin"),
        Event("attr_change", 4, entity_id=0, entity_type="World", attr="score",
              old=0, new=10, delta=10),
        Event("destroy", 4, entity_id=2, entity_type="Coin"),
    ]


def test_enum_change_is_an_enum_transition(traces: list[Trace]) -> None:
    transitions = [e for e in eventize(traces[0]) if e.kind == "enum_transition"]
    assert transitions == [
        Event("enum_transition", 80, entity_id=0, entity_type="World", attr="gameState",
              old="Playing", new="GameOver")
    ]


def test_continuous_attributes_are_not_eventized(traces: list[Trace]) -> None:
    events = eventize(traces[0])
    assert {e.attr for e in events if e.kind == "attr_change"} == {"score", "timer"}
    assert eventize(traces[0], continuous_types=frozenset()) != events


def test_timer_ticks_down_every_second(traces: list[Trace]) -> None:
    timer = [e for e in eventize(traces[0]) if e.attr == "timer"]
    assert [e.tick for e in timer] == list(range(10, 81, 10))
    assert all(e.delta == -1 for e in timer)


def test_events_are_ordered_and_typed(traces: list[Trace]) -> None:
    events = eventize(traces[1])
    assert [e.tick for e in events] == sorted(e.tick for e in events)
    moves = [e for e in events if e.kind == "input"]
    assert moves[0].action == "Move" and moves[0].phase == "value"
    assert all(e.entity_type is not None for e in events if e.kind in ("spawn", "destroy"))


def test_coverage_counts(traces: list[Trace]) -> None:
    cov = coverage(eventize(t) for t in traces)
    assert cov["contact_pairs"] == {"BigCoin-Player": 2, "Coin-Player": 9}
    assert cov["destroys"] == {"BigCoin": 2, "Coin": 9}
    assert cov["attr_changes"]["World.score"] == 11
