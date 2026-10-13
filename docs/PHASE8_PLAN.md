# Phase 8 plan: race preparation (strategy, editable checklist, rivals, setup and style)

Status: **plan only, nothing implemented.** It records decisions taken with the author on 2026-10-13 and the
open questions, so the work can start without re-deriving them. Nothing in this file is "verified" until it is
marked with the evidence that verified it.

*Resumen en español al final.*

## 0. Principles (apply to every sub-phase)

1. **Numbers come from code, words from the model.** Consumption, laps to the end, pit lap, fuel to load,
   degradation, rival sector times: deterministic and tested. The LLM drives the conversation and explains;
   it never produces a number that was not handed to it.
2. **The model edits typed objects, not text.** Every change to the plan or the checklist is a validated
   operation (`add`, `remove`, `replace`, `reorder`, `skip`, `set_rule`). Invalid operations are refused with a
   reason, valid ones are logged and undoable.
3. **Measured before claimed.** Statements about the driver ("you struggle with oversteer") need a number, a
   sample size and a confidence; below the minimum sample the engineer says it does not know yet.
4. **Local first, degrade gracefully.** Without the network or with the free tier exhausted, the planner and the
   checklist keep working with fixed phrasing; only the conversation is lost.
5. **Rivals are people.** Other drivers' names never go in the repository, in logs that are committed, or in an
   LLM prompt unless the user opts in (default: "P4", "the car ahead").

## 1. Phase 8a: race plan (own data only)

Does not depend on any rival data. Needs phases 5 (chat) and 6 (voice) for the natural conversation, but the
planner and the checklist are useful from the terminal.

### 1.1 Inputs

| Input | Source | Status |
|---|---|---|
| Fuel per lap (mean, spread) | `LapRecord.fuel_used` of timed laps | measured, **spread to characterise** (traffic, lift and coast) |
| Race length (laps or time) | `number_of_laps`, `session_time_left`, `has_extra_lap` | laps verified, time + extra lap **to verify live** |
| Tank size | `static.max_fuel` | plausible |
| Pit window | `static.pit_window_start/end`, `mandatory_pit_done` | **unverified** (never seen non-zero) |
| Compound in use | `tyre_compound` per sample | seen as a string ("Rain"); **names per car to collect** |
| Degradation per compound | lap-time trend corrected for fuel + measured rubber wear | method exists in the sibling project; **to validate here** |
| Cost of a stop | pit-in lap and pit-out lap vs a flying lap (+ the stop itself) | **to measure** (needs a real stop) |
| League rules | the user, in the conversation | the game does not know them (private leagues vary) |

### 1.2 Planner

Pure functions on a `RaceContext` (laps, rules, measured profile) returning a `Plan` (stints: compound, laps,
fuel at start, pit lap, expected time) plus the assumptions it used. Rules it must support from day one:

* no stop, one stop (any tyres, "fuel only" included), one stop with two different compounds mandatory,
  free number of stops;
* a pit window and a minimum/maximum stint length;
* fuel margin (default one third of a lap plus the spread), never below the formation/extra-lap needs;
* "what if" variants (stop one lap earlier/later, different compound) ranked by expected time.

Output always states its assumptions ("assuming 2.31 L per lap, from 6 laps, spread 0.08") so the driver can
challenge a number.

### 1.3 The editable checklist

A practice session plan, shown in the terminal dashboard (and later a web view), that **ticks itself** from
telemetry.

```
item = { id, kind: stint | quali_sim | race_sim | setup_test | info,
         params: { compound?, fuel_l?, laps?, conditions? },
         status: pending | active | done | skipped | removed,
         source: planner | user | llm, reason, evidence? }
```

* **Auto-completion:** an item is done when the telemetry matches its parameters (n timed laps, on that
  compound, within a fuel tolerance at the start of the stint). Ambiguous cases are shown, not guessed.
* **LLM-driven edits**, by voice or text, always through operations:
  * "quita lo de mojado, está seco" → `remove(item)` with the reason stored;
  * "simula la quali con 15 L" → `add(quali_sim, fuel_l=15)`; "cambia ese stint a medio" → `replace`;
  * "salta el último" → `skip`.
* **Guards that tell, not forbid:** a rain compound while the surface is dry (or the opposite) is *flagged*
  when the planner proposes it and when the user adds it; the user's explicit order wins and is logged.
  Weather/surface signals to use: `surface_grip`, `road_temp`, `tyres_out`; **which of them actually indicates
  wet is to be verified live** (one recorded wet and one dry session).
* Every change is shown as a diff and can be undone ("deshaz").

### 1.4 The conversation

Slot filling with typed answers. The engine asks only what is missing and stores the answer as a rule:

```
mandatory_stop: none | one | n
stop_requires_tyres: true | false
compounds_distinct_required: true | false
window: [lap_from, lap_to] | null
```

The three flows described by the author are cases of this: "only fuel" (`stop_requires_tyres=false`),
"two compounds" (`compounds_distinct_required=true`), "free" (`mandatory_stop=none`). The LLM turns speech into
this object and the plan into words; the planner does the maths.

### 1.5 Acceptance

* Plans reproduce by hand on three recorded sessions (fuel, laps, pit lap) within a lap and half a litre.
* Each rule type has a test with a known answer; "what if" ordering is deterministic.
* The checklist ticks correctly on a recorded practice with two compounds and two fuel loads.
* Offline mode: every operation and every plan works with the LLM provider set to `mock`.

## 2. Phase 8b: rivals

### 2.1 Spike first (before any design)

A throw-away in-game script that dumps the state of **every car** to a file for one session. The author's
`D:` install has CSP (Lua) and Python apps (SimHub, CrewChief and others), so there are two ways to try it.
Questions the spike must answer, each with evidence:

1. Which fields exist per remote car (position, name, car, laps, best/last lap, sector splits, spline position,
   speed, pit flag, compound)?
2. Do they update for remote cars **online** (LFM and private servers, where the server is not the user's)?
3. Update rate and jitter; how a car's lap/sector can be timed from its spline position.
4. Whether anything about fuel or tyre state is visible (expected: no).
5. Overhead on the game and what happens in a replay or a server with 25+ cars.

The server UDP plugin is out: the user does not own the servers.

### 2.2 What it would give

* Pre-race brief: grid, the cars around the user, where each one is faster (sector / corner, from derived
  times with the phase 4 geometry), their pace over the qualifying stint, consistency.
* In race (later, optional): gap to the car ahead and behind, who is catching up, defence warnings.

### 2.3 Limits to state in the product

Qualifying pace is not race pace; fuel and tyre state of rivals are unknown; positions seen by the game are
the server's interpolation. The brief says "based on qualifying" and gives sample sizes.

### 2.4 Privacy

Names stay local. Outgoing prompts use anonymous labels by default. Free-tier model providers may use
inputs for improvement: the user is told before the first rival-related request.

## 3. Phase 8c: setup suggestions and driving-style analysis

### 3.1 Setup suggestions

* **Evidence first:** the balance (understeer/oversteer share), tyre temperature spread, brake behaviour and
  the corners where time is lost (phase 4) measured over enough laps. Deterministic rules produce a
  *hypothesis* ("rear axle loose in slow corners, 14 % of the time in corner exit");
  the model puts it into words and links it to a setup parameter.
* **Only real parameters:** the setup file of the car is read (Documents/Assetto Corsa/setups) and a
  suggestion may only name a parameter that exists in it, with its range and step. The sibling project already
  reads setups and has an advisor; it is reused by copy with a PROVENANCE entry.
* **A/B discipline:** every suggestion becomes a checklist item ("test +2 clicks rear ARB, 5 laps, same fuel"),
  and the outcome is compared fuel-corrected. A suggestion that did not help is remembered.
* The suggestions are labelled as hypotheses, never as certain.

### 3.2 Driving-style analysis

* A **driver profile** across sessions, built from measured events: counter-steer and slide events, throttle
  application on exit, brake release shape, steering smoothness, consistency by phase of the corner.
* Statements need a sample size and a stated confidence. Example target:
  "in the last 3 sessions, 68 % of your time lost on exit comes from corners where the rear steps out
  (n = 41 corners); try a stiffer rear anti-roll bar" and *not* a sweeping claim from one session.
* The profile is stored locally and can be wiped.

### 3.3 The learning loop: a ledger of suggestions and what happened

The language model does not learn between sessions (its weights never change). What improves is a **local
ledger** of everything that was suggested and what came out of it. The ledger does two jobs: it is handed to
the model as context ("tried before, on this car and track: ..."), and it **re-ranks** the deterministic
candidates so that what worked is proposed earlier and what failed is not repeated blindly.

**One record per suggestion** (a suggestion is a checklist item of kind `setup_test`, so it is already
measurable):

```
suggestion = { id, created, car, track, layout, conditions {air, road, compound, surface},
               hypothesis, evidence (the numbers that led to it), parameter, change {from, to},
               test_plan {laps, fuel}, status: proposed | tested | accepted | rejected | inconclusive,
               measured {lap_time_delta, consistency_delta, balance_delta, tyre_temp_spread_delta, n_laps,
                         fuel_corrected: true, confidence},
               feedback {feel: better | same | worse | unsure, comment, asked_at},
               verdict {agree: measured_and_feel_agree | measured_better_feel_worse | ..., note} }
```

**Two sources of truth, kept separate.**

* *Measured*: before/after over comparable laps (same compound, fuel-corrected, track evolution and
  tyre-temperature window noted). Reported with n and an interval; a gain smaller than the lap-to-lap spread
  is `inconclusive`, never "worked".
* *Pilot feedback*: asked by voice after the test stint ("¿cómo se siente?"), stored verbatim plus a
  normalised label. It is **not** averaged into the measurement.
* **Disagreement is data**: "faster on the clock but felt worse" and "felt better, no faster" are recorded as
  such. They change what is proposed next (e.g. a change that is quicker but unsettling is offered with that
  warning, or paired with a counter-measure) and they are shown to the driver, not hidden.

**How the ledger changes later suggestions**

1. **Ranking, deterministic**: candidates for the same symptom are ordered by their record in a matching
   context (same car; then same class of car; track type and temperature band as weaker matches). An effect
   estimate shrinks towards zero with few samples, so one lucky stint does not become a rule.
2. **No blind repeats**: a rejected change is not proposed again in the same context unless the evidence
   differs materially, and then the text says what happened last time.
3. **Context for the model**: the few most relevant records, as structured facts, so that the explanation can
   say "last time, +2 clicks rear ARB at Spa gave -0.21 s, felt more stable". The model may not add
   outcomes that are not in the ledger.
4. **Driver profile feeds back**: recurring symptoms (section 3.2) and the changes that addressed them are
   linked, so the profile can say which kind of change has historically suited this driver.

**Guards**

* Confounders are listed with each result (fuel, tyre warm-up, a new track surface, rain) and an
  inconclusive verdict is the default whenever the comparison is not like for like.
* A minimum number of comparable laps before a verdict (initially 5 per side, to be tuned on real data).
* No transfer between cars of a different class without saying so; same car on another track is a weaker
  match and is labelled as such.
* Local only, human-readable (JSON lines), exportable and wipeable by the user. It never leaves the machine
  except as the few records included in a prompt, and only with the anonymisation rules of section 0.
* Cold start: with an empty ledger the engineer says it has no history for this car yet.

**Acceptance**

* Replaying a recorded A/B pair produces the same verdict every time and states n and the confidence.
* A suggestion that was rejected is not proposed again in the same context in a scripted scenario.
* The disagreement cases (measured better / felt worse and the reverse) are produced and displayed.
* Deleting the ledger returns the engineer to the cold-start behaviour.

## 4. Open questions

1. Is the in-game Python app route enough, or does CSP's Lua give materially more? (spike)
2. How does the game report compound names and wet conditions on the author's cars? (record one wet and one dry)
3. What does `pit_window_*` / `mandatory_pit_done` look like in an LFM race or a private league that sets it?
4. How much does fuel per lap vary between practice and a race with traffic? (needs several races)
5. Which providers and quotas are acceptable for a multi-turn strategy chat on the free tier?
6. How many comparable laps does a setup A/B need on real data before a verdict is trustworthy, and how much does
   the lap-to-lap spread of the author vary by track? (calibrate the minimum of section 3.3 on recorded stints)
7. What is the simplest feedback capture that the driver will actually use mid-session? Decided: voice after the
   stint **and** buttons bindable to any input device (see [INPUT_BINDINGS.md](INPUT_BINDINGS.md)).

## 5. Decisions taken (2026-10-13)

* Main install is `D:` (has CSP). Races: private leagues on other servers and LFM, so no server plugin.
* Order: phase 7 as planned, then 8a, then 8b after the spike, then 8c.
* The checklist must be editable by the model through validated operations, with undo.
* Setup suggestions and style analysis are in scope (8c) with the evidence rules above.
* The style analysis and the suggestions keep a local ledger of what was suggested, what the telemetry measured
  and what the pilot felt, including when the two disagree, and use it to rank later suggestions (3.3).

---

## Resumen en español

* **8a, plan de carrera** (solo tus datos): planificador determinista (gasolina, vuelta de parada, compuestos,
  reglas de liga) y **checklist de práctica que se marca solo** con la telemetría. Puedes pedirle al modelo
  que lo cambie ("quita lo de mojado, está seco", "simula la quali con 15 L"); cada cambio es una operación
  validada, queda registrada y se puede deshacer. Si propone mojado en seco (o al revés) lo avisa; tu orden
  manda. La charla de estrategia rellena huecos con reglas tipadas ("¿hay parada obligatoria?").
* **8b, rivales:** primero un *spike* (script en el juego que vuelca el estado de todos los coches) para saber
  qué campos existen y si funcionan online. Después el resumen de pre-carrera: dónde es más rápido cada
  rival por sector o curva, con sus límites (ritmo de quali no es ritmo de carrera; no se ve su gasolina ni
  sus neumáticos). Los nombres no salen de tu máquina por defecto.
* **8c, setup y estilo:** sugerencias como **hipótesis** con evidencia (balance, temperaturas, curvas donde se
  pierde tiempo), solo sobre parámetros que existen en el archivo de setup del coche, convertidas en
  elementos del checklist para probarlas A/B. El análisis de estilo exige muestra suficiente y la nombra.
* **Aprendizaje entre sesiones:** el modelo no cambia, lo que mejora es un **registro local** de cada
  sugerencia, lo que midió la telemetría y lo que sentiste tú, incluidos los casos en que no coinciden
  ("más rápido pero peor sensación"). El registro reordena las sugerencias siguientes y se le entrega al modelo
  como contexto; si algo falló no se repite a ciegas, y un resultado sin muestra suficiente es "no concluyente".
* **Orden:** fase 7, luego 8a, 8b tras el spike, 8c.
