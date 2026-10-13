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

Corners are numbered along the lap from the start line, as the **geometry** sees them: a chicane or a
double apex counts once. That is not necessarily the circuit's official numbering (Magione comes out with
5, the real circuit has about ten named corners). Names come from the author's own table
(`src/pit_wall_ai/data/circuits.json`) and are attached only where a table corner falls inside a geometric
one; a corner without a name keeps its number alone. Until you confirm a circuit's numbering, the voice
will not say corner numbers for it.
