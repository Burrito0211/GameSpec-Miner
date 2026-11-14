# RuleLens — Specification

> **Working title:** RuleLens: Automatic Game-Rule Inference and Specification Generation from Gameplay Observation
> (基於遊玩觀察之遊戲規則自動推論與規格生成系統)
>
> **Status:** Draft v0.1 · **Team:** 3 people (A / B / C) · **Last updated:** 2026-11-15

---

## 1. Summary

RuleLens observes a running game, records what happens (entity states, player inputs, physics contacts), and **infers the game's rules** from those observations. It then outputs a **human-readable specification** in which every rule has statistics and links to the evidence behind it.

To measure accuracy, the team builds small Unity games whose rules are known exactly (the *ground truth*). The tool infers the rules of each game, and the result is scored against the ground truth (precision / recall / F1, plus error on numeric thresholds).

```
 ┌──────────────┐   trace    ┌──────────────┐   events   ┌──────────────┐  Rule IR   ┌──────────────┐
 │ 1. Capture   │ ─────────▶ │ 2. Eventize  │ ─────────▶ │ 3. Infer     │ ─────────▶ │ 4. Generate  │
 │ Recorder SDK │  (.jsonl)  │ diff frames  │            │ rule miners  │  (.yaml)   │ spec (.md)   │
 └──────────────┘            └──────────────┘            └──────┬───────┘            └──────────────┘
        ▲                                                       │ hypotheses
        │                 ┌──────────────────────┐              │
        └──── commands ── │ 5. Active experiments │ ◀───────────┘
                          │ AgentBridge (stretch) │
                          └──────────────────────┘
                                                     6. Evaluate: Rule IR vs ground-truth IR → P / R / F1
```

---

## 2. Goals and non-goals

### 2.1 Goals
| ID | Goal |
|---|---|
| G-1 | Infer rules from observation alone. The game must not report its own rules: the recorder logs raw state only. |
| G-2 | Cover six rule kinds: **invariants, causal rules, input effects, NPC state machines, timing rules, stochastic rules** (§6). |
| G-3 | Every inferred rule carries statistics (support, confidence) and evidence links (trace file + tick). |
| G-4 | Accuracy can be measured: ground-truth specs exist and an evaluation harness reports the scores. |
| G-5 | Generated specs are readable by a game designer who has never seen the source code. |

### 2.2 Non-goals
- Online or multiplayer games, or any game with anti-cheat. **Never.**
- Injecting into or reverse-engineering commercial binaries (IL2CPP, memory reading) during the core project.
- 3D games, physics-heavy simulations, or games with procedural content beyond simple randomness.
- Inferring rules that the traces never trigger. The spec states what was observed and is explicit about coverage.
- Using an LLM to *invent* rules. An LLM may only rephrase rules that have already been inferred (§7.3).

### 2.3 Scope constraints
- **Genre:** 2D, top-down or side view, discrete entities, fixed timestep.
- **Observability levels** (one is declared in each trace header):
  - `whitebox`: entity attributes are recorded through the Recorder SDK. This is the core project.
  - `greybox`: an open-source game instrumented by the team, or a game with an official research API. This is a stretch goal.
  - `blackbox`: screen pixels + inputs only, with computer-vision extraction. This is an optional stretch goal.

---

## 3. Repository layout

```
rulelens/
├── SPEC.md                     # this file
├── CLAUDE.md                   # instructions for Claude Code
├── unity/RuleLensGames/        # Unity 6 LTS project
│   └── Assets/
│       ├── RuleLens.Recorder/      # C# SDK (own .asmdef), no game-specific code
│       ├── RuleLens.AgentBridge/   # TCP command server for automated play / experiments
│       ├── RuleLens.AutoPlayers/   # random + heuristic bots that generate traces
│       └── Games/
│           ├── G1_CoinCollector/
│           ├── G2_StealthPatrol/
│           └── G3_LootCooldown/
├── inference/                  # Python 3.11 package `rulelens`
│   ├── pyproject.toml
│   ├── rulelens/
│   │   ├── io/                 # trace reader, schema validation
│   │   ├── events/             # frame diffing → atomic events
│   │   ├── features/           # derived features (distance, LOS, angle, time-in-state…)
│   │   ├── miners/
│   │   │   ├── invariants.py
│   │   │   ├── causal.py
│   │   │   ├── input_effect.py
│   │   │   ├── fsm.py
│   │   │   ├── timing.py
│   │   │   └── stochastic.py
│   │   ├── ir/                 # Rule IR dataclasses + YAML (de)serialization
│   │   ├── report/             # Markdown spec generator (+ optional LLM polish)
│   │   ├── experiments/        # active-experiment planner + AgentBridge client
│   │   ├── eval/               # ground-truth matcher + metrics
│   │   └── cli.py
│   └── tests/
│       └── fixtures/           # small hand-written traces
├── groundtruth/
│   ├── g1.truth.yaml
│   ├── g2.truth.yaml
│   └── g3.truth.yaml
├── traces/                     # gitignored; generated recordings
├── out/                        # gitignored; inferred IR + generated specs
└── docs/                       # design notes, report drafts
```

**Language choices**
- **C# (Unity):** test games, Recorder SDK, AgentBridge, auto-players.
- **Python:** inference, report generation, evaluation. Its data and ML libraries (numpy, pandas, scikit-learn) make it the practical choice.
- **C++ (optional):** used only if profiling shows a miner is too slow. It would be exposed to Python via pybind11. Write nothing in C++ before that.

---

## 4. Test games (ground truth)

All three games use a **fixed timestep of 50 Hz**, a seeded RNG, and simple 2D colliders. Each game has a `groundtruth/gN.truth.yaml` written *before* any inference work on that game.

### G1 — Coin Collector (deterministic, simple)
| # | Rule |
|---|---|
| 1 | The player moves at 5 units/s in 8 directions; walls block movement. |
| 2 | Touching a Coin → the Coin is destroyed and `score += 10`. |
| 3 | Touching a BigCoin → the BigCoin is destroyed and `score += 50`. |
| 4 | Every 3 s a new Coin spawns at a random free cell, with at most 8 Coins alive. |
| 5 | `timer` starts at 60 and decreases by 1 each second. |
| 6 | `timer == 0` → `gameState = GameOver`; inputs are then ignored. |
| 7 | Invariants: `score >= 0`, `score % 10 == 0`, `0 <= timer <= 60`, `score` never decreases. |

### G2 — Stealth Patrol (NPC state machine)
| # | Rule |
|---|---|
| 1 | Guard states: `Patrol`, `Chase`, `Search`, `Return`. |
| 2 | Patrol: the guard walks its waypoint loop at 2 units/s. |
| 3 | Patrol/Search → Chase when `dist(guard, player) < 5` **and** the player is within the 90° FOV **and** LOS is clear. |
| 4 | Chase: the guard moves toward the player at 3.5 units/s. |
| 5 | Chase → Search when LOS has been lost for more than 1.0 s. |
| 6 | Search: the guard goes to the last known position and waits 3 s, then switches to Return. |
| 7 | Return → Patrol on reaching the nearest waypoint. |
| 8 | Guard touches player → `lives -= 1`, the player respawns at the start, and the guard switches to Return. |
| 9 | Player reaches Exit → `gameState = Win`. `lives == 0` → `gameState = GameOver`. |

The guard's `state` attribute can be **hidden** with a recorder flag. This tests whether the FSM miner can recover states from movement alone (§6.4).

### G3 — Loot & Cooldowns (timing + randomness)
| # | Rule |
|---|---|
| 1 | Pressing Attack hits enemies within 1.5 units; cooldown is 1.5 s (presses during cooldown do nothing). |
| 2 | An attack deals 10 damage; Slimes have 30 HP. HP ≤ 0 → the Slime is destroyed. |
| 3 | A destroyed Slime drops a Potion with p = 0.25. |
| 4 | Touching a Potion → `hp = min(hp + 20, 100)`. |
| 5 | A Slime touching the player → `hp -= 5`, then the player is invulnerable for 1 s. |
| 6 | A Slime spawns every 8 s, with at most 5 alive. |
| 7 | `hp <= 0` → `gameState = GameOver`. |

---

## 5. Capture: Recorder SDK and trace format

### 5.1 Recorder SDK (C#, `RuleLens.Recorder`)
- **FR-REC-1:** A game opts entities in with an `ObservedEntity` component (fields: `typeName`, auto-assigned `id`).
- **FR-REC-2:** Fields and properties marked `[Observe]` on that entity's components are recorded every tick. Supported types: `int`, `float`, `bool`, `enum`, `string`, `Vector2`.
- **FR-REC-3:** A global `World` entity carries game-level state such as `timer`, `gameState` and `score`, when those are not per-entity.
- **FR-REC-4:** Contact begin/end events are captured through `OnTriggerEnter2D/Exit2D` and `OnCollisionEnter2D/Exit2D` relays.
- **FR-REC-5:** Inputs come from the Unity Input System and are recorded as `down`/`up` events with logical action names (`Move`, `Attack`, …). Move vectors are recorded as values.
- **FR-REC-6:** Spawn and destroy events are emitted automatically when an `ObservedEntity` is enabled or destroyed.
- **FR-REC-7:** Static map geometry (wall colliders as AABBs) goes in the header, so the inference side can compute line of sight (LOS).
- **FR-REC-8:** Recording runs in `FixedUpdate`, after all game scripts (script execution order = last).
- **FR-REC-9:** A per-attribute `hidden` flag excludes an attribute from the trace (used in ablations).
- **Rule of honesty:** the SDK must never log *why* something happened. It records state and low-level events only. Game code must not call the recorder with semantic messages.
- **NFR-REC-1:** Recording overhead stays under 5% of frame time with 50 observed entities.

### 5.2 Trace format: `rulelens.trace/1` (JSON Lines)
One file per play session. Each line is one record with a `"t"` discriminator.

```jsonc
{"t":"header","schema":"rulelens.trace/1","game":"g1_coin","build":"0.1.0","observability":"whitebox",
 "tick_hz":50,"seed":1234,"session":"2026-12-01T10-00-00_s01","agent":"random",
 "entity_types":{
   "Player":{"attrs":{"pos":"vec2","vel":"vec2"}},
   "Coin":{"attrs":{"pos":"vec2"}},
   "World":{"attrs":{"score":"int","timer":"int","gameState":"enum:Playing|GameOver"}}
 },
 "static":{"walls":[[0,0,20,1],[0,0,1,15]]}}
{"t":"frame","tick":0,"ents":[{"id":0,"type":"World","a":{"score":0,"timer":60,"gameState":"Playing"}},
                              {"id":1,"type":"Player","a":{"pos":[2.0,3.0],"vel":[0,0]}}]}
{"t":"input","tick":12,"action":"Move","phase":"value","value":[1,0]}
{"t":"input","tick":30,"action":"Attack","phase":"down"}
{"t":"contact","tick":40,"phase":"enter","a":1,"b":7}
{"t":"spawn","tick":150,"id":9,"type":"Coin"}
{"t":"destroy","tick":41,"id":7}
{"t":"footer","ticks":3000,"end_reason":"GameOver"}
```

- Frames are **full snapshots every tick** in v1; delta compression is deferred. A 5-minute session at 50 Hz with about 20 entities is a few MB, which is acceptable.
- A JSON Schema lives at `inference/rulelens/io/trace.schema.json`. Both the C# writer and the Python reader are tested against it.
- Details the example leaves open, fixed in the schema and the reader:
  - The first record is the `header` and the last is the `footer`. Frame ticks are consecutive. `footer.ticks` is the number of frame records.
  - Attribute types are `int`, `float`, `bool`, `string`, `vec2` and `enum:A|B|…`. Every entity in a frame carries exactly the attributes its type declares in the header (hidden attributes are left out of the header too).
  - A wall is `[x, y, width, height]`, where `(x, y)` is the minimum corner.
  - An `input` record with `phase: "value"` must have a `value`.
  - Entities that exist at the start get `spawn` records at the first tick (FR-REC-6).
  - An attribute change made in tick *t* first shows in the frame of tick *t* or *t+1*, depending on Unity's callback order. Miners must not assume a lag of 0.

### 5.3 Trace generation
- **FR-GEN-1:** A random agent (uniform random inputs, sticky for 0.2–1 s).
- **FR-GEN-2:** A heuristic agent per game, e.g. seeks coins or sneaks toward the exit, so that deep states are reached.
- **FR-GEN-3:** Human play sessions, recorded through the same SDK.
- **FR-GEN-4:** Batch mode: run N sessions headless with different seeds (`-batchmode -rulelens-sessions 50 -rulelens-seed 1`).
- **FR-GEN-5:** Coverage report per batch: which attribute changes, contact pairs and inputs were observed, and how often.

---

## 6. Inference engine (Python `rulelens`)

### 6.1 Eventization (`events/`)
Consecutive frames are diffed into **atomic events**:

| Event | Fields |
|---|---|
| `attr_change` | tick, entity id/type, attr, old, new, delta (numeric) |
| `spawn` / `destroy` | tick, id, type |
| `contact_enter` / `contact_exit` | tick, a (id/type), b (id/type) |
| `input` | tick, action, phase, value |
| `enum_transition` | a special case of `attr_change` for enum attributes |

Continuous attributes such as `pos` and `vel` are **not** emitted as events on every tick. They feed the feature layer instead (§6.2).

### 6.2 Feature layer (`features/`)
Per-tick derived features for each entity, used by the FSM, input-effect and timing miners:
- `dist(e, nearest<Type>)`, `rel_angle(e.facing, target)`, `los(e, target)` (computed from static walls)
- `speed`, `heading`, `heading_toward(target)` (cosine)
- `time_since(event)`, `time_in_state`
- the enum attribute values themselves

### 6.3 Miners
Every miner outputs a list of Rule IR objects (§7.1) with stats and evidence.

**(a) Invariant miner (`invariants.py`)** works in the style of Daikon. For each `(entity_type, attr)` it tests templates:
- constant; range `[min, max]`; enum set; `x % k == 0`; monotonic (non-decreasing or non-increasing)
- pairwise between attributes of the same entity: `x <= y`, `x == y`
- A rule is accepted only if it has **0 violations** and support ≥ `min_support` (default 200 ticks across at least 3 sessions).

**(b) Causal-rule miner (`causal.py`)** finds `trigger → effects` relations between events.
- Candidate triggers: `contact_enter(TypeA, TypeB)`, `input(action)`, `enum_transition`, `spawn`, `destroy`, and threshold crossings such as `attr reaches 0`.
- Candidate effects: any event within a window of `[0, W]` ticks (default W = 2) that involves the trigger's entities or `World`.
- Effects are abstracted by entity *type* and by trigger role (`a`/`b`). Numeric deltas are generalized to a constant when the delta has zero variance; otherwise to a formula from a small template set (`min(x+c, cap)`, `x*c`).
- Scores: support, confidence `P(effect | trigger)`, and lift against the effect's base rate. Defaults: accept if confidence ≥ 0.95, support ≥ 10 and lift ≥ 2. Rules with confidence between 0.15 and 0.95 are passed on to the stochastic miner.
- Filters against coincidences:
  - Events at a trace's first tick are initial conditions and are never triggers.
  - An effect that recurs at one constant period (e.g. `timer -= 1`) is a clock. It belongs to the timing miner and is never attributed to a trigger that coincides with it.
  - A rule whose trigger is itself an effect of another rule does not repeat that rule's effects (`contact → destroy + score` hides `destroy → score`).
- Contact triggers order their roles by type name (`a: Coin, b: Player`). The eval matcher resolves roles to types before comparing, so the order carries no meaning.

**(c) Input-effect miner (`input_effect.py`)** handles continuous control.
- It regresses an entity's velocity on input values to estimate speed constants (e.g. `vel = 5.0 * normalize(move)`).
- It detects conditions that block an input, using the feature layer (e.g. Attack ignored when `time_since(attack) < 1.5`).

**(d) FSM miner (`fsm.py`)**
1. **State labels.** If an enum attribute such as `state` is visible, use it. If it is hidden, cluster per-tick behavior features (speed, `heading_toward(player)`, distance to the nearest waypoint) with a GMM or DBSCAN, then smooth the labels with a minimum dwell time.
2. **Transitions.** Collect `(from, to)` pairs and their frequencies.
3. **Guard conditions.** For each `from` state, train a shallow decision tree (max depth 3) on features at tick *t*, predicting whether a transition happens at *t+1*. Extract its paths as conjunctions of predicates (`dist < 4.97 AND los == true`).
4. **Threshold estimates.** Report a bootstrap 95% CI for each numeric threshold.
5. **Per-state behavior.** Summarize what the entity does in each state, e.g. mean speed and whether it moves toward the player.

**(e) Timing miner (`timing.py`)**
- **Periodic spawns:** inter-arrival times of `spawn(Type)`, checking for a constant period and a population cap (the maximum number alive at the same time).
- **Cooldowns:** the minimum interval between successful effects of an input while presses are faster than that interval.
- **Timers:** an attribute that decreases by a constant amount at a fixed period.
- **Delays:** a stable lag between a trigger and its effect (e.g. LOS lost → Search after 1.0 s).

**(f) Stochastic miner (`stochastic.py`)**
- For causal candidates with intermediate confidence, it estimates p with a Wilson 95% CI.
- It also reports distributions for random spawn positions (e.g. "uniform over free cells", based on a χ² test against a uniform distribution).

### 6.4 Hidden-state ablation
For G2 the team runs the full pipeline twice: once with `Guard.state` visible and once with it hidden. Both runs are scored against the ground truth. This experiment is a key result for the final report.

---

## 7. Output

### 7.1 Rule IR (`rules.yaml`)
This is the single intermediate representation, used for **both inferred rules and ground truth**.

```yaml
schema: rulelens.ir/1
game: g2_stealth
generated_from: {traces: 50, ticks: 750000, observability: whitebox}
rules:
  - id: R-g1-002
    kind: causal                # invariant | causal | input_effect | fsm_transition | fsm_state | timing | stochastic
    trigger: {event: contact_enter, a: Player, b: Coin}
    effects:
      - {event: destroy, target: b}
      - {event: attr_change, target: World, attr: score, op: add, value: 10}
    window_ticks: 1
    stats: {support: 143, confidence: 1.0, lift: 41.2}
    evidence: [{trace: s01.jsonl, tick: 812}, {trace: s07.jsonl, tick: 233}]

  - id: R-g2-003
    kind: fsm_transition
    entity: Guard
    from: Patrol
    to: Chase
    guard:                       # conjunction of predicates
      - {feature: dist, target: Player, op: "<", value: 4.97, ci: [4.90, 5.05]}
      - {feature: rel_angle, target: Player, op: "<", value: 45.3}
      - {feature: los, target: Player, op: "==", value: true}
    stats: {support: 88, confidence: 0.98}
    evidence: [...]

  - id: R-g3-003
    kind: stochastic
    trigger: {event: destroy, type: Slime}
    effects: [{event: spawn, type: Potion}]
    probability: {p: 0.247, ci: [0.21, 0.29], n: 520}
```

Fields by rule kind (every rule has `id` and `kind`; `description`, `stats` and `evidence` are optional on all):

| Kind | Fields |
|---|---|
| `invariant` | `entity`, `attr`, `invariant: {template, …}`. Templates: `constant {value}`, `range {min, max}`, `enum_set {values}`, `modulo {k}` (attr % k == 0), `monotonic {direction: non_decreasing \| non_increasing}`, `pair {op: "<=" \| "==", other}`. |
| `causal` | `trigger`, `effects`, `window_ticks`. Trigger events: `contact_enter {a, b}`, `input {action, phase}`, `spawn {type}`, `destroy {type}`, `enum_transition {entity, attr, from, to}`, `attr_reaches {entity, attr, value}`. Effect `target` is a trigger role (`a`, `b`, `self`) or an entity type. `attr_change` ops: `add`, `set`, `add_capped` (with `cap`), `mul`. |
| `stochastic` | `trigger`, `effects`, `probability: {p, ci, n}` |
| `fsm_transition` | `entity`, `from`, `to`, `guard` |
| `timing`, `input_effect`, `fsm_state` | `params`: a flat map of names and numbers, e.g. `{template: periodic_spawn, type: Coin, period_s: 3.0, max_alive: 8}`. **Provisional:** these get typed fields when their miners are written (M2/M3). |

### 7.2 Generated spec (`SPEC_<game>.md`)
`rulelens report` produces a Markdown document with these sections:
1. Overview: entity types, observed attributes, the World state, and trace coverage.
2. Controls: input effects and movement constants.
3. Rules by category: each rule in plain language, followed by a stats line and evidence links.
4. NPC behavior: one section per FSM, with a Mermaid state diagram, guard conditions and per-state behavior.
5. Timing and randomness.
6. Invariants.
7. **Coverage and limitations:** contact pairs, states and inputs never observed, and low-support rules.

Example rule rendering:
> **R-g2-003 — Guard starts chasing** · *confidence 98% (88 obs.)*
> A Guard in **Patrol** switches to **Chase** when the Player is closer than **≈ 5.0** units (95% CI 4.90–5.05), within **≈ 45°** of its facing direction, and in clear line of sight.
> Evidence: `s03.jsonl @ tick 1520`, `s11.jsonl @ tick 402` …

### 7.3 LLM polishing (optional, off by default)
- Flag: `--polish`. It may rewrite only the prose of rules that are already in the IR.
- A post-check confirms that every number and entity name in the polished text also appears in the IR. If the check fails, the template output is used instead.

---

## 8. Evaluation (`eval/`)

### 8.1 Matching inferred rules to ground truth
Two rules match when:
- `kind` is equal, and the entity types, attributes and events are equal;
- numeric values are within tolerance: ±5% relative or ±0.1 absolute, whichever is larger;
- for FSM guards, every ground-truth predicate has a matching inferred predicate (same feature, target and operator, value within tolerance). Extra inferred predicates count as partial matches.
- Only the fields a ground-truth rule states are compared (`score >= 0` is a `range` with only `min`). Extra inferred effects are treated like extra guard predicates.
- Matching is one-to-one. A partial match counts as a true positive and is reported separately. An inferred rule with no ground-truth counterpart is a false positive, **even if it is true** (see open question 6).

### 8.2 Metrics
- Precision, recall and F1: per game, per rule kind and overall.
- Mean relative error of the numeric thresholds and constants that matched.
- Learning curve: F1 as a function of the number of sessions (1, 5, 10, 25, 50).
- Hidden-state ablation for G2 (§6.4).
- Agent comparison: random vs heuristic vs human traces.

### 8.3 Targets (whitebox, 50 sessions)
| Game | F1 target | Threshold error |
|---|---|---|
| G1 | ≥ 0.90 | ≤ 3% |
| G2 (state visible) | ≥ 0.80 | ≤ 5% |
| G2 (state hidden) | ≥ 0.60 | ≤ 10% |
| G3 | ≥ 0.75 | ≤ 5% |

---

## 9. CLI

```bash
rulelens validate traces/g1/*.jsonl                       # schema check + coverage summary
rulelens infer   traces/g1/*.jsonl -o out/g1/rules.yaml   # run all miners
rulelens infer   ... --miners causal,invariants           # run a subset
rulelens report  out/g1/rules.yaml -o out/g1/SPEC_g1.md [--polish]
rulelens eval    out/g1/rules.yaml --truth groundtruth/g1.truth.yaml -o out/g1/eval.json
rulelens bench   --game g1 --sessions 1,5,10,25,50        # learning curve
```

Config lives in `rulelens.toml`: miner thresholds, window sizes and tolerances. Defaults appear in this spec.

---

## 10. Active experiments (stretch, M4)

- **AgentBridge (C#):** a TCP server, enabled only in development builds, with newline-delimited JSON commands: `reset {seed}`, `step {n}`, `input {action, value}`, `get_state`, `start_recording {path}`, `stop_recording`.
  - Whitebox-only extras: `set_attr`, `teleport {id, pos}`. These are labeled *interventions* and recorded in the trace header.
- **Experiment planner (Python):** for each low-confidence rule, or each wide threshold CI, it generates trials just below and just above the estimated boundary (e.g. teleport the player to `dist = 4.8` and to `5.2`), runs them, and updates the rule.
- **Success metric:** the threshold CI narrows and F1 improves for the same total number of ticks, compared with passive observation.

---

## 11. Stretch: greybox and blackbox

- **Greybox (M5):** pick one small open-source 2D game (license must allow modification), add `ObservedEntity` instrumentation, write a partial ground truth by reading its code, and run the pipeline.
- **Blackbox (M6, optional):** replace the Recorder with a CV extractor. Sprites are detected with template matching or a small YOLO model trained on the team's own games; numbers are read by OCR; inputs are recorded from the OS. The extractor outputs the same `rulelens.trace/1` format with `observability: blackbox`. Running G1 and G2 again shows how much F1 drops.

---

## 12. Milestones

The calendar assumes the academic year runs Dec 2026 – Jun 2027; adjust it to the real deadlines.

| M | Weeks | Deliverables | Exit criteria |
|---|---|---|---|
| M0 | 2 | Repo setup, trace schema frozen, G1 playable, `g1.truth.yaml` | Schema tests pass in both C# and Python |
| M1 | 4 | Recorder SDK, random + heuristic agents for G1, eventizer, invariant + causal miners, IR, basic report, eval harness | **End-to-end on G1:** traces → SPEC_g1.md + F1 score |
| M2 | 5 | G2 + truth, feature layer (LOS, angle), FSM miner, timing miner (delays) | G2 F1 ≥ 0.6 with state visible |
| M3 | 4 | G3 + truth, stochastic miner, cooldown/spawn timing, coverage report, learning-curve bench | All three games scored; first full result tables |
| M4 | 4 | AgentBridge + experiment planner | Measured improvement over passive observation |
| M5 | 4 | Hidden-state ablation, greybox target, report polish | Ablation results; one external game processed |
| M6 | opt. | Blackbox CV extractor | G1 blackbox F1 reported |
| M7 | 3 | Final report, poster, demo video | — |

### Ownership
| Member | Primary | Secondary |
|---|---|---|
| **A — Capture** | Recorder SDK, trace writer, auto-players, batch runner, AgentBridge | CV extractor (M6) |
| **B — Inference** | eventizer, feature layer, all miners | performance (C++ only if needed) |
| **C — Truth & Output** | test games G1–G3, ground-truth YAML, Rule IR, report generator, eval harness | demo, documentation |

**Week 1, all three members:** freeze the trace schema (§5.2) and the Rule IR (§7.1). After that, work proceeds in parallel against those two contracts.

---

## 13. Quality requirements

| ID | Requirement |
|---|---|
| NFR-1 | Determinism: the same seed and the same input sequence produce an identical trace. Tested in CI with a replayed input log. |
| NFR-2 | Performance: `rulelens infer` processes 50 sessions × 5 min of G2 in under 5 minutes on a laptop. |
| NFR-3 | Reproducibility: every `out/` artifact records the git commit, config hash and trace list. |
| NFR-4 | Testing: each miner has unit tests on hand-written fixture traces in which the expected rule is known. |
| NFR-5 | Python: `ruff` + `mypy --strict` on `rulelens/`, and `pytest`. C#: Unity Test Framework (EditMode tests for the Recorder serializer). |
| NFR-6 | No rule logic in the Recorder: code review checks that `[Observe]` is the only integration point. |

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Rules that never fire in traces | Heuristic agents, coverage reports, active experiments; limitations stated in the spec |
| The FSM miner fails when state is hidden | Make the ablation a reported result, not a blocker; tune features; fall back to visible state |
| Causal miner false positives (coincidences) | Lift threshold, minimum support across multiple sessions, short windows, randomized agents |
| Scope creep into blackbox CV | Blackbox stays optional until M5 exit criteria are met |
| Unity ↔ Python contract drift | One JSON Schema, tested on both sides in CI |
| Legal/ethics questions about "monitoring existing games" | Only own games plus permissively licensed open-source games; stated explicitly in the report |

---

## 15. Open questions

1. Final project name: RuleLens / GRIM / other.
2. Real deadlines: proposal, midterm and final dates, to fix the M-calendar.
3. Unity version pin (proposal: Unity 6 LTS) and Input System only (no legacy input)?
4. Which open-source game is the greybox target? Shortlist candidates by M3.
5. Is LLM polishing allowed by the course rules? It is off by default either way.
6. True-but-unlisted rules: the invariant miner finds facts that §4 does not list (`timer` never increases, `gameState ∈ {Playing, GameOver}`), and they lower precision. Options: add them to the ground truth, mark some truth rules as optional, or score invariants by recall only.
7. The §8.3 targets depend on which miners exist. `g1.truth.yaml` has 13 rules, of which the M1 miners (invariant + causal) can reach 7; the rest need the input-effect, timing and stochastic miners.

---

## 16. First tasks for Claude Code

Start with these, in order:
1. Scaffold the repo layout (§3), `inference/pyproject.toml` (deps: numpy, pandas, scikit-learn, pyyaml, jsonschema, typer, pytest, ruff, mypy) and `.gitignore` (traces/, out/, Unity Library/Temp).
2. Write `trace.schema.json` (§5.2) and the Python trace reader with validation, plus 3 fixture traces in `tests/fixtures/`.
3. Implement the Rule IR dataclasses and YAML round-trip (§7.1), with tests.
4. Write `groundtruth/g1.truth.yaml` from §4 G1.
5. Implement the eventizer (§6.1) and the invariant miner (§6.3a) against the fixtures.
6. Implement the causal miner (§6.3b), the eval matcher (§8.1) and the CLI commands `validate`, `infer`, `eval`.
7. Unity: the G1 scene, the `ObservedEntity` / `[Observe]` Recorder, the JSONL writer and the random agent. Check that the output passes `rulelens validate`.
8. End-to-end: 50 G1 sessions → `rules.yaml` → `SPEC_g1.md` → F1 score. **This completes M1.**
