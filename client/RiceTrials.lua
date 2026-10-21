-- CSP app. Local provisional timing only; server status remains PENDING_REVIEW.
local core = require('core')
local calibration, route, previous, elapsed, started, crossings, lastResult
local status = 'CALIBRATION_REQUIRED / UNVERIFIED CSP and Windows runtime'
local storageKey = 'rice_trials_pending_review_v1'
local queue, nonce, nextId = core.pending_queue(), nil, 1
local blocked, endpoint, station, token, assignment, frozen = false, nil, nil, nil, nil, nil
local sending, fetching, heartbeat, retryClock, heartbeatClock = false, false, false, 0, 0
local bootAttempted = false
local lapNumbers, lastResetCounter = {}, nil
-- Advisory cone-contact candidates (optional cone_config.lua + detectors.cone_boxes). They go
-- to the server as payload.detector for the reviewer and NEVER into events/penalties.
local coneMachine, conePrev, coneIds = nil, nil, nil
-- Automatic penalties (cone_config.lua auto_penalties = true): geometric cone boxes, asphalt-ring
-- off-course and reference-line slalom sides. They fill `events` of a PENDING_REVIEW lap; the
-- server never makes them official while scoring is disarmed and the admin can edit every count.
local limitsMachine, slalomMachine, autoPenalties, maxAutoCones, maxWheelsOut = nil, nil, false, 25, 0
local coneExcluded, autoCones = {}, 0
local penaltyStore, offTimes, gateTimes = nil, {}, {}
local function coneReset()
  conePrev, coneIds, maxWheelsOut, autoCones = nil, {}, 0, 0
  offTimes, gateTimes = {}, {}
  if coneMachine then coneMachine:reset() end
  if limitsMachine then limitsMachine:reset() end
  if slalomMachine then slalomMachine:reset() end
end
local function slalomResult()
  if not slalomMachine then return nil end
  local ok, res = pcall(slalomMachine.finalize, slalomMachine)
  return ok and type(res) == 'table' and res or nil
end
-- Penalty settings (in-game panel). Detection never depends on them; they only decide how
-- many seconds each detected incident adds when the lap closes.
local okPen, penaltyMod = pcall(require, 'penalties')
if not okPen or type(penaltyMod) ~= 'table' then penaltyMod = nil end
local function penaltyCounts()
  local counts, times = {}, {off = offTimes, slalom = {}}
  if coneMachine then counts.cone = math.min(autoCones, maxAutoCones) end
  if limitsMachine then counts.off_course = math.min(limitsMachine.count, 100) end
  local sl = slalomResult()
  if sl then
    counts.slalom = #sl.violations
    for _, id in ipairs(sl.violations) do
      if gateTimes[id] then times.slalom[#times.slalom+1] = gateTimes[id] end
    end
  end
  return counts, times, sl
end
local function penaltyOutcome()
  if not (autoPenalties and penaltyStore and penaltyMod) then return nil end
  local counts, times = penaltyCounts()
  local ok, r = pcall(penaltyMod.apply, penaltyStore.cfg, counts, times)
  if ok and type(r) == 'table' then return r end
  return nil
end
local function coneDetector()
  if not (coneMachine or limitsMachine or slalomMachine) then return nil end
  local out = {auto_penalties = autoPenalties, max_wheels_outside = maxWheelsOut}
  if coneMachine then
    local list = {}
    for i, id in ipairs(coneIds or {}) do if i <= 60 then list[#list+1] = id end end
    out.cone_source, out.cone_candidate_count, out.cone_candidates, out.cone_shrink_m =
      'geometric_candidate_uncertified', coneMachine.count, list, coneMachine.shrink
    out.cone_auto_count = autoCones
  end
  if limitsMachine then
    out.off_course_source, out.off_course_count, out.off_margin_m =
      'geometric_asphalt_uncertified', limitsMachine.count, limitsMachine.margin
  end
  local sl = slalomResult()
  if sl then
    out.slalom_source, out.slalom_violations, out.slalom_incomplete =
      'geometric_reference_line_uncertified', sl.violations, sl.incomplete
  end
  return out
end
-- events JSON: a key exists only when its detector runs ("unknown" is never reported as zero).
local function eventsJson()
  if not autoPenalties then return '{}' end
  local parts = {}
  if coneMachine then parts[#parts+1] = '"cone":'..math.min(autoCones, maxAutoCones) end
  if limitsMachine then parts[#parts+1] = '"off_course":'..math.min(limitsMachine.count, 100) end
  local sl = slalomResult()
  if sl then
    local ids = {}
    for _, id in ipairs(sl.violations) do ids[#ids+1] = '"'..id..'"' end
    parts[#parts+1] = '"slalom":['..table.concat(ids, ',')..']'
  end
  return '{'..table.concat(parts, ',')..'}'
end
-- HUD and diagnostics are optional: a load failure degrades to the plain text window
-- and never blocks timing or the queue.
local okHud, hud = pcall(require, 'hud')
if not okHud or type(hud) ~= 'table' then hud = nil end
local okDiag, diagnosticsModule = pcall(require, 'diagnostics')
if not okDiag or type(diagnosticsModule) ~= 'table' then diagnosticsModule = nil end
local hudCache = hud and hud.cache(0.25) or nil
local hudConfig, diag = {}, nil
local online, lastAck = nil, nil
local function autostart()
  if bootAttempted then return end
  bootAttempted = true
  if endpoint then return end
  -- Provisioned locally by the installer, never bundled with station credentials.
  local ok, settings = pcall(require, 'station_config')
  if not ok or type(settings) ~= 'table' then
    status = 'STATION_CONFIG_MISSING / NOT SCORED'; return end
  for _, key in ipairs({'hud_scale', 'hud_x', 'hud_y', 'hud_max_errors', 'hud_font'}) do
    hudConfig[key] = settings[key]
  end
  if diagnosticsModule and settings.diagnostics == true then
    local okNew, created = pcall(diagnosticsModule.new, settings)
    if okNew and type(created) == 'table' then diag = created end
  end
  local before = status
  if not script.connect(settings.server_url, settings.station_id, settings.station_token) then
    -- Keep a more specific reason (QUEUE_CORRUPT, QUEUE_STATION_MISMATCH, STORAGE_*) visible.
    if status == before and not blocked then status = 'STATION_CONFIG_INVALID / NOT SCORED' end
    return end
  -- Route data is a separate operator-calibrated module, not a generated default.
  local calibrated, course = pcall(require, 'route_config')
  if calibrated and type(course) == 'table' then script.configure(course) end
  local okCones, coneCfg = pcall(require, 'cone_config')
  if okCones and type(coneCfg) == 'table' then script.configureCones(coneCfg) end
end

function script.configureCones(cfg)
  local okDet, detectors = pcall(require, 'detectors')
  coneMachine, limitsMachine, slalomMachine, autoPenalties = nil, nil, nil, false
  if okDet and type(detectors) == 'table' and type(detectors.cone_boxes) == 'function' then
    coneMachine = detectors.cone_boxes(cfg)
    if coneMachine then coneMachine.shrink = cfg.shrink_m end
    local okL, lim = pcall(detectors.track_limits, cfg)
    if okL then limitsMachine = lim end
    local okS, sl = pcall(detectors.slalom_runs, cfg)
    if okS then slalomMachine = sl end
    autoPenalties = cfg.auto_penalties == true and (coneMachine ~= nil or limitsMachine ~= nil or slalomMachine ~= nil)
    maxAutoCones = type(cfg.max_auto_cones) == 'number' and cfg.max_auto_cones or 25
    coneExcluded = {}
    if type(cfg.ref_line_cones) == 'table' then for _, id in ipairs(cfg.ref_line_cones) do coneExcluded[id] = true end end
  end
  coneReset()
  return coneMachine ~= nil or limitsMachine ~= nil or slalomMachine ~= nil
end
local function validGate(g)
  if type(g) ~= 'table' or type(g.a) ~= 'table' or type(g.b) ~= 'table' or
      (g.direction ~= 1 and g.direction ~= -1) then return false end
  local a, b = g.a, g.b
  return type(a.x) == 'number' and type(a.z) == 'number' and
    type(b.x) == 'number' and type(b.z) == 'number' and (a.x ~= b.x or a.z ~= b.z)
end
local function fail(message)
  blocked = true; status = message
  return false
end
-- Durable queue: client/journal.lua (A/B slot files, seq + FNV-1a checksum, readback).
-- The whole client state is one journal record so entries, nonce, next_id and lap
-- numbers can never be persisted out of step. The old ac.storage key is migrated once.
local journalModule = require('journal')
local journal, persistence = nil, 'unavailable'
local function journalDir()
  if ac and type(ac.getFolder) == 'function' and type(ac.FolderID) == 'table' and ac.FolderID.Root ~= nil then
    local ok, root = pcall(ac.getFolder, ac.FolderID.Root)
    if ok and type(root) == 'string' and root ~= '' then return root..'/apps/lua/RiceTrials/state' end
  end
  return nil -- no documented folder: journal degrades to ac.storage and says so
end
local function persist()
  if not journal or not JSON or not JSON.stringify then
    return fail('STORAGE_UNAVAILABLE / REVIEW_NOT_DURABLE') end
  local ok = journal:save({{version=1, nonce=nonce, next_id=nextId, entries=queue.entries, lap_numbers=lapNumbers}})
  persistence = journal:state().state
  if not ok then return fail('STORAGE_UNVERIFIED / REVIEW_NOT_DURABLE') end
  return true
end
local function adopt(data)
  if type(data) ~= 'table' or data.version ~= 1 or
      type(data.nonce) ~= 'string' or not data.nonce:match('^[a-zA-Z0-9_.-]+$') or
      type(data.next_id) ~= 'number' or data.next_id < 1 or data.next_id % 1 ~= 0 or
      type(data.entries) ~= 'table' then return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
  local loaded = core.pending_queue()
  for _, entry in ipairs(data.entries) do
    if type(entry) ~= 'table' or not loaded:add(entry.id, entry.payload) then
      return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
    local valid, payload = pcall(JSON.parse, entry.payload)
    if not valid or type(payload) ~= 'table' or payload.lap_id ~= entry.id or
        payload.status ~= 'PENDING_REVIEW' then
      return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
  end
  local numbers = {}
  if data.lap_numbers ~= nil then
    if type(data.lap_numbers) ~= 'table' then return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
    for session, n in pairs(data.lap_numbers) do
      if type(session) ~= 'string' or type(n) ~= 'number' or n < 0 or n % 1 ~= 0 then
        return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
      numbers[session] = n
    end
  end
  queue, nonce, nextId, lapNumbers = loaded, data.nonce, data.next_id, numbers
  return true
end
local function restore()
  if not JSON or not JSON.parse or not JSON.stringify then
    return fail('STORAGE_UNAVAILABLE / MANUAL_RECOVERY_REQUIRED') end
  journal = journalModule.new({dir=journalDir(), name='queue'}, {
    io=io, storage=ac and ac.storage,
    json={encode=function(v) return JSON.stringify(v) end, decode=function(t) return JSON.parse(t) end},
    legacy_key='rice_trials_legacy_migrated_by_app'}) -- legacy import is done below with full validation
  local items, state = journal:load()
  persistence = state
  if state == 'corrupt' or #items > 1 then return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
  if items[1] ~= nil then return adopt(items[1]) end
  local raw = ac and ac.storage and ac.storage[storageKey]
  if raw == nil then return true end
  local parsed, data = pcall(JSON.parse, raw)
  if not parsed or not adopt(data) then return fail('QUEUE_CORRUPT / MANUAL_RECOVERY_REQUIRED') end
  if not persist() then return false end
  -- Drop the legacy copy only once a file slot is confirmed by readback.
  if persistence == 'ok' then pcall(function() ac.storage[storageKey] = nil end); persistence = 'migrated' end
  return true
end
restore()
if penaltyMod then
  penaltyStore = penaltyMod.new({dir=journalDir(), io=io, storage=ac and ac.storage,
    json={encode=function(v) return JSON.stringify(v) end, decode=function(t) return JSON.parse(t) end}})
end
-- Native AC lap counter as a local backup record (own journal, never sent to /api/laps).
local nativeLaps
do
  local okN, nativeMod = pcall(require, 'native_laps')
  if okN and type(nativeMod) == 'table' and JSON and JSON.stringify and JSON.parse then
    local okJ, nj = pcall(journalModule.new, {dir=journalDir(), name=nativeMod.JOURNAL_NAME}, {
      io=io, storage=ac and ac.storage, degraded_key='rice_trials_native_laps_v1',
      legacy_key='rice_trials_native_laps_legacy_unused',
      json={encode=function(v) return JSON.stringify(v) end, decode=function(t) return JSON.parse(t) end}})
    if okJ then
      local okC, created = pcall(nativeMod.new, {journal=nj, nonce=function() return nonce end,
        context=function() return {station_id=station, session_id=assignment and assignment.session_id,
          pilot_id=assignment and assignment.pilot_id} end,
        on_event=function(kind, t) if diag then pcall(diag.event, diag, kind, t) end end})
      if okC and type(created) == 'table' then nativeLaps = created end
    end
  end
end
-- Last authenticated assignment survives a network outage for at most this long, measured
-- on the game clock (sum of update dt), not os.clock (CPU time in plain Lua).
local ASSIGNMENT_STALE_S = 600
local assignmentAt, gameClock = nil, 0
local function headers()
  return {Authorization='Bearer '..token, ['Content-Type']='application/json'}
end
local function decoded(err, response)
  if err or type(response) ~= 'table' or response.status ~= 200 or
      type(response.body) ~= 'string' then return nil end
  local ok, body = pcall(JSON.parse, response.body)
  if ok and type(body) == 'table' then return body end
  return nil
end
local function sendPending()
  if blocked or sending or not endpoint or not web or not web.post then return end
  local item = queue:peek()
  if not item then return end
  sending = true
  local ok = pcall(web.post, endpoint..'/api/laps', headers(), item.payload, function(err, response)
    sending = false
    local body = decoded(err, response)
    if not body or body.lap_id ~= item.id or body.status ~= 'PENDING_REVIEW' then
      online = body ~= nil
      status = 'SEND_UNACKNOWLEDGED / RETRY_PENDING'; return end
    online = true
    -- Snapshot before removal so a failed persistence operation never loses the item in memory.
    local old = queue.entries
    local remainder = {}
    for i=2,#old do remainder[#remainder+1] = old[i] end
    queue.entries = remainder
    if not persist() then queue.entries = old; return end
    lastAck = {lap_id=item.id, at=os.clock()}
    status = 'PENDING_REVIEW / SERVER_ACKNOWLEDGED (NOT SCORED)'
    sendPending()
  end)
  if not ok then sending = false; status = 'SEND_UNACKNOWLEDGED / RETRY_PENDING' end
end
function script.retryPending() sendPending() end
function script.refreshAssignment()
  if blocked or fetching or not endpoint or not web or not web.get then return false end
  fetching = true
  local ok = pcall(web.get, endpoint..'/api/station/assignment', headers(), function(err, response)
    fetching = false
    local body = decoded(err, response)
    local httpStatus = type(response) == 'table' and response.status or nil
    -- Transport failure (no reply, 5xx): keep the last AUTHENTICATED assignment for a bounded
    -- time; laps carry its assignment_revision and the server keeps them PENDING_REVIEW.
    -- Any answer from the server that is not a valid assignment (4xx, other station, junk 200)
    -- clears it immediately.
    local transient = body == nil and (err ~= nil or type(httpStatus) ~= 'number' or httpStatus == 0 or
      httpStatus >= 500)
    if transient and assignment and assignmentAt and gameClock - assignmentAt <= ASSIGNMENT_STALE_S then
      online = false
      status = 'ASSIGNMENT_STALE / OFFLINE / PENDING_REVIEW ONLY'; return
    end
    if not body or body.station_id ~= station or type(body.session_id) ~= 'string' or
        type(body.pilot_id) ~= 'string' or not core.valid_id(body.session_id) or
        not core.valid_id(body.pilot_id) then
      assignment, assignmentAt = nil, nil
      status = 'ASSIGNMENT_UNAVAILABLE / NOT SCORED'; return
    end
    assignment, assignmentAt = body, gameClock
    status = 'ASSIGNMENT_AUTHENTICATED / PENDING_REVIEW ONLY'
    sendPending()
  end)
  if not ok then fetching = false; assignment, assignmentAt = nil, nil; return false end
  return true
end
local function beat()
  if heartbeat or blocked or not endpoint or not web or not web.post then return end
  heartbeat = true
  local ok = pcall(web.post, endpoint..'/api/station/heartbeat', headers(),
    JSON.stringify({station_id=station, protocol_version=1}), function(err, response)
      heartbeat = false
      if not decoded(err, response) then online = false; status = 'HEARTBEAT_UNCONFIRMED / NOT SCORED'
      else online = true end
    end)
  if not ok then heartbeat = false; status = 'HEARTBEAT_UNCONFIRMED / NOT SCORED' end
end
function script.connect(url, stationId, secret)
  if blocked or type(url) ~= 'string' or not url:match('^https?://[%w%.%-]+:?%d*$') or
      not core.valid_id(stationId) or type(secret) ~= 'string' or secret == '' or
      not web or not web.get or not web.post then return false end
  for _, item in ipairs(queue.entries) do
    local valid, saved = pcall(JSON.parse, item.payload)
    if not valid or type(saved) ~= 'table' or saved.station_id ~= stationId then
      status = 'QUEUE_STATION_MISMATCH / MANUAL_RECOVERY_REQUIRED'; return false
    end
  end
  endpoint, station, token = url, stationId, secret
  if not nonce then
    nonce = tostring(os.time())..'-'..tostring(math.random(100000000, 999999999))
    if not persist() then return false end
  end
  script.refreshAssignment()
  beat()
  return true
end
function script.configure(config)
  calibration, route, previous, elapsed, started, crossings, lastResult, frozen = nil, nil, nil, 0, nil, 0, nil, nil
  if blocked then return false end
  if type(config) ~= 'table' or config.verified ~= true or config.event ~= 'endurance' or
      type(config.asset_hash) ~= 'string' or #config.asset_hash ~= 64 or
      not config.asset_hash:match('^[a-fA-F0-9]+$') or
      type(config.route) ~= 'table' or not validGate(config.route.start) or
      type(config.route.checkpoints) ~= 'table' or #config.route.checkpoints == 0 then
    status = 'CALIBRATION_REQUIRED / ROUTE_UNVERIFIED'; return false
  end
  for _, gate in ipairs(config.route.checkpoints) do
    if not validGate(gate) then status = 'ROUTE_UNVERIFIED'; return false end
  end
  if config.track_id ~= 'FSAEJ_Endurance' then status = 'TRACK_MISMATCH / UNVERIFIED'; return false end
  route, calibration = core.route_machine(config.route), config
  status = 'READY / operator attestation only; asset identity UNVERIFIED'
  return true
end
local function resetRoute(reason)
  if calibration then route = core.route_machine(calibration.route) end
  previous, started, frozen = nil, nil, nil
  coneReset()
  status = 'OUTLAP_RESET / '..(reason or 'TELEPORT_OR_GAP')..' / NOT SCORED'
  if diag then pcall(diag.event, diag, 'route_reset', {reason=reason or 'TELEPORT_OR_GAP'}) end
end
-- CSP reset/teleport and session restart events (documented APIs; absent ones degrade to
-- the position-jump and dt guards in script.update).
if ac and type(ac.onCarJumped) == 'function' then
  pcall(ac.onCarJumped, 0, function() resetRoute('CAR_JUMPED') end)
end
if ac and type(ac.onSessionStart) == 'function' then
  pcall(ac.onSessionStart, function()
    resetRoute('SESSION_START'); lastResetCounter = nil; script.refreshAssignment()
    if nativeLaps then pcall(nativeLaps.session_start, nativeLaps) end
  end)
end
-- FFI cdata raises on fields its CSP version does not define: read optional fields safely.
local function field(obj, key)
  local ok, value = pcall(function() return obj[key] end)
  if ok then return value end
  return nil
end
-- Never time while a replay plays, the game is paused or the menu is open (fail-closed:
-- the current lap is discarded and a new outlap is required).
local gatedReason = nil
local function simGate()
  local sim = ac and type(ac.getSim) == 'function' and ac.getSim() or nil
  if not sim then return nil end
  if field(sim, 'isReplayActive') == true then return 'REPLAY' end
  if field(sim, 'isPaused') == true then return 'PAUSED' end
  if field(sim, 'isInMainMenu') == true then return 'MAIN_MENU' end
  return nil
end
local function diagnose(dt)
  if not diag then return end
  local car = ac and type(ac.getCar) == 'function' and ac.getCar(0) or nil
  local sim = ac and type(ac.getSim) == 'function' and ac.getSim() or nil
  pcall(diag.update, diag, dt, car, sim, {
    detector={phase=route and route.phase or nil, status=status, crossings=crossings},
    connection={online=online, pending=#queue.entries, persistence=persistence}})
end
function script.update(dt)
  if penaltyStore then pcall(penaltyStore.flush, penaltyStore, dt) end
  autostart()
  diagnose(dt)
  if nativeLaps and ac and type(ac.getCar) == 'function' then
    local okC, nativeCar = pcall(ac.getCar, 0)
    if okC and nativeCar then pcall(nativeLaps.observe, nativeLaps, nativeCar, dt) end
  end
  if type(dt) ~= 'number' or dt <= 0 or dt > 1 then resetRoute(); return end
  retryClock, heartbeatClock, gameClock = retryClock + dt, heartbeatClock + dt, gameClock + dt
  if retryClock >= 5 then retryClock = 0; sendPending() end
  if heartbeatClock >= 30 then heartbeatClock = 0; beat(); script.refreshAssignment() end
  if not calibration or blocked then return end
  local gate = simGate()
  if gate then
    if gatedReason ~= gate then resetRoute(gate); lastResetCounter = nil; gatedReason = gate end
    return
  end
  gatedReason = nil
  local car = ac.getCar(0)
  if not car or not car.position then resetRoute(); return end
  local current = {x=car.position.x, z=car.position.z}
  local resets = field(car, 'resetCounter')
  if type(resets) == 'number' then
    local changed = lastResetCounter ~= nil and resets ~= lastResetCounter
    lastResetCounter = resets
    if changed then resetRoute('RESET_COUNTER'); previous = current; return end
  end
  if type(current.x) ~= 'number' or type(current.z) ~= 'number' or
      (previous and ((current.x-previous.x)^2+(current.z-previous.z)^2 > (100*dt+1)^2)) then
    resetRoute(); previous = current; return
  end
  elapsed = elapsed + dt
  local wasTimed = route.phase == 'timed' and started ~= nil
  local look = field(car, 'look')
  local lx, lz = look and field(look, 'x'), look and field(look, 'z')
  if not (type(lx) == 'number' and type(lz) == 'number') and previous then
    -- Heading fallback: direction of travel (only meaningful above ~0.05 m per frame).
    local mx, mz = current.x-previous.x, current.z-previous.z
    local len = math.sqrt(mx*mx+mz*mz)
    if len > 0.05 then lx, lz = mx/len, mz/len end
  end
  local pose = {x=current.x, z=current.z, lx=lx, lz=lz}
  local transition, fraction = route:observe(previous, current)
  if coneMachine or limitsMachine or slalomMachine then
    if wasTimed then
      if coneMachine then
        local okC, res = pcall(coneMachine.observe, coneMachine, conePrev, pose)
        if okC and type(res) == 'table' and res.status == 'candidate' then
          for _, id in ipairs(res.cones) do
            coneIds[#coneIds+1] = id
            if not coneExcluded[id] then autoCones = autoCones + 1 end
          end
        end
      end
      if limitsMachine then
        local okL, res = pcall(limitsMachine.observe, limitsMachine, pose, dt)
        if okL and type(res) == 'table' and res.new then offTimes[#offTimes+1] = elapsed end
      end
      if slalomMachine and conePrev then
        local okS, res = pcall(slalomMachine.observe, slalomMachine, conePrev, pose)
        if okS and type(res) == 'table' and type(res.gates) == 'table' then
          for _, id in ipairs(res.gates) do
            gateTimes[id] = elapsed
            local pass = slalomMachine.passes and slalomMachine.passes[id]
            if diag and pass then pcall(diag.event, diag, 'slalom_gate', {id=id, sign=pass.sign, lateral=pass.lateral}) end
          end
        end
      end
      local wo = field(car, 'wheelsOutside')
      if type(wo) == 'number' and wo > maxWheelsOut then maxWheelsOut = wo end
    end
    conePrev = pose
  end
  -- Start-line crossing time interpolated inside this frame (position is linear between
  -- the previous and current samples); falls back to the frame end.
  if type(fraction) ~= 'number' or fraction < 0 or fraction > 1 then fraction = 1 end
  local crossedAt = elapsed - (1 - fraction) * dt
  if transition == 'OUTLAP' then status = 'OUTLAP / NOT SCORED'
  elseif transition == 'OUTLAP_COMPLETE' then
    if not core.assignment_matches(assignment, assignment and assignment.session_id,
        assignment and assignment.pilot_id, station) then
      resetRoute(); previous = current; status = 'ASSIGNMENT_UNAVAILABLE / NOT SCORED'; return end
    frozen = {station_id=station, session_id=assignment.session_id, pilot_id=assignment.pilot_id,
      assignment_revision=assignment.assignment_revision}
    started = crossedAt; coneReset(); status = 'OUTLAP COMPLETE / ASSIGNMENT FROZEN'
  elseif transition == 'CHECKPOINT' then status = 'ROUTE CHECKPOINT / NOT SCORED'
  elseif transition == 'COMPLETE' and started and frozen and crossedAt > started then
    local raw = math.floor((crossedAt-started)*1000+0.5)
    if raw <= 0 or raw > 3600000 then resetRoute(); return end
    local id = station..'-'..nonce..'-'..tostring(nextId)
    local lapNumber = (lapNumbers[frozen.session_id] or 0) + 1
    local okScoring, scoring = pcall(require, 'scoring')
    local payload = {protocol_version=1, station_id=frozen.station_id, lap_id=id,
      session_id=frozen.session_id, pilot_id=frozen.pilot_id, event='endurance',
      assignment_revision=frozen.assignment_revision,
      raw_ms=raw, status='PENDING_REVIEW', scoring_version=1, lap_number=lapNumber,
      rules_version=okScoring and type(scoring) == 'table' and scoring.RULES_VERSION or nil,
      calibration_version=type(calibration.calibration_version) == 'string' and calibration.calibration_version or nil,
      client_timestamp=os.time(), detector=coneDetector()}
    -- The station scores the lap: final time = raw + configured penalties. The settings used
    -- travel with the lap, so later edits never rewrite it.
    local outcome = penaltyOutcome()
    if outcome then
      payload.corrected_ms = raw + outcome.penalty_ms
      payload.penalty = {penalty_ms=outcome.penalty_ms, applied=outcome.applied,
        deduped=outcome.deduped, config=penaltyStore.cfg}
    end
    -- JSON libraries may encode an empty Lua table as []; the server requires
    -- events to be an object. Append the literal empty object to encoded scalars.
    local ok, serialized = pcall(JSON.stringify, payload)
    if ok and type(serialized) == 'string' and serialized:sub(1,1) == '{' and
        serialized:sub(-1) == '}' then
      serialized = serialized:sub(1,-2)..',"events":'..eventsJson()..'}'
    else serialized = nil end
    if not ok or type(serialized) ~= 'string' or not queue:add(id, serialized) then
      fail('QUEUE_UNAVAILABLE / NOT SENT'); return end
    nextId = nextId + 1
    coneReset()
    lapNumbers[frozen.session_id] = lapNumber
    if not persist() then return end
    crossings = crossings + 1
    lastResult = {raw_seconds=raw/1000}
    if nativeLaps then pcall(nativeLaps.note_route_lap, nativeLaps, id, raw) end
    status = 'PENDING_REVIEW / LOCALLY STORED / NOT SCORED'
    sendPending()
    started = crossedAt
    -- Each subsequent lap must be bound to current authenticated assignment.
    frozen = assignment and {station_id=station, session_id=assignment.session_id,
      pilot_id=assignment.pilot_id, assignment_revision=assignment.assignment_revision} or nil
  end
  previous = current
end
local function drawNativeLaps()
  if nativeLaps then
    local st = nativeLaps:stats()
    local d = nativeLaps:last()
    ui.text(string.format('AC native laps: %d saved (%s)%s', st.count, tostring(st.state),
      st.lapcount_available and '' or ' / lapCount NOT available'))
    if d and d.time_ms then
      ui.text(string.format('Last native: %.3f s %s (backup, NOT SCORED)', d.time_ms/1000,
        d.valid == false and 'INVALID' or 'valid'))
    end
  end
end
local function plainWindow()
  ui.text('Rice Driver Trials: NOT VALIDATED on CSP/Windows')
  ui.text(status)
  ui.text('Track: '..(calibration and calibration.track_id or 'none; spatial timing disabled'))
  ui.text('Event: '..(calibration and calibration.event or 'none')..' | Routes: '..tostring(crossings or 0))
  ui.text('Pending local reviews: '..tostring(#queue.entries))
  ui.text('Queue persistence: '..tostring(persistence))
  if lastResult then ui.text(string.format('Provisional route: %.3f s (NOT SCORED)', lastResult.raw_seconds)) end
  drawNativeLaps()
  if coneMachine then
    ui.text(string.format('Cone CANDIDATES this lap: %d (advisory, reviewer decides)', coneMachine.count))
  else ui.text('Cone contacts/off-course: unknown; manual review only') end
end
local function persistenceForHud()
  if persistence == 'migrated' or persistence == 'empty' then return 'ok' end
  if persistence == 'ok' or persistence == 'degraded' or persistence == 'corrupt' then return persistence end
  return 'unavailable'
end
local function liveLap()
  local lap = {elapsed_ms=math.floor((elapsed-started)*1000+0.5), penalties={}}
  local outcome = penaltyOutcome()
  if outcome then
    local counts, _, sl = penaltyCounts()
    lap.penalties = {cone=counts.cone, off_course=counts.off_course, slalom=sl and sl.violations or nil}
    lap.penalty_amounts = penaltyMod.amounts(penaltyStore.cfg)
    lap.projected_ms = lap.elapsed_ms + outcome.penalty_ms
  end
  return lap
end
local function hudState()
  local timed = route and route.phase == 'timed' and started ~= nil
  return {
    assignment=assignment and {pilot_id=assignment.pilot_id, pilot_name=assignment.pilot_name,
      session_id=assignment.session_id} or nil,
    event=calibration and calibration.event or nil,
    phase=calibration and route and route.phase or nil,
    -- Penalties carry no evidence until a detector is commissioned: shown as "sin evidencia".
    lap=timed and liveLap() or nil,
    last_official=nil, best=nil,
    sync={online=online, pending=#queue.entries, persistence=persistenceForHud(),
      last_ack=lastAck and {lap_id=lastAck.lap_id, age_s=math.floor(os.clock()-lastAck.at)} or nil},
    status=status,
  }
end
function script.windowMain(dt)
  if hudCache then
    local ok, model = pcall(hudCache.update, hudCache, dt, hudState(), hudConfig)
    if ok and type(model) == 'table' then
      local drew, drawn = pcall(hud.draw, model, hudConfig)
      if drew and drawn then return end
    end
  end
  plainWindow()
end
function script.penaltyStore() return penaltyStore end
function script.windowPenalties(dt)
  if penaltyMod and penaltyStore then
    local ok = pcall(penaltyMod.draw, penaltyStore, ui, ac, dt)
    if ok then pcall(drawNativeLaps); return end
  end
  ui.text('Panel de penalizaciones no disponible')
  pcall(drawNativeLaps)
end
