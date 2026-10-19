-- CSP app. Local provisional timing only; server status remains PENDING_REVIEW.
local core = require('core')
local calibration, route, previous, elapsed, started, crossings, lastResult
local status = 'CALIBRATION_REQUIRED / UNVERIFIED CSP and Windows runtime'
local storageKey = 'rice_trials_pending_review_v1'
local queue, nonce, nextId = core.pending_queue(), nil, 1
local blocked, endpoint, station, token, assignment, frozen = false, nil, nil, nil, nil, nil
local sending, fetching, heartbeat, retryClock, heartbeatClock = false, false, false, 0, 0
local bootAttempted = false
local function autostart()
  if bootAttempted then return end
  bootAttempted = true
  if endpoint then return end
  -- Provisioned locally by the installer, never bundled with station credentials.
  local ok, settings = pcall(require, 'station_config')
  if not ok or type(settings) ~= 'table' then
    status = 'STATION_CONFIG_MISSING / NOT SCORED'; return end
  if not script.connect(settings.server_url, settings.station_id, settings.station_token) then
    status = 'STATION_CONFIG_INVALID / NOT SCORED'; return end
  -- Route data is a separate operator-calibrated module, not a generated default.
  local calibrated, course = pcall(require, 'route_config')
  if calibrated and type(course) == 'table' then script.configure(course) end
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
local function persist()
  if not ac or not ac.storage or not JSON or not JSON.stringify then
    return fail('STORAGE_UNAVAILABLE / REVIEW_NOT_DURABLE') end
  local ok, serialized = pcall(JSON.stringify, {version=1, nonce=nonce, next_id=nextId, entries=queue.entries})
  if not ok or type(serialized) ~= 'string' then return fail('STORAGE_UNAVAILABLE / REVIEW_NOT_DURABLE') end
  ok = pcall(function() ac.storage[storageKey] = serialized end)
  if not ok or ac.storage[storageKey] ~= serialized then return fail('STORAGE_UNVERIFIED / REVIEW_NOT_DURABLE') end
  -- CSP storage flushes asynchronously: readback is not a crash-durability guarantee.
  return true
end
local function restore()
  if not ac or not ac.storage or not JSON or not JSON.parse then
    return fail('STORAGE_UNAVAILABLE / MANUAL_RECOVERY_REQUIRED') end
  local raw = ac.storage[storageKey]
  if raw == nil then return true end
  local ok, data = pcall(JSON.parse, raw)
  if not ok or type(data) ~= 'table' or data.version ~= 1 or
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
  queue, nonce, nextId = loaded, data.nonce, data.next_id
  return true
end
restore()
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
      status = 'SEND_UNACKNOWLEDGED / RETRY_PENDING'; return end
    -- Snapshot before removal so a failed persistence operation never loses the item in memory.
    local old = queue.entries
    local remainder = {}
    for i=2,#old do remainder[#remainder+1] = old[i] end
    queue.entries = remainder
    if not persist() then queue.entries = old; return end
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
    if not body or body.station_id ~= station or type(body.session_id) ~= 'string' or
        type(body.pilot_id) ~= 'string' or not core.valid_id(body.session_id) or
        not core.valid_id(body.pilot_id) then
      assignment = nil
      status = 'ASSIGNMENT_UNAVAILABLE / NOT SCORED'; return
    end
    assignment = body
    status = 'ASSIGNMENT_AUTHENTICATED / PENDING_REVIEW ONLY'
    sendPending()
  end)
  if not ok then fetching = false; assignment = nil; return false end
  return true
end
local function beat()
  if heartbeat or blocked or not endpoint or not web or not web.post then return end
  heartbeat = true
  local ok = pcall(web.post, endpoint..'/api/station/heartbeat', headers(),
    JSON.stringify({station_id=station, protocol_version=1}), function(err, response)
      heartbeat = false
      if not decoded(err, response) then status = 'HEARTBEAT_UNCONFIRMED / NOT SCORED' end
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
local function resetRoute()
  if calibration then route = core.route_machine(calibration.route) end
  previous, started, frozen = nil, nil, nil
  status = 'OUTLAP_RESET / TELEPORT_OR_GAP / NOT SCORED'
end
function script.update(dt)
  autostart()
  if type(dt) ~= 'number' or dt <= 0 or dt > 1 then resetRoute(); return end
  retryClock, heartbeatClock = retryClock + dt, heartbeatClock + dt
  if retryClock >= 5 then retryClock = 0; sendPending() end
  if heartbeatClock >= 30 then heartbeatClock = 0; beat(); script.refreshAssignment() end
  if not calibration or blocked then return end
  local car = ac.getCar(0)
  if not car or not car.position then resetRoute(); return end
  local current = {x=car.position.x, z=car.position.z}
  if type(current.x) ~= 'number' or type(current.z) ~= 'number' or
      (previous and ((current.x-previous.x)^2+(current.z-previous.z)^2 > (100*dt+1)^2)) then
    resetRoute(); previous = current; return
  end
  elapsed = elapsed + dt
  local transition = route:observe(previous, current)
  if transition == 'OUTLAP' then status = 'OUTLAP / NOT SCORED'
  elseif transition == 'OUTLAP_COMPLETE' then
    if not core.assignment_matches(assignment, assignment and assignment.session_id,
        assignment and assignment.pilot_id, station) then
      resetRoute(); previous = current; status = 'ASSIGNMENT_UNAVAILABLE / NOT SCORED'; return end
    frozen = {station_id=station, session_id=assignment.session_id, pilot_id=assignment.pilot_id,
      assignment_revision=assignment.assignment_revision}
    started = elapsed; status = 'OUTLAP COMPLETE / ASSIGNMENT FROZEN'
  elseif transition == 'CHECKPOINT' then status = 'ROUTE CHECKPOINT / NOT SCORED'
  elseif transition == 'COMPLETE' and started and frozen and elapsed > started then
    local raw = math.floor((elapsed-started)*1000+0.5)
    if raw <= 0 or raw > 3600000 then resetRoute(); return end
    local id = station..'-'..nonce..'-'..tostring(nextId)
    local payload = {protocol_version=1, station_id=frozen.station_id, lap_id=id,
      session_id=frozen.session_id, pilot_id=frozen.pilot_id, event='endurance',
      assignment_revision=frozen.assignment_revision,
      raw_ms=raw, status='PENDING_REVIEW', scoring_version=1}
    -- JSON libraries may encode an empty Lua table as []; the server requires
    -- events to be an object. Append the literal empty object to encoded scalars.
    local ok, serialized = pcall(JSON.stringify, payload)
    if ok and type(serialized) == 'string' and serialized:sub(1,1) == '{' and
        serialized:sub(-1) == '}' then
      serialized = serialized:sub(1,-2)..',"events":{}}'
    else serialized = nil end
    if not ok or type(serialized) ~= 'string' or not queue:add(id, serialized) then
      fail('QUEUE_UNAVAILABLE / NOT SENT'); return end
    nextId = nextId + 1
    if not persist() then return end
    crossings = crossings + 1
    lastResult = {raw_seconds=raw/1000}
    status = 'PENDING_REVIEW / LOCALLY STORED / NOT SCORED'
    sendPending()
    started = elapsed
    -- Each subsequent lap must be bound to current authenticated assignment.
    frozen = assignment and {station_id=station, session_id=assignment.session_id,
      pilot_id=assignment.pilot_id, assignment_revision=assignment.assignment_revision} or nil
  end
  previous = current
end
function script.windowMain(dt)
  ui.text('Rice Driver Trials — NOT VALIDATED on CSP/Windows')
  ui.text(status)
  ui.text('Track: '..(calibration and calibration.track_id or 'none; spatial timing disabled'))
  ui.text('Event: '..(calibration and calibration.event or 'none')..' | Routes: '..tostring(crossings or 0))
  ui.text('Pending local reviews: '..tostring(#queue.entries))
  if lastResult then ui.text(string.format('Provisional route: %.3f s (NOT SCORED)', lastResult.raw_seconds)) end
  ui.text('Cone contacts/off-course: unknown; manual review only')
end
