# CSP bridge (Pit Wall Bridge)

A two-file Lua app for Assetto Corsa with Custom Shaders Patch. It reads the car state through the CSP Lua API
and posts it, twice a second, to `127.0.0.1:28271`, where `pit-wall` listens (`telemetry.csp_bridge`).

What it sends: the real tyre wear per wheel (remaining life in %, like the tyre apps), blown tyres, the
tyre compound, the race flag and its cause, the current penalty, the four damage zones,
gearbox damage and the rain/wetness of the track. Only the wear is used by the engineer today; the rest is
what the pending flag/damage/penalty rules will read once the events are captured.

It reads, it never writes to the car, and it only talks to the local machine. Install with
`pit-wall bridge-install` (copies `pitwall_bridge/` to `<assettocorsa>/apps/lua/`), then enable *Pit Wall
Bridge* in the in-game app bar once. `pit-wall bridge-test` shows what arrives.

## Version 0.2: the other cars

Besides the car's own state, each message carries `cars` (one entry per connected car, up to 40: race position,
laps, position along the lap, speed, best/last/current lap, the sectors of the lap in progress, the last lap and
the best ones, pit/retired/finished, lap validity and tyre compound) and `session` (type, laps, duration,
timed or not, laps the leader has completed, over or not). `pit-wall` turns them into the gaps in seconds
(Assetto Corsa does not provide them: they are measured from where each car was and when, as a timing screen
does) and answers "who is ahead", "how far is the one behind", "how is the one ahead doing in the sectors".
`pit-wall bridge-test` prints a line with the rivals as they arrive.
