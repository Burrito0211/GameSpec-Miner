"""Eventization (SPEC §6.1): diff consecutive frames into atomic events."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from rulelens.io.trace import AttrValue, InputValue, Trace, is_enum_type

EventKind = Literal[
    "input", "contact_enter", "contact_exit", "attr_change", "enum_transition", "spawn", "destroy"
]

# Continuous attributes feed the feature layer (§6.2) and are not eventized.
CONTINUOUS_TYPES: frozenset[str] = frozenset({"vec2", "float"})

_KIND_ORDER: dict[str, int] = {
    "input": 0,
    "contact_enter": 1,
    "contact_exit": 2,
    "attr_change": 3,
    "enum_transition": 4,
    "spawn": 5,
    "destroy": 6,
}


@dataclass(frozen=True)
class Event:
    kind: EventKind
    tick: int
    # attr_change / enum_transition / spawn / destroy
    entity_id: int | None = None
    entity_type: str | None = None
    attr: str | None = None
    old: AttrValue | None = None
    new: AttrValue | None = None
    delta: float | None = None
    # contact_enter / contact_exit
    a_id: int | None = None
    a_type: str | None = None
    b_id: int | None = None
    b_type: str | None = None
    # input
    action: str | None = None
    phase: str | None = None
    value: InputValue | None = None


def _numeric_delta(old: AttrValue, new: AttrValue) -> float | None:
    if isinstance(old, bool) or isinstance(new, bool):
        return None
    if isinstance(old, int | float) and isinstance(new, int | float):
        return new - old
    return None


def eventize(trace: Trace, continuous_types: frozenset[str] = CONTINUOUS_TYPES) -> list[Event]:
    """Return the trace's atomic events, ordered by tick.

    An ``attr_change`` carries the tick of the first frame that shows the new value.
    """
    id_types: dict[int, str] = {s.id: s.type for s in trace.spawns}
    for frame in trace.frames:
        for ent in frame.ents:
            id_types.setdefault(ent.id, ent.type)

    events: list[Event] = []
    for inp in trace.inputs:
        events.append(
            Event("input", inp.tick, action=inp.action, phase=inp.phase, value=inp.value)
        )
    for con in trace.contacts:
        events.append(
            Event(
                "contact_enter" if con.phase == "enter" else "contact_exit",
                con.tick,
                a_id=con.a,
                a_type=id_types.get(con.a),
                b_id=con.b,
                b_type=id_types.get(con.b),
            )
        )
    for spawn in trace.spawns:
        events.append(Event("spawn", spawn.tick, entity_id=spawn.id, entity_type=spawn.type))
    for destroy in trace.destroys:
        events.append(
            Event(
                "destroy", destroy.tick, entity_id=destroy.id, entity_type=id_types.get(destroy.id)
            )
        )

    declared = trace.header.entity_types
    previous: dict[int, dict[str, AttrValue]] = {}
    for frame in trace.frames:
        current: dict[int, dict[str, AttrValue]] = {}
        for ent in frame.ents:
            current[ent.id] = ent.attrs
            before = previous.get(ent.id)
            if before is None:
                continue
            for attr, new in ent.attrs.items():
                attr_type = declared[ent.type][attr]
                old = before.get(attr)
                if attr_type in continuous_types or old is None or old == new:
                    continue
                events.append(
                    Event(
                        "enum_transition" if is_enum_type(attr_type) else "attr_change",
                        frame.tick,
                        entity_id=ent.id,
                        entity_type=ent.type,
                        attr=attr,
                        old=old,
                        new=new,
                        delta=_numeric_delta(old, new),
                    )
                )
        previous = current

    events.sort(key=lambda e: (e.tick, _KIND_ORDER[e.kind]))
    return events


def coverage(event_lists: Iterable[list[Event]]) -> dict[str, dict[str, int]]:
    """Count what a batch of traces exercised (FR-GEN-5): one counter per event family."""
    counts: dict[str, Counter[str]] = {
        "attr_changes": Counter(),
        "contact_pairs": Counter(),
        "inputs": Counter(),
        "spawns": Counter(),
        "destroys": Counter(),
    }
    for events in event_lists:
        for e in events:
            if e.kind in ("attr_change", "enum_transition"):
                counts["attr_changes"][f"{e.entity_type}.{e.attr}"] += 1
            elif e.kind == "contact_enter":
                pair = sorted([e.a_type or "?", e.b_type or "?"])
                counts["contact_pairs"][f"{pair[0]}-{pair[1]}"] += 1
            elif e.kind == "input":
                counts["inputs"][f"{e.action}:{e.phase}"] += 1
            elif e.kind == "spawn":
                counts["spawns"][e.entity_type or "?"] += 1
            elif e.kind == "destroy":
                counts["destroys"][e.entity_type or "?"] += 1
    return {family: dict(sorted(counter.items())) for family, counter in counts.items()}
