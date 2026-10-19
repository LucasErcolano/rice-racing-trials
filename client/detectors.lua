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
return M
