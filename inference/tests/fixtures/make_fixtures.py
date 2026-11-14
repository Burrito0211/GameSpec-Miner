"""Regenerate the fixture traces: ``python tests/fixtures/make_fixtures.py``.

The fixtures come from a scripted miniature of G1 (Coin Collector) so that every
rule in them is known exactly. Differences from the real G1, to keep each trace
under 200 ticks: 10 Hz instead of 50 Hz, and the timer starts at 8 instead of 60.

Rules in the fixtures (tick_hz = 10):
  * the player moves at 5 units/s (0.5 units/tick) in 8 directions
  * touching a Coin    -> one tick later: Coin destroyed, score += 10
  * touching a BigCoin -> one tick later: BigCoin destroyed, score += 50
  * a Coin spawns every 30 ticks; timer decreases by 1 every 10 ticks
  * timer == 0 -> gameState = GameOver (tick 80, the last frame)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

HZ = 10
SPEED = 5.0
TIMER_START = 8
SPAWN_PERIOD = 30
TOUCH = 0.5
VALUES = {"Coin": 10, "BigCoin": 50}
WALLS = [[0, 0, 10, 1], [0, 0, 1, 8], [0, 7, 10, 1], [9, 0, 1, 8]]

# name -> (seed, player start, initial pickups, coin spawn positions, agent script)
# script steps: ("go", x, y) walk there; ("until", tick) stand still until that tick
SESSIONS: dict[str, tuple[int, tuple[float, float], list[tuple[str, float, float]],
                          list[tuple[float, float]], list[tuple[Any, ...]]]] = {
    "g1_mini_s01": (
        1, (2, 2), [("Coin", 4, 2), ("BigCoin", 7, 5)], [(4, 5), (2, 5)],
        [("go", 7, 2), ("go", 7, 5), ("until", 32), ("go", 4, 5), ("until", 62), ("go", 2, 5)],
    ),
    "g1_mini_s02": (
        2, (2, 2), [("Coin", 2, 4), ("Coin", 5, 4)], [(5, 2), (8, 2)],
        [("go", 2, 4), ("go", 5, 4), ("until", 33), ("go", 5, 2), ("until", 61), ("go", 8, 2)],
    ),
    "g1_mini_s03": (
        3, (5, 3), [("BigCoin", 5, 6), ("Coin", 8, 6)], [(8, 3), (3, 3)],
        [("go", 5, 6), ("go", 8, 6), ("until", 31), ("go", 8, 3), ("go", 6, 5)],
    ),
}


def r(x: float) -> float:
    return round(x, 4)


def simulate(name: str) -> list[dict[str, Any]]:
    seed, start, pickups, spawn_at, script = SESSIONS[name]
    records: list[dict[str, Any]] = [{
        "t": "header", "schema": "rulelens.trace/1", "game": "g1_mini", "build": "fixture",
        "observability": "whitebox", "tick_hz": HZ, "seed": seed, "session": name,
        "agent": "scripted",
        "entity_types": {
            "World": {"attrs": {"score": "int", "timer": "int",
                                "gameState": "enum:Playing|GameOver"}},
            "Player": {"attrs": {"pos": "vec2", "vel": "vec2"}},
            "Coin": {"attrs": {"pos": "vec2"}},
            "BigCoin": {"attrs": {"pos": "vec2"}},
        },
        "static": {"walls": WALLS},
    }]
    score, timer, state = 0, TIMER_START, "Playing"
    pos, vel, move = [float(start[0]), float(start[1])], [0.0, 0.0], (0, 0)
    items: dict[int, tuple[str, float, float]] = {}
    next_id = 2
    pending: list[int] = []  # touched last tick; resolved this tick
    steps = list(script)
    spawns = list(spawn_at)

    def add_item(tick: int, kind: str, x: float, y: float) -> None:
        nonlocal next_id
        items[next_id] = (kind, x, y)
        records.append({"t": "spawn", "tick": tick, "id": next_id, "type": kind})
        next_id += 1

    def frame(tick: int) -> None:
        ents: list[dict[str, Any]] = [
            {"id": 0, "type": "World", "a": {"score": score, "timer": timer, "gameState": state}},
            {"id": 1, "type": "Player", "a": {"pos": [r(pos[0]), r(pos[1])],
                                               "vel": [r(vel[0]), r(vel[1])]}},
        ]
        ents += [{"id": i, "type": k, "a": {"pos": [x, y]}} for i, (k, x, y) in items.items()]
        records.append({"t": "frame", "tick": tick, "ents": ents})

    def touch(tick: int) -> None:
        for i, (_, x, y) in items.items():
            if i not in pending and math.hypot(x - pos[0], y - pos[1]) <= TOUCH + 1e-9:
                records.append({"t": "contact", "tick": tick, "phase": "enter", "a": 1, "b": i})
                pending.append(i)

    records.append({"t": "spawn", "tick": 0, "id": 0, "type": "World"})
    records.append({"t": "spawn", "tick": 0, "id": 1, "type": "Player"})
    for kind, x, y in pickups:
        add_item(0, kind, x, y)
    frame(0)
    touch(0)

    last = TIMER_START * HZ
    for tick in range(1, last + 1):
        for i in pending:
            score += VALUES[items.pop(i)[0]]
            records.append({"t": "destroy", "tick": tick, "id": i})
        pending.clear()

        want = (0, 0)
        while steps:
            if steps[0][0] == "until":
                if tick >= steps[0][1]:
                    steps.pop(0)
                    continue
                break
            dx, dy = steps[0][1] - pos[0], steps[0][2] - pos[1]
            if abs(dx) < 0.26 and abs(dy) < 0.26:
                steps.pop(0)
                continue
            want = (int(abs(dx) >= 0.26) * (1 if dx > 0 else -1),
                    int(abs(dy) >= 0.26) * (1 if dy > 0 else -1))
            break
        if want != move:
            move = want
            records.append({"t": "input", "tick": tick, "action": "Move", "phase": "value",
                            "value": list(move)})

        if tick % SPAWN_PERIOD == 0 and spawns:
            add_item(tick, "Coin", *spawns.pop(0))
        if tick % HZ == 0:
            timer -= 1
            if timer == 0:
                state = "GameOver"

        norm = math.hypot(*move)
        vel = [0.0, 0.0] if state == "GameOver" or norm == 0 else [
            SPEED * move[0] / norm, SPEED * move[1] / norm]
        pos = [pos[0] + vel[0] / HZ, pos[1] + vel[1] / HZ]
        frame(tick)
        if state == "Playing":
            touch(tick)

    records.append({"t": "footer", "ticks": last + 1, "end_reason": "GameOver"})
    return records


def main() -> None:
    here = Path(__file__).parent
    for name in SESSIONS:
        lines = [json.dumps(rec, separators=(",", ":")) for rec in simulate(name)]
        (here / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"wrote {name}.jsonl ({len(lines)} records)")


if __name__ == "__main__":
    main()
