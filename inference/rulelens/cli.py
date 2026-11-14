"""Command-line interface (SPEC §9): ``rulelens validate | infer | eval``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from rulelens.config import Config, load_config
from rulelens.eval.matcher import evaluate
from rulelens.events.eventizer import coverage, eventize
from rulelens.io.trace import Trace, TraceError, read_trace, validate_trace
from rulelens.ir import yaml_io
from rulelens.pipeline import infer as run_infer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Infer game rules from gameplay traces and generate a readable spec.",
)

TracesArg = Annotated[list[Path], typer.Argument(help="Trace files (.jsonl).", exists=True)]
ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", help="Config file; defaults to ./rulelens.toml if it exists."),
]


def _config(path: Path | None) -> Config:
    if path is None and Path("rulelens.toml").is_file():
        path = Path("rulelens.toml")
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _read_all(paths: list[Path]) -> list[Trace]:
    traces: list[Trace] = []
    for path in sorted(paths):
        try:
            traces.append(read_trace(path))
        except TraceError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    return traces


@app.command()
def validate(traces: TracesArg) -> None:
    """Check traces against the rulelens.trace/1 schema and print a coverage summary."""
    valid: list[Trace] = []
    failed = 0
    for path in sorted(traces):
        errors = validate_trace(path)
        if errors:
            failed += 1
            typer.secho(f"FAIL {path} ({len(errors)} error(s))", fg=typer.colors.RED)
            for message in errors[:10]:
                typer.echo(f"     {message}")
            if len(errors) > 10:
                typer.echo(f"     ... and {len(errors) - 10} more")
        else:
            trace = read_trace(path)
            valid.append(trace)
            typer.echo(f"ok   {path} ({len(trace.frames)} ticks, agent={trace.header.agent})")
    if valid:
        typer.echo(f"\nCoverage over {len(valid)} valid trace(s):")
        for family, counts in coverage(eventize(t) for t in valid).items():
            listing = ", ".join(f"{name} ×{n}" for name, n in counts.items()) or "none observed"
            typer.echo(f"  {family}: {listing}")
    if failed:
        raise typer.Exit(1)


@app.command()
def infer(
    traces: TracesArg,
    output: Annotated[Path, typer.Option("-o", "--output", help="Where to write rules.yaml.")],
    miners: Annotated[
        str | None, typer.Option("--miners", help="Comma-separated subset, e.g. causal,invariants.")
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Run the miners over the traces and write the Rule IR."""
    cfg = _config(config)
    loaded = _read_all(traces)
    try:
        ruleset = run_infer(
            loaded, cfg, [m.strip() for m in miners.split(",")] if miners else None
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    yaml_io.dump(ruleset, output)
    by_kind: dict[str, int] = {}
    for rule in ruleset.rules:
        by_kind[rule.kind] = by_kind.get(rule.kind, 0) + 1
    summary = ", ".join(f"{n} {kind}" for kind, n in sorted(by_kind.items())) or "no rules"
    typer.echo(f"{len(loaded)} trace(s) -> {len(ruleset.rules)} rule(s) ({summary}) -> {output}")


@app.command("eval")
def eval_(
    rules: Annotated[Path, typer.Argument(help="Inferred rules.yaml.", exists=True)],
    truth: Annotated[Path, typer.Option("--truth", help="Ground-truth IR.", exists=True)],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Write the full result as JSON.")
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Score inferred rules against ground truth (precision / recall / F1)."""
    cfg = _config(config)
    try:
        inferred_set, truth_set = yaml_io.load(rules), yaml_io.load(truth)
    except yaml_io.IRError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    result = evaluate(inferred_set, truth_set, cfg.eval)

    typer.echo(f"{'kind':<16}{'TP':>4}{'FP':>4}{'FN':>4}{'P':>8}{'R':>8}{'F1':>8}")
    for kind, score in [*result.per_kind.items(), ("overall", result.overall)]:
        typer.echo(
            f"{kind:<16}{score.tp:>4}{score.fp:>4}{score.fn:>4}"
            f"{score.precision:>8.2f}{score.recall:>8.2f}{score.f1:>8.2f}"
        )
    if result.mean_rel_error is not None:
        typer.echo(f"mean relative error of matched numbers: {result.mean_rel_error:.2%}")
    partial = sum(m.partial for m in result.matches)
    if partial:
        typer.echo(f"partial matches (extra effects or predicates): {partial}")
    if result.missed:
        typer.echo(f"missed truth rules: {', '.join(result.missed)}")
    if result.spurious:
        typer.echo(f"inferred rules not in truth: {', '.join(result.spurious)}")
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    app()
