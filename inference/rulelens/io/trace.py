"""Reader and validator for the ``rulelens.trace/1`` format (SPEC §5.2).

Validation has two layers:

1. every line is checked against ``trace.schema.json`` (shared with the C# writer);
2. cross-record checks that JSON Schema cannot express (record order, consecutive
   ticks, attributes matching the types declared in the header, ...).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_ID = "rulelens.trace/1"
SCHEMA_PATH = Path(__file__).with_name("trace.schema.json")
MAX_ERRORS = 50

AttrValue = int | float | bool | str | tuple[float, float]
InputValue = int | float | bool | tuple[float, float]


class TraceError(Exception):
    """Raised by :func:`read_trace` when a trace file is not valid."""

    def __init__(self, path: Path, errors: list[str]) -> None:
        self.path = path
        self.errors = errors
        shown = "\n  ".join(errors[:10])
        more = f"\n  ... and {len(errors) - 10} more" if len(errors) > 10 else ""
        super().__init__(f"{path}: {len(errors)} validation error(s)\n  {shown}{more}")


@dataclass(frozen=True)
class Header:
    game: str
    build: str
    observability: str
    tick_hz: int
    seed: int
    session: str
    agent: str
    entity_types: dict[str, dict[str, str]]
    """entity type -> attribute name -> declared type (``int``, ``vec2``, ``enum:A|B``...)."""
    walls: tuple[tuple[float, float, float, float], ...]
    """Static wall AABBs as ``(x, y, width, height)``."""


@dataclass(frozen=True)
class EntityState:
    id: int
    type: str
    attrs: dict[str, AttrValue]


@dataclass(frozen=True)
class Frame:
    tick: int
    ents: tuple[EntityState, ...]


@dataclass(frozen=True)
class InputRecord:
    tick: int
    action: str
    phase: str
    value: InputValue | None = None


@dataclass(frozen=True)
class ContactRecord:
    tick: int
    phase: str
    a: int
    b: int


@dataclass(frozen=True)
class SpawnRecord:
    tick: int
    id: int
    type: str


@dataclass(frozen=True)
class DestroyRecord:
    tick: int
    id: int


@dataclass(frozen=True)
class Footer:
    ticks: int
    end_reason: str


@dataclass(frozen=True)
class Trace:
    name: str
    """File name, used in evidence links."""
    header: Header
    footer: Footer
    frames: tuple[Frame, ...]
    inputs: tuple[InputRecord, ...] = ()
    contacts: tuple[ContactRecord, ...] = ()
    spawns: tuple[SpawnRecord, ...] = ()
    destroys: tuple[DestroyRecord, ...] = ()


def is_enum_type(attr_type: str) -> bool:
    return attr_type.startswith("enum:")


def enum_values(attr_type: str) -> tuple[str, ...]:
    return tuple(attr_type.removeprefix("enum:").split("|"))


@cache
def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema: dict[str, Any] = json.load(fh)
    return schema


@cache
def _record_validators() -> dict[str, Draft202012Validator]:
    """One validator per record kind: faster and clearer errors than the top-level oneOf."""
    schema = load_schema()
    record_kinds = [ref["$ref"].rsplit("/", 1)[1] for ref in schema["oneOf"]]
    return {
        kind: Draft202012Validator(
            {
                "$schema": schema["$schema"],
                "$ref": f"#/$defs/{kind}",
                "$defs": schema["$defs"],
            }
        )
        for kind in record_kinds
    }


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _coerce_attr(value: object, attr_type: str) -> AttrValue | None:
    """Return the typed value, or None if ``value`` does not fit ``attr_type``."""
    if attr_type == "int":
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if attr_type == "float":
        return float(value) if isinstance(value, int | float) and _is_number(value) else None
    if attr_type == "bool":
        return value if isinstance(value, bool) else None
    if attr_type == "string":
        return value if isinstance(value, str) else None
    if attr_type == "vec2":
        if isinstance(value, list) and len(value) == 2 and all(_is_number(v) for v in value):
            return (float(value[0]), float(value[1]))
        return None
    if is_enum_type(attr_type):
        return value if isinstance(value, str) and value in enum_values(attr_type) else None
    return None


@dataclass
class _Parser:
    name: str
    errors: list[str] = field(default_factory=list)
    header: Header | None = None
    footer: Footer | None = None
    frames: list[Frame] = field(default_factory=list)
    inputs: list[InputRecord] = field(default_factory=list)
    contacts: list[ContactRecord] = field(default_factory=list)
    spawns: list[SpawnRecord] = field(default_factory=list)
    destroys: list[DestroyRecord] = field(default_factory=list)
    id_types: dict[int, str] = field(default_factory=dict)

    def error(self, line_no: int, message: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(f"line {line_no}: {message}")

    def feed(self, line_no: int, text: str) -> None:
        try:
            record = json.loads(text)
        except json.JSONDecodeError as exc:
            self.error(line_no, f"invalid JSON: {exc.msg}")
            return
        kind = record.get("t") if isinstance(record, dict) else None
        validator = _record_validators().get(kind) if isinstance(kind, str) else None
        if validator is None:
            self.error(line_no, f'unknown record kind "t": {kind!r}')
            return
        schema_errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
        if schema_errors:
            for err in schema_errors:
                where = "/".join(str(p) for p in err.path)
                self.error(line_no, f"{kind}: {err.message}" + (f" (at {where})" if where else ""))
            return

        if self.footer is not None:
            self.error(line_no, "record after footer")
            return
        if kind == "header":
            if line_no != 1 or self.header is not None:
                self.error(line_no, "header must be the first record and appear once")
                return
            self.header = Header(
                game=record["game"],
                build=record["build"],
                observability=record["observability"],
                tick_hz=record["tick_hz"],
                seed=record["seed"],
                session=record["session"],
                agent=record["agent"],
                entity_types={t: dict(d["attrs"]) for t, d in record["entity_types"].items()},
                walls=tuple(
                    (float(w[0]), float(w[1]), float(w[2]), float(w[3]))
                    for w in record["static"]["walls"]
                ),
            )
            return
        if self.header is None:
            if line_no == 1:
                self.error(line_no, "first record must be the header")
            return
        if kind == "frame":
            self._frame(line_no, record)
        elif kind == "input":
            value = record.get("value")
            self.inputs.append(
                InputRecord(
                    record["tick"],
                    record["action"],
                    record["phase"],
                    (float(value[0]), float(value[1])) if isinstance(value, list) else value,
                )
            )
        elif kind == "contact":
            self.contacts.append(
                ContactRecord(record["tick"], record["phase"], record["a"], record["b"])
            )
        elif kind == "spawn":
            if record["type"] not in self.header.entity_types:
                self.error(line_no, f"spawn of undeclared entity type {record['type']!r}")
            self._bind_type(line_no, record["id"], record["type"])
            self.spawns.append(SpawnRecord(record["tick"], record["id"], record["type"]))
        elif kind == "destroy":
            self.destroys.append(DestroyRecord(record["tick"], record["id"]))
        elif kind == "footer":
            self.footer = Footer(record["ticks"], record["end_reason"])

    def _bind_type(self, line_no: int, ent_id: int, ent_type: str) -> None:
        known = self.id_types.setdefault(ent_id, ent_type)
        if known != ent_type:
            self.error(line_no, f"entity {ent_id} changes type from {known} to {ent_type}")

    def _frame(self, line_no: int, record: dict[str, Any]) -> None:
        assert self.header is not None
        tick: int = record["tick"]
        if self.frames and tick != self.frames[-1].tick + 1:
            self.error(
                line_no,
                f"frame tick {tick} does not follow previous frame tick {self.frames[-1].tick}",
            )
        ents: list[EntityState] = []
        seen: set[int] = set()
        for ent in record["ents"]:
            ent_id, ent_type = ent["id"], ent["type"]
            if ent_id in seen:
                self.error(line_no, f"entity {ent_id} appears twice in frame {tick}")
            seen.add(ent_id)
            declared = self.header.entity_types.get(ent_type)
            if declared is None:
                self.error(line_no, f"undeclared entity type {ent_type!r}")
                continue
            self._bind_type(line_no, ent_id, ent_type)
            raw: dict[str, Any] = ent["a"]
            if raw.keys() != declared.keys():
                missing = sorted(declared.keys() - raw.keys())
                extra = sorted(raw.keys() - declared.keys())
                self.error(
                    line_no,
                    f"{ent_type}#{ent_id}: attributes do not match header"
                    f" (missing {missing}, undeclared {extra})",
                )
                continue
            attrs: dict[str, AttrValue] = {}
            for attr, attr_type in declared.items():
                value = _coerce_attr(raw[attr], attr_type)
                if value is None:
                    self.error(
                        line_no, f"{ent_type}#{ent_id}.{attr}: {raw[attr]!r} is not a {attr_type}"
                    )
                else:
                    attrs[attr] = value
            ents.append(EntityState(ent_id, ent_type, attrs))
        self.frames.append(Frame(tick, tuple(ents)))

    def finish(self, last_line: int) -> None:
        if self.header is None:
            if not self.errors:
                self.error(1, "empty trace: no header")
            return
        if self.footer is None:
            self.error(last_line, "last record must be the footer")
        elif self.footer.ticks != len(self.frames):
            self.error(
                last_line,
                f"footer says {self.footer.ticks} ticks but the file has {len(self.frames)} frames",
            )
        if not self.frames:
            return
        first, last = self.frames[0].tick, self.frames[-1].tick
        events: list[tuple[str, int]] = (
            [("input", r.tick) for r in self.inputs]
            + [("contact", r.tick) for r in self.contacts]
            + [("spawn", r.tick) for r in self.spawns]
            + [("destroy", r.tick) for r in self.destroys]
        )
        for kind, tick in events:
            if not first <= tick <= last:
                self.error(last_line, f"{kind} at tick {tick} is outside frames [{first}, {last}]")
        for kind, ids in (
            ("contact", [i for c in self.contacts for i in (c.a, c.b)]),
            ("destroy", [d.id for d in self.destroys]),
        ):
            for ent_id in ids:
                if ent_id not in self.id_types:
                    self.error(last_line, f"{kind} refers to unknown entity id {ent_id}")


def _parse(path: Path) -> _Parser:
    parser = _Parser(name=path.name)
    line_no = 0
    with path.open(encoding="utf-8") as fh:
        for line_no, text in enumerate(fh, start=1):
            if text.strip():
                parser.feed(line_no, text)
    parser.finish(line_no)
    return parser


def validate_trace(path: Path) -> list[str]:
    """Return a list of human-readable problems; empty means the trace is valid."""
    return _parse(path).errors


def read_trace(path: Path) -> Trace:
    """Read and validate one trace file. Raises :class:`TraceError` if it is invalid."""
    parser = _parse(path)
    if parser.errors or parser.header is None or parser.footer is None:
        raise TraceError(path, parser.errors)
    return Trace(
        name=parser.name,
        header=parser.header,
        footer=parser.footer,
        frames=tuple(parser.frames),
        inputs=tuple(parser.inputs),
        contacts=tuple(parser.contacts),
        spawns=tuple(parser.spawns),
        destroys=tuple(parser.destroys),
    )
