"""The Lua app of tools/csp_bridge run against a mock of the CSP API: the message it builds must be the one the
Python side reads. (The real API cannot be run here; the mock follows the documented shapes: lists start at 0.)"""

from pathlib import Path

import pytest

lupa = pytest.importorskip("lupa")

from pit_wall_ai.telemetry.rivals import RivalTracker  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "tools/csp_bridge/pitwall_bridge/pitwall_bridge.lua"

PRELUDE = r"""
posted = {}
local function zero_based(t)  -- a CSP list: items start at 0 and "#" gives the count
  local n = #t
  local out = {}
  for i = 1, n do out[i - 1] = t[i] end
  return setmetatable(out, { __len = function() return n end })
end

local function enc(v)
  local tv = type(v)
  if tv == 'table' then
    local mt = getmetatable(v)
    if mt and mt.__len then
      local parts = {}
      for i = 0, #v - 1 do parts[#parts + 1] = enc(v[i]) end
      return '[' .. table.concat(parts, ',') .. ']'
    end
    if #v > 0 or next(v) == nil then
      local parts = {}
      for i = 1, #v do parts[#parts + 1] = enc(v[i]) end
      return '[' .. table.concat(parts, ',') .. ']'
    end
    local parts = {}
    for k, x in pairs(v) do parts[#parts + 1] = string.format('%q:%s', k, enc(x)) end
    return '{' .. table.concat(parts, ',') .. '}'
  elseif tv == 'string' then return string.format('%q', v)
  elseif tv == 'boolean' then return v and 'true' or 'false'
  elseif tv == 'nil' then return 'null'
  else return tostring(v) end
end

JSON = { stringify = enc }
script = {}
web = { post = function(url, headers, data, cb) posted[#posted + 1] = data; cb(nil, {}) end }
ui = { text = function() end, textWrapped = function() end }

local function wheel(wear) return { tyreWear = wear, isBlown = false, brakeTemperature = 412.6, discTemperature = 430, tyreGrain = 0.25, tyreBlister = 0, tyreFlatSpot = 0.05, tyreVirtualKM = 12.345 } end
local function car(i, pos, spline)
  return {
    isConnected = true, racePosition = pos, lapCount = 3, splinePosition = spline, speedKmh = 181.4,
    bestLapTimeMs = 90000 + i, previousLapTimeMs = 91000, lapTimeMs = 45000, currentSector = 1,
    currentSplits = zero_based({ 30000, 15000 }), lastSplits = zero_based({ 30500, 29000, 31500 }),
    bestSplits = zero_based({ 30000, 28900, 31000 }), isInPitlane = false, isRetired = false,
    isRaceFinished = false, isLapValid = true, compoundIndex = 1,
    wheels = { [0] = wheel(0.1), wheel(0.12), wheel(0.3), wheel(0.35) },
    damage = { [0] = 0, 1, 2, 3 }, gearboxDamage = 0, currentPenaltyType = 0, currentPenaltyParameter = 0,
  }
end
local cars = { [0] = car(0, 2, 0.5), car(1, 1, 0.53), car(2, 3, 0.45) }

ac = {
  getSim = function()
    return { focusedCar = 0, carsCount = 3, raceFlagType = 0, raceFlagCause = -1, rainIntensity = 0,
             rainWetness = 0, rainWater = 0, time = 12500, currentSessionIndex = 0 }
  end,
  getCar = function(i) return cars[i] end,
  getDriverName = function(i) return 'Piloto' .. i end,
  getTyresName = function() return 'S' end,
  getSession = function()
    return { type = 3, laps = 10, durationMinutes = 0, isTimedRace = false, leaderCompletedLaps = 3, isOver = false }
  end,
}
"""


def _run_once() -> str:
    rt = lupa.LuaRuntime(unpack_returned_tuples=True)
    rt.execute(PRELUDE)
    rt.execute(APP.read_text(encoding="utf-8"))
    rt.execute("script.update(1.0)")
    return str(rt.eval("posted[1]"))


def test_the_message_the_app_builds_is_the_one_python_reads():
    import json

    msg = json.loads(_run_once())
    assert msg["wear"] == [90.0, 88.0, 70.0, 65.0] and msg["tyre"] == "S" and msg["me"] == 0
    assert (
        msg["brake"] == [413, 413, 413, 413]
        and msg["flat"] == [0.05] * 4
        and msg["vkm"][0] == 12.35
    )
    assert len(msg["cars"]) == 3 and msg["cars"][0]["ls"] == [30500, 29000, 31500]
    assert msg["session"]["type"] == 3 and msg["session"]["leader_laps"] == 3
    tr = RivalTracker(track_length_m=5000)
    assert tr.update(msg)
    b = tr.board()
    assert b.me.position == 2 and b.me.compound == "S" and b.me.last_splits == (30500, 29000, 31500)
    assert b.session.kind == "race"
