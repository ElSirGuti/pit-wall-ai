# Assetto Corsa shared memory: what is verified and what is not

*Español: ver la sección al final.*

## Sources used

| Source | What it gives | Limits |
|---|---|---|
| Layout of the three pages as read from the game's own Python helper (`apps/python/acti/sim_info_acti.py` and the older `AccelTest/sim_info.py` in a stock install) | Field order and types | Not copied: `shm_structs.py` is written from scratch with its own names. It is a community file, not an official Kunos document |
| Live game, `pit-wall dump-shm` | Real values | Only what the game publishes in its current state |
| ACC shared-memory PDF (Kunos forum) | Nothing for AC | It documents **Assetto Corsa Competizione**, a different game; do not use it for AC |

There is **no official Kunos reference for the original Assetto Corsa pages** that this project could find, so
no unit is declared "verified" on the strength of a document. Units are confirmed against the game itself.

## Verified live

**Static page, game in the menu (AC 1.16.4, shared-memory `1.7`)**: version strings, `car_model` and `track` decode correctly and every later field is plausible all the way to the end of the structure.

**Physics and graphics, car on track (LFM practice, Supra GT4 at Hockenheim)**: 20 s in the pit box and 60 s driving. The structures decode correctly: values sit in the right fields and satisfy independent physical relationships (see the table). Two findings matter for later phases: `brake_temp` never heats on this car, and `static.max_power` / `max_torque` do not describe the real cars.

## How to confirm the units (5 minutes, game on track)

1. Start Assetto Corsa, load a session and drive (or sit in the car in the pits with the engine on).
2. `pit-wall dump-shm --watch 60 --json report.json` while you drive a lap with some braking and steering.
3. Compare with the in-game HUD / app values: tyre pressure (psi or bar), fuel (litres), the steering wheel angle,
   speed, gear shown, brake and throttle bars.
4. `report.json` has min / max / mean per field and a `unit_hint` (a suggestion from the observed range).
   The driver's name is left out unless `--include-player`.

A unit moves to "verified" in the README table only after that comparison.

## Field status

Status: **verified** = confirmed by an independent relationship inside a real capture (wall clock, speed vs wheel speed and tyre radius, distance vs mean speed); **plausible** = the range fits the unit but nothing independent has checked it yet (compare with the HUD); **undetermined** = not understood yet; **unreliable** = the game does not provide a usable value for this car.

Capture used: Assetto Corsa 1.16.4, shared-memory 1.7, LFM practice server, Toyota Supra GT4 at Hockenheim (GP, no DRS), 20 s in the pit box + 60 s on track (1 199 + 3 600 samples). The two reports are in `tests/fixtures/` and `tests/test_live_reports.py` re-checks every *verified* row on each run.

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
| `tyre_wear` | percent; 100 = new | plausible | 100.0 on new tyres and unchanged for ~1.7 laps |
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
| `static.max_power, max_torque` | — | unreliable | MX-5 Cup 108.7 kW / 187 N·m is plausible, but Supra GT4 151 kW / 240 N·m and Audi TCR 237.6 kW / 115 N·m are far from the real cars: it depends on the car, do not rely on it |

## Steering lock-to-lock test (done)

Standing in the pit box (Alfa Romeo 4C, 1 194 samples), wheel turned slowly to full left lock and then to full
right lock: `steer_angle` read **−1.0000 … +1.0000**. So the value is a **fraction of the car's steering lock**,
not degrees or radians: ±1.0 is full lock and a hairpin lap peaks at about half lock. To get degrees multiply by
the car's steering-wheel lock (a property of each car, not published in these pages). A second capture with one full turn (360°) of the driver's 900° wheel read −0.827, i.e. 372° at 450° per unit,
so the scale is linear with the wheel's range (that factor is the driver's wheel setup, not a property of the
game: it is `steer_angle × half of the wheel's lock-to-lock`). The captures are
`tests/fixtures/live_ac_alfa4c_lock.json` and `live_ac_alfa4c_turn.json`. **Sign: turning left reads negative** (the driver turned the wheel one full turn to the left
and read −0.827); turning right reads positive.

## Replay is an approximation

`ReplaySource` maps an **ACTI export** (which converts values into its own units) onto the same fields. Where
the live unit is unknown, replay uses the ACTI unit and that can differ from the live one. The mapping is
for developing and testing without the game, not a statement about the live units.

## Practical notes

* The source opens the pages **read-only with `OpenFileMappingW`**. Python's `mmap` with a tag name would create
  an empty page if the game were not running (and could pre-empt the game's own page).
* A page stays alive while a handle is open even after the game exits, so the source re-opens after 2 s without
  new packets; if the name is gone it reports the game as unavailable.
* The mapped region is rounded up to a 4 KB page by Windows, so the check is "the page is not smaller than the
  structure"; the exact size cannot be recovered from the mapping.

---

## Español

**Qué está verificado y qué no.** No existe (que este proyecto haya encontrado) una referencia oficial de Kunos
para las páginas del Assetto Corsa original: el PDF que circula es de **ACC**, otro juego. Por eso ninguna unidad se
declara "verificada" por un documento; se confirma contra el propio juego.

**Verificado en vivo.** Página `static` (juego en el menú): se lee correctamente de principio a fin. Páginas
`physics` y `graphics` (coche en pista, práctica de LFM, Supra GT4 en Hockenheim, 20 s en el box + 60 s
conduciendo): se decodifican bien y cumplen relaciones físicas independientes (velocidad frente a velocidad de
rueda y radio, distancia frente a velocidad media, reloj frente a milisegundos). La tabla de arriba tiene el estado de
cada campo. La marcha queda verificada con el HUD del piloto; el ángulo de volante es una fracción del giro de −1 a 1 (prueba de tope a tope hecha); falta el signo de la izquierda y la semántica de `tc`/`abs`; el embrague parece el embrague engranado y depende del mando; y dos hallazgos
importantes: `brake_temp` no se calienta en este coche y `max_power`/`max_torque` no describen los coches reales.

**Cómo confirmar lo que falta:** con el juego en pista, `pit-wall dump-shm --watch 60 --json report.json`, y
comparar con el HUD el ángulo del volante en una curva conocida y la marcha en un momento dado.
