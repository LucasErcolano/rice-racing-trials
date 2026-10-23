-- Rice Trials lap clock: the time base of the geometric (route) timer.
--
-- Why: v0.3.0 integrated `elapsed += dt`. On the Windows VM the render/update loop stalls
-- (CSP keeps handing small, clamped `dt` values while AC physics keeps running), so the
-- sum of dt lost 3.5 s and 5.7 s against AC's own lap timer on laps n-10 and n-14, while
-- clean laps agreed within 7 ms (same timing line). See docs/native-timing.md.
--
-- What: every frame the clock advances by the physics time AC itself measured since the
-- previous frame, read from car.lapTimeMs (and car.previousLapTimeMs across the native
-- line). Frame dt is only the fallback when the native counter is unavailable, goes
-- backwards (reset, teleport, new session), jumps implausibly, or has never advanced since
-- the last baseline while frames keep arriving (a CSP build that does not fill lapTimeMs).
-- Once the counter has advanced, a zero native step is a frozen physics frame and adds 0.
-- The result is still the ROUTE estimate: crossings are interpolated geometric gates. It is
-- never presented as AC's certified lap time; that comes from previousLapTimeMs only.
--
-- Pure logic: no ui, no ac. CSP 0.2.11 fields used: car.lapCount, car.lapTimeMs,
-- car.previousLapTimeMs (ac_apps lib.lua, StateCar).
local M = {}

M.MAX_NATIVE_STEP_MS = 60000 -- larger native jumps are discontinuities, not frames
M.FROZEN_MS = 250            -- counter never advanced while dt ran this long: use dt
M.STALL_MS = 250             -- native minus dt above this in one frame counts as a stall

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function int(n) return finite(n) and n % 1 == 0 end
local function field(obj, key)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if ok then return v end
  return nil
end

local KEYS = {'frames', 'dt_ms', 'native_ms', 'clock_ms', 'fallback_frames', 'fallback_dt_ms',
  'stall_frames', 'native_frozen_frames'}

function M.new()
  local c = {lc = nil, lt = nil, frozen_ms = 0, advanced = false,
    -- cumulative counters; a lap reports the difference between two snapshots
    total = {frames = 0, dt_ms = 0, native_ms = 0, clock_ms = 0, fallback_frames = 0,
      fallback_dt_ms = 0, stall_frames = 0, max_frame_deficit_ms = 0, native_frozen_frames = 0}}

  -- Forget the previous native sample (route reset, gate, session start): the next frame
  -- re-baselines and is timed with dt.
  function c:reset() self.lc, self.lt, self.frozen_ms, self.advanced = nil, nil, 0, false end

  -- dt: frame seconds (already validated by the caller); car: ac.getCar(0) or nil.
  -- Returns the step in SECONDS and its source ('native' | 'dt').
  function c:step(dt, car)
    local dtms = finite(dt) and dt > 0 and dt * 1000 or 0
    local lc, lt = field(car, 'lapCount'), field(car, 'lapTimeMs')
    local nstep
    if int(lc) and int(lt) and lt >= 0 and self.lc ~= nil then
      if lc == self.lc and lt >= self.lt then
        nstep = lt - self.lt
      elseif lc == self.lc + 1 then
        local plt = field(car, 'previousLapTimeMs')
        if int(plt) and plt >= self.lt then nstep = (plt - self.lt) + lt end
      end
      if nstep and nstep > M.MAX_NATIVE_STEP_MS then nstep = nil end
    end
    if int(lc) and int(lt) and lt >= 0 then self.lc, self.lt = lc, lt
    else self.lc, self.lt, self.advanced, self.frozen_ms = nil, nil, false, 0 end
    if not nstep then self.advanced, self.frozen_ms = false, 0 end
    local t = self.total
    t.frames = t.frames + 1
    t.dt_ms = t.dt_ms + dtms
    local ms, source = dtms, 'dt'
    if nstep then
      if nstep > 0 then self.advanced, self.frozen_ms = true, 0
      elseif not self.advanced then self.frozen_ms = self.frozen_ms + dtms end
      if not self.advanced and self.frozen_ms > M.FROZEN_MS then
        t.native_frozen_frames = t.native_frozen_frames + 1
      else
        ms, source = nstep, 'native'
        t.native_ms = t.native_ms + nstep
        local deficit = nstep - dtms
        if deficit > M.STALL_MS then t.stall_frames = t.stall_frames + 1 end
        if deficit > t.max_frame_deficit_ms then t.max_frame_deficit_ms = deficit end
      end
    end
    if source == 'dt' then
      t.fallback_frames = t.fallback_frames + 1
      t.fallback_dt_ms = t.fallback_dt_ms + dtms
    end
    t.clock_ms = t.clock_ms + ms
    return ms / 1000, source
  end

  function c:snapshot()
    local s = {}
    for k, v in pairs(self.total) do s[k] = v end
    return s
  end

  -- Evidence of one lap: differences between the snapshot taken at its start and now.
  -- dt_minus_clock_ms is what a pure dt accumulator would have lost (negative) or gained.
  -- max_frame_deficit_ms is the largest single-frame stall seen in this app run.
  function c:since(start)
    if type(start) ~= 'table' then return nil end
    local t, e = self.total, {source = 'native_lap_time_ms', fallback = 'frame_dt'}
    for _, k in ipairs(KEYS) do
      e[k] = math.floor((t[k] - (start[k] or 0)) + 0.5)
    end
    e.dt_minus_clock_ms = e.dt_ms - e.clock_ms
    e.max_frame_deficit_ms = math.floor(t.max_frame_deficit_ms + 0.5)
    return e
  end
  return c
end

return M
