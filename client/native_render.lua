-- Native-style overlay RENDERER. Draws models produced by native_overlay.lua
-- using only CSP 2.11 ui.* calls confirmed in docs/csp-api-reference.md and in
-- the acc-lua-sdk definitions shipped in ext/acc-lua-sdk.
--
-- Confirmed APIs used here (never invented):
--   ui.drawRectFilled(p1, p2, color, rounding)
--   ui.drawRect(p1, p2, color, rounding, flags, thickness)
--   ui.drawLine(p1, p2, color, thickness)
--   ui.setCursor(pos)
--   ui.dwriteText(text, size, color)  + ui.pushDWriteFont(name)/ui.popDWriteFont()
--   ui.acText(text, letter, spacing, color) + ui.pushACFont(name)/ui.popACFont()
--   ui.windowSize()
--   vec2(x, y), rgbm(r, g, b, a)
--
-- Every call is existence-checked and wrapped in pcall, and the font stacks are
-- balanced even on failure: a broken render can never affect timing (the same
-- contract as hud.draw). acText is preferred (AC's own font: native look) and
-- DWrite is the fallback.
local M = {}
local overlay = require('native_overlay')

local function fn(t, k) return type(t) == 'table' and type(t[k]) == 'function' end
local function finite(n) return type(n) == 'number' and n == n and n ~= math.huge and n ~= -math.huge end

local FONT_AC = 'aria'   -- AC's own UI font (content/fonts), SDK default
local FONT_DW = 'Segoe UI;Weight=Bold'

local function color(rgbm, c, alpha)
  if not rgbm then return nil end
  return rgbm(c[1], c[2], c[3], alpha or 1)
end

-- Draw one line of text with the AC font, DWrite, or plain text, in that order.
-- Returns the drawn width in px (approximate, used to right-align values).
local function drawText(u, text, x, y, size, col, vec2, useAc, useDw)
  text = tostring(text)
  if useAc and vec2 and col then
    if fn(u, 'setCursor') then pcall(u.setCursor, vec2(x, y)) end
    if fn(u, 'acText') then pcall(u.acText, text, vec2(size, math.floor(size * 2 + 0.5)), 0, col) end
  elseif useDw and vec2 and col then
    if fn(u, 'setCursor') then pcall(u.setCursor, vec2(x, y)) end
    if fn(u, 'dwriteText') then pcall(u.dwriteText, text, size, col) end
  elseif col and fn(u, 'textColored') then
    if fn(u, 'setCursor') then pcall(u.setCursor, vec2(x, y)) end
    pcall(u.textColored, text, col)
  elseif fn(u, 'text') then
    if fn(u, 'setCursor') then pcall(u.setCursor, vec2(x, y)) end
    pcall(u.text, text)
  end
  return #text * size * 0.55
end

-- Text width: AC font measurement when available, else a conservative estimate.
local function measure(u, text, size, useAc, vec2)
  if useAc and vec2 and fn(u, 'calculateACTextSize') then
    local ok, s = pcall(u.calculateACTextSize, text, vec2(size, math.floor(size * 2 + 0.5)))
    if ok and type(s) == 'table' and finite(s.x) then return s.x end
  end
  if fn(u, 'measureDWriteText') then
    local ok, s = pcall(u.measureDWriteText, text, size)
    if ok and type(s) == 'table' and finite(s.x) then return s.x end
  end
  return #text * size * 0.55
end

-- Usable width of the window this overlay is drawn into.
local function windowWidth(u, x, fallback)
  if fn(u, 'windowSize') then
    local ok, s = pcall(u.windowSize)
    if ok and type(s) == 'table' and finite(s.x) and s.x > x + 60 then return s.x - x end
  end
  return fallback
end

-- Panel: dark translucent, slightly rounded, like AC's own overlay panels.
local function panel(u, x, y, w, h, vec2, rgbm, alpha, rounding)
  if not (vec2 and rgbm and fn(u, 'drawRectFilled')) then return end
  local p = overlay.palette().panel
  pcall(u.drawRectFilled, vec2(x, y), vec2(x + w, y + h), rgbm(p[1], p[2], p[3], alpha), rounding or 3)
end

-- Height of the timing panel in px (so the delta panel can be stacked under it).
function M.height(model)
  if type(model) ~= 'table' or model.kind ~= 'timing' then return 0 end
  local lay = type(model.layout) == 'table' and model.layout or overlay.layout(nil)
  local scale = lay.scale
  local pad = math.floor(6 * scale + 0.5)
  local lineH = math.floor(15 * scale * 1.45 + 0.5)
  local rows = type(model.rows) == 'table' and #model.rows or 0
  return 2 * pad + math.max(1, rows) * lineH + 4
end

local function renderTiming(u, model, vec2, rgbm, useAc, useDw)
  local lay = model.layout
  local scale = lay.scale
  local pad = math.floor(6 * scale + 0.5)
  local labelSize = math.floor(11 * scale + 0.5)
  local valueSize = math.floor(19 * scale + 0.5)
  local lineH = math.floor(15 * scale * 1.45 + 0.5)
  local x, y = lay.x, lay.y
  local rows = type(model.rows) == 'table' and model.rows or {}
  local width = math.max(210, 210 * scale)
  local w = windowWidth(u, x, width)
  panel(u, x, y, w, 2 * pad + math.max(1, #rows) * lineH + 4, vec2, rgbm, lay.alpha, 3)
  local labelCol = color(rgbm, overlay.palette().label)
  for i, r in ipairs(rows) do
    local size = r.size == 'small' and labelSize or valueSize
    local ry = y + pad + (i - 1) * lineH
    local col = color(rgbm, r.color)
    if col then
      drawText(u, r.label, x + pad, ry, size, labelCol or col, vec2, useAc, useDw)
      -- Right-aligned value inside the panel, like AC's timing table.
      local vx = x + w - pad - measure(u, r.text, size, useAc, vec2)
      drawText(u, r.text, vx, ry, size, col, vec2, useAc, useDw)
    end
  end
end

-- AC's delta meter: a thin horizontal track, centre tick at zero, fill growing
-- left when faster (green) and right when slower (red), with the signed number.
local function renderDelta(u, model, vec2, rgbm, useAc, useDw)
  local lay = model.layout
  local scale = lay.scale
  local x, y = lay.x, lay.y
  local pad = math.floor(6 * scale + 0.5)
  local trackW = 260 * scale
  local w = windowWidth(u, x, trackW)
  local h = math.floor(14 * scale + 0.5)
  local textSize = math.floor(15 * scale + 0.5)
  panel(u, x, y, w, pad * 2 + h + textSize + 4, vec2, rgbm, lay.alpha, 3)
  local bx, by = x + pad, y + pad
  if fn(u, 'drawRectFilled') then
    pcall(u.drawRectFilled, vec2(bx, by), vec2(bx + w - 2 * pad, by + h),
      rgbm(0.08, 0.08, 0.08, 0.9), math.floor(h / 2 + 0.5))
  end
  local inner = w - 2 * pad
  local mid = bx + inner / 2
  local frac = (type(model.frac) == 'number') and model.frac or 0
  if frac ~= 0 and fn(u, 'drawRectFilled') then
    local xe = mid + frac * (inner / 2)
    local col = color(rgbm, model.color) or rgbm(1, 1, 1, 1)
    pcall(u.drawRectFilled, vec2(math.min(mid, xe), by + 1), vec2(math.max(mid, xe), by + h - 1), col,
      math.floor(h / 2 + 0.5))
  end
  -- Zero marker
  if fn(u, 'drawRectFilled') then
    pcall(u.drawRectFilled, vec2(mid - 1, by - 2), vec2(mid + 1, by + h + 2), rgbm(1, 1, 1, 0.85), 0)
  end
  -- Signed readout under the bar (right-aligned when a value exists)
  local text = model.text or '--'
  local tw = measure(u, text, textSize, useAc, vec2)
  local col = color(rgbm, model.color) or rgbm(1, 1, 1, 1)
  local hasValue = model.has_reference and model.value_ms ~= nil
  local unitW = hasValue and (measure(u, 's', textSize, useAc, vec2) + 2) or 0
  local tx = hasValue and (bx + inner - tw - unitW) or (bx + (inner - tw) / 2)
  drawText(u, text, tx, by + h + 3, textSize, col, vec2, useAc, useDw)
  if model.has_reference and model.value_ms ~= nil then
    drawText(u, 's', tx + tw + 2, by + h + 3, textSize, color(rgbm, overlay.palette().label) or col,
      vec2, useAc, useDw)
  end
end

-- Draw a model. Returns true when the frame was drawn. Never raises, and the
-- font stack is always balanced (same contract as hud.draw).
function M.draw(model, config)
  if type(model) ~= 'table' or type(model.kind) ~= 'string' then return false end
  local u = rawget(_G, 'ui')
  if type(u) ~= 'table' then return false end
  local lay = type(model.layout) == 'table' and model.layout or overlay.layout(config)
  model.layout = lay
  local vec2, rgbm = rawget(_G, 'vec2'), rawget(_G, 'rgbm')
  -- AC font only when push/pop/text are all present, so the stack stays balanced.
  local useAc = fn(u, 'pushACFont') and fn(u, 'popACFont') and fn(u, 'acText')
  local useDw = false
  local font = type(config) == 'table' and type(config.font) == 'string' and config.font or nil
  local pushedAc, pushedDw = false, false
  if useAc then
    local okFont, err = pcall(u.pushACFont, font or FONT_AC)
    if okFont then pushedAc = true else
      -- Font not available on this build: degrade to DWrite instead of failing.
      useAc = false
    end
  end
  if not useAc then
    useDw = fn(u, 'pushDWriteFont') and fn(u, 'popDWriteFont') and fn(u, 'dwriteText')
    if useDw then
      local okFont = pcall(u.pushDWriteFont, FONT_DW)
      if okFont then pushedDw = true else useDw = false end
    end
  end
  local ok = pcall(function()
    if model.kind == 'timing' then
      renderTiming(u, model, vec2, rgbm, useAc, useDw)
    elseif model.kind == 'delta' then
      renderDelta(u, model, vec2, rgbm, useAc, useDw)
    else
      error('unknown overlay kind: ' .. tostring(model.kind), 0)
    end
  end)
  if pushedAc then pcall(u.popACFont) elseif pushedDw then pcall(u.popDWriteFont) end
  return ok
end

return M
