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

return M
