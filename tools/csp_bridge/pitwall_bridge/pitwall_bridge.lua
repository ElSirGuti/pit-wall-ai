-- Pit Wall Bridge: posts the CSP car state that the classic shared memory does not carry to pit-wall-ai.
-- Two requests at most are in flight (CSP limits web requests), twice per second, to 127.0.0.1 only.
-- The wear is sent as "remaining life in %", the same number the tyre apps draw: (1 - tyreWear) * 100.

local URL = 'http://127.0.0.1:28271/'
local PERIOD = 0.5

local sinceSend = 0
local sent, failed = 0, 0
local lastError = ''
local busy = false
local ticks, built, errors = 0, 0, 0

-- an optional field that does not exist in this CSP version must not cost the whole message
local function try(fn, default)
  local ok, v = pcall(fn)
  if ok and v ~= nil then return v end
  return default
end

local MAX_CARS = 40

-- a CSP list (items start at 0, "#" gives the count) as a plain array of numbers
local function list(t)
  local out = {}
  if t == nil then return out end
  local n = #t
  for k = 0, n - 1 do out[#out + 1] = t[k] or 0 end
  return out
end

local function rounded(v, places)
  local m = 10 ^ places
  return math.floor(v * m + 0.5) / m
end

-- one entry per connected car, short keys to keep 40 cars small (see pit_wall_ai/telemetry/rivals.py)
local function buildCars(sim)
  local cars = {}
  local count = math.min(sim.carsCount, MAX_CARS)
  for i = 0, count - 1 do
    local c = ac.getCar(i)
    if c ~= nil and try(function() return c.isConnected end, true) then
      cars[#cars + 1] = {
        i = i,
        n = try(function() return ac.getDriverName(i) end, ''),
        p = try(function() return c.racePosition end, 0),
        l = try(function() return c.lapCount end, 0),
        s = rounded(try(function() return c.splinePosition end, 0), 5),
        v = math.floor(try(function() return c.speedKmh end, 0) + 0.5),
        b = try(function() return c.bestLapTimeMs end, 0),
        a = try(function() return c.previousLapTimeMs end, 0),
        c = try(function() return c.lapTimeMs end, 0),
        sec = try(function() return c.currentSector end, 0),
        sp = try(function() return list(c.currentSplits) end, {}),
        ls = try(function() return list(c.lastSplits) end, {}),
        bs = try(function() return list(c.bestSplits) end, {}),
        pit = try(function() return c.isInPitlane and 1 or 0 end, 0),
        ret = try(function() return c.isRetired and 1 or 0 end, 0),
        fin = try(function() return c.isRaceFinished and 1 or 0 end, 0),
        val = try(function() return c.isLapValid and 1 or 0 end, 1),
        cmp = try(function() return ac.getTyresName(i, c.compoundIndex) end, ''),
      }
    end
  end
  return cars
end

local function buildSession(sim)
  local ok, sess = pcall(function() return ac.getSession(sim.currentSessionIndex) end)
  if not ok or sess == nil then return nil end
  return {
    type = try(function() return sess.type end, 0),
    laps = try(function() return sess.laps end, 0),
    dur = try(function() return sess.durationMinutes end, 0),
    timed = try(function() return sess.isTimedRace and 1 or 0 end, 0),
    leader_laps = try(function() return sess.leaderCompletedLaps end, 0),
    over = try(function() return sess.isOver and 1 or 0 end, 0),
  }
end

local function build()
  local sim = ac.getSim()
  local idx = sim.focusedCar
  if idx < 0 then idx = 0 end
  local car = ac.getCar(idx)
  if car == nil then return nil end
  local wear, blown, brake, disc, grain, blister, flat, vkm = {}, {}, {}, {}, {}, {}, {}, {}
  for w = 0, 3 do
    local wheel = car.wheels[w]
    wear[#wear + 1] = math.floor((1 - wheel.tyreWear) * 1000 + 0.5) / 10
    blown[#blown + 1] = try(function() return wheel.isBlown and 1 or 0 end, 0)
    -- brake temperature in °C (0 on the cars that do not have it), and the tyre condition extras
    brake[#brake + 1] = rounded(try(function() return wheel.brakeTemperature end, 0), 0)
    disc[#disc + 1] = rounded(try(function() return wheel.discTemperature end, 0), 0)
    grain[#grain + 1] = rounded(try(function() return wheel.tyreGrain end, 0), 3)
    blister[#blister + 1] = rounded(try(function() return wheel.tyreBlister end, 0), 3)
    flat[#flat + 1] = rounded(try(function() return wheel.tyreFlatSpot end, 0), 3)
    vkm[#vkm + 1] = rounded(try(function() return wheel.tyreVirtualKM end, 0), 2)
  end
  return {
    v = 1,
    car = idx,
    wear = wear,
    blown = blown,
    brake = brake,
    disc = disc,
    grain = grain,
    blister = blister,
    flat = flat,
    vkm = vkm,
    tyre = try(function() return ac.getTyresName(idx) end, ''),
    flag = try(function() return sim.raceFlagType end, 0),
    flag_cause = try(function() return sim.raceFlagCause end, -1),
    penalty = try(function() return car.currentPenaltyType end, 0),
    penalty_param = try(function() return car.currentPenaltyParameter end, 0),
    damage = try(function() return { car.damage[0], car.damage[1], car.damage[2], car.damage[3] } end, {}),
    gearbox_damage = try(function() return car.gearboxDamage end, 0),
    rain_intensity = try(function() return sim.rainIntensity end, 0),
    rain_wetness = try(function() return sim.rainWetness end, 0),
    rain_water = try(function() return sim.rainWater end, 0),
    t = sim.time / 1000,
    me = idx,
    cars = try(function() return buildCars(sim) end, {}),
    session = buildSession(sim),
  }
end

function script.update(dt)
  ticks = ticks + 1
  sinceSend = sinceSend + dt
  if busy or sinceSend < PERIOD then return end
  sinceSend = 0
  local ok, payload = pcall(build)
  if not ok or payload == nil then
    errors = errors + 1
    lastError = 'build: ' .. tostring(payload)
    return
  end
  built = built + 1
  busy = true
  web.post(URL, { ['Content-Type'] = 'application/json' }, JSON.stringify(payload), function(err, _)
    busy = false
    if err ~= nil and err ~= '' then
      failed = failed + 1
      lastError = tostring(err)
    else
      sent = sent + 1
    end
  end)
end

function windowMain()
  if sent > 0 then
    ui.text('Sending to pit-wall-ai: ' .. sent)
  else
    ui.text('Waiting for pit-wall-ai (is `pit-wall voice` or `run` open?)')
  end
  ui.text(string.format('updates %d, built %d, sent %d, failed %d, build errors %d', ticks, built, sent, failed, errors))
  if lastError ~= '' then ui.textWrapped(lastError) end
end
