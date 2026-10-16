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
| AC Evo | shared memory with **different names**, `Localcevo_pmf_*` (three blocks; not `acpmf_*`), per third-party readers; official MoTeC logs since 0.6 | unknown | **layout partially undocumented** and early access: see 1.1 |
| iRacing | shared memory of the official SDK (memory-mapped file with a variable table and a YAML session string) | per-car arrays (lap distance, position, class, ...) | documented, 60 Hz; licence of the SDK to read before using its headers. **Tyre wear is only known at a pit stop** (author, 2026-10-15): between stops it is an estimate from the stops and the laps, see `PHASE8_PLAN.md` 3.5 |
| RaceRoom Racing Experience | shared memory `$R3E` | all drivers | public SDK; layout versioned |
| rFactor 2 | **third-party plugin** (rF2 Shared Memory Map) that must be installed and enabled | all vehicles (scoring) | the user has to set it up |
| Le Mans Ultimate | rFactor 2 engine: the same plugin or a native equivalent | all vehicles | which one the current build uses must be checked |
| Automobilista 2 | the Project CARS 2 shared memory (`$pcars2$`), enabled in the game options; also UDP | participants array | option in the settings |

### 1.1 AC Evo: what a web search turned up (2026-10-13, third-party, not tested here)

* The open-source overlay *Live Telemetry Evo* (Python, PySide6) reads three named shared-memory blocks,
  `Localcevo_pmf_*`, attached with `OpenFileMappingW` like the AC reader of this project. Its README says the
  struct layout "is still partially undocumented" and is seeded from the AC1 structures plus publicly
  confirmed AC Evo field names, with range clamping. Fields it lists: pedals, steering, g-forces, per-wheel tyre
  temperatures (core and faces), pressure, load, wear, camber, suspension, brake temperature, compound, damage,
  fuel and some engine readouts.
* A Rust crate (`kerb`) also lists AC Evo shared memory (physics and graphics: inputs, tyres, suspension,
  lap times, position, flags, fuel, pit state).
* AC1, ACC and AC Rally are reported to share the `acpmf_*` names; **AC Evo does not**.
* The Steam page reportedly says that **official MoTeC telemetry export** arrived in 0.6, written to
  `Saved Games\ACE\MoTeC\<car>\<track>` (reported by a setup-guide post, not verified). That would be a
  second, file-based route, comparable to the `.ld` files this project already reads from ACTI.
* Not installed on the author's PC for now (uninstalled to save disk space), so none of the above can be
  checked yet. It is the last step of the order; `pit-wall probe` will answer it once the game is back.

Consequences: the AC Evo adapter would be a *new layout* of the same kind of reader (not a flag on the AC
one), its fields would be verified one by one like the AC table, and the third-party code is read as
documentation only (licences checked before anything is reused; own structures, as always).

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

## 5. What the author has, and the resulting order

Availability on 2026-10-13 (author's answers):

| Sim | Owned | Can be tested now | Constraint |
|---|---|---|---|
| ACC | yes | yes | runs on the author's PC |
| Automobilista 2 | yes (AMS1 not needed) | yes | the one the author uses most after AC |
| RaceRoom | yes (free) | yes | |
| rFactor 2 | yes, never used | yes, after installing the shared memory plugin | the author does not know the plugin yet: setup is part of the work |
| AC Evo | owned, **not installed** (uninstalled to free disk space; runs at 20-30 fps at 720p) | no | install it when this step comes; interface unverified (see 1.1) |
| iRacing | subscription inactive | when an account is available | a borrowed or reactivated subscription |
| Le Mans Ultimate | no | when an account is available | not owned |
| AC Rally | no | not planned | see section 7 |

**On a slow PC.** The physics of these sims runs at its own fixed rate, so a low frame rate does not make the
telemetry wrong; what matters is that a session loads and the car can be driven for a few minutes. A
minimum-quality, small-window session is enough to record and probe. A second computer is only needed if the
game does not run at all.

**On borrowing accounts.** For a Steam game such as Le Mans Ultimate the supported way is Steam Family Sharing
(the friend authorises the library on the author's machine); sharing a password breaks Steam's and
iRacing's terms of service. For iRacing, a one-month reactivation or the free trial covers a test campaign.

Proposed order, driven by what can be tested first and what is most valuable:

| Step | Sim | Reason | Needs |
|---|---|---|---|
| 0 | refactor + `pit-wall probe` | interface, capabilities, per-sim verification table; AC unchanged | none |
| 1 | ACC | closest to AC, same map names, official documentation | 1-2 recorded sessions |
| 2 | Automobilista 2 | owned, played, one shared memory option | enable it in the game options |
| 3 | RaceRoom | owned, public SDK | 1 recorded session |
| 4 | rFactor 2, then Le Mans Ultimate | one plugin route for both | plugin installed; LMU when an account exists |
| 5 | iRacing | well documented, large user base | an active account for a test campaign |
| 6 | AC Evo | early access, layout partly undocumented; the live route and the MoTeC export route both exist on paper | `pit-wall probe` earlier; implement when the layout is confirmed against the game |

Per simulator the work is the same and ends with the same artefacts: a `docs/<SIM>_TELEMETRY.md` with the
unit table, recorded sessions replayable with `pit-wall run --file`, the detection evidence, and a regression
test on a trimmed recording (never redistributing anything from the game itself).

### 5.1 `pit-wall probe`: learn before implementing

A small read-only command to run **with each sim open**, before any adapter exists: it lists the known
executables that are running, tries to open the known shared-memory objects of every sim (existence, size,
first bytes, version strings) and prints a report that can be pasted back. It also works as the detection
evidence of section 3. It writes nothing and never injects into a game.

The AC Evo question (is there telemetry yet, and in which form?) is answered by running this once with the
game in a session; no implementation guess is made before that.

## 6. Risks

* **Testing cost**: each sim needs the real game, a car and a few sessions. Without hardware access an
  adapter stays "implemented, untested", labelled as such in the README.
* **Terms and anti-cheat**: reading shared memory or a documented SDK is the normal route, but online
  services (iRacing, ranked leagues) have rules; the tool only reads, never writes or injects.
* **Interface drift**: early-access titles change their layout between builds; adapters check a version
  field and refuse to run on an unknown one instead of decoding garbage.
* **Scope creep**: supporting seven sims badly is worse than three well. The order above can stop at any step.

## 7. Questions for the author, and what is not planned

Answered 2026-10-13: owned sims and constraints are in section 5; AC Evo is on Steam (reported as 0.10) and
its telemetry is unknown; rFactor 2 is installed but never used (no plugin yet); AC Rally is not planned.
Still open: which sim the author most wants to race with the engineer first (it can move up the order).

Not planned:

* **AC Rally**: a different discipline whose game already includes a co-driver; the value of a circuit
  engineer is low and its interface is untested. Revisit only if asked.
* **Automobilista 1**: superseded by AMS2 for the author.

## 9. Notes from the author's conversation of 2026-10-15

**The order the author stated** (AC first, then ACC, iRacing and RaceRoom, rFactor 2 / LMU / AC Evo last)
differs from the order proposed in section 5, which was driven by what could be tested first (AMS2 and ACC
before iRacing because iRacing needs an active subscription to test). Both are valid: the integration order
follows what the author can run on a given day, and **the adapter interface does not depend on it**. The
author's list adds Assetto Corsa Competizione, AC Evo, RaceRoom, iRacing, rFactor 2, Automobilista 2 and Le
Mans Ultimate as the sims the tool should serve; AC Rally and AMS1 stay out.

**A common telemetry type.** Every adapter fills the same `Snapshot`; the engine, the rules, the chat and the
voice read only that. A new simulator is a new adapter plus its `detect()`; nothing in the rules changes. This
is what section 3 of this plan already describes, and it is kept as the guard against sim-specific code
leaking into the analysis (the corner geometry of phase 4 is the known exception: it needs a per-sim source).

**A remote "open pit wall" viewer (idea).** A local web page, served by the program, with the lap times, the
delta, the fuel and the tyres, so a friend or a coach can watch from another device while the driver races.

* Hosting stays on the driver's PC: no server of the project, no cost, no accounts.
* Another device reaches it through the user's own private network (Tailscale or ZeroTier, which are free
  and open alternatives to Hamachi) or the same LAN; a direct peer-to-peer link (WebRTC) is possible but a
  second project in itself.
* It reuses the settings page of phase 11 (a local web server already planned) and the read-only parts of the
  fact sheet; it exposes **no controls** and binds to the local network only, with a token.
* Risks: exposing telemetry to a network (token, local-only default, no port forwarding advice), and the CPU
  cost of a web server next to the game (measure it like the speech recogniser).

Proposed as phase 12; nothing is built.

---

## Resumen en español

La arquitectura ya separa la fuente de telemetría del resto, pero el `Snapshot` nació del AC original. Antes de
añadir otro simulador hace falta: una interfaz `SimAdapter` con **capacidades** declaradas (qué campos tiene
cada sim), una tabla de unidades verificada **por simulador**, estructuras propias por sim y la detección
automática (proceso en ejecución + memoria compartida con cabecera válida; si hay duda, manda la
configuración). Lo que no se traslada tal cual es la geometría de pista de la Fase 4, que usa `fast_lane.ai`
(solo AC): en los demás se aprenderá de tus propias vueltas y se medirá su precisión igual que la de AC. Los
rivales, en cambio, son **más fáciles** en casi todos los demás sims. Orden propuesto según lo que tienes: refactor y
`pit-wall probe`, ACC, AMS2, RaceRoom, rFactor 2 (y LMU cuando haya cuenta), iRacing cuando haya cuenta activa y
AC Evo al final. `pit-wall probe` es un comando de solo lectura que se ejecuta con cada sim abierto para saber
qué interfaz publica antes de escribir ningún adaptador. AC Rally y Automobilista 1 quedan fuera. Cada sim necesita pruebas con el juego real; hasta
entonces, un adaptador queda como "implementado, sin probar".
