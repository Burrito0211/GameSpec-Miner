"""Causal-rule miner (SPEC §6.3b): ``trigger -> effects`` relations between events.

For every trigger occurrence, the events in the next ``window_ticks`` ticks that
involve the trigger's entities or ``World`` (or that spawn something) are candidate
effects. Candidates are abstracted by entity type and trigger role, then scored by
support, confidence ``P(effect | trigger)`` and lift against the effect's base rate.

Three filters keep coincidences out:

* events at a trace's first tick are initial conditions, not triggers;
* an effect that recurs at one constant period is a clock (timing miner, §6.3e), so
  it is never attributed to a trigger that merely coincides with it;
* a rule whose trigger is itself an effect of another rule does not repeat that
  rule's effects (``contact -> destroy + score`` hides ``destroy -> score``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from rulelens.config import CausalConfig
from rulelens.events.eventizer import Event, eventize
from rulelens.io.trace import AttrValue, Trace
from rulelens.ir.model import Effect, Evidence, Rule, Scalar, Stats, Trigger, resolve_target

WORLD = "World"
_EPS = 1e-6

TriggerKey = tuple[str, ...]
EffectKey = tuple[str, str, str]  # (family, target-or-type, attr)
BaseKey = tuple[str, str, str]  # (family, entity type, attr): effect with roles resolved


@dataclass(frozen=True)
class _Occurrence:
    trace: int
    tick: int  # trigger tick
    lag: int
    old: AttrValue | None
    new: AttrValue | None


@dataclass(frozen=True)
class CausalCandidate:
    """One scored ``trigger -> effect`` pair, before any acceptance threshold."""

    trigger: Trigger
    effect: Effect
    n_triggers: int
    support: int
    confidence: float
    lift: float
    max_lag: int
    evidence: tuple[Evidence, ...]


def _trigger_of(event: Event) -> tuple[TriggerKey, dict[int, str]] | None:
    """Trigger key and the role of each participating entity id, if ``event`` is a trigger."""
    if event.kind == "contact_enter":
        if event.a_id is None or event.b_id is None:
            return None
        # Role order is by type name so that (Player, Coin) and (Coin, Player) agree.
        (a_type, a_id), (b_type, b_id) = sorted(
            [(event.a_type or "?", event.a_id), (event.b_type or "?", event.b_id)]
        )
        return ("contact_enter", a_type, b_type), {a_id: "a", b_id: "b"}
    if event.kind == "input":
        if event.phase not in ("down", "up"):  # continuous values: input-effect miner
            return None
        return ("input", event.action or "?", event.phase), {}
    if event.entity_id is None or event.entity_type is None:
        return None
    roles = {event.entity_id: "self"}
    if event.kind in ("spawn", "destroy"):
        return (event.kind, event.entity_type), roles
    if event.kind == "enum_transition":
        return (
            "enum_transition", event.entity_type, event.attr or "?", str(event.old), str(event.new)
        ), roles
    if event.kind == "attr_change" and event.delta is not None and event.new == 0:
        return ("attr_reaches", event.entity_type, event.attr or "?", "0"), roles
    return None


def _trigger_ir(key: TriggerKey) -> Trigger:
    event = key[0]
    if event == "contact_enter":
        return Trigger(event, a=key[1], b=key[2])
    if event == "input":
        return Trigger(event, action=key[1], phase=key[2])
    if event == "enum_transition":
        return Trigger(event, entity=key[1], attr=key[2], from_=key[3], to=key[4])
    if event == "attr_reaches":
        return Trigger(event, entity=key[1], attr=key[2], value=0)
    return Trigger(event, type=key[1])


def _effect_of(event: Event, roles: dict[int, str]) -> EffectKey | None:
    if event.entity_type is None or event.entity_id is None:
        return None
    if event.kind == "spawn":
        return ("spawn", event.entity_type, "")
    role = WORLD if event.entity_type == WORLD else roles.get(event.entity_id)
    if role is None:
        return None
    if event.kind == "destroy":
        return ("destroy", role, "")
    if event.kind in ("attr_change", "enum_transition") and event.attr is not None:
        return ("attr", role, event.attr)
    return None


def _base_key(event: Event) -> BaseKey | None:
    """Role-free identity of a potential effect, for base rates and clock detection."""
    if event.entity_type is None:
        return None
    if event.kind in ("spawn", "destroy"):
        return (event.kind, event.entity_type, "")
    if event.kind in ("attr_change", "enum_transition") and event.attr is not None:
        return ("attr", event.entity_type, event.attr)
    return None


def _as_scalar(value: float) -> Scalar:
    return int(round(value)) if abs(value - round(value)) < _EPS else value


def _generalize(key: EffectKey, occurrences: list[_Occurrence]) -> Effect | None:
    """Abstract the observed changes into one effect, or None if no template fits."""
    family, target, attr = key
    if family == "spawn":
        return Effect("spawn", type=target)
    if family == "destroy":
        return Effect("destroy", target=target)

    pairs = [(o.old, o.new) for o in occurrences]
    numeric: list[tuple[float, float]] = []
    for old, new in pairs:
        if (
            isinstance(old, int | float)
            and isinstance(new, int | float)
            and not isinstance(old, bool)
            and not isinstance(new, bool)
        ):
            numeric.append((float(old), float(new)))
    if len(numeric) != len(pairs):
        news = {new for _, new in pairs}
        if len(news) == 1:
            (only,) = news
            if isinstance(only, str | bool):
                return Effect("attr_change", target=target, attr=attr, op="set", value=only)
        return None

    deltas = [new - old for old, new in numeric]
    if max(deltas) - min(deltas) < _EPS:
        return Effect("attr_change", target=target, attr=attr, op="add",
                      value=_as_scalar(deltas[0]))
    # min(x + c, cap)
    cap = max(new for _, new in numeric)
    free = [new - old for old, new in numeric if new < cap - _EPS]
    if free and max(free) - min(free) < _EPS and free[0] > 0:
        c = free[0]
        if all(abs(new - min(old + c, cap)) < _EPS for old, new in numeric):
            return Effect("attr_change", target=target, attr=attr, op="add_capped",
                          value=_as_scalar(c), cap=float(cap))
    # x * c
    if all(abs(old) > _EPS for old, _ in numeric):
        ratios = [new / old for old, new in numeric]
        if max(ratios) - min(ratios) < _EPS:
            return Effect("attr_change", target=target, attr=attr, op="mul",
                          value=_as_scalar(ratios[0]))
    # x = c
    news_numeric = [new for _, new in numeric]
    if max(news_numeric) - min(news_numeric) < _EPS:
        return Effect("attr_change", target=target, attr=attr, op="set",
                      value=_as_scalar(news_numeric[0]))
    return None


def _spread_evidence(
    occurrences: list[_Occurrence], names: Sequence[str], limit: int
) -> tuple[Evidence, ...]:
    """Pick up to ``limit`` occurrences, preferring distinct sessions."""
    by_trace: dict[int, list[_Occurrence]] = defaultdict(list)
    for occ in occurrences:
        by_trace[occ.trace].append(occ)
    picked: list[Evidence] = []
    depth = 0
    while len(picked) < limit and any(len(v) > depth for v in by_trace.values()):
        for trace_idx in sorted(by_trace):
            if depth < len(by_trace[trace_idx]) and len(picked) < limit:
                occ = by_trace[trace_idx][depth]
                picked.append(Evidence(names[trace_idx], occ.tick))
        depth += 1
    return tuple(picked)


def causal_candidates(
    traces: Sequence[Trace], config: CausalConfig | None = None
) -> list[CausalCandidate]:
    """Score every observed ``trigger -> effect`` pair (clock-like effects excluded)."""
    cfg = config or CausalConfig()
    window = cfg.window_ticks

    n_triggers: dict[TriggerKey, int] = defaultdict(int)
    occurrences: dict[tuple[TriggerKey, EffectKey], list[_Occurrence]] = defaultdict(list)
    base_of: dict[tuple[TriggerKey, EffectKey], BaseKey] = {}
    base_ticks: dict[BaseKey, list[set[int]]] = defaultdict(lambda: [set() for _ in traces])
    total_ticks = 0

    for trace_idx, trace in enumerate(traces):
        if not trace.frames:
            continue
        first_tick, last_tick = trace.frames[0].tick, trace.frames[-1].tick
        total_ticks += len(trace.frames)
        events = eventize(trace)
        by_tick: dict[int, list[Event]] = defaultdict(list)
        for event in events:
            by_tick[event.tick].append(event)
            base_key = _base_key(event)
            if base_key is not None and event.tick != first_tick:
                base_ticks[base_key][trace_idx].add(event.tick)

        for event in events:
            if event.tick == first_tick:
                continue
            trig = _trigger_of(event)
            if trig is None:
                continue
            key, roles = trig
            n_triggers[key] += 1
            seen: dict[EffectKey, tuple[int, AttrValue | None, AttrValue | None]] = {}
            for tick in range(event.tick, min(event.tick + window, last_tick) + 1):
                for other in by_tick.get(tick, ()):
                    if other is event:
                        continue
                    effect_key = _effect_of(other, roles)
                    other_base = _base_key(other)
                    if effect_key is None or other_base is None:
                        continue
                    base_of[(key, effect_key)] = other_base
                    if effect_key in seen:  # several changes in the window: first old, last new
                        lag, old, _ = seen[effect_key]
                        seen[effect_key] = (lag, old, other.new)
                    else:
                        seen[effect_key] = (tick - event.tick, other.old, other.new)
            for effect_key, (lag, old, new) in seen.items():
                occurrences[(key, effect_key)].append(
                    _Occurrence(trace_idx, event.tick, lag, old, new)
                )

    def is_clock(base_key: BaseKey) -> bool:
        intervals: set[int] = set()
        count = 0
        for ticks in base_ticks[base_key]:
            ordered = sorted(ticks)
            for before, after in zip(ordered, ordered[1:], strict=False):
                intervals.add(after - before)
                count += 1
        return count >= 3 and len(intervals) == 1

    def base_rate(base_key: BaseKey) -> float:
        """P(the effect falls in a random window of ``window + 1`` ticks)."""
        covered = 0
        for trace_idx, ticks in enumerate(base_ticks[base_key]):
            if not ticks:
                continue
            first = traces[trace_idx].frames[0].tick
            starts = {s for t in ticks for s in range(max(first, t - window), t + 1)}
            covered += len(starts)
        return covered / total_ticks if total_ticks else 1.0

    names = [t.name for t in traces]
    candidates: list[CausalCandidate] = []
    for (key, effect_key), occs in sorted(occurrences.items()):
        base_key = base_of[(key, effect_key)]
        if is_clock(base_key):
            continue
        effect = _generalize(effect_key, occs)
        if effect is None:
            continue
        confidence = len(occs) / n_triggers[key]
        rate = base_rate(base_key)
        candidates.append(
            CausalCandidate(
                trigger=_trigger_ir(key),
                effect=effect,
                n_triggers=n_triggers[key],
                support=len(occs),
                confidence=confidence,
                lift=confidence / rate if rate > 0 else float("inf"),
                max_lag=max(o.lag for o in occs),
                evidence=_spread_evidence(occs, names, cfg.max_evidence),
            )
        )
    return candidates


def stochastic_candidates(
    traces: Sequence[Trace], config: CausalConfig | None = None
) -> list[CausalCandidate]:
    """Candidates with intermediate confidence, handed to the stochastic miner (§6.3f)."""
    cfg = config or CausalConfig()
    return [
        c
        for c in causal_candidates(traces, cfg)
        if cfg.stochastic_min_confidence <= c.confidence < cfg.min_confidence
        and c.support >= cfg.min_support
        and c.lift >= cfg.min_lift
    ]


def _effect_signature(trigger: Trigger, effect: Effect) -> tuple[object, ...]:
    target = effect.type if effect.event == "spawn" else resolve_target(trigger, effect.target)
    return (effect.event, target, effect.attr, effect.op, effect.value)


def _trigger_as_effect(trigger: Trigger) -> tuple[object, ...] | None:
    """The effect signature that would produce this trigger, if any."""
    if trigger.event in ("spawn", "destroy"):
        return (trigger.event, trigger.type, None, None, None)
    if trigger.event == "enum_transition":
        return ("attr_change", trigger.entity, trigger.attr, "set", trigger.to)
    return None


def _drop_chained(rules: list[Rule]) -> list[Rule]:
    produced_by: dict[tuple[object, ...], list[Rule]] = defaultdict(list)
    for rule in rules:
        assert rule.trigger is not None
        for effect in rule.effects:
            produced_by[_effect_signature(rule.trigger, effect)].append(rule)
    kept: list[Rule] = []
    for rule in rules:
        assert rule.trigger is not None
        as_effect = _trigger_as_effect(rule.trigger)
        parents = [p for p in produced_by.get(as_effect, []) if p is not rule] if as_effect else []
        inherited = {
            _effect_signature(p.trigger, e) for p in parents if p.trigger for e in p.effects
        }
        effects = tuple(
            e for e in rule.effects if _effect_signature(rule.trigger, e) not in inherited
        )
        if effects:
            kept.append(replace(rule, effects=effects))
    return kept


def mine_causal(traces: Sequence[Trace], config: CausalConfig | None = None) -> list[Rule]:
    cfg = config or CausalConfig()
    grouped: dict[Trigger, list[CausalCandidate]] = defaultdict(list)
    for cand in causal_candidates(traces, cfg):
        if (
            cand.confidence >= cfg.min_confidence
            and cand.support >= cfg.min_support
            and cand.lift >= cfg.min_lift
        ):
            grouped[cand.trigger].append(cand)

    rules: list[Rule] = []
    for trigger, cands in grouped.items():
        lifts = [c.lift for c in cands]
        rules.append(
            Rule(
                id="",
                kind="causal",
                trigger=trigger,
                effects=tuple(c.effect for c in cands),
                window_ticks=max(c.max_lag for c in cands),
                stats=Stats(
                    support=min(c.support for c in cands),
                    confidence=round(min(c.confidence for c in cands), 4),
                    lift=round(min(lifts), 2),
                ),
                evidence=cands[0].evidence,
            )
        )
    return _drop_chained(rules)
