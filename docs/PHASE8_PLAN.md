# Phase 8 plan: race preparation (strategy, editable checklist, rivals, setup and style)

Status: **approved on 2026-10-17; 8a implemented in part** (the fuel planner, the stop plan with the stated rules, the editable
checkable practice checklist with undo, in `analysis/plan.py`, `analysis/checklist.py`, `analysis/checklist_edit.py`; the "what if" ranking, the
degradation per compound and the measured pit loss still wait for data). 8b is served by the CSP bridge (the spike's questions are
answered in `docs/AC_APPS_NOTES.md`); in the first online race (2026-10-18) the gaps, names and positions held, and the chat answers the start position, the positions gained and the laps left in a timed race. 8c and the strategy engines of 4b (pit window with traffic, weather) are still plans. It records decisions taken with the author on 2026-10-13 and the
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

### 3.4 Tyre wear by corner and how to reduce it (approved 2026-10-14; analysis implemented, voice use gated)

Target conversation:

> Driver: "¿Cómo tengo los neumáticos delanteros?"
> Engineer: "Están muy desgastados: estás bloqueando mucho en las frenadas de las curvas 5 y 11 y metiendo
> demasiado volante en la 7."

**What the data allows (honest limits).** The measured wear (rubber grip, already used for the degradation
trend) is a value **per tyre and per lap**, not per corner. Attributing it to corners is therefore an
**estimate** from a proxy, and must be said as such ("lo que más castiga..."), never as a measurement:

| Cause | Proxy per corner, from channels already read | Advice (rule, not LLM) |
|---|---|---|
| Lock-ups under braking (front) | front wheel slip while brake > threshold, time and peak | "suelta un poco el freno antes de girar / frena más recto" |
| Too much steering (scrubbing, understeer) | front slip angle or steering angle above the reference lap at the same speed and lateral g | "menos volante: frena un poco antes y entra más lento" |
| Wheelspin on exit (rear) | rear slip while throttle > threshold | "acelera más progresivo en la salida" |
| Overheating of one tyre | temperature rise per corner and per axle side | "esa curva carga el lado X: revisa la línea o la presión" |

* **Per corner score** = integral of (slip x load) over the corner window, normalised by the lap's total; the
  corners are those of the geometry layer, so the names are the real ones.
* **Validation before use**: the sum of the proxy over a stint must correlate with the measured wear drop per
  lap; if it does not on the author's recordings, the feature is not enabled (it would be inventing).
* **Same rules as the rest**: numbers come from code; the model only phrases; the grounding check applies to
  the corner numbers and to the percentages; a minimum sample (for example 5 valid laps on the same compound)
  and the incident laps excluded, as the degradation trend already does.
* **Output**: a ranked list ("curvas 5, 7 y 11: el 48 % de la carga de los delanteros") plus the dominant cause
  of each; on request ("¿en qué curvas gasto más los neumáticos?") and, optionally, in the stint summary.
* **Needs**: a recording of at least 8 laps on one compound with the tyre wear channel moving (the author's
  existing sessions may be enough to try), and a check of which wheel-slip and steering channels each car
  fills (same caution as with brake temperature).
* **Risks**: the proxy may not follow the real wear model of the car; a cause diagnosed from slip alone can
  be wrong when the car is a different setup; advice must stay conservative and be learnt against the ledger
  of section 3.3.

**Status (2026-10-14).** `pit-wall tyre-corners FILE` computes the index per corner, axle and phase (brake /
turn / throttle) from `wheel_slip x wheel_load`, leaves out incident laps (load above twice the median) and
runs the validation gate (`analysis/tyre_corners.py`). On the author's three recordings (MX-5 Cup, Magione) the
gate **refuses**: the measured wear moved 0.25 points over 9 laps (the tyres stop wearing after lap 4: the
channel is flat), so there is nothing to validate against. The ranking is printed as an estimate and the
engineer does **not** say it. It is enabled by one recording with a car that really wears the tyres
(`docs/SESION_MEDICION.md`, block 3). The proxy units are not slip ratio: `wheel_slip` in this car's data is
0.04 braking and 0.12 coasting at the median, so thresholds are relative (shares), never absolute.

**Update (2026-10-15): the game's wear channel does not move on the author's setup, but the game's own
tyre app does.** On the Shelby Cobra 427 SC at 200 % tyre wear the shared-memory `tyre_wear` stayed at 100
(0.17 points over 13 laps) while the tyre app showed 83 / 88 / 70 / 75 % after 15 laps. So the check uses a
**reference read off the app**: `pit-wall tyre-corners FILE --reference 83,88,70,75 [--start-wear
100,100,100,100]`. On `sesion3` (13 complete laps, one incident lap left out) the index per wheel is
20.7 / 13.7 / 36.4 / 29.1 % against 20.2 / 14.3 / 35.7 / 29.8 % of the measured drop: correlation 1.00 and the
same order of the four wheels. **Limits to keep in mind:** four points only (a chance match of the order has a
probability of 1 in 24), one car, one circuit, and it validates the distribution between wheels, not corner by
corner. A second independent stint (`sesion4`) is the next check before the corner advice is voiced.

**Second stint (2026-10-15, `sesion4`, the game's autopilot, 9 laps, app 83/87/69/74 -> 71/78/53/60):** index
per wheel against the measured drop: correlation 0.99 and the same order of the four wheels, with a
different driver (the autopilot). Two sessions now agree at wheel level. The corner rankings differ between
the human and the autopilot (front: 6, 2, 1 for the author; 1, 2, 8, 3 for the autopilot), as they should:
they describe the driving, not the circuit. Still not verified: the split by corner itself (the app gives
no per-corner wear); the wheel-level agreement is the evidence that the index is a sound proxy.

### 3.5 Tyre wear: what each simulator lets the engineer see (added 2026-10-15, from the author)

Not every simulator shows the wear live. **In iRacing the wear of the tyres is only known when the car stops in
the pits**: during the stint the telemetry does not carry it, and the driver reads it at the stop. So the wear
is a *source-dependent* quantity and the engineer must say which kind it is talking about:

| `wear_source` | Where it comes from | How it is spoken |
|---|---|---|
| `csp` / `shm` | read live every sample (Assetto Corsa with the CSP bridge, ACC) | "el trasero derecho está al 62 %" |
| `pit` | read at the last stop; nothing in between (iRacing) | "en la última parada estaban al 71 %; desde entonces van 6 vueltas" |
| `estimate` | none readable: derived from the stops and the laps | "estimo que andan por el 55 %, es una estimación" |

The estimate is a simple model, honest about its error: wear per lap per compound and circuit, fitted on the
stints already read at stops (wear at the stop / laps of the stint), applied to the laps run since the last
reading, with a range that widens with the laps since that reading. It is a different number from the corner
index of 3.4 (that one says *where*, this one says *how much*), and it would feed the strategy engine (4b) when no
live value exists. Nothing in 3.4 needs it for Assetto Corsa now that the bridge gives the real value.

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

## 4b. Phase 8d: the strategy engine (added 2026-10-15, from the author's notes)

Three small engines whose inputs exist in every simulator the project targets, so they stay sim-agnostic
(they read the common snapshot, never a game's own fields). They come **after** 8a (own data) and, for the
parts that need other cars, after 8b (rivals).

| Engine | Question | Inputs | Needs rivals | Output (always from numbers) |
|---|---|---|---|---|
| **Fuel** | can I finish, and how much must I save per lap? | fuel, laps or time left, the weighted consumption of the last laps (more weight on the recent ones) | no | "ahorra 0,15 L por vuelta para llegar" / "margen para apretar"; per-corner lift-and-coast only when the corner data supports it |
| **Pit window and traffic** | if I stop now, where do I come out? | pit-lane loss per circuit (**measured from the author's own stops** and kept; a setting until then), gaps ahead and behind | yes (8b) | "si paramos ahora salimos P6 detrás del Porsche; dos vueltas más y salimos limpios" |
| **Track and weather** | when do wets beat slicks? | wetness / grip signals (**which fields exist per sim is unverified**: `surface_grip`, rain intensity), lap times on each tyre | no, but needs a sim with dynamic weather | "con la pista a 40 % de humedad los mojados son 2 s más rápidos: box" |

* **Simulation**: a Monte Carlo run of the rest of the race (pit lap, tyre degradation from the measured
  trend, traffic from the gaps) only after the three engines above are verified on recorded data; it is a
  decision aid and says its spread, not a single promise.
* **Safety car is deliberately not a dependency.** It is not native in several of the sims (iRacing road
  racing, AC without a league plugin) and where there is a yellow or a full-course yellow its behaviour
  depends on the server. Strategy messages never assume one; a yellow-flag rule may add a note where the
  flag is verified.
### 4b.1 Rain near the end: is it worth stopping? (added 2026-10-15, from the author)

Rain that starts with few laps or little time left must not trigger the textbook "box for wets". The
simulation (and the plain rules before it exists) has to weigh **what the stop costs against what the new
tyres give back over what is left**, and only override that when staying out becomes unsafe.

* **The comparison.** Stay out: the time lost per lap on slicks in the wet (growing if the rain grows).
  Stop: the pit-lane loss (measured from the author's own stops, per circuit) plus the laps still to be driven
  at the lower speed until the tyres are up to temperature, then the gain per lap on the right tyre for the
  laps that remain. It pays only if `laps_left x gain_per_lap > pit_loss + margin`, and the margin carries the
  uncertainty, so with 2 or 3 laps to go the answer is almost always "stay out", said with the numbers
  ("quedan 2 vueltas: parar nos cuesta unos 22 s y recuperaríamos unos 3 s: nos quedamos").
* **Scenarios, not a point guess.** The Monte Carlo run draws the rain over the remaining laps (it stays, it
  grows, it stops), the lap-time loss on slicks, the gain on wets and the traffic on rejoining, and reports
  the **chance that stopping beats staying** and the places at stake. The engineer says the recommendation and
  how sure it is; below a stated confidence it says "es una moneda al aire" and leaves it to the driver.
* **The override: a downpour.** If the conditions make slicks unsafe, the stop is urgent whatever the
  arithmetic says. "Unsafe" is **measured, not declared**: the lap-time loss against the dry reference beyond
  a limit, a rising count of slides and off-track events (the incident detection already exists), a falling
  grip estimate, and the sim's own rain or wetness field where one exists. All thresholds are settings.
* **Say what is not known.** The gain per lap on wets is unknown until the car has run them: a prior with a
  wide spread (from the crossover model) is used and labelled as an estimate; once the driver has done laps on
  both tyres the measured difference replaces it.
* **The driver decides.** The message is advice with its reason; "sigo en pista" is respected and not repeated
  every lap, and the quiet mode applies (an urgent downpour warning is the exception, like a critical alert).
* **What it needs before it can be built**: a recording of a race or practice with a rain start and a stop
  (to check the measured signals and the lap-time loss), the author's pit-lane loss at a few circuits, and
  which fields each simulator gives for rain and track wetness (`docs/MULTI_SIM_PLAN.md`, the `probe`).

### 4b.2 When the wet pace at this circuit is not known (added 2026-10-15, from the author)

At many circuits no session has been run in the rain, so there is no lap time on wets to compare with. The
engineer must not pretend to know it. The sources, in the order they are trusted:

1. **Own laps on wets at this circuit and car** (the local memory of earlier sessions): measured, used as is.
2. **Other cars that have already stopped for wets** ("wait for a brave one"): once a rival comes out on
   wet tyres, the gap it makes to the cars still on slicks, lap after lap, is the best live measurement of
   the gain. It needs the rivals feed (8b) **and** the tyre compound of each car, which each simulator
   exposes differently (unverified: to be checked with the `probe`). Without it, only the gaps are visible,
   and the engineer says what is observed ("el coche de delante salió con mojados y nos saca 2 s por
   vuelta") and does not claim a cause.
3. **A calibration table learned from recordings** (below): the typical ratio between a wet and a dry lap by
   car class and circuit type, with its spread. It is a **prior**, labelled as such, and it is replaced by 1 or
   2 as soon as they exist.
4. **Nothing**: with no data at all the engineer says so, keeps the rule of 4b.1 on the safe side (the
   downpour override still works, because it is measured on the car's own behaviour) and does not
   recommend a stop for lap-time reasons.

The Monte Carlo treats the unknown gain as a **distribution**, so an unknown circuit gives a wide answer and
the report says how much of the uncertainty is the missing wet pace.

**Calibration sessions** (the author offers to run them in the simulator and record them):

| Step | What | Recorded as |
|---|---|---|
| 1 | a car and a circuit; 3 clean dry laps | the dry reference |
| 2 | the same, with a steady light rain, on slicks, 3 laps | the loss on slicks (light) |
| 3 | the same rain, on the wet or intermediate tyre, 3 laps (warm-up lap not counted) | the gain on wets (light) |
| 4 | repeat 2 and 3 with a heavy rain | the same for a downpour; the point where slicks become unsafe |

`pit-wall record` captures each stint; a small analysis writes one row per (car class, circuit, rain level)
into a local calibration file. The recordings are not committed (they are the author's own and large); the
resulting table can be, as numbers, once it has enough rows to be useful.

* **Rules that stay**: numbers from code, the model only phrases, "no tengo ese dato" when the input is
  missing (no rivals, no wetness field), and every estimate labelled as such.
* **Open**: which sims expose the wetness field and the cars' gaps (the 9 and 10 phases of
  `docs/MULTI_SIM_PLAN.md` answer it); the pit-lane loss needs the author's own pit stops to be measured.

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
