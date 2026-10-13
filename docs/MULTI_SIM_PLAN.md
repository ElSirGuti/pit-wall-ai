# Multi-simulator support (plan)

Status: **plan only.** Goal stated by the author on 2026-10-13: work with the popular simulators (ACC, AC Evo,
iRacing, RaceRoom, Le Mans Ultimate, rFactor 2, Automobilista 2, besides the original Assetto Corsa) and detect
the running one automatically. Every statement about another simulator's interface below comes from general
knowledge, **not** from this project's tests, and is marked *to verify*: the unit table of the README is
the standard, and nothing is called supported until each simulator has been tested with the real game.

*Resumen en español al final.*

## 1. What the interfaces probably look like (to verify per sim)

| Simulator | Telemetry route | Rivals | Notes (all *to verify*) |
|---|---|---|---|
| Assetto Corsa (original) | shared memory `acpmf_*`, **done** | not exposed (only `num_cars`) | verified here |
| Assetto Corsa Competizione | shared memory with the same map names but a **different, larger layout**; UDP broadcasting API | all cars in the graphics page and the broadcasting API | has an official document; the `acVersion`/`smVersion` strings tell it apart from AC |
| AC Evo | unknown; early access, the interface may change | unknown | first thing to check is whether the `acpmf_*` maps exist |
| iRacing | shared memory of the official SDK (memory-mapped file with a variable table and a YAML session string) | per-car arrays (lap distance, position, class, ...) | documented, 60 Hz; licence of the SDK to read before using its headers |
| RaceRoom Racing Experience | shared memory `$R3E` | all drivers | public SDK; layout versioned |
| rFactor 2 | **third-party plugin** (rF2 Shared Memory Map) that must be installed and enabled | all vehicles (scoring) | the user has to set it up |
| Le Mans Ultimate | rFactor 2 engine: the same plugin or a native equivalent | all vehicles | which one the current build uses must be checked |
| Automobilista 2 | the Project CARS 2 shared memory (`$pcars2$`), enabled in the game options; also UDP | participants array | option in the settings |

## 2. What has to change in the code

The project is already built around `TelemetrySource` -> `Snapshot`. The Snapshot, however, was shaped by
the original AC (gear = raw + 1, steering as a fraction of lock, session codes, `normalized_pos`). Work needed
before a second simulator is added:

1. **A `SimAdapter` interface** next to `TelemetrySource`: `name`, `detect()`, `open()`, `read() -> Snapshot`,
   `capabilities`, `static()`. The AC adapter stays as it is and becomes the first implementation.
2. **Capabilities per adapter** (`has_tyre_temps_per_layer`, `has_brake_temps`, `has_other_cars`,
   `has_fuel`, `has_flags`, `steering_in_degrees`, ...). Rules and analysis check them and stay silent
   when a field is absent, exactly as the brake-temperature rule already does for sensors that do not move.
3. **Normalised units and conventions documented per adapter**, each with its own verification table
   (same statuses: verified / plausible / undetermined / unreliable). No field of a new sim is trusted
   because the AC one was.
4. **Own struct definitions per sim**, never vendor headers copied verbatim (same rule as for AC: PROVENANCE
   entry per adapter and a check of each SDK's licence).
5. **Session kinds mapped per sim** to the existing `practice | qualify | race | hotlap`, with
   `unknown` as an honest fallback, so the policies keep working.
6. **Track identity independent of the AC folder name** (sim + track + layout + length); the AI-line file is
   AC specific (see section 4).

## 3. Automatic detection

A `detect()` per adapter, tried in order, cheap and side-effect free:

1. Which known **process** is running (a table of executable names per sim; *to verify* each name).
2. Whether the **shared memory object** that sim publishes exists and has a sane header (the AC pages carry
   version strings; ACC is told apart from AC by them; iRacing by its status flag; rF2 by the plugin's maps).
3. If more than one matches (two sims running) or none: the explicit config `sim: ac | acc | iracing | ...`
   wins, and otherwise the tool waits and says what it is waiting for.

The detection result is logged with the evidence that decided it, and `pit-wall run --sim X` overrides it.

## 4. What does not transfer as it is

* **Phase 4 geometry** reads `fast_lane.ai`, which only AC has. The corner detection itself works on a
  curvature profile and does not care where the polyline came from, so the other sims need a **track
  geometry learned from the user's own laps** (position or heading plus lap distance), checked against the
  AC result on Magione/Spa/Imola where both exist. Accuracy of the learned geometry has to be measured
  (same alignment test as TRACK_GEOMETRY.md) before the braking hint is enabled for a sim.
* **Rival data (phase 8b)** is the opposite: it is *easier* in most of these sims, which expose all cars
  natively. Original AC is the hard case, which is why the spike exists.
* **Tyre and fuel models** differ (wear, temperatures, compounds); the strategy planner must read what the
  adapter says it has, and the degradation method must be validated again per sim.
* **Voice and chat** are independent of the sim.

## 5. Order (proposal) and what each step needs

| Step | Sim | Why this order | Needs from the author |
|---|---|---|---|
| 0 | refactor | interface + capabilities + per-sim verification table, AC unchanged | none |
| 1 | ACC | closest to AC, official documentation, same map names | the game, 1-2 sessions recorded |
| 2 | iRacing | well documented SDK, large user base | the game and a subscription session |
| 3 | rFactor 2 / Le Mans Ultimate | one plugin route for both | plugin installed, sessions in both |
| 4 | Automobilista 2 | shared memory option | the game |
| 5 | RaceRoom | public SDK | the game |
| 6 | AC Evo | interface still moving | the game; revisit when it is stable |

Per simulator the work is the same and ends with the same artefacts: a `docs/<SIM>_TELEMETRY.md` with the
unit table, recorded sessions replayable with `pit-wall run --file`, the detection evidence, and a regression
test on a trimmed recording (never redistributing anything from the game itself).

## 6. Risks

* **Testing cost**: each sim needs the real game, a car and a few sessions. Without hardware access an
  adapter stays "implemented, untested", labelled as such in the README.
* **Terms and anti-cheat**: reading shared memory or a documented SDK is the normal route, but online
  services (iRacing, ranked leagues) have rules; the tool only reads, never writes or injects.
* **Interface drift**: early-access titles change their layout between builds; adapters check a version
  field and refuse to run on an unknown one instead of decoding garbage.
* **Scope creep**: supporting seven sims badly is worse than three well. The order above can stop at any step.

## 7. Questions for the author

1. Which of these simulators do you own and actually play? (decides the order and what can be tested)
2. Is AC Evo installed, and on which platform/version?
3. Do you run rFactor 2 / LMU with the shared memory plugin already?
4. Any sim you would drop from the list?

---

## Resumen en español

La arquitectura ya separa la fuente de telemetría del resto, pero el `Snapshot` nació del AC original. Antes de
añadir otro simulador hace falta: una interfaz `SimAdapter` con **capacidades** declaradas (qué campos tiene
cada sim), una tabla de unidades verificada **por simulador**, estructuras propias por sim y la detección
automática (proceso en ejecución + memoria compartida con cabecera válida; si hay duda, manda la
configuración). Lo que no se traslada tal cual es la geometría de pista de la Fase 4, que usa `fast_lane.ai`
(solo AC): en los demás se aprenderá de tus propias vueltas y se medirá su precisión igual que la de AC. Los
rivales, en cambio, son **más fáciles** en casi todos los demás sims. Orden propuesto: refactor, ACC, iRacing,
rFactor 2 / LMU, AMS2, RaceRoom y AC Evo al final. Cada sim necesita pruebas con el juego real; hasta
entonces, un adaptador queda como "implementado, sin probar".
