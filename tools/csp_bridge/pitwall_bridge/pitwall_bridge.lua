-- Pit Wall Bridge: posts the CSP car state that the classic shared memory does not carry to pit-wall-ai.
-- Two requests at most are in flight (CSP limits web requests), twice per second, to 127.0.0.1 only.
-- The wear is sent as "remaining life in %", the same number the tyre apps draw: (1 - tyreWear) * 100.

local URL = 'http://127.0.0.1:28271/'
local PERIOD = 0.5

local sinceSend = 0
local sent, failed = 0, 0
local lastError = ''
local busy = false

local function build()
  local sim = ac.getSim()
  local idx = sim.focusedCar
  if idx < 0 then idx = 0 end
  local car = ac.getCar(idx)
  if car == nil then return nil end
  local wear, blown, press = {}, {}, {}
  for w = 0, 3 do
    local wheel = car.wheels[w]
    wear[#wear + 1] = math.floor((1 - wheel.tyreWear) * 1000 + 0.5) / 10
    blown[#blown + 1] = wheel.isBlown and 1 or 0
  end
  return {
    v = 1,
    car = idx,
    wear = wear,
    blown = blown,
    tyre = ac.getTyresName(idx),
    flag = sim.raceFlagType,
    flag_cause = sim.raceFlagCause,
    black_flagged = car.blackFlagged and 1 or 0,
    penalty = car.currentPenaltyType,
    penalty_param = car.currentPenaltyParameter,
    damage = { car.damage[0], car.damage[1], car.damage[2], car.damage[3] },
    gearbox_damage = car.gearboxDamage,
    rain_intensity = sim.rainIntensity,
    rain_wetness = sim.rainWetness,
    rain_water = sim.rainWater,
  }
end

function script.update(dt)
  sinceSend = sinceSend + dt
  if busy or sinceSend < PERIOD then return end
  sinceSend = 0
  local ok, payload = pcall(build)
  if not ok or payload == nil then
    lastError = tostring(payload)
    return
  end
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
  if failed > 0 then ui.text('Failed: ' .. failed .. ' ' .. lastError) end
end
