# Session detection and lap tracking (phase 2)

*Español: al final.*

## What it does

* **`session/detector.py`**: the session type and the driving phase, with events when the session changes
  or the car enters/leaves the pits.
* **`analysis/laps.py`**: laps and sectors, the best lap, the reference lap and the live delta.
* **`session/engine.py`**: runs both in a thread of their own from a poller subscription and exposes a
  consistent `view()`. It never delays the telemetry loop.
* **`telemetry/recorder.py`** + `pit-wall record`: record the real fields of the game to `.pwrec.gz` and
  replay them later with the original timing (`pit-wall run --file race.pwrec.gz`).

## How a lap is closed

| Signal | Use | Status |
|---|---|---|
| `completed_laps` increases | primary: closes the lap | observed in 173 laps of real ACTI logs |
| `last_time_ms` changes | the game's own lap time (waits up to 1.5 s for it) | observed in the same laps |
| `current_time_ms` (lap timer) | fallback time, flagged `estimated` | used when the game's time never arrives |
| `normalized_pos` wraps (end -> start) | fallback when the counter never moves; ignored if the lap in progress covers < 60 % (leaving the pits crosses the line without being a lap) | tested on synthetic data |
| `flush()` | at the end of a recording, closes a lap still waiting for its time | |

**Validation against real data.**

* **ACTI logs** (36 files, 12 circuits, 15 cars): the 173 laps finished during the recordings were detected
  with the game's own lap time to within 2 ms, none counted twice and none invented.
* **A live session recorded with `pit-wall record`** (Mazda MX-5 Cup at Magione: qualifying, a race against AI
  with a pause and a restart): 6 laps, every time equal to the game's own (`tests/test_live_lfm_session.py`).

What the live game does at a line crossing (observed): the position wraps first; a few samples later the lap
timer (`current_time_ms`) drops to ~0, `completed_laps` increases and `last_time_ms` / `best_time_ms` /
`last_sector_time_ms` update in the same sample. This is why a timer drop away from the line, with a position
jump, is read as a *restart* and a timer drop with a continuous position as a crossing.

## What a lap is worth

A lap is eligible for "best" only if it is complete (>= 90 % of the lap length covered), not an out lap (it
started in the pits, or it is lap 1), not an in lap (the line was crossed in the pit lane) and not suspect.
`suspect` is a **hint**, not the game's verdict: the shared memory has no lap-validity field, so all four
wheels off the track for 0.5 s marks the lap.

## Phases

`menu`, `replay`, `paused`, `pits`, `out_lap`, `flying`, `stopped`. There is no field for "lights out" or
"formation lap": a race start shows up as `stopped` then `flying`.

## Session type

Verified on a real game: **`practice` = 0, `qualify` = 1, `race` = 2, `hotlap` = 3** (`kind_verified_live`).
`time_attack`, `drift` and `drag` are the codes of the shared-memory enum and are **not verified
yet**; the dashboard says so (`time_attack (code not verified live)`).

Other things observed live and handled:

* **The menu** publishes session 0 and zeros with `status` 0: it is the phase `menu`, never a new session
  and never a reason to clear the laps of the session just played.
* **Start or restart** of a session: `session_time_left` reads **+inf** for one sample. The detector reports
  `SessionChanged(reason="restart")` and the tracker drops the lap in progress (a restart moves the car to the
  grid: the timer drops and the position jumps).
* **A race without a time limit** counts `session_time_left` negative (elapsed time): it is exposed as
  `session_elapsed_s`, and `session_time_left_s` is `None`.
* **Pause** (`status` 3) freezes the values; samples that are not `live` do not feed the lap tracker.
* **Circuits with 2 sectors** (Magione) report `sector_count` 2 and `sector_index` 0..1: sectors follow the
  game's count.

## Live delta

The reference lap (the best by default, or pinned with `set_reference`) is resampled over 400 equal steps of
`normalized_pos`; the delta is the lap timer minus the reference time at the same position (positive =
slower). It needs a reference lap and is not computed on the out lap.

## Known limits

* A sector time equal, to the millisecond, to the previous one is missed when the game gives no
  `sector_index` (the sector time is the only trigger then).
* After a race restart the session clock may not jump up; the lap counter going back to 0 is also used.
* Out laps and in laps are excluded from the best sector times as well.
* A race standing start is **not** an out lap (it is a lap that starts on the grid); an out lap is a lap
  that started in the pit lane.
* The first time a session is joined mid-lap, that lap has partial coverage and is not eligible for best.

## Recording format

`.pwrec.gz` is gzip text: `#static {json}` lines (a new one when the static page changes) and a CSV with `t`,
`source` and every dynamic field of `Snapshot` flattened as in `dump-shm`. The driver's name is never
written. Gzip is flushed every second, so a crash loses at most a second.

---

## Español

**Qué hace.** `session/detector.py` detecta el tipo de sesión y la fase (menú, pausa, pits, vuelta de salida,
vuelta lanzada, parado); `analysis/laps.py` registra vueltas y sectores, la mejor vuelta, la vuelta de
referencia y el delta en vivo; `session/engine.py` los ejecuta en su propio hilo; y `pit-wall record` graba los
campos reales del juego en `.pwrec.gz` para reproducirlos después con su tiempo original.

**Validación con datos reales.** Tus 36 logs de ACTI: las 173 vueltas terminadas salieron con el tiempo oficial
a ±2 ms. Y una sesión en vivo grabada con `pit-wall record` (MX-5 Cup en Magione: clasificación y carrera contra
IA con pausa y reinicio): 6 vueltas, todas con el tiempo del propio juego (`tests/test_live_lfm_session.py`).

**Verificado en vivo:** `practice` = 0, `qualify` = 1, `race` = 2, `hotlap` = 3. En el cruce de meta la posición da la vuelta
primero y unas muestras después el cronómetro de vuelta cae a ~0, `completed_laps` sube y `last_time_ms` se
actualiza. El menú publica sesión 0 con ceros (no es una sesión nueva y no borra las vueltas); al empezar o
reiniciar, `session_time_left` vale `+inf` una muestra; una carrera sin límite de tiempo lo cuenta en negativo
(tiempo transcurrido); circuitos de 2 sectores (Magione) dan `sector_count` 2.

**Lo que sigue sin verificar:** los códigos de `time_attack`, `drift` y `drag`, y la validez de vuelta,
que la memoria compartida no publica: `suspect` es solo una pista.
