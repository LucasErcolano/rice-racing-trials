-- Rice Trials native lap capture: Assetto Corsa's own lap counter as a local backup record.
--
-- Whenever ac.getCar(0).lapCount goes up, a draft {time_ms, valid, ids} is stored in its OWN
-- journal (name 'native_laps', same A/B slots + readback as the queue) and is never sent to
-- /api/laps: a native lap has no cone/off-track/slalom evidence, so it is not a scorable lap.
-- It exists so a lap the geometric detector missed (or lost across a restart) still leaves a
-- confirmed time on disk.
--
--  * First reading (app start/reload) is a baseline, not a lap. No duplicates after a reload.
--  * lapCount going DOWN (session restart / reset) becomes the new reference, not a lap.
--  * A draft is saved and read back before anything else; if the save fails it stays in memory
--    and is retried every few seconds (state 'unsaved').
--  * When the route detector completes a lap with a similar raw time in the same session, the
--    draft is tagged with that lap_id (reconciled) so the two are never counted twice.
local M = {}
M.JOURNAL_NAME = 'native_laps'
M.TOLERANCE_MS = 2000   -- native vs detector raw time still counted as the same lap
M.MAX_DRAFTS = 500
M.RETRY_S = 3

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end
local function isint(n) return finite(n) and n % 1 == 0 end
-- CSP cdata raises on fields its version does not define.
local function field(obj, key)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if ok then return v end
  return nil
end

-- opts: journal (object with load()/save(items)), nonce (string or function), context
--       (function -> {station_id, session_id, pilot_id}), on_event (function(kind, table)).
function M.new(opts)
  opts = type(opts) == 'table' and opts or {}
  local o = {drafts = {}, ref = nil, session = 0, seen = false, state = 'new', error = nil,
    routeLaps = {}, dirty = false, retry = 0, counter = 0}
  local journal = opts.journal

  local function event(kind, t)
    if type(opts.on_event) == 'function' then pcall(opts.on_event, kind, t) end
  end
  local function nonce()
    local n = type(opts.nonce) == 'function' and select(2, pcall(opts.nonce)) or opts.nonce
    return type(n) == 'string' and n:match('^[%w_.-]+$') and n or 'n0'
  end

  local function save()
    if not journal then o.state, o.error = 'unsaved', 'no journal'; return false end
    local ok, saved, err = pcall(journal.save, journal, o.drafts)
    if ok and saved == true then o.state, o.error, o.dirty = 'ok', nil, false; return true end
    o.state, o.error, o.dirty = 'unsaved', tostring(ok and err or saved), true
    return false
  end

  function o:load()
    if not journal then self.state, self.error = 'unsaved', 'no journal'; return false end
    local ok, items, state = pcall(journal.load, journal)
    if not ok then self.state, self.error = 'unsaved', tostring(items); return false end
    if state == 'corrupt' then self.state, self.error = 'corrupt', 'manual recovery required'; return false end
    self.drafts = {}
    for _, d in ipairs(type(items) == 'table' and items or {}) do
      if type(d) == 'table' and type(d.id) == 'string' then self.drafts[#self.drafts + 1] = d end
    end
    self.state = 'ok'
    return true
  end

  local function trim()
    while #o.drafts > M.MAX_DRAFTS do
      local drop = 1
      for i, d in ipairs(o.drafts) do if d.rt_lap_id then drop = i; break end end
      table.remove(o.drafts, drop)
    end
  end

  -- Pair unreconciled drafts with detector laps of the same session (closest time wins).
  local function match()
    local changed = false
    for _, r in ipairs(o.routeLaps) do
      if not r.used then
        local best, bestDiff
        for _, d in ipairs(o.drafts) do
          if not d.rt_lap_id and d.session_seq == r.session and isint(d.time_ms) then
            local diff = math.abs(d.time_ms - r.raw_ms)
            if diff <= M.TOLERANCE_MS and (not bestDiff or diff < bestDiff) then best, bestDiff = d, diff end
          end
        end
        if best then
          best.rt_lap_id, best.rt_raw_ms, r.used, changed = r.lap_id, r.raw_ms, true, true
          event('native_reconciled', {id = best.id, rt_lap_id = r.lap_id})
        end
      end
    end
    if changed then save() end
  end

  function o:session_start()
    self.session, self.ref = self.session + 1, nil
  end

  -- Called by RiceTrials when the detector completes a lap (after it is queued).
  function o:note_route_lap(lap_id, raw_ms)
    if type(lap_id) ~= 'string' or not isint(raw_ms) then return end
    self.routeLaps[#self.routeLaps + 1] = {lap_id = lap_id, raw_ms = raw_ms, session = self.session}
    while #self.routeLaps > 8 do table.remove(self.routeLaps, 1) end
    match()
  end

  function o:observe(car, dt)
    local lc = field(car, 'lapCount')
    if isint(lc) and lc >= 0 then
      self.seen = true
      if self.ref == nil or lc < self.ref then
        self.ref = lc                    -- baseline, or counter reset: a reference, not a lap
      elseif lc > self.ref then
        local missed = lc - self.ref - 1
        self.ref = lc
        local t, valid = field(car, 'previousLapTimeMs'), field(car, 'isLastLapValid')
        local ctx = type(opts.context) == 'function' and select(2, pcall(opts.context)) or nil
        ctx = type(ctx) == 'table' and ctx or {}
        self.counter = self.counter + 1
        local d = {id = string.format('%s-%d-%d-%d', nonce(), self.session, lc, self.counter),
          session_seq = self.session, lap_count = lc, missed = missed > 0 and missed or nil,
          time_ms = (isint(t) and t > 0 and t <= 3600000) and t or nil,
          valid = nil,
          station_id = ctx.station_id, session_id = ctx.session_id, pilot_id = ctx.pilot_id,
          captured_at = os.time()}
        if type(valid) == 'boolean' then d.valid = valid end -- not `and/or`: false must survive
        self.drafts[#self.drafts + 1] = d
        trim()
        save()
        event('native_lap', {lap_count = lc, time_ms = d.time_ms, valid = d.valid, saved = not self.dirty})
        match()
      end
    end
    if self.dirty and finite(dt) then
      self.retry = self.retry + dt
      if self.retry >= M.RETRY_S then self.retry = 0; save() end
    end
  end

  function o:stats()
    local unmatched = 0
    for _, d in ipairs(self.drafts) do if not d.rt_lap_id then unmatched = unmatched + 1 end end
    return {state = self.state, error = self.error, count = #self.drafts, unmatched = unmatched,
      lapcount_available = self.seen}
  end

  function o:last()
    return self.drafts[#self.drafts]
  end

  o:load()
  return o
end

return M
