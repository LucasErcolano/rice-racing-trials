-- Rice Trials diagnostics recorder (JSONL). Enabled only by config.diagnostics == true.
-- Never blocks or raises: io problems are counted in :stats(), never propagated.
-- Every line is one JSON object {t=<sim seconds>, kind=..., ...}; position samples are
-- {t, kind='sample', pos={x,y,z}, look={x,y,z}, ...} (consumed by the track analysis tool).
--
-- Why the current file is mirrored in memory: io.save(filename, data) rewrites the
-- whole file and CSP documents no append. To keep earlier lines we keep the text of
-- the current file (bounded by max_bytes, default 2 MB) and rewrite it on each
-- periodic flush (default every 10 s, never per frame). When the next write would
-- exceed max_bytes the file rotates: delete <name>.1.jsonl, move current to
-- <name>.1.jsonl (destination deleted first because io.move over an existing file is
-- undocumented), start a fresh in-memory segment. Peak memory is about 2x max_bytes
-- during a flush; disk usage is at most 2 files of max_bytes each.
local M = {}
M.FORMAT_VERSION = 1

local DEFAULTS = {
  max_bytes = 2 * 1024 * 1024, buffer_max = 1000, flush_s = 10, sample_hz = 2,
  file = 'rice_diagnostics',
}

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function clamp(v, lo, hi, default)
  if not finite(v) then return default end
  return math.max(lo, math.min(hi, v))
end

-- Minimal deterministic JSON encoder (no dependency on CSP's JSON, which may
-- encode an empty table as [] and whose behavior on cdata is unverified).
local ESC = {['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t'}
local function escape(s)
  return '"'..s:gsub('[%c"\\]', function(c)
    return ESC[c] or string.format('\\u%04x', c:byte())
  end)..'"'
end
local function number(n)
  if not finite(n) then return 'null' end
  if n % 1 == 0 and math.abs(n) < 2^53 then return string.format('%d', n) end
  return string.format('%.10g', n)
end
local encode
local function encodeTable(t, depth)
  if depth > 6 then return '"<depth>"' end
  local n, count, keys = #t, 0, {}
  for k in pairs(t) do
    count = count + 1
    if type(k) == 'string' then keys[#keys + 1] = k end
  end
  if n > 0 and count == n then
    local parts = {}
    for i = 1, n do parts[i] = encode(t[i], depth + 1) end
    return '['..table.concat(parts, ',')..']'
  end
  table.sort(keys)
  if depth == 0 then -- t and kind first for readability; rest sorted
    local ordered = {}
    if t.t ~= nil then ordered[1] = 't' end
    if t.kind ~= nil then ordered[#ordered + 1] = 'kind' end
    for _, k in ipairs(keys) do if k ~= 't' and k ~= 'kind' then ordered[#ordered + 1] = k end end
    keys = ordered
  end
  local parts = {}
  for _, k in ipairs(keys) do parts[#parts + 1] = escape(k)..':'..encode(t[k], depth + 1) end
  return '{'..table.concat(parts, ',')..'}'
end
encode = function(v, depth)
  local ty = type(v)
  if ty == 'string' then return escape(v)
  elseif ty == 'number' then return number(v)
  elseif ty == 'boolean' then return tostring(v)
  elseif ty == 'table' then return encodeTable(v, depth or 0)
  end
  return 'null'
end
M.encode = function(v) return encode(v, 0) end

-- Safe field read: on CSP ac.StateCar is FFI cdata and reading an absent field raises.
local function field(obj, key)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if ok then return v end
  return nil
end
local function vec(v)
  local x, y, z = field(v, 'x'), field(v, 'y'), field(v, 'z')
  if finite(x) and finite(y) and finite(z) then return {x = x, y = y, z = z} end
  return nil
end
local function call(t, name, ...)
  local f = field(t, name)
  if type(f) ~= 'function' then return nil end
  local ok, v = pcall(f, ...)
  if ok then return v end
  return nil
end

M.API_NAMES = {
  'ac.getCar', 'ac.getSim', 'ac.getPatchVersion', 'ac.getPatchVersionCode', 'ac.getCarID',
  'ac.getTrackFullID', 'ac.onCarJumped', 'ac.onSessionStart', 'ac.getFolder', 'ac.storage',
  'io.save', 'io.load', 'io.move', 'io.createDir', 'io.deleteFile', 'io.fileExists',
  'web.get', 'web.post', 'JSON.parse', 'JSON.stringify',
  'ui.pushDWriteFont', 'ui.popDWriteFont', 'ui.dwriteText', 'ui.drawRectFilled',
  'ui.textColored', 'ui.windowSize', 'ui.setCursor',
}
M.CAR_FIELDS = {'position', 'look', 'speedKmh', 'lapTimeMs', 'lapCount', 'isLapValid',
  'previousLapTimeMs', 'isLastLapValid', 'splinePosition', 'collidedWith', 'collisionDepth',
  'wheelsOutside', 'resetCounter'}

local function lookup(env, dotted)
  local v = env
  for part in dotted:gmatch('[^%.]+') do
    v = field(v, part)
    if v == nil then return nil end
  end
  return v
end
function M.api_availability(env, names)
  local out = {}
  for _, name in ipairs(names or M.API_NAMES) do out[name] = lookup(env, name) ~= nil end
  return out
end

local function noop() end
local function disabled()
  local d = {enabled = false}
  d.update, d.flush, d.event = noop, function() return false end, noop
  function d:stats() return {enabled = false} end
  return d
end

-- config: diagnostics (bool), diagnostics_dir, diagnostics_file, diagnostics_max_bytes,
--         diagnostics_buffer_max, diagnostics_flush_s, diagnostics_sample_hz.
-- deps:   io (default global io), env (table with ac/io/web/JSON/ui, default _G),
--         clock (fun(): sim seconds; default sum of dt), dir (fallback directory).
function M.new(config, deps)
  config = type(config) == 'table' and config or {}
  if config.diagnostics ~= true then return disabled() end
  deps = type(deps) == 'table' and deps or {}
  local env = deps.env or _G
  local fs = deps.io
  if fs == nil then fs = rawget(_G, 'io') end
  local o = {enabled = true}
  local maxBytes = math.floor(clamp(config.diagnostics_max_bytes, 4096, 64 * 1024 * 1024, DEFAULTS.max_bytes))
  local bufferMax = math.floor(clamp(config.diagnostics_buffer_max, 10, 100000, DEFAULTS.buffer_max))
  local flushEvery = clamp(config.diagnostics_flush_s, 1, 300, DEFAULTS.flush_s)
  local samplePeriod = 1 / clamp(config.diagnostics_sample_hz, 0.1, 20, DEFAULTS.sample_hz)
  local dir = type(config.diagnostics_dir) == 'string' and config.diagnostics_dir or deps.dir
  if type(dir) ~= 'string' then
    local root = call(field(env, 'ac'), 'getFolder', field(field(field(env, 'ac'), 'FolderID'), 'Root'))
    dir = type(root) == 'string' and root..'/apps/lua/RiceTrials/diagnostics' or nil
  end
  local base = type(config.diagnostics_file) == 'string' and config.diagnostics_file:match('^[%w_%-]+$')
    and config.diagnostics_file or DEFAULTS.file
  local path = dir and (dir..'/'..base..'.jsonl') or nil
  local rotated = dir and (dir..'/'..base..'.1.jsonl') or nil

  local buf, head, tail = {}, 1, 0     -- ring of pending encoded lines
  local segment, segmentBytes = '', 0  -- text of the current file on disk
  local started, prepared = false, false
  local clock, sampleClock, flushClock = 0, samplePeriod, 0
  local last = {}
  local s = {enabled = true, buffered = 0, dropped = 0, lines_written = 0, file_bytes = 0,
    flushes = 0, rotations = 0, io_errors = 0, encode_errors = 0, samples = 0,
    last_error = nil, path = path}

  local function ioError(what)
    s.io_errors = s.io_errors + 1
    s.last_error = what
  end
  local function now()
    if type(deps.clock) == 'function' then
      local ok, v = pcall(deps.clock)
      if ok and finite(v) then return v end
    end
    return clock
  end
  local function push(record)
    record.t = record.t or now()
    local ok, line = pcall(encode, record, 0)
    if not ok or type(line) ~= 'string' then s.encode_errors = s.encode_errors + 1; return end
    if tail - head + 1 >= bufferMax then
      buf[head] = nil; head = head + 1; s.dropped = s.dropped + 1
    end
    tail = tail + 1
    buf[tail] = line
  end
  local function fsCall(name, ...)
    local f = field(fs, name)
    if type(f) ~= 'function' then return false, 'io.'..name..' unavailable' end
    local ok, result = pcall(f, ...)
    if not ok then return false, 'io.'..name..' raised' end
    if result == false then return false, 'io.'..name..' returned false' end
    return true, result
  end
  local function exists(p)
    local f = field(fs, 'fileExists')
    if type(f) == 'function' then
      local ok, r = pcall(f, p)
      if ok then return r == true end
    end
    -- fileExists missing or raising: fall back to io.load(filename, fallback).
    local loaded, data = fsCall('load', p, nil)
    return loaded and type(data) == 'string'
  end
  local function rotate()
    if exists(rotated) then
      local ok, err = fsCall('deleteFile', rotated)
      if not ok then ioError(err) end
    end
    local ok, err = fsCall('move', path, rotated)
    if not ok then
      ioError(err)
      fsCall('deleteFile', path) -- keep the size bound even if move failed
    end
    s.rotations = s.rotations + 1
  end

  function o:event(kind, fields)
    local record = {}
    if type(fields) == 'table' then for k, v in pairs(fields) do record[k] = v end end
    record.kind = type(kind) == 'string' and kind or 'event'
    push(record)
  end

  function o:flush()
    if tail < head then return true end
    if not path or type(fs) ~= 'table' and type(fs) ~= 'userdata' then
      ioError('io or diagnostics_dir unavailable'); return false
    end
    if not prepared then
      -- Result not counted as an error: createDir on an existing folder may return
      -- false (undocumented); the io.save below is what decides success.
      s.create_dir_ok = (fsCall('createDir', dir))
      -- Keep the previous run's file instead of overwriting it.
      if exists(path) then rotate() end
      prepared = true
    end
    local chunk = table.concat(buf, '\n', head, tail)..'\n'
    local count = tail - head + 1
    local content
    if segmentBytes > 0 and segmentBytes + #chunk > maxBytes then
      rotate()
      segment, segmentBytes = '', 0
    end
    if #chunk > maxBytes then -- one flush larger than the file cap: keep the newest lines
      local cut = chunk:find('\n', #chunk - maxBytes, true)
      chunk = cut and chunk:sub(cut + 1) or ''
      local kept = select(2, chunk:gsub('\n', '\n'))
      s.dropped = s.dropped + (count - kept)
      count = kept
    end
    content = segment..chunk
    local ok, err = fsCall('save', path, content)
    if not ok then ioError(err); return false end -- lines stay buffered (bounded)
    segment, segmentBytes = content, #content
    for i = head, tail do buf[i] = nil end
    head, tail = 1, 0
    s.lines_written = s.lines_written + count
    s.flushes = s.flushes + 1
    return true
  end

  local function start(car)
    started = true
    local acEnv = field(env, 'ac')
    push({kind = 'session_start', format = M.FORMAT_VERSION,
      csp_version = call(acEnv, 'getPatchVersion'), csp_code = call(acEnv, 'getPatchVersionCode'),
      car_id = call(acEnv, 'getCarID', 0), track_id = call(acEnv, 'getTrackFullID', '/'),
      api = M.api_availability(env), config = {max_bytes = maxBytes, buffer_max = bufferMax,
        flush_s = flushEvery, sample_hz = 1 / samplePeriod}})
    if car ~= nil then
      local fields = {}
      for _, k in ipairs(M.CAR_FIELDS) do fields['car.'..k] = field(car, k) ~= nil end
      push({kind = 'car_fields', available = fields})
    end
  end

  local function changed(key, value)
    if last[key] == value then return false end
    last[key] = value
    return true
  end

  -- Called from script.update. car = ac.getCar(0) (may be nil), sim = ac.getSim()
  -- (optional), extra = {detector=table|string, connection=table, errors={string...}}.
  function o:update(dt, car, sim, extra)
    if finite(dt) and dt > 0 and dt < 10 then clock = clock + dt else dt = 0 end
    if not started then start(car) end
    if car == nil then
      if changed('car_missing', true) then push({kind = 'car_missing'}) end
    else
      last.car_missing = nil
      -- Collision signals every frame (cheap scalar compares), logged only on change.
      local cw, wo, depth = field(car, 'collidedWith'), field(car, 'wheelsOutside'), field(car, 'collisionDepth')
      local contact = finite(depth) and depth > 0
      local a, b, c = changed('collidedWith', cw), changed('wheelsOutside', wo), changed('contact', contact)
      if a or b or c then
        push({kind = 'collision', collidedWith = cw, wheelsOutside = wo, collisionDepth = depth})
      end
      local lapCount = field(car, 'lapCount')
      if changed('lapCount', lapCount) and lapCount ~= nil then
        push({kind = 'lap', lapCount = lapCount, previousLapTimeMs = field(car, 'previousLapTimeMs'),
          isLastLapValid = field(car, 'isLastLapValid')})
      end
    end
    sampleClock = sampleClock + dt
    if sampleClock >= samplePeriod then
      sampleClock = 0
      if car ~= nil then
        s.samples = s.samples + 1
        push({kind = 'sample', pos = vec(field(car, 'position')), look = vec(field(car, 'look')),
          speedKmh = field(car, 'speedKmh'), lapTimeMs = field(car, 'lapTimeMs'),
          lapCount = field(car, 'lapCount'), isLapValid = field(car, 'isLapValid'),
          splinePosition = field(car, 'splinePosition')})
      end
      -- Detector/connection state compared at sample rate, not per frame.
      if type(extra) == 'table' then
        for _, key in ipairs({'detector', 'connection'}) do
          if extra[key] ~= nil then
            local ok, text = pcall(encode, extra[key], 1)
            if ok and changed(key, text) then push({kind = key, state = extra[key]}) end
          end
        end
      end
      if sim ~= nil then
        local paused = field(sim, 'isPaused')
        if paused ~= nil and changed('paused', paused) then push({kind = 'sim', isPaused = paused}) end
      end
    end
    if type(extra) == 'table' and type(extra.errors) == 'table' then
      for _, e in ipairs(extra.errors) do push({kind = 'error', message = tostring(e)}) end
    end
    flushClock = flushClock + dt
    if flushClock >= flushEvery then
      flushClock = 0
      self:flush()
    end
  end

  function o:stats()
    s.buffered = tail - head + 1
    s.file_bytes = segmentBytes
    local copy = {}
    for k, v in pairs(s) do copy[k] = v end
    return copy
  end

  return o
end

return M
