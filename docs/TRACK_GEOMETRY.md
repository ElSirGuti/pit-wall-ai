# Track geometry (phase 4, step 1)

The corners of a circuit come from the racing line the AI follows in **your own** Assetto Corsa install
(`<tracks>/<track>/[<layout>/]ai/fast_lane.ai`). It is read, never written, and nothing derived from it is
committed: it is generated on your machine each time (a few ms).

```bash
pit-wall corners --track spa --length 6947        # numbered corners with distances (and names, if known)
pit-wall corners --file race.pwrec.gz             # track, layout and length come from the recording
pit-wall corners --track monza --any-layout       # try every layout; the length must pick one
```

Set `analysis.ac_dirs` in `config/local.yaml` if your game is not in a usual Steam location.

## Drawing the line with its numbers (2026-10-15)

`pit-wall corners --track monza --length 5793 --map monza.svg` writes the racing line with its numbered
apexes, the names, the start line and the straights (main in green, back in blue, named in orange).
`pit-wall circuit-maps` does it for every circuit of the table that the local game has and writes an
`index.html` with a checklist. The drawings are generated from the game's own lines on the author's PC and are
**not** part of the repository. Orientation is the game's (the handedness is kept: a clockwise circuit
stays clockwise).

## How the line is chosen

* `static.track` is the folder; `static.track_configuration` the layout sub-folder (empty = none).
* Libraries can hold several copies (two drives, mods with many layouts). The game's `track_length_m` picks
  the line (within 1.5 %). If it cannot be decided the answer is "no geometry" **with the reason** and the
  braking analysis stays off for that track; it never guesses.

## Verification (2026-10-13)

`normalized_pos` against the position of the car projected on the line (`error = (normalized_pos − line
fraction) × length`), samples within 8 m of the line and above 30 km/h:

| Source | Track / cars | median | std | p5 / p95 | max |
|---|---|---|---|---|---|
| Live shared memory | Magione, MX-5 Cup, 10 986 samples | −0.08 m | 0.03 m | −0.13 / 0.00 m | 0.16 m |
| ACTI logs | Magione (MX-5) | +0.04 m | 0.04 m | −0.03 / +0.11 m | 0.3 m |
| | Imola (F1 2020, 458, 919, 991) | ≈ 0 | 0.13–0.14 m | ±0.25 m | 1.5 m |
| | Spa (R8, C9, Cayman, F1 2020) | −0.04…−0.12 m | 0.20 m | −0.45 / +0.16 m | 2.4 m |
| | Mugello (Exos 125) | −0.02 m | 0.08 m | −0.16 / +0.11 m | 0.2 m |
| | Monza (F1 2020, 919, P92) | −0.15 m | 0.66–0.78 m | ±1.2 m | 1.4 m |

Where it does not hold: Sepang (3 m, the log does not say which of its 5 layouts was driven), and the
mod tracks whose installed version is not the one that was driven. These are reported, not corrected.

### Handedness (checked live)

In the game's (x, z) plane a path that turns **anticlockwise is a right turn**: over 4 750 live samples the
sign of `x'·z'' − z'·x''` of `car_coordinates` agrees with the sign of `steer_angle` (left = negative) in 96 %
of them (correlation 0.71). `curvature` is signed so a **left** turn is positive.

### ACTI logs (`.ld`)

`Car Coord Y` is the horizontal z axis and `Car Coord X` has the opposite sign (`x_game = −X`, `z_game = Y`);
`Car Coord Z` is the height. Live shared memory needs no transform.

## Numbering

The geometry finds **apexes** (peaks of curvature) and groups the close ones into *complexes* (a chicane, an
esse). The circuit's own numbers are per apex, so corners are **numbered per apex along the lap** from the
start line; the grouping is shown only as the number of groups.

Checked against a real circuit map on 2026-10-14: Magione's map has 11 numbered corners and the geometry has
exactly 11 apexes (in 5 groups: 1 + 1 + 3 + 2 + 4); corner 6 (Curvone) is at 1726 m, right after
the ~800 m straight, which is the braking point the author identified as "the one after the long straight".
The names come from that map (corner 7 has none).

Other circuits are numbered the same way and **not verified** until the author checks them against the real
circuit: Spa comes out with 18 apexes (the official numbering may differ). Names come from the author's own
table (`src/pit_wall_ai/data/circuits.json`) and are given to the nearest apex within 1.5 % of the lap; an
apex without a name keeps its number alone. The voice says a name when there is one, a number only for
circuits listed in `analysis.verified_numbering`, and otherwise describes the stretch.

## Braking per corner (phase 4, step 2)

```bash
pit-wall corners --file race.pwrec.gz --laps      # every lap against the best valid lap, per corner
pit-wall corners --file "spa_..._stint_1.ld" --laps --session practice
```

For each corner of the geometry and each lap: where the brake went on (last braking event before the corner
entry that reaches 0.30), its peak, the speed at that point, the slowest point, where the throttle came
back (≥ 0.5) and the time through the corner. A lap is compared with the **best valid lap of the session
with the same tyre compound**; a lap that is itself that best lap has nothing to be compared with.

* Time is measured over the whole corner: from 10 m before the *reference* braking point (20 m before the
  corner start if the reference did not brake) to the end of the corner, the same span for both laps.
* A difference is only reported as significant with a braking-point difference of at least **10 m** *and* a
  loss of at least **0.1 s** (`analysis.braking.min_distance_m` / `min_time_s`).
* No distance is reported when the pedal was already down at the start of the search window (the braking of
  the previous corner runs into this one) or when a lap did not brake at all there; the time still is.
* Precision follows the sampling: `.ld` logs are 20 Hz (3.5 m per sample at 250 km/h), live data 60 Hz
  (1.2 m). Laps are stored at 4000 points (1.7 m at Spa).
* The table is measured fact. It does not say why a corner was slower.

## The spoken hint (rule `braking_hint`)

After each completed lap, only in practice / qualifying / other sessions (the race policy drops it), at most
one message: the corner where that lap braked differently from the best valid lap of the same compound
**and** lost the most time, if both minimum thresholds are reached. Wording, fact first and no cause:

* with a known name: `Pouhon: frenas 14 metros antes que tu mejor vuelta, pierdes 2 décimas`
* with a number you have verified (`analysis.verified_numbering: [magione]`): `Curva 4: frenas ...`
* otherwise it describes the stretch: `Curva de derechas a 1730 metros de meta: frenas ...`

A second kind of message covers a **big loss without a braking point to compare** (a corner taken without
braking, braking that ran on from the previous corner, or the same braking point): when a corner costs at
least `analysis.braking.big_loss_s` (0.5 s) it is said as time and, if clearly lower, the slowest speed:
`Curva 7: pierdes 1 coma 9 segundos, velocidad mínima 27 kilómetros por hora menor`. Only one message per
lap: whichever corner cost more time.

The log line `alert fired` carries both braking points, the time difference, the span and the speed
difference. Without the track's racing line (not installed, ambiguous layout) the rule is silent and logs
the reason once (`braking analysis off for this track`).

## Replay of ACTI logs: mapping fixes found on the way

* `Chassis Velocity` is X forward, Y lateral, Z vertical (its norm equals `Ground Speed`); it is now
  reordered to the shared memory's convention. Before, every log looked like a permanent spin.
* `Steering Angle` is in degrees of the wheel; it is scaled by 1/450 to the shared memory's fraction of
  lock, so the "calm moment" detector works on logs.


## Asking about a corner (used by the phase 5/6 conversation)

The engineer must be able to answer "¿cuál es esa?". `analysis/corner_text.py` does it without a model:

* `describe_corner(geometry, 11, "es", names)` -> `Prima Esse, que es la curva 11, la última, a izquierdas`;
  it adds "la primera tras la recta larga" / "la última antes de la recta larga" when the stretch before or
  after is 400 m or more without a corner.
* `find_corner(geometry, "la prima esse", names)` -> `[11]`; by number ("curva 6"), by name ("curvone")
  or by ordinal ("la última curva"). An ambiguous text ("la esse") returns several numbers, so the caller
  asks which one.

```
pit-wall corners --track magione --length 2455.7 --what "prima esse"
```

The language model only receives these sentences; it does not decide which corner is which.
