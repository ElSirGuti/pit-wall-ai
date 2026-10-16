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

local function build()
  local sim = ac.getSim()
  local idx = sim.focusedCar
  if idx < 0 then idx = 0 end
  local car = ac.getCar(idx)
  if car == nil then return nil end
  local wear, blown = {}, {}
  for w = 0, 3 do
    local wheel = car.wheels[w]
    wear[#wear + 1] = math.floor((1 - wheel.tyreWear) * 1000 + 0.5) / 10
    blown[#blown + 1] = try(function() return wheel.isBlown and 1 or 0 end, 0)
  end
  return {
    v = 1,
    car = idx,
    wear = wear,
    blown = blown,
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
