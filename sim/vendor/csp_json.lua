-- VENDORED, NOT WRITTEN BY RICE RACING. Exact copies (no edits inside the copied blocks)
-- of the JSON implementation CSP ships to Lua apps, taken from
--   https://github.com/ac-custom-shaders-patch/acc-lua-sdk  commit 7cf60f5a406820ee4e39f3a62ed121fa36274001
--   (MIT License, Copyright (c) 2021 Ilja Jusupov; see sim/vendor/acc-lua-sdk.LICENSE)
-- Blocks: common/table.lua L10-19, L57-61, L433-461; common/internal.lua L642-661;
--   lib_jsonparse.lua (whole file; only its final `return function` became a local);
--   tests/_core.lua L85-87, L93-106 (the SDK's own Lua stand-ins for the native
--   string.trim / string.codePointToUTF8 that CSP implements in C++: MOCK FIDELITY GAP).
-- The real CSP runtime may differ from this SDK snapshot: only in-game validation is proof.
local __util = {}
local table, string = table, string

local function __isArray(t, N)
  -- return type(t) == 'table' and _tisarray(t)
  -- return type(t) == 'table' and N == _tnkeys(t)  -- alternative implementation. note: table.nkeys iterates over all elements for counting 🙄
  -- return type(t) == 'table' and _tisarray(t) and N == _tnkeys(t)  -- might be faster with earlier check, but let’s play it safe
  if type(t) ~= 'table' then return false end
  local f = next(t)
  if type(f) ~= 'number' or f < 0 or f > N then return f == nil end
  local k = next(t, N > 0 and N or nil)  -- does iterate over things, but seems to be more efficient overall
  return k == nil or type(k) == 'number' and k > 0 and k < N  -- specially for {[1]=1,[2]=2} table (and similar ones), where N==2, but next(2) can return 1
end

function table.isArray(t)
  if type(t) ~= 'table' then return false end
  local N = #t
  return __isArray(t, N)
end

function table.map(t, callback, callbackData)
  local ret = {}
  local N = #t
  local I = 1
  if __isArray(t, N) then
    for key = next(t) or 1, N do
      local newValue, newKey = callback(t[key], key, callbackData)
      if newValue ~= nil then
        if newKey ~= nil then
          ret[newKey] = newValue
        else
          ret[I], I = newValue, I + 1
        end
      end
    end
  else
    for key, value in pairs(t) do
      local newValue, newKey = callback(value, key, callbackData)
      if newValue ~= nil then
        if newKey ~= nil then
          ret[newKey] = newValue
        else
          ret[I], I = newValue, I + 1
        end
      end
    end
  end
  return ret
end

function string.trim(s)
  return s:match"^%s*(.*)":match"(.-)%s*$"
end

function string.codePointToUTF8(n)
  -- http://scripts.sil.org/cms/scripts/page.php?site_id=nrsi&id=iws-appendixa
  if n <= 0x7f then
    return string.char(n)
  elseif n <= 0x7ff then
    return string.char(math.floor(n / 64) + 192, n % 64 + 128)
  elseif n <= 0xffff then
    return string.char(math.floor(n / 4096) + 224, math.floor(n % 4096 / 64) + 128, n % 64 + 128)
  elseif n <= 0x10ffff then
    return string.char(math.floor(n / 262144) + 240, math.floor(n % 262144 / 4096) + 128,
      math.floor(n % 4096 / 64) + 128, n % 64 + 128)
  end
  return ''
end

local __escapeMap = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function __escapeChar(c)
  return __escapeMap[c] or string.format('\\u%04x', string.byte(c))
end

function __util.json(v, b, s)
  local t = type(v)
  if t == 'table' then
    if not s then s = {[v] = true} elseif s[v] then return 'null' else s[v] = true end
    s, s[v] = table.isArray(v) 
      and '['..table.concat(table.map(v, function (i) return __util.json(i, b, s) end), ',')..']'
      or '{'..table.concat(table.map(v, function (i, k) return __util.json(tostring(k), b)..':'..__util.json(i, b, s) end), ',')..'}', nil
    if b then s = string.__jsonFormat(s) end
    return s
  end
  return (t == 'string' or t == 'cdata') and '"'..string.gsub(tostring(v), '[\1-\31\\"]', __escapeChar)..'"'
    or (t == 'boolean' or t == 'number' and v == v and v > -math.huge and v < math.huge) and tostring(v) or 'null'
end

local __escapeMapInv = { [34] = '"', [39] = '\'', [47] = '/', [92] = '\\', [98] = '\8', [102] = '\12', [110] = '\n', [114] = '\13', [116] = '\9' }

local function __nextNonWhitespace(str, i)
  for j = i, math.max(i, #str + 1) do
    local b = string.byte(str, j)
    if b ~= 9 and b ~= 10 and b ~= 13 and b ~= 32 then return j end
  end
end

local function __nextDelimiter(str, i)
  for j = i, math.max(i, #str + 1) do
    local b = string.byte(str, j)
    if not b or b == 9 or b == 10 or b == 13 or b == 32 or b == 44 or b == 93 or b == 125 then return j end
  end
end

local function __skip(str, i, commas)
  local o = string.byte(str, i)
  while o == 47 or commas and o == 44 do
    if o == 44 then
      i = __nextNonWhitespace(str, i + 1)
    elseif string.byte(str, i + 1) == 47 then
      for j = i + 2, #str do
        if string.byte(str, j) == 10 or string.byte(str, j) == 13 then
          i = __nextNonWhitespace(str, j + 1)
          break
        end
      end
    elseif string.byte(str, i + 1) == 42 then
      for j = i + 2, #str do
        if string.byte(str, j) == 42 and string.byte(str, j + 1) == 47 then
          i = __nextNonWhitespace(str, j + 2)
          break
        end
      end
    else
      return #str + 1, nil
    end
    o = string.byte(str, i)
  end
  return i, o
end


local function __endOfKey(str, i)
  for j = i, math.max(i, #str + 1) do
    local b = string.byte(str, j)
    if not b or b == 58 then return str:sub(i - 1, j - 1):trim(), j + 1 end
    if b == 47 and (string.byte(str, j + 1) == 47 or string.byte(str, j + 1) == 42) then
      local k = str:sub(i - 1, j - 1):trim()
      return k, __skip(str, j) + 1
    end
  end
end

local function __jsonParse(str, i)
  local o
  i, o = __skip(str, i)
  if o == 34 or o == 39 then
    local res, j, k = '', i + 1, i + 1
    while true do
      local x = string.byte(str, j)
      if not x or x == o then
        return res..str:sub(k, j - 1), j + 1
      elseif x == 92 then
        res, j = res..str:sub(k, j - 1), j + 1
        local c = string.byte(str, j)
        if c == 117 then
          local hex = str:match('^[dD][89aAbB]%x%x\\u%x%x%x%x', j + 1) or str:match('^%x%x%x%x', j + 1) or ''
          local n1, n2 = tonumber(hex:sub(1, 4), 16), tonumber(hex:sub(7, 10), 16)
          res, j = res..(n2 and (string.codePointToUTF8((n1 - 0xd800) * 0x400 + (n2 - 0xdc00) + 0x10000))
            or n1 and string.codePointToUTF8(n1) or ''), j + #hex
        else
          res = res..(__escapeMapInv[c] or '')
        end
        k = j + 1
      end
      j = j + 1
    end
  elseif o == 116 then
    if str:sub(i, i + 3) == 'true' then return true, i + 4 end
    return nil, __nextDelimiter(str, i)
  elseif o == 102 then
    if str:sub(i, i + 4) == 'false' then return false, i + 5 end
    return nil, __nextDelimiter(str, i)
  elseif o == 110 then
    if str:sub(i, i + 3) == 'null' then return nil, i + 4 end
    return nil, __nextDelimiter(str, i)
  elseif o >= 48 and o <= 57 or o == 45 then
    local x = __nextDelimiter(str, i)
    return tonumber(str:sub(i, x - 1)), x
  elseif o == 91 or o == 123 then
    local res, key = {}, nil
    i = i + 1
    -- print(string.sub(str, i))
    while i < #str do
      i = __nextNonWhitespace(str, i)
      i = __skip(str, i, true)
      if string.byte(str, i) == 93 or string.byte(str, i) == 125 then --[[ ], } ]]
        -- print('END: %s' % string.sub(str, i))
        return res, i + 1
      end
      if o == 91 then --[[ [ ]]
        res[#res + 1], i = __jsonParse(str, i)
      elseif o == 123 then --[[ { ]]
        if string.byte(str, i) == 34 or string.byte(str, i) == 39 then --[[ ", ' ]]
          key, i = __jsonParse(str, i)
          i = __nextNonWhitespace(str, i)
          i = __skip(str, i)
          -- print(key)
          -- print(string.byte(str, i))
          if string.byte(str, i) == 58 --[[ : ]] then i = i + 1 end
        else
          key, i = __endOfKey(str, i + 1)
        end
        res[tostring(key)], i = __jsonParse(str, __nextNonWhitespace(str, i))
      end
    end
    -- print('ERR:'.. string.sub(str, i))
    return res, #str + 1
  else
    return nil, #str + 1
  end
end

local __parseEntry = function(str)
  if str == nil then return nil end
  str = tostring(str)
  local s, r = pcall(__jsonParse, str, __nextNonWhitespace(str, 1))
  if s then return r end
  return nil
end

function __util.jsonParse(str)
  return __parseEntry(str)
end

return {stringify = __util.json, parse = __util.jsonParse}
