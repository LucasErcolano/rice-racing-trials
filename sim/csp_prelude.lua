-- MOCK CSP ENVIRONMENT (not Custom Shaders Patch). Builds the globals a CSP Lua app sees
-- (ac, io, web, ui, JSON, vec2, rgbm, script) over a Python bridge so the UNMODIFIED
-- production client (client/RiceTrials.lua and modules) can run under LuaJIT 2.1.
--
-- Fidelity notes (see docs/csp-mock.md for the full table):
--  * ac.getCar(0) / ac.getSim() return LuaJIT FFI cdata structs, like CSP: reading a field
--    the struct does not define RAISES, and position/look are float32 vectors.
--    Field sets are chosen by the test (CSP versions differ); real layout is unverified.
--  * JSON is the CSP SDK implementation vendored verbatim (sim/vendor/csp_json.lua).
--  * web callbacks run on the main thread between frames with (err, {status, headers, body}),
--    following acc-lua-sdk lib_web.lua; error strings and the 2-request limit policy are
--    assumptions.
--  * io.* semantics (move refuses an existing destination, save does not create folders)
--    are assumptions taken from docs/csp-api-reference.md, not observed CSP behaviour.
local B, carFields, simFields = ...
local ffi = require('ffi')

ffi.cdef('typedef struct { float x, y, z; } rr_vec3;')
local function struct(name, fields)
  local decl = {}
  for _, f in ipairs(fields) do decl[#decl + 1] = f[2]..' '..f[1]..';' end
  ffi.cdef('typedef struct { '..table.concat(decl, ' ')..' } '..name..';')
  return ffi.new(name)
end
local car = struct('rr_state_car', carFields)
local sim = struct('rr_state_sim', simFields)
local carHas, simHas = {}, {}
for _, f in ipairs(carFields) do carHas[f[1]] = f[2] end
for _, f in ipairs(simFields) do simHas[f[1]] = f[2] end

JSON = (loadfile(B.vendor_json_path()))()

local jumped, sessionStart = {}, {}
local folderRoot = B.ac_root()
ac = {
  FolderID = {Root = 1, ACApps = 2, ScriptConfig = 3},
  getFolder = function(id) if id == 1 then return folderRoot end return nil end,
  getCar = function(index) if index == 0 then return car end return nil end,
  getSim = function() return sim end,
  getPatchVersion = function() return 'MOCK-not-CSP' end,
  getPatchVersionCode = function() return 0 end,
  getCarID = function() return B.car_id() end,
  getTrackFullID = function() return B.track_full_id() end,
  log = function(...) B.log(table.concat({tostring(select(1, ...))}, ' ')) end,
  debug = function() end,
}
if B.has_api('ac.onCarJumped') then
  ac.onCarJumped = function(index, cb) jumped[#jumped + 1] = {index, cb}; return function() end end
end
if B.has_api('ac.onSessionStart') then
  ac.onSessionStart = function(cb) sessionStart[#sessionStart + 1] = cb; return function() end end
end
-- acc-lua-sdk common/ac_storage.lua: __index/__newindex map to native string storage.
ac.storage = setmetatable({}, {
  __index = function(_, key) return B.storage_get(key) end,
  __newindex = function(_, key, value)
    if value ~= nil then value = tostring(value) end
    B.storage_set(key, value)
  end,
})

local function ioCall(name)
  return function(...)
    local ok, a, b = B.io(name, ...)
    if not ok then error(a, 2) end -- injected "raise" fault
    return a, b
  end
end
io = {
  save = ioCall('save'), load = ioCall('load'), move = ioCall('move'),
  fileExists = ioCall('fileExists'), createDir = ioCall('createDir'), deleteFile = ioCall('deleteFile'),
}

-- web: same overloads as acc-lua-sdk common/ac_web.lua
local callbacks = {}
local function encodeHeaders(headers)
  if headers == nil then return nil end
  local out = {}
  for k, v in pairs(headers) do out[#out + 1] = tostring(k)..'='..tostring(v) end
  table.sort(out)
  return table.concat(out, '\n')
end
local function request(method, url, headers, data, callback)
  if url == nil then error('URL is required', 2) end
  local id = B.web_request(method, url, encodeHeaders(headers), data)
  callbacks[id] = callback or false
end
web = {
  get = function(url, headers, callback)
    if type(headers) == 'function' then headers, callback = nil, headers end
    request('GET', url, headers, nil, callback)
  end,
  post = function(url, headers, data, callback)
    if type(headers) == 'function' then headers, data, callback = nil, nil, headers
    elseif type(headers) == 'string' then headers, data, callback = nil, headers, data
    elseif type(data) == 'function' then data, callback = nil, data end
    request('POST', url, headers, data, callback)
  end,
}

-- ui: records what the HUD draws (text only) for black-box assertions.
local frameText = {}
local function rec(text) frameText[#frameText + 1] = tostring(text) end
vec2 = function(x, y) return {x = x or 0, y = y or 0} end
rgbm = function(r, g, b, m) return {r = r, g = g, b = b, mult = m} end
ui = {
  text = rec, textColored = function(t) rec(t) end,
  dwriteText = function(t) rec(t) end,
  pushDWriteFont = function() end, popDWriteFont = function() end,
  drawRectFilled = function() end, setCursor = function() end,
  windowSize = function() return vec2(420, 220) end,
}
for _, name in ipairs(B.hidden_apis()) do -- simulate an older CSP without some functions
  local ns, fn = name:match('^(%w+)%.(%w+)$')
  if ns and _G[ns] then _G[ns][fn] = nil end
end

script = {}

local M = {}
function M.load_app(dir)
  package.path = dir..'/?.lua;'..package.path
  local chunk, err = loadfile(dir..'/RiceTrials.lua')
  if not chunk then error(err) end
  chunk()
end
-- One simulated frame: set car/sim state, deliver finished web replies, update, draw.
function M.frame(dt, s, draw)
  for k, v in pairs(s.car) do
    if carHas[k] then
      if carHas[k] == 'rr_vec3' then car[k].x, car[k].y, car[k].z = v[1], v[2], v[3] else car[k] = v end
    end
  end
  for k, v in pairs(s.sim) do if simHas[k] then sim[k] = v end end
  while true do
    local id, err, status, body = B.next_reply()
    if id == nil then break end
    local cb = callbacks[id]; callbacks[id] = nil
    if cb then cb(err, {status = status, headers = {}, body = body}) end
  end
  local errors = {}
  if s.update ~= false and type(script.update) == 'function' then
    local ok, e = pcall(script.update, dt)
    if not ok then errors[#errors + 1] = 'update: '..tostring(e) end
  end
  frameText = {}
  if draw and type(script.windowMain) == 'function' then
    local ok, e = pcall(script.windowMain, dt)
    if not ok then errors[#errors + 1] = 'windowMain: '..tostring(e) end
  end
  return frameText, errors
end
function M.fire_car_jumped() for _, j in ipairs(jumped) do j[2](j[1]) end return #jumped end
function M.fire_session_start(index, restarted)
  for _, cb in ipairs(sessionStart) do cb(index or 0, restarted == true) end
  return #sessionStart
end
return M
