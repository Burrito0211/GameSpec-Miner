"""YAML (de)serialization of the Rule IR. Unknown keys are errors, not silently dropped."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, TypeVar

import yaml

from rulelens.ir.model import (
    INVARIANT_TEMPLATES,
    IR_SCHEMA,
    RULE_KINDS,
    Effect,
    Evidence,
    Invariant,
    Predicate,
    Probability,
    Rule,
    RuleSet,
    Stats,
    Trigger,
)

T = TypeVar("T")

# dataclass field name <-> YAML key (``from`` is a Python keyword)
_RENAMES = {"from_": "from"}


class IRError(ValueError):
    """The YAML document is not a valid ``rulelens.ir/1`` rule set."""


def _to_plain(obj: Any) -> Any:
    """Dataclass tree -> plain dicts/lists, dropping None and empty containers."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out: dict[str, Any] = {}
        for f in dataclasses.fields(obj):
            value = _to_plain(getattr(obj, f.name))
            if value is None or value == [] or value == {}:
                continue
            out[_RENAMES.get(f.name, f.name)] = value
        return out
    if isinstance(obj, tuple | list):
        return [_to_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    return obj


def _build(cls: type[T], data: Any, where: str, **converted: Any) -> T:
    """Construct dataclass ``cls`` from a mapping; ``converted`` overrides nested fields."""
    if not isinstance(data, dict):
        raise IRError(f"{where}: expected a mapping, got {type(data).__name__}")
    names = {_RENAMES.get(f.name, f.name): f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - set(names))
    if unknown:
        raise IRError(f"{where}: unknown key(s) {unknown}")
    kwargs = {names[k]: v for k, v in data.items() if v is not None}
    kwargs.update({k: v for k, v in converted.items() if v is not None})
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise IRError(f"{where}: {exc}") from exc


def _pair(value: Any, where: str) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise IRError(f"{where}: expected [low, high]")
    return (value[0], value[1])


def _items(data: dict[str, Any], key: str, where: str) -> list[Any]:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise IRError(f"{where}.{key}: expected a list")
    return value


def rule_from_dict(data: Any) -> Rule:
    if not isinstance(data, dict) or "id" not in data or "kind" not in data:
        raise IRError(f"rule needs at least 'id' and 'kind': {data!r}")
    where = f"rule {data['id']}"
    if data["kind"] not in RULE_KINDS:
        raise IRError(f"{where}: unknown kind {data['kind']!r}")

    invariant = None
    if data.get("invariant") is not None:
        inv = data["invariant"]
        invariant = _build(
            Invariant,
            inv,
            f"{where}.invariant",
            values=tuple(inv["values"]) if isinstance(inv, dict) and inv.get("values") else None,
        )
        if invariant.template not in INVARIANT_TEMPLATES:
            raise IRError(f"{where}: unknown invariant template {invariant.template!r}")
    probability = None
    if data.get("probability") is not None:
        prob = data["probability"]
        probability = _build(
            Probability,
            prob,
            f"{where}.probability",
            ci=_pair(prob.get("ci") if isinstance(prob, dict) else None, f"{where}.probability.ci"),
        )
    params = data.get("params") or {}
    if not isinstance(params, dict):
        raise IRError(f"{where}.params: expected a mapping")

    return _build(
        Rule,
        data,
        where,
        trigger=_build(Trigger, data["trigger"], f"{where}.trigger")
        if data.get("trigger") is not None
        else None,
        effects=tuple(
            _build(Effect, e, f"{where}.effects") for e in _items(data, "effects", where)
        ),
        invariant=invariant,
        guard=tuple(
            _build(
                Predicate,
                p,
                f"{where}.guard",
                ci=_pair(p.get("ci") if isinstance(p, dict) else None, f"{where}.guard.ci"),
            )
            for p in _items(data, "guard", where)
        ),
        probability=probability,
        params=dict(params),
        stats=_build(Stats, data["stats"], f"{where}.stats")
        if data.get("stats") is not None
        else None,
        evidence=tuple(
            _build(Evidence, e, f"{where}.evidence") for e in _items(data, "evidence", where)
        ),
    )


def ruleset_to_dict(ruleset: RuleSet) -> dict[str, Any]:
    return {
        "schema": ruleset.schema,
        "game": ruleset.game,
        "generated_from": _to_plain(ruleset.generated_from),
        "rules": [_to_plain(rule) for rule in ruleset.rules],
    }


def ruleset_from_dict(data: Any) -> RuleSet:
    if not isinstance(data, dict):
        raise IRError("rule set: expected a mapping at the top level")
    if data.get("schema") != IR_SCHEMA:
        raise IRError(f"rule set: schema must be {IR_SCHEMA!r}, got {data.get('schema')!r}")
    unknown = sorted(set(data) - {"schema", "game", "generated_from", "rules"})
    if unknown:
        raise IRError(f"rule set: unknown key(s) {unknown}")
    if not isinstance(data.get("game"), str):
        raise IRError("rule set: 'game' is required")
    rules = tuple(rule_from_dict(r) for r in _items(data, "rules", "rule set"))
    ids = [r.id for r in rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise IRError(f"rule set: duplicate rule id(s) {duplicates}")
    return RuleSet(
        game=data["game"],
        rules=rules,
        generated_from=dict(data.get("generated_from") or {}),
    )


def dumps(ruleset: RuleSet) -> str:
    text: str = yaml.safe_dump(
        ruleset_to_dict(ruleset), sort_keys=False, allow_unicode=True, width=100
    )
    return text


def loads(text: str) -> RuleSet:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise IRError(f"invalid YAML: {exc}") from exc
    return ruleset_from_dict(data)


def dump(ruleset: RuleSet, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(ruleset), encoding="utf-8")


def load(path: Path) -> RuleSet:
    return loads(path.read_text(encoding="utf-8"))
