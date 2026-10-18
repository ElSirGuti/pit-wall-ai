# The race engineer (phase 3)

Rule-based alerts, spoken by a local voice. No network, no LLM: the path from a telemetry sample to the
voice call is pure Python and measured below.

## How a message is decided

1. `engineer/rules.py`: each rule reads one sample (plus the lap tracker and session events) and may return
   an `Alert` (rule id, priority, short text, the values that fired it). Rules never speak.
2. `engineer/engine.py` (`Engineer`) applies, in order: a muted phase (menu, replay, paused) or the first
   `startup_quiet_s` after a session starts; the session policy (practice: everything; qualify: medium and
   up; race: only critical plus `lap_summary`, `fuel_low`, `penalty`, `damage`, `flags`, `csp_flags`, `braking_hint`, `rival_close`, `rival_pit`, so the corner feedback and the flags are heard during the race); then delivery.
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
| `penalty` | `penalty_time` rises | on | seconds left to serve: seen as 10.0 at a 10 s penalty and then counting down in real time (2026-10-17) |
| `flags` | the game's own flag changes | on | seen live: 2 = yellow (an online LFM race, 2026-10-18), 5 = chequered, 6 = the black-and-white penalty flag (not announced: the penalty rule does it); the chequered flag left over from the previous session is not said at the start; blue not seen yet. Spoken in a race (race policy) |
| `damage` | the sum of the damage zones rises by `min_increase` | on | seen in real contacts: zones in km/h of the hit (7 to 40); allowed in a race |
| `rival_close` | in a race, the car behind (or ahead) is within `gap_s` and the gap shrank by `min_closing_s` in 30 s | **off** | needs the CSP bridge (`tools/csp_bridge`); gaps measured from position history |
| `rival_pit` | the car ahead in the standings enters the pit lane | **off** | needs the bridge |
| `csp_flags` | the flag reported by CSP changes (yellow, blue with the name of the car, black, penalty, white, chequered) | **off** | needs the bridge; yellow, white and chequered are left to `flags` when that rule is on (the game's flag arrives first), so the bridge adds the blue (with the name) and the black |
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

The blue, yellow, black and white flags, which zone is which in the damage values, tyre temperature windows per
compound and `brake_temp` on the cars without the sensor.
