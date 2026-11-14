"""Match inferred rules against ground truth and compute P / R / F1 (SPEC §8).

A truth rule matches an inferred rule when the kinds are equal and everything the
truth rule states is found in the inferred rule: names must be equal, numbers must
be within tolerance. Fields the truth rule leaves out are not compared. If the
inferred rule carries extra effects or guard predicates, the match is *partial*;
partial matches count as true positives and are reported separately.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from rulelens.config import EvalConfig
from rulelens.ir.model import Effect, Predicate, Rule, RuleSet, Trigger, resolve_target


@dataclass
class _Comparison:
    """Accumulates the numeric errors and partial flag of one rule comparison."""

    tol: EvalConfig
    rel_errors: list[float] = field(default_factory=list)
    partial: bool = False

    def same(self, truth: object, inferred: object) -> bool:
        """Numbers within tolerance (±rel or ±abs, whichever is larger); else equality."""
        if _is_number(truth) and _is_number(inferred):
            t, i = float(truth), float(inferred)  # type: ignore[arg-type]
            if abs(t - i) > max(self.tol.rel_tol * abs(t), self.tol.abs_tol):
                return False
            if t != 0:
                self.rel_errors.append(abs(t - i) / abs(t))
            return True
        return truth == inferred


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _trigger_matches(truth: Trigger, inferred: Trigger, cmp: _Comparison) -> bool:
    if truth.event != inferred.event:
        return False
    if truth.event.startswith("contact"):
        return sorted([truth.a or "", truth.b or ""]) == sorted(
            [inferred.a or "", inferred.b or ""]
        )
    names = ("type", "entity", "attr", "action", "phase", "from_", "to")
    if any(
        getattr(truth, n) is not None and getattr(truth, n) != getattr(inferred, n) for n in names
    ):
        return False
    return truth.value is None or cmp.same(truth.value, inferred.value)


def _effect_matches(
    truth: Effect, truth_trigger: Trigger | None,
    inferred: Effect, inferred_trigger: Trigger | None, cmp: _Comparison,
) -> bool:
    if truth.event != inferred.event or truth.type != inferred.type:
        return False
    if resolve_target(truth_trigger, truth.target) != resolve_target(
        inferred_trigger, inferred.target
    ):
        return False
    if truth.attr != inferred.attr or truth.op != inferred.op:
        return False
    if truth.value is not None and not cmp.same(truth.value, inferred.value):
        return False
    return truth.cap is None or cmp.same(truth.cap, inferred.cap)


def _predicate_matches(truth: Predicate, inferred: Predicate, cmp: _Comparison) -> bool:
    return (
        truth.feature == inferred.feature
        and truth.target == inferred.target
        and truth.op == inferred.op
        and cmp.same(truth.value, inferred.value)
    )


def _all_found(truth_items: Sequence[Any], inferred_items: Sequence[Any], matches: Any) -> bool:
    """Every truth item pairs with a distinct inferred item."""
    free = list(inferred_items)
    for item in truth_items:
        hit = next((cand for cand in free if matches(item, cand)), None)
        if hit is None:
            return False
        free.remove(hit)
    return True


def compare_rules(truth: Rule, inferred: Rule, tol: EvalConfig) -> _Comparison | None:
    """Return the comparison if ``inferred`` matches ``truth``, else None."""
    cmp = _Comparison(tol)
    if truth.kind != inferred.kind:
        return None
    for name in ("entity", "attr", "from_", "to"):
        if getattr(truth, name) is not None and getattr(truth, name) != getattr(inferred, name):
            return None

    if truth.trigger is not None:
        if inferred.trigger is None or not _trigger_matches(truth.trigger, inferred.trigger, cmp):
            return None
    if truth.effects:
        if not _all_found(
            truth.effects,
            inferred.effects,
            lambda t, i: _effect_matches(t, truth.trigger, i, inferred.trigger, cmp),
        ):
            return None
        cmp.partial = cmp.partial or len(inferred.effects) > len(truth.effects)

    if truth.invariant is not None:
        t_inv, i_inv = truth.invariant, inferred.invariant
        if i_inv is None or t_inv.template != i_inv.template:
            return None
        if t_inv.values is not None and set(t_inv.values) != set(i_inv.values or ()):
            return None
        for name in ("k", "direction", "op", "other"):
            if getattr(t_inv, name) is not None and getattr(t_inv, name) != getattr(i_inv, name):
                return None
        for name in ("value", "min", "max"):
            expected = getattr(t_inv, name)
            if expected is not None and not cmp.same(expected, getattr(i_inv, name)):
                return None

    if truth.guard:
        if not _all_found(
            truth.guard, inferred.guard, lambda t, i: _predicate_matches(t, i, cmp)
        ):
            return None
        cmp.partial = cmp.partial or len(inferred.guard) > len(truth.guard)

    if truth.probability is not None:
        if inferred.probability is None or not cmp.same(
            truth.probability.p, inferred.probability.p
        ):
            return None
    for key, expected in truth.params.items():
        if key not in inferred.params or not cmp.same(expected, inferred.params[key]):
            return None
    return cmp


@dataclass(frozen=True)
class Score:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


@dataclass(frozen=True)
class Match:
    truth_id: str
    inferred_id: str
    partial: bool
    rel_errors: tuple[float, ...]


@dataclass(frozen=True)
class EvalResult:
    game: str
    overall: Score
    per_kind: dict[str, Score]
    matches: tuple[Match, ...]
    missed: tuple[str, ...]
    """Truth rule ids with no inferred counterpart (false negatives)."""
    spurious: tuple[str, ...]
    """Inferred rule ids with no truth counterpart (false positives)."""
    mean_rel_error: float | None
    """Mean relative error over the numbers of matched rules (truth value != 0)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "game": self.game,
            "overall": self.overall.to_dict(),
            "per_kind": {k: s.to_dict() for k, s in self.per_kind.items()},
            "mean_rel_error": self.mean_rel_error,
            "partial_matches": sum(m.partial for m in self.matches),
            "matches": [
                {"truth": m.truth_id, "inferred": m.inferred_id, "partial": m.partial}
                for m in self.matches
            ],
            "missed": list(self.missed),
            "spurious": list(self.spurious),
        }


def evaluate(inferred: RuleSet, truth: RuleSet, tol: EvalConfig | None = None) -> EvalResult:
    """One-to-one greedy matching in truth order, preferring exact over partial matches."""
    tolerance = tol or EvalConfig()
    free = list(inferred.rules)
    matches: list[Match] = []
    missed: list[Rule] = []
    for truth_rule in truth.rules:
        best: tuple[Rule, _Comparison] | None = None
        for candidate in free:
            cmp = compare_rules(truth_rule, candidate, tolerance)
            if cmp is not None and (best is None or (best[1].partial and not cmp.partial)):
                best = (candidate, cmp)
        if best is None:
            missed.append(truth_rule)
            continue
        free.remove(best[0])
        matches.append(
            Match(truth_rule.id, best[0].id, best[1].partial, tuple(best[1].rel_errors))
        )

    matched_kinds = {m.truth_id: r.kind for r in truth.rules for m in matches if m.truth_id == r.id}
    kinds = sorted({r.kind for r in truth.rules} | {r.kind for r in inferred.rules})
    per_kind = {
        kind: Score(
            tp=sum(1 for k in matched_kinds.values() if k == kind),
            fp=sum(1 for r in free if r.kind == kind),
            fn=sum(1 for r in missed if r.kind == kind),
        )
        for kind in kinds
    }
    errors = [e for m in matches for e in m.rel_errors]
    return EvalResult(
        game=truth.game,
        overall=Score(tp=len(matches), fp=len(free), fn=len(missed)),
        per_kind=per_kind,
        matches=tuple(matches),
        missed=tuple(r.id for r in missed),
        spurious=tuple(r.id for r in free),
        mean_rel_error=round(sum(errors) / len(errors), 6) if errors else None,
    )
