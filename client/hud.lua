-- Rice Trials HUD. hud.model() is pure (no ui/io/ac access) and testable;
-- hud.draw() renders a model using only guarded ui.* calls listed in
-- docs/csp-api-reference.md. Scoring never depends on this module: it lives in
-- script.update, so closing the HUD window does not stop timing or the queue.
-- HUD strings are ASCII on purpose: glyph coverage of the CSP fonts is unverified.
local M = {}
M.SCHEMA_VERSION = 1

local LEVEL_RANK = {ok = 0, info = 1, warn = 2, error = 3}
local KNOWN_STATUS = {VALID = 'ok', PENDING_REVIEW = 'warn', OUTLAP = 'info',
  INVALID = 'error', DETECTOR_ERROR = 'error'}
-- Penalty fields shown per event (labels in the HUD). Skidpad needs per-side
-- splits, so the HUD does not project it.
local PENALTY_FIELDS = {
  endurance = {{key = 'cone', label = 'Cones'}, {key = 'off_course', label = 'Off-course'},
    {key = 'slalom', label = 'Slalom', list = true}},
  autocross = {{key = 'doo', label = 'Cones (DOO)'}, {key = 'oc', label = 'Off-course (OC)'}},
}

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function int(n) return finite(n) and n % 1 == 0 end
local function clamp(v, lo, hi, default)
  if not finite(v) then return default end
  if v < lo then return lo end
  if v > hi then return hi end
  return v
end
-- Server or operator supplied text: strip control characters and bound length.
local function clean(s, max)
  if type(s) ~= 'string' and type(s) ~= 'number' then return nil end
  s = tostring(s):gsub('%c', '?')
  max = max or 40
  if #s > max then s = s:sub(1, max - 3)..'...' end
  return s
end

function M.format_ms(ms)
  if not int(ms) or ms < 0 or ms > 359999999 then return '--' end
  local minutes = math.floor(ms / 60000)
  local seconds = math.floor(ms / 1000) % 60
  return string.format('%d:%02d.%03d', minutes, seconds, ms % 1000)
end

-- Lazily required so tests and the app can inject a module via hud.scoring.
local function scoringModule()
  if M.scoring == nil then
    local ok, mod = pcall(require, 'scoring')
    M.scoring = (ok and type(mod) == 'table') and mod or false
  end
  return M.scoring or nil
end

function M.layout(config)
  config = type(config) == 'table' and config or {}
  return {
    scale = clamp(config.hud_scale, 0.5, 3, 1),
    x = clamp(config.hud_x, 0, 10000, 8),
    y = clamp(config.hud_y, 0, 10000, 8),
    max_errors = math.floor(clamp(config.hud_max_errors, 1, 10, 3)),
  }
end

-- Penalty value semantics: nil = detector has no evidence (never shown as 0);
-- integer >= 0 = evidence-backed count; slalom = array of missed gate ids.
local function penaltyLine(field, value, amount)
  if value == nil then return field.label..': No Evidence', 'warn', 'missing' end
  if field.list then
    if type(value) ~= 'table' then return field.label..': dato invalido', 'error', 'invalid' end
    local n = #value
    for k in pairs(value) do
      if not int(k) or k < 1 or k > n then return field.label..': dato invalido', 'error', 'invalid' end
    end
    if n == 0 then return field.label..': 0', 'ok', 'ok' end
    local ids = {}
    for i = 1, math.min(n, 4) do ids[i] = clean(value[i], 12) or '?' end
    return string.format('%s: %d (%s%s) +%s', field.label, n, table.concat(ids, ','),
      n > 4 and ',...' or '', amount and M.format_ms(n * amount) or '?'), 'warn', 'ok'
  end
  if not int(value) or value < 0 or value > 100 then
    return field.label..': dato invalido', 'error', 'invalid'
  end
  if value == 0 then return field.label..': 0', 'ok', 'ok' end
  return string.format('%s: %d +%s', field.label, value,
    amount and M.format_ms(value * amount) or '?'), 'warn', 'ok'
end

function M.model(state, config)
  state = type(state) == 'table' and state or {}
  local layout = M.layout(config)
  local lines, banner = {}, nil
  local function add(text, level) lines[#lines + 1] = {text = text, level = level or 'info'} end
  -- The banner keeps the first message of the highest severity raised.
  local function raise(text, level)
    if not banner or LEVEL_RANK[level] > LEVEL_RANK[banner.level] then
      banner = {text = text, level = level}
    end
  end

  -- Pilot assignment.
  local a = type(state.assignment) == 'table' and state.assignment or nil
  local pilot = a and (clean(a.pilot_name) or clean(a.pilot_id)) or nil
  if pilot then
    add('Driver: '..pilot, 'ok')
  else
    add('Driver: none assigned (not scored)', 'warn')
    raise('NO DRIVER ASSIGNED', 'warn')
  end

  -- Route phase / outlap.
  local phase = state.phase
  if phase == 'outlap' then
    add('OUTLAP: out lap, NOT timed', 'warn')
    raise('OUTLAP - not counted', 'info')
  elseif phase == 'timed' then
    add('Lap in progress', 'info')
  elseif phase == 'waiting' then
    add('Waiting for start line', 'info')
  else
    add('Route not calibrated: no timing', 'warn')
  end

  -- Current time, penalties and provisional projection (timed lap only).
  local lap = type(state.lap) == 'table' and state.lap or {}
  local event = type(state.event) == 'string' and state.event or nil
  local timed = phase == 'timed'
  local elapsed = timed and lap.elapsed_ms or nil
  add('Time: '..M.format_ms(elapsed), 'info')
  if timed then
    local fields = event and PENALTY_FIELDS[event]
    local scoring = scoringModule()
    local amounts = scoring and type(scoring.PENALTIES) == 'table' and scoring.PENALTIES[event] or {}
    if type(lap.penalty_amounts) == 'table' then amounts = lap.penalty_amounts end
    local penalties = type(lap.penalties) == 'table' and lap.penalties or {}
    if not fields then
      add('Penalties: not available for '..(clean(event, 16) or 'unknown event'), 'warn')
      add('Projected: not available', 'warn')
    else
      local missing, invalid, events = {}, false, {}
      for _, field in ipairs(fields) do
        local value = penalties[field.key]
        local text, level, kind = penaltyLine(field, value, amounts[field.key])
        add(text, level)
        if kind == 'missing' then missing[#missing + 1] = field.label
        elseif kind == 'invalid' then invalid = true
        else events[field.key] = value end
      end
      if #missing > 0 then
        add('Projected: No Evidence ('..table.concat(missing, ', ')..')', 'warn')
      elseif int(lap.projected_ms) and lap.projected_ms >= 0 then
        add('Projected PROVISIONAL: '..M.format_ms(lap.projected_ms), 'info')
      elseif invalid or not scoring or type(scoring.corrected_ms) ~= 'function' then
        add('Projected: cannot compute', 'error')
      else
        local ok, corrected = pcall(scoring.corrected_ms, event, elapsed, events)
        if ok and int(corrected) then
          add('Projected PROVISIONAL: '..M.format_ms(corrected), 'info')
        else
          add('Projected: cannot compute', 'error')
        end
      end
    end
  else
    add('Penalties: --', 'info')
  end

  -- Last official result as returned by the server, with its status.
  local last = type(state.last_official) == 'table' and state.last_official or nil
  if not last then
    add('Last official: --', 'info')
  else
    local status = type(last.status) == 'string' and last.status or nil
    local level = status and KNOWN_STATUS[status] or nil
    if not level then
      add('Last official: unknown status ('..(clean(status, 20) or 'nil')..')', 'error')
    elseif status == 'VALID' then
      add('Last official: '..M.format_ms(last.corrected_ms)..' VALID', 'ok')
    else
      add('Last: '..M.format_ms(last.raw_ms)..' raw '..status..' (unofficial)', level)
    end
  end

  -- This station's own result: raw + configured penalties (not an official time).
  -- The raw part is AC's own lap time only when state == 'confirmed'; otherwise it is the
  -- route estimate and is labelled as such (an estimate is never shown as AC's time).
  local SOURCE = {confirmed = 'AC', pending = 'EST, AC pending', unmatched = 'EST',
    mismatch = 'EST, AC MISMATCH'}
  local ll = type(state.last_local) == 'table' and state.last_local or nil
  if ll and int(ll.raw_ms) and int(ll.corrected_ms) then
    local src = ll.state ~= nil and (SOURCE[ll.state] or 'EST') or nil
    add('Last Final: '..M.format_ms(ll.corrected_ms)..' ('..M.format_ms(ll.raw_ms)..' + '..
      string.format('%.1f', (ll.penalty_ms or 0) / 1000)..' s)'..(src and ' '..src or ''),
      ((ll.penalty_ms or 0) > 0 or (ll.state ~= nil and ll.state ~= 'confirmed')) and 'warn' or 'ok')
  end
  local bl = type(state.best_local) == 'table' and state.best_local or nil
  if bl and int(bl.corrected_ms) then
    local src = bl.state ~= nil and (SOURCE[bl.state] or 'EST') or nil
    add('Best Final: '..M.format_ms(bl.corrected_ms)..(src and ' '..src or ''),
      (bl.state ~= nil and bl.state ~= 'confirmed') and 'warn' or 'ok')
  end

  -- Best corrected lap: only a VALID result from the server counts.
  local best = type(state.best) == 'table' and state.best or nil
  if best and best.status == 'VALID' and int(best.corrected_ms) and best.corrected_ms > 0 then
    add('Best Corrected: '..M.format_ms(best.corrected_ms), 'ok')
  else
    add('Best Corrected: --', 'info')
  end

  -- Synchronisation.
  local sync = type(state.sync) == 'table' and state.sync or {}
  local pending = int(sync.pending) and sync.pending >= 0 and sync.pending or nil
  if sync.online == true then add('Server: Online', 'ok')
  elseif sync.online == false then add('Server: Offline', 'warn')
  else add('Server: unknown', 'warn') end
  if pending == nil then add('Pending queue: unknown', 'warn')
  elseif pending == 0 then add('Pending queue: 0', 'ok')
  else add('Pending queue: '..pending, 'warn') end
  if sync.online ~= true and pending and pending > 0 then
    raise('OFFLINE: '..pending..' lap(s) in local queue', 'warn')
  end
  local ack = type(sync.last_ack) == 'table' and sync.last_ack or nil
  if ack and clean(ack.lap_id) then
    local age = finite(ack.age_s) and ack.age_s >= 0 and string.format(' hace %ds', math.floor(ack.age_s)) or ''
    add('Last ack: '..clean(ack.lap_id, 32)..age, 'info')
  else
    add('Last ack: --', 'info')
  end
  local persistence = sync.persistence
  if persistence == 'corrupt' then
    add('Storage CORRUPT: manual recovery', 'error')
    raise('STORAGE CORRUPT - tell the operator', 'error')
  elseif persistence == 'unavailable' then
    add('Storage UNAVAILABLE: not durable', 'error')
    raise('STORAGE UNAVAILABLE', 'error')
  elseif persistence == 'degraded' then
    add('Storage DEGRADED: not durable', 'warn')
    raise('STORAGE DEGRADED', 'warn')
  elseif persistence ~= 'ok' then
    add('Storage: unknown', 'warn')
  end

  -- Critical detector errors (bounded).
  local errors = type(state.detector_errors) == 'table' and state.detector_errors or {}
  local shown = 0
  for i = 1, #errors do
    local text = clean(errors[i], 48)
    if text then
      if shown < layout.max_errors then add('Detector ERROR: '..text, 'error') end
      shown = shown + 1
    end
  end
  if shown > layout.max_errors then add(string.format('(+%d more errors)', shown - layout.max_errors), 'error') end
  if shown > 0 then raise('DETECTOR ERROR', 'error') end

  local status = clean(state.status, 60)
  if status then add('Status: '..status, 'info') end

  return {
    schema = M.SCHEMA_VERSION,
    title = 'Rice Trials - PROVISIONAL / CSP not validated',
    banner = banner,
    lines = lines,
    layout = layout,
  }
end

-- Recompute the model at most every interval seconds (default 0.25 s) so the
-- per-frame window callback only draws a cached table.
function M.cache(interval)
  local c = {interval = clamp(interval, 0, 5, 0.25), clock = 0, current = nil}
  function c:update(dt, state, config)
    self.clock = self.clock + (finite(dt) and dt > 0 and dt or 0)
    if not self.current or self.clock >= self.interval then
      self.clock = 0
      self.current = M.model(state, config)
    end
    return self.current
  end
  function c:invalidate() self.current = nil end
  return c
end

local COLORS = {ok = {0.35, 1, 0.35}, info = {1, 1, 1}, warn = {1, 0.85, 0.1}, error = {1, 0.3, 0.3}}

local function fn(t, k) return type(t) == 'table' and type(t[k]) == 'function' end

local function render(u, model, layout, useDWrite, vec2, rgbm)
  local scale = layout.scale
  local size = 15 * scale
  local lineH = math.floor(size * 1.35 + 0.5)
  local pad = math.floor(6 * scale + 0.5)
  local x, y = layout.x, layout.y
  local rows = {{text = model.title or 'Rice Trials', level = 'info'}}
  if type(model.banner) == 'table' and model.banner.text then
    rows[#rows + 1] = {text = '>> '..tostring(model.banner.text), level = model.banner.level}
  end
  for _, line in ipairs(type(model.lines) == 'table' and model.lines or {}) do rows[#rows + 1] = line end
  if vec2 and rgbm and fn(u, 'drawRectFilled') then
    local width = 420 * scale
    if fn(u, 'windowSize') then
      local ok, size2 = pcall(u.windowSize)
      if ok and size2 and finite(size2.x) and size2.x > x then width = size2.x - x end
    end
    u.drawRectFilled(vec2(x, y), vec2(x + width, y + 2 * pad + #rows * lineH), rgbm(0, 0, 0, 0.8), 4)
  end
  for i, row in ipairs(rows) do
    if vec2 and fn(u, 'setCursor') then u.setCursor(vec2(x + pad, y + pad + (i - 1) * lineH)) end
    local c = COLORS[row.level] or COLORS.info
    local color = rgbm and rgbm(c[1], c[2], c[3], 1) or nil
    local text = tostring(row.text)
    if useDWrite and color then u.dwriteText(text, size, color)
    elseif color and fn(u, 'textColored') then u.textColored(text, color)
    elseif fn(u, 'text') then u.text(text) end
  end
end

-- Returns true when the frame was drawn. Never raises: a broken UI must not
-- affect the caller (scoring runs in script.update anyway).
function M.draw(model, config)
  local u = rawget(_G, 'ui')
  if type(model) ~= 'table' or type(u) ~= 'table' then return false end
  local layout = type(model.layout) == 'table' and model.layout or M.layout(config)
  local vec2, rgbm = rawget(_G, 'vec2'), rawget(_G, 'rgbm')
  -- DWrite only when push/pop are both present, so the font stack stays balanced.
  local useDWrite = fn(u, 'pushDWriteFont') and fn(u, 'popDWriteFont') and fn(u, 'dwriteText')
  local font = type(config) == 'table' and type(config.hud_font) == 'string' and config.hud_font or 'Segoe UI;Weight=Bold'
  if useDWrite and not pcall(u.pushDWriteFont, font) then useDWrite = false end
  local ok = pcall(render, u, model, layout, useDWrite, vec2, rgbm)
  if useDWrite then pcall(u.popDWriteFont) end
  return ok
end

return M
