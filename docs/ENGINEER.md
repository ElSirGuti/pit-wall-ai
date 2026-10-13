# The race engineer (phase 3)

Rule-based alerts, spoken by a local voice. No network, no LLM: the path from a telemetry sample to the
voice call is pure Python and measured below.

## How a message is decided

1. `engineer/rules.py`: each rule reads one sample (plus the lap tracker and session events) and may return
   an `Alert` (rule id, priority, short text, the values that fired it). Rules never speak.
2. `engineer/engine.py` (`Engineer`) applies, in order: a muted phase (menu, replay, paused) or the first
   `startup_quiet_s` after a session starts; the session policy (practice: everything; qualify: medium and
   up; race: only critical plus `lap_summary`, `fuel_low`, `penalty`); then delivery.
3. **Critical** alerts (spin, puncture, fuel almost gone, black flag) are said at once and cut whatever is
   being said. Everything else waits for a *calm moment* (brake < 0.10 and |steering| < 0.15 held 0.3 s, or the
   car stopped), is dropped if it is older than its TTL, and is separated from the previous message by at least
   `calm.min_gap_s`.
4. `voice/speech.py`: one thread and one queue. `say()` returns immediately; a critical message drops the
   queued ones. A broken voice never raises into the telemetry loop.

Message format is fixed: *fact + action*, F1-radio style ("Bloqueo delantero izquierdo, suelta freno",
"Gasolina para 2 vueltas, ahorra"). Spanish by default, `language: en` or `--language en` for English.

## Logging (calibration)

Every fired alert is logged (`alert fired`) with its rule and the values that made it fire, every spoken one
(`alert said`, with how long it waited) and every dropped one (`alert dropped`, with the reason: `policy:race`,
`muted`, `expired`). The voice never says the rule; the log does. Use `--log-level INFO --plain`, or
`logging.file` for JSON lines.

## Rules

| Rule | Fires when | Default | Notes |
|---|---|---|---|
| `lockup` | a wheel at < 0.70 of the car speed while braking > 0.4 above 50 km/h for 0.25 s | on | threshold from a real race: worst-wheel ratio p1 0.65, p5 0.77, median 0.91 (MX-5 Cup) |
| `wheelspin` | driven wheels > 1.30 of the car speed at > 0.7 throttle | off | chatty |
| `spin` | angle between heading and travel direction > 60° at > 30 km/h | on | critical; seen on a real spin |
| `offtrack` | 3+ wheels out for 1 s | on | dropped by the race policy |
| `fuel_low` | laps of fuel left ≤ 2 (critical ≤ 0.8) | on | needs 2 timed laps of consumption |
| `tyre_hot` | core temperature > 115 °C for 3 s, re-armed below 105 °C | on | **uncalibrated per compound** |
| `tyre_pressure` | > 40 psi, or a drop > 4 psi in 3 s (puncture) | on | |
| `brake_hot` | brake temperature > 800 °C | on | auto-ignored when the sensor does not move (Supra GT4) |
| `penalty` | `penalty_time` rises | on | unit of `penalty_time` still to be confirmed |
| `flags`, `damage` | flag / damage changes | **off** | no flag or damage seen in a real capture yet |
| `lap_summary` | a lap is completed | on | time and delta to the best |
| `best_sector` | a sector is a best | off | chatty |

All thresholds are in `config/default.yaml`, commented. `pit-wall rules` prints what is active.

## Measured latency

* Sample → `backend.speak()` call (software, 30 samples through session engine, rules and queue): under the
  100 ms the test asserts; the speech thread adds < 20 ms of queue time (tests/test_engineer.py).
* `backend.speak()` → first audio with Windows SAPI, voice Sabina (es-MX), measured with `pit-wall say`:
  242 ms the first time (cold), then 110 / 89 / 101 ms. The rule-alert goal of < 200 ms holds once the voice
  is warm; the first message of a run is slower.

## Not verified yet

Flags and car damage (no real capture), the unit of `penalty_time`, tyre temperature windows per compound and
`brake_temp` on cars other than the MX-5.
