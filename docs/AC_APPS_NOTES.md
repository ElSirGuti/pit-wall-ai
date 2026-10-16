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
| acti | telemetry logger (`.ld`): what `pit-wall` already reads as recordings | existing loaders |

## Limits to keep in mind
- Online, the server decides which data of other drivers reaches the client; names of real people are in the
  data. They stay on the machine: nothing is stored in the memory file and nothing is sent anywhere.
- The CSP Lua fields were read from the SDK, not run in the game from this side: the bridge wraps every optional
  field so one that is missing in some CSP version only loses that datum.
- Different simulators expose different things (iRacing shows the tyre wear only at the stops): see
  `PHASE8_PLAN.md` 3.5 and `tyre wear estimate` (`analysis/wear_estimate.py`).
