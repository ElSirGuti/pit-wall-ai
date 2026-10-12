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

## Verified live (Assetto Corsa 1.16.4, shared-memory version `1.7`, 2026-10-12, game in the menu)

Reading the `static` page of a running game, through `pit-wall dump-shm`:

* The version strings, `car_model` (`fw_audi_rs3_tcr_2024`) and `track` (`prototype_zwartkops_raceway`) decode correctly.
* Every field after them is plausible, all the way to the end of the structure (`num_cars` 19, `sector_count` 3,
  `max_rpm` 7000, `max_fuel` 100.0, `tyre_radius` 0.326, `track_length_m` 2367.68, `aid_*` rates 1.0):
  the `static` layout is consistent with the real page from start to finish.
* `max_power` read `237648.06` and `max_torque` `115.0`. The power looks like **watts** (about 318 hp for a TCR
  car); the torque value is lower than a TCR's real figure and is **not understood yet**: do not use it.

The `physics` and `graphics` pages were still empty (the game was in the menu: `status` 0, `packet_id` 0).
Their layout and units are confirmed in the procedure below, with a car on track.

## How to confirm the units (5 minutes, game on track)

1. Start Assetto Corsa, load a session and drive (or sit in the car in the pits with the engine on).
2. `pit-wall dump-shm --watch 60 --json report.json` while you drive a lap with some braking and steering.
3. Compare with the in-game HUD / app values: tyre pressure (psi or bar), fuel (litres), the steering wheel angle,
   speed, gear shown, brake and throttle bars.
4. `report.json` has min / max / mean per field and a `unit_hint` (a suggestion from the observed range).
   The driver's name is left out unless `--include-player`.

A unit moves to "verified" in the README table only after that comparison.

## Field status

Legend: **observed** = a real value was read from the live game; **pending** = needs a car on track;
**hypothesis** = what the field name and the ACTI export suggest (not a claim).

| Field (page) | Expected unit (hypothesis) | Status |
|---|---|---|
| `static.track_length_m` | metres | observed: 2367.68 on a 2.4 km-class track; plausible, confirm with the HUD |
| `static.tyre_radius` | metres | observed: 0.326 (a 650 mm tyre); plausible |
| `static.max_fuel` | litres | observed: 100.0; plausible |
| `static.max_rpm` | rpm | observed: 7000; plausible |
| `static.max_power` | watts | observed: 237648; plausible as W (about 318 hp) |
| `static.max_torque` | N·m | observed 115.0 but implausible for the car: **not understood** |
| `physics.gas`, `brake`, `clutch` | 0..1 | pending |
| `physics.steer_angle` | degrees or radians, unknown | pending |
| `physics.fuel` | litres | pending |
| `physics.speed_kmh` | km/h | pending (name says km/h) |
| `physics.gear` | raw index (0 = reverse and 1 = neutral is common but unconfirmed) | pending |
| `physics.tyre_pressure` | psi | pending |
| `physics.tyre_core_temp`, `tyre_temp_inner/middle/outer` | °C | pending |
| `physics.brake_temp` | °C | pending |
| `physics.tyre_wear` | percent or fraction; whether 100 or 1 means new is unknown | pending |
| `physics.tyre_dirty_level` | 0..N index | pending |
| `physics.wheel_slip` | slip ratio (definition unconfirmed) | pending |
| `physics.acc_g` | g; axis order unconfirmed | pending |
| `physics.local_velocity` | m/s in the car frame; axis order unconfirmed | pending |
| `physics.heading`, `pitch`, `roll` | radians | pending |
| `physics.brake_bias` | fraction or percent | pending |
| `graphics.normalized_pos` | 0..1 around the lap | pending |
| `graphics.current_time_ms`, `last_time_ms`, `best_time_ms` | milliseconds | pending |
| `graphics.session_time_left` | milliseconds? | pending |
| `graphics.distance_traveled` | metres | pending |
| `graphics.surface_grip` | fraction or percent | pending |
| `graphics.wind_speed`, `wind_direction` | m/s or km/h; degrees | pending |
| Wheel order of every per-wheel field | FL, FR, RL, RR | pending |

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

**Verificado en vivo** (AC 1.16.4, memoria compartida v1.7, juego en el menú): la página `static` se lee
correctamente de principio a fin (versiones, coche, pista, `num_cars` 19, `sector_count` 3, `max_rpm` 7000,
`max_fuel` 100, `tyre_radius` 0,326, `track_length_m` 2367,68). `max_power` (237648) parece vatios; `max_torque`
(115) no encaja con el coche y **no se entiende todavía**. Las páginas `physics` y `graphics` estaban vacías
(menú): sus unidades se confirman con el procedimiento de arriba, con un coche en pista.

**Cómo confirmarlas:** con el juego en pista, `pit-wall dump-shm --watch 60 --json report.json`, comparar con el
HUD (presión, combustible, ángulo de volante, velocidad, marcha, pedales) y revisar `unit_hint`. Una unidad solo pasa
a "verificada" en la tabla del README tras esa comparación. La tabla anterior tiene el estado de cada campo.
