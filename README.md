# pit-wall-ai

**A real-time AI race engineer for Assetto Corsa.** It runs in the background while you drive, reads the game's live
telemetry, warns you about what matters (tyres, brakes, fuel, pace, braking points) and, in later phases, answers
when you talk to it. Rule-based alerts are local and instant; a language model (Gemini's free API, swappable) is
only used for the conversation.

*Español: [README.es.md](README.es.md) · Deutsch: [README.de.md](README.de.md)*

> **Status: usable for the original Assetto Corsa and tested live in practice, qualifying and a race; what is pending is
> mostly seeing the newest pieces in the game.** Live telemetry, session and lap tracking, recording and replay, a
> rule-based engineer with a local Windows voice, per-corner braking analysis against your best lap, tyre wear by corner,
> a text and voice chat (local answers first, a cloud model only when you allow it, other drivers' names never sent),
> three languages (Spanish, English, German), spoken congratulations on the pole and the result, a memory of what was
> said, a Custom Shaders Patch bridge (real tyre wear, brake temperatures, the other cars with their gaps in seconds,
> flags), the post-session debrief, a fuel and stop planner with an editable practice checklist, and a team-radio voice
> effect with Piper and local zero-shot voices. See the [roadmap](#roadmap).

<!-- DEMO: add a GIF or video here (terminal dashboard now, voice demo later) -->
<p align="center"><em>Demo placeholder: a GIF of the dashboard will go here.</em></p>

## Architecture

```mermaid
flowchart LR
    subgraph Game["Assetto Corsa (Windows)"]
        SHM[("Shared memory<br/>physics / graphics / static")]
    end
    LOG[("Recorded session<br/>.ld / MoTeC CSV")]

    subgraph Telemetry["telemetry/ (phase 1)"]
        SRC["AC source (read-only)<br/>Replay source"]
        POLL["Poller thread<br/>30-60 Hz, never blocks"]
        BUF[("Ring buffer<br/>last N seconds")]
    end
    SHM --> SRC
    LOG --> SRC
    SRC --> POLL --> BUF
    POLL -- "bounded queues<br/>(drop oldest)" --> SUBS

    subgraph Later["next phases"]
        SUBS["session/ and analysis/<br/>laps, corners, tyres, brakes, delta"]
        RULES["engineer/ rules<br/>priorities + cooldown"]
        LLM["engineer/ LLM<br/>Gemini behind an interface<br/>adaptive rate limit"]
        VOICE["voice/<br/>push-to-talk, STT, TTS queue"]
    end
    SUBS --> RULES --> VOICE
    SUBS --> LLM --> VOICE
```

The telemetry loop only reads a source and appends to the buffer. Everything slow (network, audio, analysis)
lives in other threads and receives samples through bounded queues: a slow consumer loses its oldest samples
instead of delaying the loop.

## What phase 1 gives you

* **`telemetry/`**: typed immutable `Snapshot`; `ACSharedMemorySource` (Windows, read-only `OpenFileMappingW`,
  torn-read retry, game-exit detection); `ReplaySource` for `.ld` and MoTeC CSV logs (real-time clock, speed
  multiplier, loop, start offset); `RingBuffer`; `TelemetryPoller`.
* **Dashboard** in the terminal (`pit-wall run`) and **`pit-wall dump-shm`** to inspect every field and confirm units.
* **Config** in YAML + `.env`, validated (a typo in the YAML is an error), and **structured logging** (key=value or JSON lines).
* **LLM foundations** ready for phase 5: provider interface with a registry (an offline `mock` ships now), an
  **adaptive rate limiter** (learns from HTTP 429 / `Retry-After`, optional daily budget), a short cache and graceful degradation.

## Install

Requires Windows for the live game (replay works anywhere) and Python 3.11+.

```powershell
git clone https://github.com/ElSirGuti/pit-wall-ai.git
cd pit-wall-ai
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env     # only needed from phase 5 (Gemini key)
```

## Use

```powershell
# Live, with Assetto Corsa running
pit-wall run

# Without the game: replay one of your own ACTI logs at 4x speed from second 100
pit-wall run --source replay --file C:\path\to\session.ld --speed 4 --start 100

# One status line per second (no dashboard)
pit-wall run --plain

# Record a real session (the game's own fields) and replay it later
pit-wall record --out race.pwrec.gz
pit-wall run --file race.pwrec.gz --speed 4

# Phase 4: numbered corners of a track from the game's racing line, and every lap against your best lap
pit-wall corners --file race.pwrec.gz
pit-wall corners --file race.pwrec.gz --laps
pit-wall corners --track spa --length 6947

# Show every field the game publishes (and, with --watch, the range of each one)
pit-wall dump-shm
pit-wall dump-shm --watch 60 --json report.json

# The engineer: speak the alerts (Windows voice; pip install -e ".[voice]")
pit-wall run --voice                 # Spanish, Sabina if installed
pit-wall run --voice --language en
pit-wall run --mute                  # same rules, printed instead of spoken
pit-wall tyre-corners race.pwrec.gz   # which corners work the tyres hardest (estimate, validated or not)
pit-wall say "Box this lap"          # test the voice and see its latency

# Talk to the engineer (push-to-talk): see docs/VOICE.md (German: docs/VOICE.de.md)
pit-wall devices                     # microphones and outputs
pit-wall voices --test               # hear every installed voice
pit-wall voice                       # live conversation (button + microphone)
pit-wall stt-test --device both      # speech recognition: speed and accuracy, CPU vs GPU
pit-wall rules                       # active rules, thresholds and per-session policies
```

See [docs/ENGINEER.md](docs/ENGINEER.md) for how a message is decided, the rules and the measured latency, and
[docs/TRACK_GEOMETRY.md](docs/TRACK_GEOMETRY.md) for the corner geometry, the braking comparison, the spoken hint
and how the alignment with the game's lap position was verified (error below 0.5 m on Magione, Imola, Spa).

```bash
# Phase 5: the text chat (local answers always; the cloud model only if you turn it on, see docs/LLM.md)
pit-wall ask "how much fuel is left?" --file race.pwrec.gz
pit-wall chat                       # live game; type exit to finish
pit-wall llm-check --allow-cloud    # test the cloud model
```

One chat, any service: the plan for other providers and the final `.exe` is in
[docs/PROVIDERS_PLAN.md](docs/PROVIDERS_PLAN.md).

```powershell
# The CSP bridge: the real tyre wear, brake temperatures, flags, penalty, damage and the other cars (gaps in seconds)
pit-wall bridge-install              # copies the Lua app into the game (Assetto Corsa with Custom Shaders Patch)
pit-wall bridge-test                 # shows what arrives while a session runs (see tools/csp_bridge/README.md)

# Phase 7: the debrief of a session (report, facts, a short spoken summary, a page with charts)
pit-wall debrief race.pwrec.gz --summary
pit-wall debrief race.pwrec.gz --out report.md --save   # also report.html and report.json; keeps the session for "against last time"

# Phase 8a: the race planner and the practice checklist
pit-wall plan --laps 20 --fuel-per-lap 2.0 --tank 25 --window 8-12 --lap-time 80 --deg 0.05 --pit-loss 22
pit-wall checklist add --kind stint --compound Medium --laps 5 --fuel 20
pit-wall checklist show              # it ticks itself from the laps; edit it by voice too ("quita lo de mojado")

# Voices: the team-radio sound, Piper, the cache, and the recogniser tested on your own phrases
pit-wall say "Bloqueo delantero izquierdo" --radio strong --out radio.wav
pit-wall voices --piper-download es_ES-davefx-medium
pit-wall voices --prerender
pit-wall phrases record --set A      # the 40 phrases of docs/FRASES_VOZ.md, one by one
pit-wall phrases test --set A --device cuda
```

The debrief, the race planner and the voices are explained in [docs/PHASE7_PLAN.md](docs/PHASE7_PLAN.md),
[docs/PHASE8_PLAN.md](docs/PHASE8_PLAN.md) and [docs/VOICES_PLAN.md](docs/VOICES_PLAN.md); what the apps of the game
expose and what is used is in [docs/AC_APPS_NOTES.md](docs/AC_APPS_NOTES.md). Other drivers' names stay on your
machine: they are not sent to the model unless `chat.rival_names_to_model` is on.

`pit-wall --help` lists every option. Run the checks with `pytest` and `ruff check src tests`.

## Configuration

Defaults live in [`config/default.yaml`](config/default.yaml) (they equal the built-in ones). Copy it to
`config/local.yaml` (git-ignored) or point `PIT_WALL_CONFIG` at a file. Secrets never go in YAML: put
`GEMINI_API_KEY` in `.env`, which is git-ignored from the first commit. The LLM section already selects the
provider and its adaptive pacing (`rate_limit`): the free tier's quotas change over time and are only shown
per project in Google AI Studio, so the limiter learns instead of trusting a fixed number.

## Units of the game's fields

The game publishes its values in its own units and there is **no official Kunos reference for the original Assetto Corsa pages** (the PDF that circulates is for ACC, a different game). So every unit is confirmed against the game itself. Detail and the procedure: [`docs/AC_SHARED_MEMORY.md`](docs/AC_SHARED_MEMORY.md).

Status: **verified** = confirmed by an independent relationship inside a real capture (wall clock, speed vs wheel speed and tyre radius, distance vs mean speed); **plausible** = the range fits the unit but nothing independent has checked it yet (compare with the HUD); **undetermined** = not understood yet; **unreliable** = the game does not provide a usable value for this car.

| Field | Unit | Status | Evidence |
|---|---|---|---|
| `speed_kmh` | km/h | verified | equals the norm of `local_velocity` × 3.6 to 7 digits |
| `velocity, local_velocity` | m/s; in the car frame z = forward, x = lateral, y = vertical | verified | norm × 3.6 = `speed_kmh`; z dominates |
| `wheel_angular_speed` | rad/s | verified | ω × tyre radius ≈ speed (front −0.4 %…−1.9 %, driven rear +4.7 % under wheelspin) |
| `static.tyre_radius` | metres | verified | same check (0.34 m front, 0.3525 m rear) |
| `distance_traveled` | metres | verified | 2256.9 m vs mean speed × 60 s = 2257.6 m |
| `current_time_ms, session_time_left` | milliseconds | verified | Δ = 59 993 ms over a 60 s capture (20 002 ms over 20 s) |
| `gas, brake` | 0..1 | verified | observed 0.0 … 1.0 (brake held at 1.0 in the pit) |
| `heading` | radians, −π..π | verified | observed −3.134 … 3.135 over one lap |
| `normalized_pos` | 0..1 around the lap | verified | 0.18 … 0.68, rising with `distance_traveled` |
| `acc_g` | g; x = lateral, y = vertical (gravity excluded), z = longitudinal | plausible | peaks ±2 g; braking (−1.9) stronger than acceleration (+1.1) on z; HUD pending for signs |
| `wheel_load` | newtons | plausible | pit total 14.56 kN ≈ 1 484 kg·g for a GT4 with fuel and driver |
| `fuel` | litres | plausible | 45.0 loaded, tank 110.0; burn 0.73 L/km (3.3 L per 4.5 km lap) |
| `tyre_pressure` | psi | plausible | 25.7 … 28.9 (a bar value would be ≈ 1.9) |
| `tyre_core_temp, tyre_temp_inner/middle/outer` | °C | plausible | 55 … 89 °C; 80 °C at the pit box |
| `tyre_wear` | percent; 100 = new | plausible, **stale with CSP** | 100.0 on new tyres and unchanged for ~1.7 laps; with Custom Shaders Patch it stays at 100 while the game's tyre apps show the real wear: the CSP bridge (`tools/csp_bridge`) replaces it (checked against the CMRT tyre app: 99/98/69/62 %) |
| `camber` | radians | plausible | −0.056 rad = −3.2° front left, mirrored on the right |
| `suspension_travel` | metres | plausible | 0.011 … 0.080 |
| `brake_bias` | fraction front | plausible | 0.74 constant |
| `static.track_length_m` | metres | plausible | 4537.19 for the Hockenheim GP layout used |
| `clutch` | looks like the clutch engagement (1.0 = engaged), not the pedal | undetermined | 0.0 in neutral (Supra in the pit, Alfa 4C at idle) and 1.0 in gear in every capture; the driver's controls also have the pedal axis inverted. Do not build rules on it |
| `gear` | raw index = gear number + 1: 1 = neutral, 2…7 = 1st…6th (reverse presumably 0, not seen) | verified | driver's HUD: raw 7 = 6th gear, raw 1 = neutral (also seen at 131 km/h mid-shift) |
| `steer_angle` | fraction of the steering lock, −1..1 (full lock = ±1.0; degrees need the car's lock) | verified | lock-to-lock test: min −1.0000, max +1.0000 exactly. One full turn (360°) of a 900° wheel read −0.827 (×450° = 372°): linear with the wheel's ±450°. A hairpin lap peaks at ≈ 0.5. Turning left reads negative (driver turned left: −0.827), right is positive |
| `tc`, `abs` | `tc` looks like a setting-derived value, `abs` a constant | undetermined | `tc` 1.0 in the pit and 0.065 … 1.0 in the first capture, constant 0.082 in the second with TC set to 4/8 (driver); `abs` constant 0.12 |
| `wheel_slip` | definition unknown | undetermined | 0 … 977.7 with outliers; not a bounded ratio |
| `wind_speed` | unit unknown | undetermined | 2.0 constant (m/s or km/h) |
| `brake_temp` | °C in principle | unreliable | 26 in the pit, 15.0 constant on track (= air temperature) while braking at 244 km/h: not simulated for this car; do not build brake rules on it |
| `static.max_power, max_torque` | — | unreliable | MX-5 Cup 108.7 kW / 187 N·m is plausible, Supra GT4 and Audi TCR are not: it depends on the car |

Capture used: Assetto Corsa 1.16.4, shared-memory 1.7, LFM practice server, Toyota Supra GT4 at Hockenheim (GP, no DRS), 20 s in the pit box + 60 s on track (1 199 + 3 600 samples). The two reports are in `tests/fixtures/` and `tests/test_live_reports.py` re-checks every *verified* row on each run.

## Roadmap

| Phase | Content | Status |
|---|---|---|
| 1 | Telemetry reader, mock/replay, console view, LLM foundations | done |
| 2 | Session detector (practice / qualify / race), lap and sector tracker, `pit-wall record` | done, checked on a real qualifying + race ([docs](docs/SESSION_AND_LAPS.md)) |
| 3 | Rule-based alert engine with local TTS | done ([docs](docs/ENGINEER.md)); thresholds conservative, to calibrate |
| 4 | Braking analysis per corner vs the best lap (track geometry generated locally) | done ([docs](docs/TRACK_GEOMETRY.md)): geometry, numbering, per-corner comparison and the spoken hint, checked on real circuits |
| 5 | Text chat with the cloud model (Gemini): compact telemetry summary, local answers first, grounding check on every number ([docs](docs/PHASE5_PLAN.md), [docs/LLM.md](docs/LLM.md)) | done: evaluation 34/34, several keys as reserve |
| 6 | Voice: push-to-talk on any button of any device, microphone and voice selection, local speech recognition (CPU or GPU), spoken answers, half-duplex radio rules, "do not bother me" ([guide](docs/VOICE.md), [plan](docs/PHASE6_PLAN.md)) | done and tested live (microphone, wheel button, GPU recognition, in practice, qualifying and a race); the recognition mistakes are being corrected as they appear |
| 6c | The engineer as an MCP server, for people without an API key ([proposal](docs/MCP_PLAN.md)) | proposal, deferred |
| 6b | The engineer as a character: persona and names, conversation memory by corner, languages es / en / de | partly done ([docs](docs/ENGINEER_EXPERIENCE_PLAN.md)): persona, memory, languages, quiet mode; profiles per simulator and fuel saving per corner pending |
| 6d | Neural local voices (Piper and zero-shot servers with the user's own reference audio), voice profiles, radio effect ([plan](docs/VOICES_PLAN.md)) | built (radio effect, Piper, phrase cache, profiles, local `http` engine), checked by tests and rendering; not yet listened to in the game |
| 7 | Post-session debrief: written report and a short spoken summary, with the comparison to last time ([plan](docs/PHASE7_PLAN.md)) | approved 2026-10-17 and built: `pit-wall debrief FILE` (report, facts, spoken summary with a checked model phrasing or a template, `--save` for the comparison with last time); live at the end of a race or qualifying and on request ("dame el resumen de la sesión") |
| 8 | Race preparation: strategy plan with an editable practice checklist, rivals brief, setup and driving-style suggestions, **tyre wear by corner** | tyre wear by corner is done (an estimate, validated per wheel against the game's own tyre app: `pit-wall tyre-corners`, and the chat answers it); 8a is built: the fuel planner ("how much fuel for 10 minutes", "will I make it"), the stop plan from the rules you state, and the editable practice checklist that ticks itself (`pit-wall checklist`, or by voice); the rivals come from the CSP bridge; setup and style suggestions are still planned, see [docs/PHASE8_PLAN.md](docs/PHASE8_PLAN.md) |
| 9 | More simulators (ACC, iRacing, rFactor 2 / Le Mans Ultimate, Automobilista 2, RaceRoom, AC Evo) with automatic detection | planned, see [docs/MULTI_SIM_PLAN.md](docs/MULTI_SIM_PLAN.md); only the original AC is supported today |
| 10 | Race events: flags, damage, safety car, overtakes and gaps | partly done: penalty, damage and the chequered / penalty flags seen live and used; rival and flag rules from the CSP bridge (off until confirmed); the safety car only exists online with Real Penalty |
| 11 | Settings interface (local web page) and profiles per simulator | planned, same plan |
| 12 | Remote read-only "open pit wall" viewer over the user's own private network ([notes](docs/MULTI_SIM_PLAN.md)) | idea |

## Open items for the author

What a person has to do (record a second session, rotate the key, voice samples) is in
[docs/PENDIENTES.md](docs/PENDIENTES.md) (Spanish).

## Provenance and licence

Nothing from the game's install is copied or shipped, and no track geometry is included (it is generated locally
in a later phase): see [PROVENANCE.md](PROVENANCE.md). MIT licence, see [LICENSE](LICENSE). Not affiliated with
Kunos Simulazioni.
