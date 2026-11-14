"""Rule IR ``rulelens.ir/1`` (SPEC §7.1).

The same classes describe inferred rules and hand-written ground truth. Every
field except ``id`` and ``kind`` is optional; which ones are used depends on the
rule kind:

=================  ==========================================================
kind               fields
=================  ==========================================================
invariant          entity, attr, invariant
causal             trigger, effects, window_ticks
stochastic         trigger, effects, probability (and/or params)
fsm_transition     entity, from, to, guard
fsm_state          entity, params
timing             params (template + numbers; see SPEC §7.1)
input_effect       params
=================  ==========================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

IR_SCHEMA = "rulelens.ir/1"

Scalar = int | float | str | bool

RuleKind = Literal[
    "invariant", "causal", "input_effect", "fsm_transition", "fsm_state", "timing", "stochastic"
]
RULE_KINDS: tuple[str, ...] = get_args(RuleKind)

InvariantTemplate = Literal["constant", "range", "enum_set", "modulo", "monotonic", "pair"]
INVARIANT_TEMPLATES: tuple[str, ...] = get_args(InvariantTemplate)


@dataclass(frozen=True)
class Evidence:
    trace: str
    tick: int


@dataclass(frozen=True)
class Stats:
    support: int | None = None
    confidence: float | None = None
    lift: float | None = None
    sessions: int | None = None


@dataclass(frozen=True)
class Trigger:
    """What starts a rule.

    ``event`` is one of ``contact_enter`` (uses ``a``/``b``), ``input`` (``action``,
    ``phase``), ``spawn`` / ``destroy`` (``type``), ``enum_transition`` (``entity``,
    ``attr``, ``from``, ``to``) or ``attr_reaches`` (``entity``, ``attr``, ``value``).
    """

    event: str
    a: str | None = None
    b: str | None = None
    type: str | None = None
    entity: str | None = None
    attr: str | None = None
    action: str | None = None
    phase: str | None = None
    value: Scalar | None = None
    from_: str | None = None
    to: str | None = None


@dataclass(frozen=True)
class Effect:
    """One consequence of a trigger.

    ``target`` is a trigger role (``a``, ``b``, ``self``) or an entity type such as
    ``World``. For ``attr_change``, ``op`` is ``add`` (x + value), ``set`` (x = value),
    ``add_capped`` (min(x + value, cap)) or ``mul`` (x * value).
    """

    event: str
    target: str | None = None
    type: str | None = None
    attr: str | None = None
    op: str | None = None
    value: Scalar | None = None
    cap: float | None = None


@dataclass(frozen=True)
class Predicate:
    feature: str
    op: str
    value: Scalar
    target: str | None = None
    ci: tuple[float, float] | None = None


@dataclass(frozen=True)
class Invariant:
    """A property that held on every observed tick.

    constant: ``value`` · range: ``min`` and/or ``max`` · enum_set: ``values`` ·
    modulo: ``attr % k == 0`` · monotonic: ``direction`` (``non_decreasing`` or
    ``non_increasing``) · pair: ``attr <op> other`` with op ``<=`` or ``==``.
    """

    template: str
    value: Scalar | None = None
    min: float | None = None
    max: float | None = None
    values: tuple[str, ...] | None = None
    k: int | None = None
    direction: str | None = None
    op: str | None = None
    other: str | None = None


@dataclass(frozen=True)
class Probability:
    p: float
    ci: tuple[float, float] | None = None
    n: int | None = None


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    description: str | None = None
    entity: str | None = None
    attr: str | None = None
    trigger: Trigger | None = None
    effects: tuple[Effect, ...] = ()
    window_ticks: int | None = None
    invariant: Invariant | None = None
    from_: str | None = None
    to: str | None = None
    guard: tuple[Predicate, ...] = ()
    probability: Probability | None = None
    params: dict[str, Scalar] = field(default_factory=dict)
    stats: Stats | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class RuleSet:
    game: str
    rules: tuple[Rule, ...] = ()
    generated_from: dict[str, Any] = field(default_factory=dict)
    schema: str = IR_SCHEMA


def resolve_target(trigger: Trigger | None, target: str | None) -> str | None:
    """Turn a trigger role (``a``, ``b``, ``self``) into the entity type it stands for."""
    if trigger is None or target is None:
        return target
    if target == "a" and trigger.a is not None:
        return trigger.a
    if target == "b" and trigger.b is not None:
        return trigger.b
    if target == "self":
        return trigger.type or trigger.entity or target
    return target
