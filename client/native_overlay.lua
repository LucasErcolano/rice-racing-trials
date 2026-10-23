-- Native-style timing/delta overlay: PURE presentation model (no ui, no ac, no io).
--
-- Visual goal: reproduce the look of Assetto Corsa's own lap timing and delta
-- meter, not invent a new competition aesthetic. Recreated, not copied: only
-- layout/colours/formatting are derived from the native UI, no original asset
-- is redistributed.
--
-- Split of concerns (see docs/native-overlays.md):
--   * timing/penalty math stays in delta.lua, scoring.lua, penalties.lua and
--     RiceTrials.lua. Nothing here recomputes or mutates a lap result.
--   * this module only turns an already computed state into a render model.
-- A failure while drawing can never interrupt timing or the queue: the caller
-- wraps every call in pcall and falls back to the existing text window.
local M = {}
M.SCHEMA_VERSION = 1

-- AC's native timing look: thin uppercase labels, big monospaced times, a dark
-- translucent panel. Values that carry no evidence are never shown as 0.
local LEVELS = {ok = 'ok', info = 'info', warn = 'warn', error = 'error'}
local PALETTE = {
  label = {0.60, 0.62, 0.66},   -- dim grey for field labels (AC style)
  value = {1.00, 1.00, 1.00},   -- white for times
  best = {0.45, 0.75, 1.00},    -- AC "best" blue-ish highlight
  good = {0.30, 0.85, 0.35},    -- green: faster / clean
  bad = {0.95, 0.28, 0.28},     -- red: slower / penalised
  pending = {1.00, 0.78, 0.15}, -- amber: provisional, evidence still missing
  dim = {0.45, 0.45, 0.45},     -- unavailable (--, never zero)
  panel = {0.0, 0.0, 0.0},
}
local PANEL_ALPHA = 0.62

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function int(n) return finite(n) and n % 1 == 0 end

function M.palette() return PALETTE end
function M.panel_alpha() return PANEL_ALPHA end

-- AC shows lap times as m:ss.mmm (delta values as +s.sss / -s.sss).
-- Returns nil for anything that is not a usable value, so the caller can draw
-- the native "--" placeholder instead of a misleading zero.
function M.format_time(ms)
  if not int(ms) or ms < 0 or ms > 359999999 then return nil end
  local minutes = math.floor(ms / 60000)
  local seconds = math.floor(ms / 1000) % 60
  return string.format('%d:%02d.%03d', minutes, seconds, ms % 1000)
end

-- Seconds with sign, two decimals (AC's delta meter precision).
function M.format_signed(seconds)
  if not finite(seconds) or math.abs(seconds) >= 100 then return nil end
  return string.format('%+.2f', seconds)
end

local function clamp(v, lo, hi, default)
  if not finite(v) then return default end
  if v < lo then return lo end
  if v > hi then return hi end
  return v
end

function M.layout(config)
  config = type(config) == 'table' and config or {}
  return {
    scale = clamp(config.scale, 0.5, 3, 1),
    x = clamp(config.x, 0, 10000, 8),
    y = clamp(config.y, 0, 10000, 8),
    alpha = clamp(config.alpha, 0, 1, PANEL_ALPHA),
    accent = type(config.accent) == 'string' and config.accent or nil,
  }
end

-- One rendered row of the timing overlay.
local function row(label, text, color, size)
  return {label = label, text = text or '--', color = color or PALETTE.dim, size = size or 'value'}
end

-- A penalty line: discrete, under the lap time, only when evidence exists.
-- `pending` marks a still provisional/unknown penalty total (never zero).
local function penalty_row(text, color, pending)
  return {label = 'PENALTY', text = text, color = color, size = 'small', pending = pending or false}
end

-- LAST / BEST rows. A final time built on AC's own lap time is white/blue; one still built on
-- the route estimate is amber and says so on a small SOURCE row (never passed off as AC's).
local SOURCE_TEXT = {confirmed = 'AC TIME', pending = 'ESTIMATE, AC PENDING',
  unmatched = 'ESTIMATE', mismatch = 'ESTIMATE, AC MISMATCH'}
local function last_best(rows, state, best)
  local lastEst = state.last_ms ~= nil and state.last_state ~= nil and state.last_state ~= 'confirmed'
  rows[#rows + 1] = row('LAST', M.format_time(state.last_ms) or '--', lastEst and PALETTE.pending or PALETTE.value)
  if state.last_ms ~= nil and SOURCE_TEXT[state.last_state] then
    rows[#rows + 1] = row('SOURCE', SOURCE_TEXT[state.last_state], lastEst and PALETTE.pending or PALETTE.label, 'small')
  end
  local bestEst = state.official_status ~= 'VALID' and best ~= nil and state.best_state ~= nil and
    state.best_state ~= 'confirmed'
  rows[#rows + 1] = row('BEST', M.format_time(best) or '--', bestEst and PALETTE.pending or PALETTE.best)
end

-- Build the Lap Timing overlay model from the driver-visible state.
--
-- state (every field optional, all values already computed by the client):
--   lap_number     integer          -- LAP nn shown by AC
--   current_ms     integer          -- live lap time (nil: no timed lap)
--   last_ms        integer          -- last finished final time (raw + penalties)
--   last_state     string           -- confirmed (AC time) | pending | unmatched | mismatch
--   best_ms        integer          -- best final time of this run
--   best_state     string           -- same states as last_state
--   official_ms    integer          -- server-confirmed VALID time, if any
--   official_status string           -- VALID | PENDING_REVIEW | ...
--   penalty_ms     integer          -- penalty total already applied to current_ms
--   penalty_pending boolean         -- true when a detector has no evidence yet
--   outlap         boolean          -- outlap in progress: AC shows no timing
--   phase          'waiting'|'outlap'|'timed'|nil  (nil: route not calibrated)
local function timing_rows(state)
  local rows = {}
  local timed = state.current_ms ~= nil
  local best = state.official_status == 'VALID' and state.official_ms or state.best_ms

  -- LAP number, AC's own header.
  if int(state.lap_number) and state.lap_number > 0 then
    rows[#rows + 1] = row('LAP', string.format('%02d', math.min(99, state.lap_number)), PALETTE.label, 'small')
  end

  if state.phase == nil then
    rows[#rows + 1] = row('TIMING', 'NO CALIBRATED', PALETTE.bad, 'small')
    return rows
  end
  if state.outlap or state.phase == 'outlap' then
    rows[#rows + 1] = row('CURRENT', 'OUTLAP', PALETTE.pending, 'small')
    last_best(rows, state, best)
    return rows
  end
  if not timed then
    rows[#rows + 1] = row('CURRENT', '--:--.---', PALETTE.dim)
    last_best(rows, state, best)
    return rows
  end

  rows[#rows + 1] = row('CURRENT', M.format_time(state.current_ms) or '--:--.---', PALETTE.value)
  -- Penalties stay discrete: a separate amber line, never merged into the time,
  -- so a jump caused by a detected penalty is visible without hiding it.
  if int(state.penalty_ms) and state.penalty_ms > 0 then
    rows[#rows + 1] = penalty_row(string.format('+%.3f', state.penalty_ms / 1000), PALETTE.pending,
      state.penalty_pending == true)
  elseif state.penalty_pending == true then
    rows[#rows + 1] = penalty_row('NO EVIDENCE', PALETTE.pending, true)
  end
  last_best(rows, state, best)
  return rows
end

function M.timing(state, config)
  state = type(state) == 'table' and state or {}
  return {
    schema = M.SCHEMA_VERSION,
    kind = 'timing',
    layout = M.layout(config),
    rows = timing_rows(state),
  }
end

-- Build the delta meter model. `delta_ms` is the ALREADY computed value from
-- delta.lua (current DRIVING time vs the best lap's driving time; penalties are
-- shown on their own PENALTY row and are not part of the delta);
-- this module never touches the delta math. Signature matches timing():
-- (value, options, config).
--
-- opts:
--   has_reference boolean          -- false: native "--" state, bar neutral
--   pending boolean                -- evidence missing or provisional value
--   full_scale_ms number           -- saturating scale (default 2000)
local DEFAULT_FULL_SCALE = 2000

function M.delta(delta_ms, opts, config)
  opts = type(opts) == 'table' and opts or {}
  local full = clamp(opts.full_scale_ms, 100, 60000, DEFAULT_FULL_SCALE)
  local has_ref = opts.has_reference == true
  local model = {
    schema = M.SCHEMA_VERSION,
    kind = 'delta',
    layout = M.layout(config),
    has_reference = has_ref,
    value_ms = delta_ms,
    text = nil,
    frac = 0,
    direction = 'none',
    color = PALETTE.dim,
    full_scale_ms = full,
    pending = opts.pending == true,
  }
  if not has_ref then
    model.text = '--'
    return model
  end
  if not finite(delta_ms) then
    -- Evidence still missing (a penalty just landed, or the sample was dropped):
    -- keep the bar neutral and say so instead of snapping to zero.
    model.text = opts.pending == true and 'PENDING' or '--'
    model.pending = true
    return model
  end
  model.text = M.format_signed(delta_ms / 1000) or '--'
  local frac = math.max(-1, math.min(1, delta_ms / full))
  model.frac = frac
  model.direction = frac < 0 and 'faster' or (frac > 0 and 'slower' or 'level')
  model.color = frac < 0 and PALETTE.good or (frac > 0 and PALETTE.bad or PALETTE.value)
  if opts.pending == true then
    -- A penalty detection can move the corrected delta abruptly: keep the
    -- incident visible (amber) while the value is still provisional.
    model.color = PALETTE.pending
    model.pending = true
  end
  return model
end

return M
