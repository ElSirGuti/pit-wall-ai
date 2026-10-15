# CSP bridge (Pit Wall Bridge)

A two-file Lua app for Assetto Corsa with Custom Shaders Patch. It reads the car state through the CSP Lua API
and posts it, twice a second, to `127.0.0.1:28271`, where `pit-wall` listens (`telemetry.csp_bridge`).

What it sends: the **real tyre wear** per wheel (remaining life in %, like the tyre apps), blown tyres, the
tyre compound, the race flag and its cause, the black flag, the current penalty, the four damage zones,
gearbox damage and the rain/wetness of the track. Only the wear is used by the engineer today; the rest is
what the pending flag/damage/penalty rules will read once the events are captured.

It reads, it never writes to the car, and it only talks to the local machine. Install with
`pit-wall bridge-install` (copies `pitwall_bridge/` to `<assettocorsa>/apps/lua/`), then enable *Pit Wall
Bridge* in the in-game app bar once. `pit-wall bridge-test` shows what arrives.
