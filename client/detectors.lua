-- Offline evidence engines. Never turn their results directly into official penalties.
local M = {}
local function finite(n)
  return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge
end
local function point(p)
  return type(p) == 'table' and finite(p.x) and finite(p.z)
end
local function integer(n, lower, upper)
  return finite(n) and n % 1 == 0 and n >= lower and n <= upper
end

-- The JSAE InvalidateLap app reads this AC physics field; it counts rising edges,
-- not physical cone strikes. Lost samples can hide multiple excursions.
function M.off_course(config)
  if type(config) ~= 'table' or config.source ~= 'ac_physics_numberOfTyresOut'
      or not integer(config.min_out, 1, 4) then return nil end
  local machine = {count = 0, active = false, confidence = 'physics_proxy_uncertified', official = false}
  function machine:observe(sample)
    local n = type(sample) == 'table' and sample.numberOfTyresOut or nil
    if not integer(n, 0, 4) then return {status = 'unknown', confidence = self.confidence} end
    if n >= config.min_out then
      if not self.active then self.count = self.count + 1 end
      self.active = true
      return {status = 'off_course', count = self.count, confidence = self.confidence}
    end
    self.active = false
    return {status = 'on_course', count = self.count, confidence = self.confidence}
  end
  return machine
end

local function crossed(p, q, gate)
  local a, b = gate.a, gate.b
  local dx, dz = b.x - a.x, b.z - a.z
  local before = dx * (p.z-a.z) - dz * (p.x-a.x)
  local after = dx * (q.z-a.z) - dz * (q.x-a.x)
  if gate.direction * before >= 0 or gate.direction * after < 0 then return false end
  local t = before/(before-after)
  local x, z = p.x+t*(q.x-p.x), p.z+t*(q.z-p.z)
  local u = ((x-a.x)*dx+(z-a.z)*dz)/(dx*dx+dz*dz)
  if u < 0 or u > 1 then return false end
  local lateral = ((x-gate.pylon.x)*dx+(z-gate.pylon.z)*dz)/math.sqrt(dx*dx+dz*dz)
  if (gate.side == 'left' and lateral > -gate.clearance_m) or
      (gate.side == 'right' and lateral < gate.clearance_m) then return 'wrong_side' end
  return 'pass'
end
function M.slalom(config)
  if type(config) ~= 'table' or config.version ~= 'slalom-gates-v1'
      or config.status ~= 'surveyed' or type(config.gates) ~= 'table'
      or type(config.survey_id) ~= 'string' or config.survey_id == ''
      or type(config.asset_sha256) ~= 'string' or config.asset_sha256 == ''
      or #config.gates < 2 then return nil end
  local ids, last_side = {}, nil
  for _, gate in ipairs(config.gates) do
    if type(gate) ~= 'table' or type(gate.id) ~= 'string' or gate.id == '' or ids[gate.id]
        or not point(gate.a) or not point(gate.b) or not point(gate.pylon)
        or not finite(gate.clearance_m) or gate.clearance_m <= 0 or
        (gate.a.x == gate.b.x and gate.a.z == gate.b.z) or
        (gate.direction ~= 1 and gate.direction ~= -1) or
        (gate.side ~= 'left' and gate.side ~= 'right') or gate.side == last_side then return nil end
    ids[gate.id], last_side = true, gate.side
  end
  local machine = {next_gate = 1, confidence = 'surveyed_geometry_offline', official = false}
  function machine:observe(previous, current)
    if not point(previous) or not point(current) then return {status = 'unknown'} end
    local hits = {}
    for i, gate in ipairs(config.gates) do
      local hit = crossed(previous, current, gate)
      if hit then hits[#hits+1] = {index = i, status = hit} end
    end
    if #hits > 1 then return {status = 'ambiguous'} end
    if #hits == 0 then return {status = 'pending'} end
    if hits[1].index ~= self.next_gate then return {status = 'out_of_order', gate = config.gates[hits[1].index].id} end
    if hits[1].status ~= 'pass' then return {status = 'wrong_side', gate = config.gates[hits[1].index].id} end
    self.next_gate = self.next_gate + 1
    if self.next_gate > #config.gates then
      return {status = 'sequence_observed', confidence = self.confidence}
    end
    return {status = 'progress', gate = config.gates[hits[1].index].id, confidence = self.confidence}
  end
  return machine
end

function M.cones()
  local machine = {count = 0, seen = {}, official = false, confidence = 'operator_review_only'}
  function machine:observe(evidence)
    if type(evidence) ~= 'table' or type(evidence.cone_id) ~= 'string' or
        evidence.cone_id == '' then return {status = 'unknown'} end
    if evidence.source ~= 'operator_adjudication' or evidence.contact ~= true or
        evidence.decision ~= 'hit' or type(evidence.reviewer) ~= 'string' or
        evidence.reviewer == '' or type(evidence.evidence_id) ~= 'string' or
        evidence.evidence_id == '' then
      if evidence.contact == true then return {status = 'review'} end
      return {status = 'unknown'}
    end
    if self.seen[evidence.cone_id] then return {status = 'duplicate', count = self.count} end
    self.seen[evidence.cone_id] = true
    self.count = self.count + 1
    return {status = 'adjudicated', count = self.count, confidence = self.confidence}
  end
  return machine
end

-- Geometric cone-contact CANDIDATES: oriented car box (AC collider footprint, shrunk by
-- config.shrink_m to cut false positives) swept between frames against a list of cone
-- centres. Advisory only: results are 'candidate', never an official penalty.
function M.cone_boxes(config)
  if type(config) ~= 'table' or config.version ~= 'cone-boxes-v1' or type(config.cones) ~= 'table'
      or #config.cones < 1 then return nil end
  local hw, hl, shrink, radius, depth = config.half_width_m, config.half_length_m,
    config.shrink_m, config.cone_radius_m, config.min_depth_m
  if not (finite(hw) and finite(hl) and finite(shrink) and finite(radius) and finite(depth))
      or hw <= 0 or hl <= 0 or shrink < 0 or radius <= 0 or depth < 0
      or shrink >= hw or shrink >= hl then return nil end
  local cell, grid, ids = 4, {}, {}
  for _, cone in ipairs(config.cones) do
    if type(cone) ~= 'table' or type(cone.id) ~= 'string' or cone.id == '' or ids[cone.id]
        or not point(cone) then return nil end
    ids[cone.id] = true
    local key = math.floor(cone.x / cell) .. ':' .. math.floor(cone.z / cell)
    grid[key] = grid[key] or {}
    grid[key][#grid[key] + 1] = cone
  end
  local bw, bl = hw - shrink, hl - shrink
  local machine = {seen = {}, count = 0, official = false, confidence = 'geometric_candidate_uncertified'}
  -- Penetration (m) of one cone into the car box at pose (x, z, look); <= 0 is no contact.
  local function penetration(x, z, lx, lz, cone)
    local dx, dz = cone.x - x, cone.z - z
    local f, l = dx * lx + dz * lz, -dx * lz + dz * lx
    local ox, oz = math.max(math.abs(f) - bl, 0), math.max(math.abs(l) - bw, 0)
    return radius - math.sqrt(ox * ox + oz * oz)
  end
  local function nearby(x, z, out)
    local cx, cz = math.floor(x / cell), math.floor(z / cell)
    for i = cx - 1, cx + 1 do for j = cz - 1, cz + 1 do
      local bucket = grid[i .. ':' .. j]
      if bucket then for _, cone in ipairs(bucket) do out[#out + 1] = cone end end
    end end
  end
  function machine:reset() self.seen, self.count = {}, 0 end
  -- previous/current: {x, z, lx, lz}; the pose is interpolated every <= 0.1 m.
  function machine:observe(previous, current)
    local function pose(p) return type(p) == 'table' and point(p) and finite(p.lx) and finite(p.lz) end
    if not pose(current) then return {status = 'unknown'} end
    local a = pose(previous) and previous or current
    local dist = math.sqrt((current.x - a.x) ^ 2 + (current.z - a.z) ^ 2)
    if dist > 20 then a = current; dist = 0 end
    local norm = math.sqrt(current.lx ^ 2 + current.lz ^ 2)
    if norm < 0.5 then return {status = 'unknown'} end
    local steps = math.max(1, math.ceil(dist / 0.1))
    local candidates, new = {}, {}
    for k = 1, steps do
      local t = k / steps
      local x, z = a.x + (current.x - a.x) * t, a.z + (current.z - a.z) * t
      local lx, lz = a.lx + (current.lx - a.lx) * t, a.lz + (current.lz - a.lz) * t
      local n = math.sqrt(lx * lx + lz * lz)
      if n >= 0.5 then
        lx, lz = lx / n, lz / n
        candidates = {}
        nearby(x, z, candidates)
        for _, cone in ipairs(candidates) do
          if not self.seen[cone.id] and penetration(x, z, lx, lz, cone) >= depth then
            self.seen[cone.id] = true
            self.count = self.count + 1
            new[#new + 1] = cone.id
          end
        end
      end
    end
    if #new > 0 then return {status = 'candidate', cones = new, count = self.count, confidence = self.confidence} end
    return {status = 'clear', count = self.count}
  end
  return machine
end

-- ---------------------------------------------------------------- track limits (off-course)
local function pip(poly, x, z)
  local inside, j = false, #poly
  for i = 1, #poly do
    local a, b = poly[i], poly[j]
    if (a[2] > z) ~= (b[2] > z) and x < (b[1] - a[1]) * (z - a[2]) / (b[2] - a[2]) + a[1] then inside = not inside end
    j = i
  end
  return inside
end
local function edge_distance(poly, x, z)
  local best, n = math.huge, #poly
  for i = 1, n do
    local a, b = poly[i], poly[i % n + 1]
    local dx, dz = b[1] - a[1], b[2] - a[2]
    local q = dx * dx + dz * dz
    local t = q > 0 and math.max(0, math.min(1, ((x - a[1]) * dx + (z - a[2]) * dz) / q)) or 0
    local d = math.sqrt((x - a[1] - t * dx) ^ 2 + (z - a[2] - t * dz) ^ 2)
    if d < best then best = d end
  end
  return best
end
local function valid_polygon(poly)
  if type(poly) ~= 'table' or #poly < 3 then return false end
  for _, p in ipairs(poly) do
    if type(p) ~= 'table' or not finite(p[1]) or not finite(p[2]) then return false end
  end
  return true
end

-- Off-course = ALL FOUR wheel contact points outside the asphalt ring (FSAE rule), beyond a
-- tolerance margin, for hold_s; one event per excursion, re-armed after rearm_s back on course.
function M.track_limits(config)
  local lim = type(config) == 'table' and config.limits or nil
  if type(lim) ~= 'table' or not valid_polygon(lim.outer) or not valid_polygon(lim.inner) then return nil end
  local function opt(v, d) return finite(v) and v >= 0 and v or d end
  local margin, hold, rearm = opt(config.off_margin_m, 0.3), opt(config.off_hold_s, 0.15), opt(config.off_rearm_s, 0.5)
  local hw, hl = opt(config.wheel_half_track_m, 0.6), opt(config.wheel_half_base_m, 0.85)
  local outer, inner = lim.outer, lim.inner
  local machine = {count = 0, ids = {}, official = false, confidence = 'geometric_asphalt_uncertified',
    out_s = 0, in_s = 0, active = false, margin = margin}
  function machine:reset() self.count, self.ids, self.out_s, self.in_s, self.active = 0, {}, 0, 0, false end
  local function outside(x, z)
    if pip(outer, x, z) and not pip(inner, x, z) then return false end
    return math.min(edge_distance(outer, x, z), edge_distance(inner, x, z)) > margin
  end
  function machine:observe(pose, dt)
    if type(pose) ~= 'table' or not point(pose) or not finite(pose.lx) or not finite(pose.lz)
        or not finite(dt) or dt <= 0 or dt > 1 then return {status = 'unknown'} end
    local norm = math.sqrt(pose.lx ^ 2 + pose.lz ^ 2)
    if norm < 0.5 then return {status = 'unknown'} end
    local fx, fz = pose.lx / norm, pose.lz / norm
    local lx, lz = -fz, fx
    local allout = false
    -- The car centre inside the ring can never mean four wheels out (ring is ~10 m wide).
    if not (pip(outer, pose.x, pose.z) and not pip(inner, pose.x, pose.z)) then
      allout = true
      for _, sz in ipairs({-1, 1}) do for _, sx in ipairs({-1, 1}) do
        if allout and not outside(pose.x + fx * sz * hl + lx * sx * hw, pose.z + fz * sz * hl + lz * sx * hw) then allout = false end
      end end
    end
    if allout then
      self.out_s, self.in_s = self.out_s + dt, 0
      if not self.active and self.out_s >= hold then
        self.active = true
        self.count = self.count + 1
        self.ids[#self.ids + 1] = 'OC' .. self.count
        return {status = 'off_course', count = self.count, new = true}
      end
      return {status = self.active and 'off_course' or 'on_course', count = self.count}
    end
    self.out_s, self.in_s = 0, self.in_s + dt
    if self.active and self.in_s >= rearm then self.active = false end
    return {status = 'on_course', count = self.count}
  end
  return machine
end

-- ---------------------------------------------------------------- slalom (pylon runs)
-- Each gate is a line through a pylon perpendicular to the direction of travel. The expected
-- side (+1/-1 of the lateral offset from the pylon) comes from the reference racing line: it
-- weaves around the pylons and tells which entry phase is preferred, but either phase is legal: only a
-- break of the alternation (the minimum set of gates on the wrong side) is a slalom penalty. A run with a gate never crossed is INCOMPLETE and
-- is reported, never penalised automatically.
function M.slalom_runs(config)
  local sl = type(config) == 'table' and config.slalom or nil
  if type(sl) ~= 'table' or type(sl.runs) ~= 'table' or #sl.runs < 1 then return nil end
  local ids = {}
  for _, run in ipairs(sl.runs) do
    if type(run) ~= 'table' or type(run.id) ~= 'string' or type(run.gates) ~= 'table' or #run.gates < 3 then return nil end
    for _, g in ipairs(run.gates) do
      if type(g) ~= 'table' or type(g.id) ~= 'string' or ids[g.id] or not finite(g.x) or not finite(g.z)
          or not finite(g.tx) or not finite(g.tz) or (g.expected ~= 1 and g.expected ~= -1)
          or math.abs(math.sqrt(g.tx ^ 2 + g.tz ^ 2) - 1) > 0.01 then return nil end
      ids[g.id] = true
    end
  end
  local half = finite(sl.half_m) and sl.half_m > 0 and sl.half_m or 5.5
  local machine = {official = false, confidence = 'geometric_reference_line_uncertified', passes = {}}
  function machine:reset() self.passes = {} end
  function machine:observe(previous, current)
    if not point(previous) or not point(current) then return {status = 'unknown'} end
    local crossed = {}
    for _, run in ipairs(sl.runs) do
      for i, g in ipairs(run.gates) do
        local d0 = (previous.x - g.x) * g.tx + (previous.z - g.z) * g.tz
        local d1 = (current.x - g.x) * g.tx + (current.z - g.z) * g.tz
        if d0 < 0 and d1 >= 0 and not self.passes[g.id] then
          local t = d0 / (d0 - d1)
          local x, z = previous.x + t * (current.x - previous.x), previous.z + t * (current.z - previous.z)
          local lateral = (x - g.x) * -g.tz + (z - g.z) * g.tx
          if math.abs(lateral) <= half then
            self.passes[g.id] = {sign = lateral >= 0 and 1 or -1, lateral = lateral}
            crossed[#crossed + 1] = g.id
          end
        end
      end
    end
    return {status = #crossed > 0 and 'gate' or 'pending', gates = crossed}
  end
  -- Lap result: violations (gate ids passed on the wrong side) and runs not fully observed.
  function machine:finalize()
    local violations, incomplete = {}, {}
    for _, run in ipairs(sl.runs) do
      local seen = 0
      for _, g in ipairs(run.gates) do if self.passes[g.id] then seen = seen + 1 end end
      if seen > 0 and seen < #run.gates then incomplete[#incomplete + 1] = run.id end
      if seen == #run.gates then
        -- Both entry phases are legal: what is required is that the sides ALTERNATE. The reference
        -- line only picks the phase when both fit equally well, so a driver who enters on the
        -- other side and weaves correctly is never penalised on every pylon.
        local wrongAi, wrongOther = {}, {}
        for _, g in ipairs(run.gates) do
          if self.passes[g.id].sign ~= g.expected then wrongAi[#wrongAi + 1] = g.id
          else wrongOther[#wrongOther + 1] = g.id end
        end
        local pick = #wrongAi <= #wrongOther and wrongAi or wrongOther
        for _, id in ipairs(pick) do violations[#violations + 1] = id end
      end
    end
    return {violations = violations, incomplete = incomplete}
  end
  return machine
end

return M
