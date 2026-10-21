-- Rice Trials penalty settings: values, persistence, application and the in-game panel.
-- The station computes the final time itself; the server stores what the station sends.
-- Detection is independent of these settings: a switched-off penalty is still detected and
-- reported in `events`, it just adds 0 s. The settings are read when a lap closes, and each
-- lap payload carries the snapshot it was scored with, so older laps are never recalculated.
-- Only ui.text/button/slider/sameLine and ac.setClipboardText are used (all guarded).
local M = {}
M.VERSION = 1
M.KEYS = {'cone', 'off_course', 'slalom'}
M.LABELS = {cone = 'Conos', off_course = 'Off-track', slalom = 'Slalom omitido'}
M.MAX_S = 60
-- An off-track that starts this close (game seconds) to a missed slalom gate is the same
-- incident: only the slalom penalty is charged (while the slalom penalty is on).
M.DEDUPE_WINDOW_S = 3
M.FILE = 'penalty_config.json'
M.STORAGE_KEY = 'rice_trials_penalty_config'

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function count(n) return finite(n) and n >= 0 and n % 1 == 0 and n or 0 end

function M.defaults()  -- FSAE rules: cone 2 s, off-course 20 s, missed slalom gate 20 s
  return {cone = {on = true, s = 2}, off_course = {on = true, s = 20}, slalom = {on = true, s = 20}}
end

local function clampSeconds(v, default)
  if not finite(v) then return default end
  v = math.max(0, math.min(M.MAX_S, v))
  return math.floor(v * 2 + 0.5) / 2  -- half-second steps
end

function M.sanitize(t)
  local out = M.defaults()
  if type(t) ~= 'table' then return out end
  for _, k in ipairs(M.KEYS) do
    local e = t[k]
    if type(e) == 'table' then
      if type(e.on) == 'boolean' then out[k].on = e.on end
      out[k].s = clampSeconds(e.s, out[k].s)
    end
  end
  return out
end

local function ms(s) return math.floor(s * 1000 + 0.5) end

-- counts = {cone=, off_course=, slalom=} detected this lap.
-- times  = {off = {game-clock s of each off-course start}, slalom = {game-clock s of each missed gate}}
-- Returns {penalty_ms, applied = {cone=, off_course=, slalom=} (counts actually charged),
--          deduped = off-course incidents merged into a slalom miss}.
function M.apply(cfg, counts, times)
  cfg = M.sanitize(cfg)
  counts = type(counts) == 'table' and counts or {}
  local applied = {cone = count(counts.cone), off_course = count(counts.off_course), slalom = count(counts.slalom)}
  local deduped = 0
  if cfg.slalom.on and cfg.slalom.s > 0 and applied.off_course > 0 and type(times) == 'table'
      and type(times.off) == 'table' and type(times.slalom) == 'table' then
    for _, t in ipairs(times.off) do
      for _, g in ipairs(times.slalom) do
        if finite(t) and finite(g) and math.abs(t - g) <= M.DEDUPE_WINDOW_S then deduped = deduped + 1; break end
      end
    end
    deduped = math.min(deduped, applied.off_course)
    applied.off_course = applied.off_course - deduped
  end
  local total = 0
  for _, k in ipairs(M.KEYS) do
    if cfg[k].on then total = total + applied[k] * ms(cfg[k].s) else applied[k] = 0 end
  end
  return {penalty_ms = total, applied = applied, deduped = deduped}
end

-- Per-key milliseconds currently charged (0 when switched off), for the HUD.
function M.amounts(cfg)
  cfg = M.sanitize(cfg)
  local out = {}
  for _, k in ipairs(M.KEYS) do out[k] = cfg[k].on and ms(cfg[k].s) or 0 end
  return out
end

-- ------------------------------------------------------------------ persistence
-- opts: io (io.save/io.load/io.createDir, optional), dir, json = {encode, decode},
--       storage (ac.storage-like, optional fallback)
function M.new(opts)
  opts = type(opts) == 'table' and opts or {}
  local store = {cfg = M.defaults(), state = 'default', error = nil, rev = 0, _idle = nil}
  local json = type(opts.json) == 'table' and opts.json or nil
  local path = type(opts.dir) == 'string' and opts.dir ~= '' and (opts.dir..'/'..M.FILE) or nil

  local function encode()
    if not json then return nil end
    local ok, text = pcall(json.encode, store.cfg)
    return ok and type(text) == 'string' and text or nil
  end
  local function decode(text)
    if not json or type(text) ~= 'string' or text == '' then return nil end
    local ok, t = pcall(json.decode, text)
    return ok and type(t) == 'table' and t or nil
  end

  function store:json() return encode() end

  function store:load()
    local text
    if path and opts.io and type(opts.io.load) == 'function' then
      local ok, r = pcall(opts.io.load, path)
      if ok and type(r) == 'string' and r ~= '' then text = r end
    end
    local t = decode(text)
    if t then self.cfg, self.state = M.sanitize(t), 'ok'; return true end
    if opts.storage ~= nil then
      local ok, r = pcall(function() return opts.storage[M.STORAGE_KEY] end)
      t = ok and decode(r) or nil
      if t then self.cfg, self.state = M.sanitize(t), 'degraded'; return true end
    end
    self.cfg, self.state = M.defaults(), 'default'
    return false
  end

  function store:save()
    local text = encode()
    if not text then self.state, self.error = 'unsaved', 'json unavailable'; return false end
    if path and opts.io and type(opts.io.save) == 'function' then
      if type(opts.io.createDir) == 'function' then pcall(opts.io.createDir, opts.dir) end
      local ok, r = pcall(opts.io.save, path, text)
      if ok and r ~= false then
        local okb, back = pcall(opts.io.load, path)
        if okb and back == text then self.state, self.error = 'ok', nil; return true end
      end
    end
    if opts.storage ~= nil and pcall(function() opts.storage[M.STORAGE_KEY] = text end) then
      self.state, self.error = 'degraded', 'file not writable'; return true
    end
    self.state, self.error = 'unsaved', 'no writable storage'
    return false
  end

  function store:set(key, field, value)
    if not M.LABELS[key] then return false end
    local candidate = M.sanitize(self.cfg)
    if field == 'on' and type(value) == 'boolean' then candidate[key].on = value
    elseif field == 's' and finite(value) then candidate[key].s = clampSeconds(value, candidate[key].s)
    else return false end
    self.cfg, self.rev, self._idle = candidate, self.rev + 1, 0
    return true
  end

  function store:reset()
    self.cfg, self.rev, self._idle = M.defaults(), self.rev + 1, 0
    self:save(); self._idle = nil
  end

  -- Called every frame by the panel: writes the file shortly after the last edit, not on
  -- every slider frame.
  function store:flush(dt)
    if self._idle == nil then return end
    self._idle = self._idle + (finite(dt) and dt or 0)
    if self._idle >= 0.5 then self._idle = nil; self:save() end
  end

  store:load()
  return store
end

-- ------------------------------------------------------------------ panel
function M.draw(store, ui, ac, dt)
  if type(store) ~= 'table' or type(ui) ~= 'table' then return false end
  store:flush(dt)
  ui.text('Configuracion de penalizaciones')
  for _, k in ipairs(M.KEYS) do
    local e = store.cfg[k]
    if ui.button((e.on and '[x] ' or '[ ] ')..M.LABELS[k]..'##on_'..k) then store:set(k, 'on', not e.on) end
    if ui.sameLine then ui.sameLine(150) end
    if ui.setNextItemWidth then ui.setNextItemWidth(130) end
    local v, changed = ui.slider('##s_'..k, e.s, 0, M.MAX_S, '%.1f seg.')
    if changed and finite(v) then store:set(k, 's', v) end
  end
  if ui.button('Restaurar FSAE##reset') then store:reset() end
  if ui.sameLine then ui.sameLine() end
  if ui.button('Copiar configuracion##copy') and ac and type(ac.setClipboardText) == 'function' then
    local text = store:json()
    if text then pcall(ac.setClipboardText, text) end
  end
  ui.text('Aplica al cerrar cada vuelta; no recalcula vueltas anteriores.')
  ui.text('Apagada = se detecta y se registra, pero suma 0 s.')
  ui.text(string.format('Off-track a menos de %d s de un slalom omitido: se cobra una vez.', M.DEDUPE_WINDOW_S))
  ui.text('Guardado: '..tostring(store.state)..(store.error and (' ('..store.error..')') or ''))
  return true
end

return M
