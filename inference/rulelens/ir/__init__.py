"""Rule IR ``rulelens.ir/1`` dataclasses and YAML round-trip (SPEC §7.1)."""

from rulelens.ir.model import (
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
from rulelens.ir.yaml_io import IRError, dump, dumps, load, loads

__all__ = [
    "Effect", "Evidence", "IRError", "Invariant", "Predicate", "Probability", "Rule",
    "RuleSet", "Stats", "Trigger", "dump", "dumps", "load", "loads",
]
