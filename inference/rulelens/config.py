"""Configuration (``rulelens.toml``, SPEC §9). Defaults are the values stated in SPEC.md."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InvariantConfig:
    """SPEC §6.3(a)."""

    min_support_ticks: int = 200
    min_sessions: int = 3
    max_enum_values: int = 16


@dataclass(frozen=True)
class CausalConfig:
    """SPEC §6.3(b)."""

    window_ticks: int = 2
    min_confidence: float = 0.95
    min_support: int = 10
    stochastic_min_confidence: float = 0.15
    min_lift: float = 2.0
    max_evidence: int = 5


@dataclass(frozen=True)
class EvalConfig:
    """SPEC §8.1: a number matches within max(rel_tol * |truth|, abs_tol)."""

    rel_tol: float = 0.05
    abs_tol: float = 0.1


@dataclass(frozen=True)
class Config:
    invariants: InvariantConfig = field(default_factory=InvariantConfig)
    causal: CausalConfig = field(default_factory=CausalConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)


def _section(cls: type[Any], data: dict[str, Any], name: str) -> Any:
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"rulelens.toml [{name}]: unknown key(s) {unknown}")
    return cls(**data)


def load_config(path: Path | None = None) -> Config:
    """Load ``rulelens.toml``; with no path, return the SPEC defaults."""
    if path is None:
        return Config()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    sections = {f.name: f.default_factory for f in dataclasses.fields(Config)}
    unknown = sorted(set(data) - set(sections))
    if unknown:
        raise ValueError(f"rulelens.toml: unknown section(s) {unknown}")
    return Config(
        invariants=_section(InvariantConfig, data.get("invariants", {}), "invariants"),
        causal=_section(CausalConfig, data.get("causal", {}), "causal"),
        eval=_section(EvalConfig, data.get("eval", {}), "eval"),
    )


def config_hash(config: Config) -> str:
    """Short stable hash recorded in every output artifact (NFR-3)."""
    blob = json.dumps(dataclasses.asdict(config), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]
