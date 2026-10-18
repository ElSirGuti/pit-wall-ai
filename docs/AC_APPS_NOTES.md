# What the apps installed with Assetto Corsa expose (and what pit-wall uses)

Read on 2026-10-16 in the author's install (`assettocorsa/apps/lua` and `apps/python`). The point: the classic
shared memory has the player's car only; other data (rivals, flags, the real tyre wear) is reachable through the
game's own app APIs. Nothing here is copied code: the apps were read to learn which data exists; the field names
come from the public CSP Lua SDK (`extension/internal/lua-sdk/ac_apps/lib.lua`) and the classic Python API.

## Lua apps (Custom Shaders Patch API, `ac.getCar(i)`, `ac.getSim()`, `ac.getSession(i)`)

| App | What it shows | Where the data is | Used by pit-wall |
|---|---|---|---|
| CMRT Complete HUD, tyres | wear in %, core/inner/middle/outer temperature, pressure against the ideal, grip curve of the compound | `wheel.tyreWear` (0 new .. 1 worn; the app draws `(1 - wear) * 100`), `wheel.tyreCoreTemperature`, `tyres.ini` of the car | the real wear: the classic shared memory keeps 100 % under CSP (`tools/csp_bridge`) |
| CMRT, leaderboard | positions, interval in seconds, sector times, last lap | `racePosition`, `splinePosition`, `currentSplits`/`lastSplits`/`bestSplits`, `bestLapTimeMs`, `isInPitlane` | the same fields, sent by the bridge (0.2); the intervals are **not** a CSP field: they are measured from each car's position history (`telemetry/rivals.py`) |
| CMRT, damage panel | damage by zone, gearbox, engine life | `car.damage[0..3]`, `gearboxDamage`, `engineLifeLeft` | sent by the bridge; the damage rule stays off until a real capture |
| CMRT, sectors / deltabar | sector times against the best, live delta | splits and a per-car "time at each point of the lap" table | the sector comparison with the car ahead |
| Session | type, laps, duration, timed or not, leader laps, over | `ac.getSession(sim.currentSessionIndex)` | end of a race by time (`session.over`, `me.finished`) |
| Flags | yellow, blue (with the car that causes it), black, penalty, white, chequered | `sim.raceFlagType`, `sim.raceFlagCause`, `car.currentPenaltyType` | the CSP flags rule (off until each flag is seen live) |

## Python apps (classic API: `ac.getCarState(car, acsys.CS.*)`)

| App | What it does | Use |
|---|---|---|
| CrewChiefEx | publishes a second shared-memory page, `acpmf_crewchief`, with **every car**: name, position, laps, best/last/current lap, distance along the lap, pit state, engine life, suspension damage and tyre inflation (the last three only with its internal DLL) | `telemetry/crewchief.py` reads it as a **fallback** for the rivals when the CSP app is not sending, if CrewChiefEx is enabled in the game |
| SimHub | the same page layout (based on the CrewChief work) | same reader works |
| F12020Leaderboard | `getCarTyreCompound` (the tyre of any car), `getCarRealTimeLeaderboardPosition` | possible source of the rivals' compound without CSP |
| Real Penalty | online penalties plugin (cuts, pit lane, safety car, DRS); writes `logs/session_*.log` | the logs list events (cutting warnings, pit entries and exits, laps counted); they contain **other drivers' names**: read locally, never committed |
| tyres_app_ext (installed 2026-10-17) | tyre app for CSP custom physics: wear, grain, blister, flat spots, carcass and core temperatures, slip and Mz, ride height, **brake temperatures** (an option in Content Manager > Settings > Apps) and a list of the cars that have them | CSP Python extension (`ac.ext_getTyreWear`, `ext_getTyreGrain`, `ext_getTyreBlister`, `ext_getTyreFlatSpot`, `ext_getTyreVirtualKM`, `ext_getBrakeTemp`, `ext_getBrakeCoreTemp`, `ext_getTyreCarcassTemp`); the same values exist in the Lua `ac.StateWheel` | the bridge sends them (0.3): the brake temperatures replace the shared-memory ones when the car has them; grain, blister, flat spot and virtual km are sent and not used yet |
| acti | telemetry logger (`.ld`): what `pit-wall` already reads as recordings | existing loaders |

## Flags and safety car: what the apps do (read 2026-10-18)
- **CMRT, blue flag**: nothing special. The leaderboard reads `sim.raceFlagType` and maps it (`None`, `Caution` = yellow,
  `Stop` = black, `ReturnToPits` = penalty, `FasterCar` = blue, `OneLapLeft` = white, `Finished`); it is the same field
  the bridge sends (12 = blue). So if the server raises it, the bridge sees it; if it never fires online, the server
  does not set the flag for the client (CMRT would not show it either).
- **BLMLights**: only drives the track's light materials from CSP's own `RACING_FLAG` condition and reads the classic
  `flag` of the shared memory: it adds no source of its own.
- **Real Penalty** (compiled `.pyd`, no source): talks to its server plugin with **encrypted UDP** and **chat messages
  prefixed `RP:`** ("RP:Real Penalty v.4.09.02 is running well", `leavePit`, `teleport`, `pitStop`...). The safety car,
  virtual safety car and full-course yellow (with their sounds and the server's speed limits, e.g. FCY 80 km/h) are
  **its own state**: they are not in the shared memory, nor in a CSP field, nor in its log (the logs never named an SC event).
  Ways in, to be tried in a race that has one: (1) the Lua `ac.onChatMessage` may show the server's SC announcements;
  (2) infer it: most cars of the bridge's list far below racing speed at once, with the yellow flag up.

## Limits to keep in mind
- Online, the server decides which data of other drivers reaches the client; names of real people are in the
  data. They stay on the machine: nothing is stored in the memory file and nothing is sent anywhere.
- The CSP Lua fields were read from the SDK, not run in the game from this side: the bridge wraps every optional
  field so one that is missing in some CSP version only loses that datum.
- Different simulators expose different things (iRacing shows the tyre wear only at the stops): see
  `PHASE8_PLAN.md` 3.5 and `tyre wear estimate` (`analysis/wear_estimate.py`).
