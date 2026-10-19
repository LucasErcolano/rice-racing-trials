-- Deterministic scoring, rules v1. Mirrors server/scoring.py; parity tested against rules/scoring-v1.json.
-- Integer milliseconds only. Malformed input returns nil (never a guessed time).
-- The HUD uses this only for a PROVISIONAL projection; the server stays the authority.
local M = {}
M.RULES_VERSION = 1
M.PENALTIES = {
  endurance = {cone = 2000, off_course = 20000, slalom = 20000},
  autocross = {doo = 2000, oc = 20000},
  skidpad = {doo = 125},
}
M.STATUSES = {VALID = true, INVALID = true, OUTLAP = true, PENDING_REVIEW = true, DETECTOR_ERROR = true}

local function int(v) return type(v) == 'number' and v == v and v % 1 == 0 end
local function count(v) return int(v) and v >= 0 and v <= 100 end
local function ms(v) return int(v) and v > 0 and v <= 3600000 end
local function onlyKeys(t, allowed)
  for k in pairs(t) do if not allowed[k] then return false end end
  return true
end
local function gateId(g)
  return type(g) == 'string' and #g >= 1 and #g <= 64 and g:match('^[%w_%.:%-]+$') ~= nil
end

function M.corrected_ms(event, raw, events)
  local p = M.PENALTIES[event]
  if not p or not ms(raw) or type(events) ~= 'table' then return nil end
  if event == 'endurance' then
    if not onlyKeys(events, {cone = true, off_course = true, slalom = true}) then return nil end
    local cone, off, slalom = events.cone or 0, events.off_course or 0, events.slalom or {}
    if not count(cone) or not count(off) or type(slalom) ~= 'table' or #slalom > 100 then return nil end
    local seen = {}
    for _, g in ipairs(slalom) do
      if not gateId(g) or seen[g] then return nil end
      seen[g] = true
    end
    for k in pairs(slalom) do if not int(k) or k < 1 or k > #slalom then return nil end end
    return raw + cone * p.cone + off * p.off_course + #slalom * p.slalom
  elseif event == 'autocross' then
    if not onlyKeys(events, {doo = true, oc = true}) then return nil end
    local doo, oc = events.doo or 0, events.oc or 0
    if not count(doo) or not count(oc) then return nil end
    return raw + doo * p.doo + oc * p.oc
  end
  if not onlyKeys(events, {right_ms = true, left_ms = true, doo = true, oc = true}) then return nil end
  if (events.oc or 0) ~= 0 then return nil end
  local right, left, doo = events.right_ms, events.left_ms, events.doo or 0
  if not ms(right) or not ms(left) or not count(doo) then return nil end
  return math.floor((right + left) / 2) + doo * p.doo
end

function M.resolve_status(proposed, detectorOk)
  if not M.STATUSES[proposed] then return 'DETECTOR_ERROR' end
  if proposed == 'VALID' and detectorOk ~= true then return 'DETECTOR_ERROR' end
  return proposed
end

return M
