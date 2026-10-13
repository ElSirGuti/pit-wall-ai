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

**Validation against real data.** The ACTI logs in the author's machine (36 files, 12 circuits, 15 cars) were
replayed: the 173 laps finished during the recordings were detected with the game's own lap time to within
2 ms, with none counted twice and none invented. 6 more laps in 4 logs ended before the recording began and
have no samples, so they are correctly not reported. Note that this validates the logic on the *ACTI
export*; the live game has been observed across no start/finish line yet (the three live captures were taken
before the first crossing). `pit-wall record` on the first lap of the next session settles it.

## What a lap is worth

A lap is eligible for "best" only if it is complete (>= 90 % of the lap length covered), not an out lap (it
started in the pits, or it is lap 1), not an in lap (the line was crossed in the pit lane) and not suspect.
`suspect` is a **hint**, not the game's verdict: the shared memory has no lap-validity field, so all four
wheels off the track for 0.5 s marks the lap.

## Phases

`menu`, `replay`, `paused`, `pits`, `out_lap`, `flying`, `stopped`. There is no field for "lights out" or
"formation lap": a race start shows up as `stopped` then `flying`.

## Session type

`practice` has been seen live (LFM practice server). `qualify`, `race`, `hotlap`, `time_attack`, `drift` and
`drag` are the codes of the shared-memory enum and are **not verified yet**; the dashboard says so
(`race (code not verified live)`). To verify: `pit-wall record` a short stretch of a qualifying and of a race.

## Live delta

The reference lap (the best by default, or pinned with `set_reference`) is resampled over 400 equal steps of
`normalized_pos`; the delta is the lap timer minus the reference time at the same position (positive =
slower). It needs a reference lap and is not computed on the out lap.

## Known limits

* A sector time equal, to the millisecond, to the previous one is missed when the game gives no
  `sector_index` (the sector time is the only trigger then).
* After a race restart the session clock may not jump up; the lap counter going back to 0 is also used.
* Out laps and in laps are excluded from the best sector times as well.

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

**Validación con datos reales.** Se reprodujeron tus 36 logs de ACTI (12 circuitos, 15 coches): las 173 vueltas
terminadas durante las grabaciones salieron con el tiempo oficial del juego a ±2 ms, ninguna duplicada ni
inventada. Esto valida la lógica con la *exportación de ACTI*; en el juego en vivo todavía no se ha observado
ningún cruce de meta. Con `pit-wall record` en la primera vuelta de tu próxima sesión se confirma.

**Lo que no está verificado:** los códigos de sesión distintos de `practice` (clasificación, carrera...), y la
validez de vuelta, que la memoria compartida no publica: `suspect` es solo una pista.
