-- Rice Trials delta bar: current lap vs the best FINAL lap (raw + penalties), like AC's own meter.
--
-- A lap is recorded as `value_ms` (raw elapsed + penalties so far) at N evenly spaced progress
-- points. The best lap's trace is the reference; delta = value now - reference at the same progress.
-- Progress comes from car.splinePosition (relative to where the timed lap started). When the game
-- does not provide it, progress is the distance driven: the trace is stored by metres and
-- normalised by the lap's total distance when the lap ends (the reference then also drives the
-- distance mode for the next lap).
-- Pure logic: no ui, no ac. A lap whose spline progress jumps forward by a big step is discarded
-- and never becomes the reference.
local M = {}

M.BUCKETS = 400
local JUMP = 0.25      -- a forward progress jump above this (fraction of a lap) invalidates the lap
local MIN_DONE = 0.9   -- a spline lap must reach this progress before it can become the reference
local PT_STEP = 2.0    -- distance mode: metres between stored points

local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end

function M.new(opts)
  opts = type(opts) == 'table' and opts or {}
  local n = math.floor(finite(opts.buckets) and opts.buckets or M.BUCKETS)
  if n < 20 then n = 20 end
  local d = {n = n, ref = nil, cur = nil}

  function d:begin(spline)
    self.cur = {s0 = finite(spline) and spline or nil, dist = 0, last = 0, values = {[0] = 0},
      lastValue = 0, valid = true, rel = 0, pts = {{0, 0}}, lastPt = 0, bucket = 0}
  end

  function d:abort() self.cur = nil end

  -- spline: car.splinePosition or nil; step_m: metres driven since the last sample; value_ms:
  -- raw elapsed + penalties. Returns the current delta in ms or nil.
  function d:feed(spline, step_m, value_ms)
    local c = self.cur
    if not c or not finite(value_ms) then return nil end
    if finite(step_m) and step_m > 0 and step_m < 100 then c.dist = c.dist + step_m end
    if c.dist - c.lastPt >= PT_STEP then
      c.pts[#c.pts + 1] = {c.dist, value_ms}; c.lastPt = c.dist
    end
    local rel
    if c.s0 and finite(spline) then
      c.mode = 'spline'
      rel = (spline - c.s0) % 1
      if rel < c.rel then
        rel = nil                      -- backwards, or the line wrapped: ignore this sample
      elseif rel - c.rel > JUMP then
        c.valid = false; rel = nil
      else
        local b = math.min(self.n, math.floor(rel * self.n))
        if b > c.last then
          for i = c.last + 1, b do
            c.values[i] = c.lastValue + (value_ms - c.lastValue) * (i - c.last) / (b - c.last)
          end
          c.last = b
        end
        c.rel, c.lastValue = rel, value_ms
      end
    else
      c.mode = c.mode or 'distance'
      if self.ref and self.ref.dist then rel = c.dist / self.ref.dist end
    end
    c.via = c.mode
    return self:delta(rel, value_ms)
  end

  function d:delta(rel, value_ms)
    local c, r = self.cur, self.ref
    if not (c and r and c.valid and rel and finite(value_ms)) then return nil end
    local b = math.min(self.n, math.floor(rel * self.n))
    if b < 1 or not r.values[b] then return nil end
    return value_ms - r.values[b]
  end

  -- Resamples the stored (metres, value) points into n normalised buckets.
  local function resample(c, final_ms)
    local total = c.dist
    if total <= 0 or #c.pts < 3 then return nil end
    c.pts[#c.pts + 1] = {total, final_ms}
    local values, j = {[0] = 0}, 1
    for i = 1, n do
      local target = total * i / n
      while j < #c.pts - 1 and c.pts[j + 1][1] < target do j = j + 1 end
      local a, b = c.pts[j], c.pts[j + 1]
      local span = b[1] - a[1]
      local f = span > 0 and math.max(0, math.min(1, (target - a[1]) / span)) or 1
      values[i] = a[2] + (b[2] - a[2]) * f
    end
    values[n] = final_ms
    return values
  end

  -- final_ms: the lap's final time. Returns true when this lap became the reference.
  function d:finish(final_ms)
    local c = self.cur
    self.cur = nil
    if not (c and c.valid and finite(final_ms) and final_ms > 0) then return false end
    if self.ref and final_ms >= self.ref.final then return false end
    local values
    if c.mode == 'spline' then
      if c.last < n * MIN_DONE then return false end
      for i = c.last + 1, n do
        c.values[i] = c.lastValue + (final_ms - c.lastValue) * (i - c.last) / math.max(1, n - c.last)
      end
      c.values[n] = final_ms
      values = c.values
    else
      values = resample(c, final_ms)
    end
    if not values then return false end
    self.ref = {values = values, final = final_ms, dist = c.dist > 0 and c.dist or nil}
    return true
  end

  function d:has_reference() return self.ref ~= nil end
  function d:source() return self.cur and self.cur.via or nil end
  return d
end

-- Text for the bar: "-0.83" / "+3.20" seconds.
function M.format(delta_ms)
  if not finite(delta_ms) then return '--' end
  local s = delta_ms / 1000
  if math.abs(s) >= 100 then return s < 0 and '-99.99' or '+99.99' end
  return string.format('%+.2f', s)
end

-- Bar fill: fraction in [-1, 1] (negative = faster than the reference) and a colour level.
function M.bar(delta_ms, full_scale_ms)
  if not finite(delta_ms) then return {frac = 0, level = 'info'} end
  local scale = finite(full_scale_ms) and full_scale_ms > 0 and full_scale_ms or 2000
  local frac = math.max(-1, math.min(1, delta_ms / scale))
  return {frac = frac, level = delta_ms <= 0 and 'ok' or 'error'}
end

return M
