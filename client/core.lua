-- Pure geometry and timing logic; coordinates must be supplied by verified calibration.
local core = {}
local function side(p, a, b)
  return (b.x-a.x)*(p.z-a.z) - (b.z-a.z)*(p.x-a.x)
end
function core.crossed(previous, current, line, direction)
  if not previous or not current or not line or not line.a or not line.b then return false end
  local before, after = side(previous, line.a, line.b), side(current, line.a, line.b)
  if direction ~= 1 and direction ~= -1 then return false end
  if direction * before >= 0 or direction * after < 0 then return false end
  local t = before / (before-after)
  local x = previous.x + t*(current.x-previous.x)
  local z = previous.z + t*(current.z-previous.z)
  local dx, dz = line.b.x-line.a.x, line.b.z-line.a.z
  local projected = ((x-line.a.x)*dx+(z-line.a.z)*dz)/(dx*dx+dz*dz)
  -- Second value: fraction of the previous->current segment where the line was crossed,
  -- so callers can time the crossing inside the frame instead of at its end.
  if projected >= 0 and projected <= 1 then return true, t end
  return false
end
-- A crossing count alone cannot distinguish outlap, reversal or route completion.
-- No surveyed route/checkpoint evidence is available in the supplied assets.
function core.full_loop_proven(evidence)
  return false
end
function core.score(event, raw, doo, oc, right, left)
  if type(doo) ~= 'number' or doo < 0 or doo % 1 ~= 0 then return nil end
  if event == 'autocross' and type(raw) == 'number' and raw > 0 and type(oc) == 'number' and oc >= 0 and oc % 1 == 0 then
    return raw + 2*doo + 20*oc
  end
  if event == 'skidpad' and type(right) == 'number' and right > 0 and type(left) == 'number' and left > 0 and oc == 0 then
    return (right+left)/2 + 0.125*doo
  end
  return nil
end
-- Ordered operator-surveyed checkpoints; a start crossing arms the outlap.
function core.route_machine(route)
  if type(route) ~= 'table' or type(route.start) ~= 'table' or
      type(route.checkpoints) ~= 'table' or #route.checkpoints == 0 then return nil end
  local machine = {phase='waiting', next_checkpoint=1}
  function machine:observe(previous, current)
    if self.phase == 'waiting' then
      local hit, t = core.crossed(previous, current, route.start, route.start.direction)
      if hit then
        self.phase = 'outlap'; return 'OUTLAP', t
      end
      return nil
    end
    local checkpoint = route.checkpoints[self.next_checkpoint]
    if checkpoint and core.crossed(previous, current, checkpoint, checkpoint.direction) then
      self.next_checkpoint = self.next_checkpoint + 1
      return 'CHECKPOINT'
    end
    local hit, t = core.crossed(previous, current, route.start, route.start.direction)
    if hit then
      if self.next_checkpoint <= #route.checkpoints then return nil end
      self.next_checkpoint = 1
      if self.phase == 'outlap' then
        self.phase = 'timed'; return 'OUTLAP_COMPLETE', t
      end
      return 'COMPLETE', t
    end
    return nil
  end
  return machine
end

function core.pending_queue(entries)
  local q = {entries = entries or {}}
  function q:add(id, payload)
    if type(id) ~= 'string' or id == '' or type(payload) ~= 'string' then return false end
    for _, entry in ipairs(self.entries) do if entry.id == id then return false end end
    self.entries[#self.entries + 1] = {id=id, payload=payload}
    return true
  end
  function q:peek() return self.entries[1] end
  function q:ack(id)
    if not self.entries[1] or self.entries[1].id ~= id then return false end
    table.remove(self.entries, 1)
    return true
  end
  return q
end

function core.valid_id(value)
  return type(value) == 'string' and #value > 0 and #value <= 80 and
    value:match('^[A-Za-z0-9_.-]+$') ~= nil
end

function core.assignment_matches(assignment, session, pilot, station)
  return type(assignment) == 'table' and type(session) == 'string' and session ~= '' and
    type(pilot) == 'string' and pilot ~= '' and type(station) == 'string' and station ~= '' and
    assignment.session_id == session and assignment.pilot_id == pilot and assignment.station_id == station
end
return core
