-- Standalone READ-ONLY CSP app; no RiceTrials imports or physics writes.
-- Requires CSP LuaSocket and JSON. Missing API stops export (watchdog releases).
local socket = require('shared/socket')
local udp = assert(socket.udp())
assert(udp:setsockname('127.0.0.1', 9877))
udp:settimeout(0)
local boot = tostring(socket.gettime()) .. '-' .. tostring({})
local epoch, seq = 0, 0
local function reset() epoch = epoch + 1 end
-- Direct calls (CSP functions may be callable cdata, not Lua functions).
ac.onCarJumped(0, reset)
ac.onSessionStart(reset)

local function vector(v) return {x=v.x,y=v.y,z=v.z} end
local function captureWheels(c)
  local wheels = {}
  for i=0,3 do
    local w=c.wheels[i]
    wheels[#wheels+1]={index=i,position=vector(w.position),look=vector(w.look),
      up=vector(w.up),outside=vector(w.outside),radius_m=w.tyreRadius,width_m=w.tyreWidth,
      suspension_travel_m=w.suspensionTravel}
  end
  return {origin=vector(c.position),look=vector(c.look),up=vector(c.up),side=vector(c.side),
    steering_wheel_deg=c.steer,wheels=wheels}
end

function script.update(dt)
  -- Bound work: discard all but the most recent request in this update.
  local nonce, address, port
  for i = 1, 32 do
    local n, a, p = udp:receivefrom()
    if not n then break end
    if a == '127.0.0.1' and #n == 32 and n:match('^[a-f0-9]+$') then
      nonce, address, port = n, a, p
    end
  end
  if not nonce then return end
  local ok, payload = pcall(function()
    local c, sim = ac.getCar(0), ac.getSim()
    assert(c and sim, 'player state unavailable')
    assert(c.look.x*c.look.x+c.look.z*c.look.z > 0.5, 'invalid heading')
    seq = seq + 1
    local wheelsOK, wheelCapture = pcall(captureWheels,c)
    return JSON.stringify({protocol='cleanlap-1',nonce=nonce,
      wheel_capture_status=wheelsOK and 'OBSERVED_NOT_COMMISSIONED' or 'UNKNOWN',
      wheel_capture=wheelsOK and wheelCapture or nil,
      epoch=boot .. '-' .. tostring(epoch),seq=seq,source_t=c.timestamp/1000,
      x=c.position.x,z=c.position.z,heading=math.atan2(c.look.z,c.look.x),
      speed=c.speedKmh/3.6,gear=c.gear,in_pit=c.isInPit,
      live=sim.isPaused == false and sim.isInMainMenu == false
        and sim.isReplayActive == false and sim.isReplayOnlyMode == false,
      track=ac.getTrackID(),car=ac.getCarID(0),lap_count=c.lapCount,lap_ms=c.lapTimeMs,
      native_last_ms=c.previousLapTimeMs,native_last_valid=c.isLastLapValid,
      collision_depth_m=c.collisionDepth,wheels_outside=c.wheelsOutside})
  end)
  if ok then udp:sendto(payload,address,port) end
end
