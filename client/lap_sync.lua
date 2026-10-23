-- Rice Trials lap sync: binds a finished ROUTE lap to AC's native time of the SAME lap.
--
-- Identity, never proximity: a route lap is tied to one native lap by AC's lap counter.
--   * At the route start crossing we read car.lapCount / car.lapTimeMs ("phase"). The offset
--     between AC's own line crossing and the route crossing is lapTimeMs minus the part of
--     the frame after the interpolated route crossing; when it is within LINE_SYNC_MS both
--     lines were crossed together and the lap being timed is native lap lapCount+1 (also when
--     one long stalled frame contained both crossings). If AC's counter moves a frame later,
--     resync() re-phases the lap once, still requiring |offset| <= LINE_SYNC_MS. Otherwise the
--     lap has no native identity and stays UNMATCHED.
--   * The native result for that counter (Field V2 slot or native_laps draft) is applied
--     only when its lap_count is exactly the expected one. A contradiction (other counter, or
--     an implausible route/native divergence) is a MISMATCH: route time kept, review needed.
--     The route/native time difference itself never decides a pairing.
-- A lap's displayed time is then: native raw + penalties (CONFIRMED) or route estimate +
-- penalties (PENDING / UNMATCHED / MISMATCH, always labelled provisional). Penalties are
-- carried separately (penalty_ms) and never folded into a raw time or a delta trace.
-- Pure logic: no ui, no ac.
local M = {}

M.LINE_SYNC_MS = 1000     -- |AC line crossing - route line crossing| for "same line"
M.DIVERGENCE_MS = 20000   -- same counter but |native - route| above this: contradiction
M.NATIVE_WAIT_S = 5       -- native time not delivered after this: UNMATCHED (Field V2 uses 5 s too)
M.MAX_LAPS = 50

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function int(n) return finite(n) and n % 1 == 0 end
local function field(obj, key)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if ok then return v end
  return nil
end
local function round(n) return math.floor(n + 0.5) end

-- Native phase at a route start crossing, or nil when the counters are unavailable.
-- back_ms: physics time between the interpolated route crossing and the end of this frame.
-- offset_ms > 0: AC's line was crossed that long BEFORE the route line.
function M.phase(car, back_ms)
  local lc, lt = field(car, 'lapCount'), field(car, 'lapTimeMs')
  if not (int(lc) and lc >= 0 and int(lt) and lt >= 0) then return nil end
  local back = finite(back_ms) and back_ms >= 0 and back_ms or 0
  local offset = round(lt - back)
  return {lap_count = lc, lap_time_ms = lt, offset_ms = offset, synced = math.abs(offset) <= M.LINE_SYNC_MS}
end

-- An unsynced phase becomes synced when AC's counter advances by exactly one and AC's
-- crossing happened within LINE_SYNC_MS AFTER the route crossing. since_start_ms is the route
-- clock since the route crossing at the end of this frame. Returns the new phase or nil; a
-- phase that can no longer sync is closed (never re-tried).
function M.resync(phase, car, since_start_ms)
  if type(phase) ~= 'table' or phase.synced or phase.closed then return nil end
  if not (finite(since_start_ms) and since_start_ms >= 0) then return nil end
  local lc, lt = field(car, 'lapCount'), field(car, 'lapTimeMs')
  if not (int(lc) and int(lt) and lt >= 0) then return nil end
  if lc == phase.lap_count + 1 then
    local offset = round(lt - since_start_ms)   -- negative: AC crossed after the route line
    if math.abs(offset) <= M.LINE_SYNC_MS then
      return {lap_count = lc, lap_time_ms = lt, offset_ms = offset, synced = true, late = true}
    end
    phase.closed = 'late_native_crossing_out_of_window'
  elseif lc ~= phase.lap_count then
    phase.closed = 'native_counter_moved'
  elseif since_start_ms > M.LINE_SYNC_MS then
    phase.closed = 'native_line_not_crossed'
  end
  return nil
end

-- A new local result. outcome: penaltyOutcome() or nil (no detector evidence). phase: the
-- (final) native phase of the lap or nil. id: the route lap id (V1) or nil.
function M.lap(route_raw_ms, outcome, phase, id)
  local pen = outcome and outcome.penalty_ms or 0
  local l = {id = id, route_raw_ms = route_raw_ms, raw_ms = route_raw_ms, penalty_ms = pen,
    corrected_ms = route_raw_ms + pen, applied = outcome and outcome.applied or nil,
    scored = outcome ~= nil, source = 'route', wait_s = 0,
    phase = type(phase) == 'table' and {lap_count = phase.lap_count, offset_ms = phase.offset_ms,
      synced = phase.synced, late = phase.late} or nil}
  if type(phase) == 'table' and phase.synced then
    l.state, l.expected_lap_count = 'pending', phase.lap_count + 1
  else
    l.state, l.reason = 'unmatched', phase and 'native_line_phase_unsynced' or 'native_phase_unavailable'
  end
  return l
end

M.GIVE_UP_S = 120        -- an overdue lap is still checked for a late native time this long

-- True while a lap can still change state: pending, or overdue (no native time after
-- NATIVE_WAIT_S) but with a known expected counter and still inside GIVE_UP_S.
function M.open(l)
  if type(l) ~= 'table' then return false end
  if l.state == 'pending' then return true end
  return l.state == 'unmatched' and l.reason == 'native_capture_missing' and l.expected_lap_count ~= nil
    and (l.wait_s or 0) < M.GIVE_UP_S
end

-- native: {state = 'confirmed'|'pending'|'unavailable'|'ambiguous', raw_ms, valid, lap_count}
-- from the channel that owns the lap's identity. Returns true when the lap changed state.
function M.apply(l, native, dt)
  if not M.open(l) then return false end
  local st = type(native) == 'table' and native.state or 'pending'
  if st == 'pending' then
    if finite(dt) and dt > 0 and dt <= 1 then l.wait_s = l.wait_s + dt end
    if l.state == 'pending' and l.wait_s >= M.NATIVE_WAIT_S then
      l.state, l.reason = 'unmatched', 'native_capture_missing'; return true
    end
    return false
  end
  if st ~= 'confirmed' then l.state, l.reason, l.wait_s = 'unmatched', 'native_'..tostring(st), M.GIVE_UP_S; return true end
  if not (int(native.raw_ms) and native.raw_ms > 0) then
    l.state, l.reason, l.wait_s = 'unmatched', 'native_time_invalid', M.GIVE_UP_S; return true end
  l.native_ms, l.native_lap_count = native.raw_ms, native.lap_count
  l.divergence_ms = native.raw_ms - l.route_raw_ms
  if native.lap_count ~= l.expected_lap_count then
    l.state, l.reason = 'mismatch', 'native_lap_count_mismatch'; return true end
  if math.abs(l.divergence_ms) > M.DIVERGENCE_MS then
    l.state, l.reason = 'mismatch', 'route_native_divergence'; return true end
  l.state, l.source, l.raw_ms, l.reason = 'confirmed', 'native', native.raw_ms, nil
  l.native_valid = native.valid
  l.corrected_ms = native.raw_ms + l.penalty_ms
  return true
end

-- Best local final time. A MISMATCH lap (identity contradicted) is never a record.
function M.best(laps)
  local best
  for _, l in ipairs(type(laps) == 'table' and laps or {}) do
    if l.state ~= 'mismatch' and int(l.corrected_ms) and (not best or l.corrected_ms < best.corrected_ms) then
      best = l
    end
  end
  return best
end

function M.push(laps, l)
  laps[#laps + 1] = l
  while #laps > M.MAX_LAPS do table.remove(laps, 1) end
end

-- Short tag for the windows: AC (confirmed native) or a provisional route estimate.
function M.tag(l)
  if type(l) ~= 'table' then return '' end
  if l.state == 'confirmed' then return 'AC' end
  if l.state == 'pending' then return 'EST, AC pending' end
  if l.state == 'mismatch' then return 'EST, AC MISMATCH' end
  return 'EST'
end

return M
