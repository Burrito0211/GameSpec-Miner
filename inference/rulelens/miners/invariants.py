"""Invariant miner (SPEC §6.3a), in the style of Daikon.

For each ``(entity_type, attr)`` it tests a fixed set of templates and keeps those
with zero violations and enough support. Continuous ``vec2`` attributes are skipped.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from rulelens.config import InvariantConfig
from rulelens.io.trace import AttrValue, Trace, is_enum_type
from rulelens.ir.model import Evidence, Invariant, Rule, Stats


@dataclass
class _Column:
    """Everything observed for one (entity_type, attr)."""

    attr_type: str
    count: int = 0
    sessions: set[int] = field(default_factory=set)
    distinct: set[AttrValue] = field(default_factory=set)
    min_at: tuple[float, Evidence] | None = None
    max_at: tuple[float, Evidence] | None = None
    last: dict[tuple[int, int], float] = field(default_factory=dict)  # (trace, entity) -> value
    rose: bool = False
    fell: bool = False


@dataclass
class _Pair:
    """Running comparison of two numeric attributes of the same entity."""

    count: int = 0
    sessions: set[int] = field(default_factory=set)
    always_le: bool = True
    always_ge: bool = True


def _is_numeric(attr_type: str) -> bool:
    return attr_type in ("int", "float")


def _number(value: float) -> float:
    """Keep integral values as ints so the YAML stays tidy."""
    return int(value) if float(value).is_integer() else value


def mine_invariants(
    traces: Sequence[Trace], config: InvariantConfig | None = None
) -> list[Rule]:
    cfg = config or InvariantConfig()
    columns: dict[tuple[str, str], _Column] = {}
    pairs: dict[tuple[str, str, str], _Pair] = {}

    for trace_idx, trace in enumerate(traces):
        declared = trace.header.entity_types
        for frame in trace.frames:
            for ent in frame.ents:
                numeric: list[tuple[str, float]] = []
                for attr, value in ent.attrs.items():
                    attr_type = declared[ent.type][attr]
                    if attr_type == "vec2":
                        continue
                    col = columns.get((ent.type, attr))
                    if col is None:
                        col = columns[(ent.type, attr)] = _Column(attr_type)
                    col.count += 1
                    col.sessions.add(trace_idx)
                    col.distinct.add(value)
                    if _is_numeric(attr_type) and isinstance(value, int | float):
                        number = float(value)
                        numeric.append((attr, number))
                        if col.min_at is None or number < col.min_at[0]:
                            col.min_at = (number, Evidence(trace.name, frame.tick))
                        if col.max_at is None or number > col.max_at[0]:
                            col.max_at = (number, Evidence(trace.name, frame.tick))
                        key = (trace_idx, ent.id)
                        before = col.last.get(key)
                        if before is not None:
                            col.rose = col.rose or number > before
                            col.fell = col.fell or number < before
                        col.last[key] = number
                numeric.sort()
                for i, (x_attr, x) in enumerate(numeric):
                    for y_attr, y in numeric[i + 1 :]:
                        pair = pairs.get((ent.type, x_attr, y_attr))
                        if pair is None:
                            pair = pairs[(ent.type, x_attr, y_attr)] = _Pair()
                        pair.count += 1
                        pair.sessions.add(trace_idx)
                        pair.always_le = pair.always_le and x <= y
                        pair.always_ge = pair.always_ge and x >= y

    def supported(count: int, sessions: set[int]) -> bool:
        return count >= cfg.min_support_ticks and len(sessions) >= cfg.min_sessions

    rules: list[Rule] = []

    def emit(
        entity: str, attr: str, invariant: Invariant, count: int, sessions: set[int],
        evidence: tuple[Evidence, ...] = (),
    ) -> None:
        rules.append(
            Rule(
                id="",
                kind="invariant",
                entity=entity,
                attr=attr,
                invariant=invariant,
                stats=Stats(support=count, confidence=1.0, sessions=len(sessions)),
                evidence=evidence,
            )
        )

    for (entity, attr), col in sorted(columns.items()):
        if not supported(col.count, col.sessions):
            continue
        if len(col.distinct) == 1:
            (only,) = col.distinct
            assert not isinstance(only, tuple)
            emit(entity, attr, Invariant("constant", value=only), col.count, col.sessions)
            continue
        if _is_numeric(col.attr_type):
            assert col.min_at is not None and col.max_at is not None
            emit(
                entity,
                attr,
                Invariant("range", min=_number(col.min_at[0]), max=_number(col.max_at[0])),
                col.count,
                col.sessions,
                (col.min_at[1], col.max_at[1]),
            )
            if col.attr_type == "int":
                k = math.gcd(*(int(v) for v in col.distinct if isinstance(v, int)))
                if k >= 2:
                    emit(entity, attr, Invariant("modulo", k=k), col.count, col.sessions)
            if col.rose != col.fell:
                direction = "non_decreasing" if col.rose else "non_increasing"
                emit(
                    entity, attr, Invariant("monotonic", direction=direction),
                    col.count, col.sessions,
                )
        elif is_enum_type(col.attr_type) or col.attr_type == "string":
            if len(col.distinct) <= cfg.max_enum_values:
                values = tuple(sorted(str(v) for v in col.distinct))
                emit(entity, attr, Invariant("enum_set", values=values), col.count, col.sessions)

    for (entity, x_attr, y_attr), pair in sorted(pairs.items()):
        if not supported(pair.count, pair.sessions):
            continue
        # A relation between constants adds nothing beyond the two constant rules.
        if len(columns[(entity, x_attr)].distinct) == 1 or len(
            columns[(entity, y_attr)].distinct
        ) == 1:
            continue
        if pair.always_le and pair.always_ge:
            emit(entity, x_attr, Invariant("pair", op="==", other=y_attr), pair.count,
                 pair.sessions)
        elif pair.always_le:
            emit(entity, x_attr, Invariant("pair", op="<=", other=y_attr), pair.count,
                 pair.sessions)
        elif pair.always_ge:
            emit(entity, y_attr, Invariant("pair", op="<=", other=x_attr), pair.count,
                 pair.sessions)
    return rules
