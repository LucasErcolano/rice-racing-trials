-- Rice Trials durable queue journal (pure module, dependencies injected).
--
-- Two slot files <dir>/<name>.a.json and <dir>/<name>.b.json. Each holds one JSON
-- envelope {v=1, seq=<int>, checksum=<FNV-1a 32 hex of items>, items=<items JSON string>}.
-- items is embedded as a string so the checksum covers the exact bytes.
--
-- save(items) writes the slot that does NOT hold the highest valid seq, with
-- seq = max + 1, then reads it back with io.load. Only an identical readback with a
-- valid checksum counts as confirmed (returns true). On any failure the other slot is
-- untouched, so the previous confirmed queue is still recoverable.
--
-- load() validates both slots (and the degraded ac.storage copy) and picks the valid
-- envelope with the highest seq. If something exists but nothing is valid the journal
-- is 'corrupt': raw bytes are copied to <name>.quarantine.<n>.json, items are empty,
-- can_send() is false and save() refuses (fail-closed, manual recovery required).
--
-- Guarantee: survives a restart of the Lua VM or of AC after a readback-confirmed
-- save. NOT claimed: power-loss durability (the CSP io API exposes no fsync).
local M = {}
M.FORMAT_VERSION = 1
M.DEGRADED_KEY = 'rice_trials_journal_v2'
M.LEGACY_KEY = 'rice_trials_pending_review_v1'

---------------------------------------------------------------------------
-- FNV-1a 32 bits
---------------------------------------------------------------------------
local FNV_OFFSET, TWO24, TWO32 = 2166136261, 16777216, 4294967296

local function xor8(a, b) -- a, b in [0, 255]
  local r, p = 0, 1
  for _ = 1, 8 do
    local x, y = a % 2, b % 2
    if x ~= y then r = r + p end
    a, b, p = (a - x) / 2, (b - y) / 2, p * 2
  end
  return r
end

-- Portable path (Lua 5.1 to 5.4): modular arithmetic on doubles, every
-- intermediate below 2^53. prime = 2^24 + 403, so h*prime mod 2^32 =
-- ((h mod 2^8) * 2^24 + h * 403) mod 2^32.
function M.fnv1a_pure(s)
  local h = FNV_OFFSET
  for i = 1, #s do
    local lo = h % 256
    h = h - lo + xor8(lo, s:byte(i))
    h = ((h % 256) * TWO24 + h * 403) % TWO32
  end
  return string.format('%08x', math.floor(h))
end

local bitlib = rawget(_G, 'bit')
if type(bitlib) ~= 'table' then
  local ok, b = pcall(require, 'bit')
  bitlib = ok and type(b) == 'table' and b or nil
end
if bitlib and type(bitlib.bxor) == 'function' and type(bitlib.lshift) == 'function'
    and type(bitlib.tobit) == 'function' and type(bitlib.tohex) == 'function' then
  local bxor, lshift, tobit, tohex = bitlib.bxor, bitlib.lshift, bitlib.tobit, bitlib.tohex
  function M.fnv1a_bit(s)
    local h = tobit(FNV_OFFSET)
    for i = 1, #s do
      h = bxor(h, s:byte(i))
      -- h is signed 32-bit; |h*403| < 2^41, sum exact in a double; tobit wraps mod 2^32
      h = tobit(lshift(h, 24) + h * 403)
    end
    return tohex(h)
  end
  M.fnv1a = M.fnv1a_bit
  M.FNV_IMPL = 'bit'
else
  M.fnv1a = M.fnv1a_pure
  M.FNV_IMPL = 'pure'
end

---------------------------------------------------------------------------
-- helpers
---------------------------------------------------------------------------
local function field(obj, key)
  if obj == nil then return nil end
  local ok, v = pcall(function() return obj[key] end)
  if ok then return v end
  return nil
end

local function isint(n)
  return type(n) == 'number' and n == n and n >= 1 and n < 2^53 and n % 1 == 0
end

local function copyArray(t)
  local out, n = {}, 0
  if type(t) ~= 'table' then return nil end
  for k in pairs(t) do
    if type(k) ~= 'number' or k < 1 or k % 1 ~= 0 then return nil end
    if k > n then n = k end
  end
  for i = 1, n do
    if t[i] == nil then return nil end -- sparse: not an array
    out[i] = t[i]
  end
  return out
end

---------------------------------------------------------------------------
-- journal
---------------------------------------------------------------------------
local J = {}
J.__index = J

-- config: dir (string, required for file mode), name (default 'queue', [%w_-]+)
-- deps:   io (default global io), json {encode, decode} (default global JSON
--         stringify/parse), storage (ac.storage-like, optional), legacy_key,
--         degraded_key.
function M.new(config, deps)
  config = type(config) == 'table' and config or {}
  deps = type(deps) == 'table' and deps or {}
  local self = setmetatable({}, J)
  local name = type(config.name) == 'string' and config.name:match('^[%w_%-]+$') and config.name or 'queue'
  self._dir = type(config.dir) == 'string' and config.dir:gsub('[/\\]+$', '') or nil
  self._name = name
  self._io = deps.io
  if self._io == nil and deps.io_disabled ~= true then self._io = rawget(_G, 'io') end
  local js = deps.json
  if js == nil then
    local G = rawget(_G, 'JSON')
    if G then js = {encode = field(G, 'stringify'), decode = field(G, 'parse')} end
  end
  self._encode, self._decode = field(js, 'encode'), field(js, 'decode')
  self._storage = deps.storage
  self._legacyKey = type(deps.legacy_key) == 'string' and deps.legacy_key or M.LEGACY_KEY
  self._degradedKey = type(deps.degraded_key) == 'string' and deps.degraded_key or M.DEGRADED_KEY
  self._paths = {}
  if self._dir then
    self._paths.a = self._dir..'/'..name..'.a.json'
    self._paths.b = self._dir..'/'..name..'.b.json'
  end
  self._slotSeq = {a = 0, b = 0}
  self._st = {state = 'empty', seq = 0, slot = nil, last_error = nil, quarantined = nil}
  self._loaded = false
  return self
end

function J:_fs(name, ...)
  local f = field(self._io, name)
  if type(f) ~= 'function' then return false, 'io.'..name..' unavailable' end
  local ok, r = pcall(f, ...)
  if not ok then return false, 'io.'..name..' raised: '..tostring(r) end
  return true, r
end

function J:_fileMode()
  return self._dir ~= nil and type(field(self._io, 'save')) == 'function'
    and type(field(self._io, 'load')) == 'function'
end

function J:_exists(p)
  if type(field(self._io, 'fileExists')) == 'function' then
    local ok, r = self:_fs('fileExists', p)
    if ok then return r == true end
  end
  local ok, r = self:_fs('load', p, nil)
  return ok and type(r) == 'string'
end

function J:_readFile(p)
  if not self:_exists(p) then return nil end
  local ok, r = self:_fs('load', p, nil)
  if ok and type(r) == 'string' then return r end
  return '' -- exists but unreadable: treat as invalid content
end

function J:_enc(v)
  if type(self._encode) ~= 'function' then return nil, 'json.encode unavailable' end
  local ok, s = pcall(self._encode, v)
  if not ok or type(s) ~= 'string' then return nil, 'json.encode failed' end
  return s
end

function J:_dec(s)
  if type(self._decode) ~= 'function' then return nil, 'json.decode unavailable' end
  local ok, v = pcall(self._decode, s)
  if not ok then return nil, 'json.decode failed' end
  return v
end

-- Returns {seq=, items=} or nil, reason.
function J:_parse(raw)
  if type(raw) ~= 'string' or raw == '' then return nil, 'empty' end
  local env = self:_dec(raw)
  if type(env) ~= 'table' then return nil, 'invalid_json' end
  if env.v ~= M.FORMAT_VERSION then return nil, 'bad_version' end
  if not isint(env.seq) then return nil, 'bad_seq' end
  if type(env.items) ~= 'string' or type(env.checksum) ~= 'string' then return nil, 'bad_fields' end
  if M.fnv1a(env.items) ~= env.checksum:lower() then return nil, 'checksum_mismatch' end
  local items = copyArray(self:_dec(env.items))
  if not items then return nil, 'items_not_array' end
  return {seq = env.seq, items = items}
end

function J:_envelope(items, seq)
  local itemsStr, err = self:_enc(items)
  if not itemsStr then return nil, err end
  if itemsStr == '{}' then itemsStr = '[]' end -- some encoders emit {} for an empty table
  return self:_enc({v = M.FORMAT_VERSION, seq = seq, checksum = M.fnv1a(itemsStr), items = itemsStr})
end

function J:_storageGet(key)
  if self._storage == nil then return nil end
  local v = field(self._storage, key)
  if type(v) == 'string' and v ~= '' then return v end
  return nil
end

function J:_storageSet(key, value)
  if self._storage == nil then return false end
  local ok = pcall(function() self._storage[key] = value end)
  return ok and field(self._storage, key) == value
end

function J:_quarantine(raws)
  local first, all = nil, {}
  if not self:_fileMode() then return nil, all end
  local n = 1
  for _, raw in ipairs(raws) do
    local p
    repeat
      p = self._dir..'/'..self._name..'.quarantine.'..n..'.json'
      n = n + 1
    until not self:_exists(p) or n > 1000
    local ok, r = self:_fs('save', p, raw)
    if ok and r ~= false then
      local rok, back = self:_fs('load', p, nil)
      if rok and back == raw then
        all[#all + 1] = p
        first = first or p
      end
    end
  end
  return first, all
end

function J:_set(state, seq, slot, err)
  self._st.state, self._st.seq, self._st.slot = state, seq, slot
  if err ~= nil then self._st.last_error = err end
end

-- load() -> items, state
function J:load()
  self._loaded = true
  self._st.quarantined, self._st.quarantined_all, self._st.last_error = nil, nil, nil
  self._slotSeq = {a = 0, b = 0}
  local fileMode = self:_fileMode()
  local best, bestSlot, raws, errors = nil, nil, {}, {}

  if fileMode then
    for _, slot in ipairs({'a', 'b'}) do
      local raw = self:_readFile(self._paths[slot])
      if raw ~= nil then
        local env, why = self:_parse(raw)
        if env then
          self._slotSeq[slot] = env.seq
          if not best or env.seq > best.seq then best, bestSlot = env, slot end
        else
          raws[#raws + 1] = raw
          errors[#errors + 1] = 'slot '..slot..': '..why
        end
      end
    end
  end
  local degradedRaw = self:_storageGet(self._degradedKey)
  if degradedRaw then
    local env, why = self:_parse(degradedRaw)
    if env then
      if not best or env.seq > best.seq then best, bestSlot = env, 'storage' end
    else
      raws[#raws + 1] = degradedRaw
      errors[#errors + 1] = 'storage '..self._degradedKey..': '..why
    end
  end
  local errText = #errors > 0 and table.concat(errors, '; ') or nil

  if best then
    local state = (bestSlot == 'storage' or not fileMode) and 'degraded' or 'ok'
    self:_set(state, best.seq, bestSlot, errText)
    return best.items, state
  end
  if #raws > 0 then
    local q, all = self:_quarantine(raws)
    self._st.quarantined, self._st.quarantined_all = q, all
    self:_set('corrupt', 0, nil, errText)
    return {}, 'corrupt'
  end

  -- Nothing in the journal: migrate the legacy ac.storage queue if present.
  local legacy = self:_storageGet(self._legacyKey)
  if legacy then
    local data = self:_dec(legacy)
    local items = copyArray(data)
    if not items and type(data) == 'table' then
      items = copyArray(data.entries)
      if items then self.legacy_meta = {nonce = data.nonce, next_id = data.next_id, version = data.version} end
    end
    if not items then
      local q, all = self:_quarantine({legacy})
      self._st.quarantined, self._st.quarantined_all = q, all
      self:_set('corrupt', 0, nil, 'legacy '..self._legacyKey..': not a JSON array')
      return {}, 'corrupt'
    end
    self:_set(fileMode and 'empty' or 'degraded', 0, nil)
    local ok, err = self:save(items)
    if ok and self._st.state ~= 'degraded' then
      -- File copy confirmed by readback: only now drop the legacy key.
      local deleted = pcall(function() self._storage[self._legacyKey] = nil end)
      if not deleted or field(self._storage, self._legacyKey) ~= nil then
        self._st.last_error = 'legacy key not deleted (harmless: journal takes precedence)'
      end
      self._st.state = 'migrated'
      return items, 'migrated'
    end
    -- Save failed, or only the degraded storage copy exists: keep the legacy key.
    self._st.state = 'degraded'
    if not ok then self._st.last_error = 'migration save failed: '..tostring(err) end
    return items, 'degraded'
  end

  local state = fileMode and 'empty' or 'degraded'
  self:_set(state, 0, nil, fileMode and nil or 'io unavailable')
  return {}, state
end

-- save(items) -> bool, err
function J:save(items)
  if not self._loaded then self:load() end
  if self._st.state == 'corrupt' then
    return false, 'corrupt: manual recovery required'
  end
  local arr = copyArray(items)
  if not arr then return false, 'items_not_array' end
  local seq = (self._st.seq or 0) + 1
  if self._slotSeq.a >= seq then seq = self._slotSeq.a + 1 end
  if self._slotSeq.b >= seq then seq = self._slotSeq.b + 1 end
  local data, err = self:_envelope(arr, seq)
  if not data then self._st.last_error = err; return false, err end

  local ioErr
  if self:_fileMode() then
    local slot = (self._slotSeq.a <= self._slotSeq.b) and 'a' or 'b'
    local path = self._paths[slot]
    if type(field(self._io, 'createDir')) == 'function' then self:_fs('createDir', self._dir) end
    local ok, r = self:_fs('save', path, data)
    if ok and r ~= false then
      self._slotSeq[slot] = 0 -- unknown until confirmed
      local rok, back = self:_fs('load', path, nil)
      if not rok or back ~= data or not self:_parse(back) then
        self._st.last_error = 'readback_mismatch: '..path
        return false, self._st.last_error
      end
      self._slotSeq[slot] = seq
      self:_set('ok', seq, slot, nil)
      self._st.last_error = nil
      return true
    end
    ioErr = ok and 'io.save returned false' or r
  else
    ioErr = 'io unavailable'
  end

  -- Degraded fallback: ac.storage (asynchronous flush, weaker than a file).
  if self._storage ~= nil and self:_storageSet(self._degradedKey, data) then
    self:_set('degraded', seq, 'storage', ioErr)
    return true
  end
  self._st.last_error = ioErr..'; storage fallback unavailable'
  return false, self._st.last_error
end

function J:can_send()
  return self._loaded and self._st.state ~= 'corrupt'
end

function J:state()
  local s = {}
  for k, v in pairs(self._st) do s[k] = v end
  s.can_send = self:can_send()
  return s
end

M.Journal = J
return M
