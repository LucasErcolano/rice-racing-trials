-- Opt-in OR-channel producer, independent of V1 and native_laps local backup.
-- Readback-confirmed journals survive app/AC restarts; no fsync/power-loss claim.
local M = {}
local function integer(n) return type(n)=='number' and n==n and n>=0 and n<2^53 and n%1==0 end
local function field(t,k) local ok,v=pcall(function() return t[k] end); if ok then return v end end
local function validContext(c)
  if type(c)~='table' then return false end
  for _,k in ipairs({'station_id','session_id','pilot_id','track_config'}) do
    if type(c[k])~='string' or not c[k]:match('^[%w_.-]+$') or #c[k]>80 then return false end
  end
  return true
end
function M.new(opts)
  local o = {busy={}, dirty={},clock=0,due={},attempt={}}
  local journals,states,data={},{},{}
  for _,ch in ipairs({'native','penalty'}) do
    journals[ch]=opts.journal('field_'..ch..'_v2')
    local items,state=journals[ch]:load()
    states[ch]=state
    data[ch]=items[1] or {version=1,nonce=opts.nonce,seq=0,epoch=0,records={},queue={}}
  end
  local n,p=data.native,data.penalty
  local function save(ch)
    if states[ch]=='corrupt' then return false end
    local ok=journals[ch]:save({data[ch]})
    o.dirty[ch]=not ok
    states[ch]=ok and journals[ch]:state().state or 'unsaved'
    return ok
  end
  local function clone(v) return opts.decode(opts.encode(v)) end
  local function envelope(channel,id,ctx,raw)
    return {protocol='rice-lap-or-v2',message_id=id..'-'..channel,
      station_id=ctx.station_id,session_id=ctx.session_id,pilot_id=ctx.pilot_id,
      track_config=ctx.track_config,lap_id=id,raw_ms=raw,rules_version=1,channel=channel}
  end
  local function find(id)
    for _,r in ipairs(n.records) do if r.id==id then return r end end
  end
  local function slot(target)
    for _,r in ipairs(n.records) do if r.epoch==n.epoch and r.lap_count==target then return r end end
    n.seq=n.seq+1
    local r={id=n.nonce..'-n-'..n.seq,lap_count=target,epoch=n.epoch,
      context=validContext(n.context) and clone(n.context) or nil}
    n.records[#n.records+1]=r
    return r
  end
  local function resolve()
    local changed=false
    for _,r in ipairs(p.records) do
      if not r.queued then
        local native=find(r.id)
        if not r.standalone and not (native and native.raw_ms) and (r.wait_s or 0)>=5 then
          local candidate=r.id
          p.seq=p.seq+1; r.id=p.nonce..'-r-'..p.seq; r.standalone=true
          r.snapshot.evidence.candidate_lap_id=candidate
          r.snapshot.evidence.detector_ok=false
          r.snapshot.evidence.review_reasons=r.snapshot.evidence.review_reasons or {}
          table.insert(r.snapshot.evidence.review_reasons,'native_capture_missing')
          native=nil
        end
        if r.standalone or (native and integer(native.raw_ms) and native.raw_ms>0) then
          local body=envelope('penalty',r.id,r.context,r.standalone and r.raw_ms or native.raw_ms)
          body.events,body.settings,body.evidence=r.snapshot.events,r.snapshot.settings,r.snapshot.evidence
          body.evidence.route_raw_ms=r.raw_ms
          body.native_valid=native and native.native_valid==true or false
          if native then body.evidence.native_lap_count=native.lap_count end
          p.queue[#p.queue+1]={id=body.message_id,payload=opts.encode(body)}
          r.queued=true; changed=true
        end
      end
    end
    if changed then save('penalty') end
  end
  function o:begin_lap(previous)
    if n.ref==nil or states.native=='corrupt' then
      local ctx=opts.context()
      if not validContext(ctx) or states.penalty=='corrupt' then return nil end
      p.seq=p.seq+1
      local id=p.nonce..'-r-'..p.seq
      p.reservations=p.reservations or {}
      p.reservations[id]={id=id,context=clone(ctx),standalone=true}
      if save('penalty') then return id end
      return nil
    end
    local target=n.ref+1
    local prev=previous and find(previous)
    if prev and prev.epoch==n.epoch then target=math.max(target,prev.lap_count+1) end
    local r=slot(target)
    if not validContext(r.context) or not save('native') then return nil end
    return r.id
  end
  function o:finish_lap(id,raw,snapshot)
    local r=find(id) or (p.reservations and p.reservations[id])
    if not r or not validContext(r.context) or states.penalty=='corrupt' then return false end
    for _,entry in ipairs(p.records) do if entry.id==id or entry.route_token==id then return false end end
    local standalone=r.standalone or r.epoch~=n.epoch or n.ref>r.lap_count
    local routeToken=id
    snapshot=clone(snapshot)
    if standalone then
      p.seq=p.seq+1; id=p.nonce..'-r-'..p.seq
      snapshot.evidence.review_reasons={'identity_ambiguous'}
      snapshot.evidence.detector_ok=false
    end
    p.records[#p.records+1]={id=id,route_token=routeToken,raw_ms=raw,context=clone(r.context),snapshot=snapshot,standalone=standalone}
    local ok=save('penalty'); resolve(); return ok
  end
  function o:session_start()
    n.epoch=n.epoch+1; n.ref=nil; n.context=nil
    save('native')
  end
  function o:observe(car,dt)
    if type(dt)=='number' and dt>0 and dt<=1 then
      self.clock=self.clock+dt
      for _,r in ipairs(p.records) do if not r.queued then r.wait_s=(r.wait_s or 0)+dt end end
    end
    if states.native=='corrupt' then return end
    local lc=field(car,'lapCount')
    if not integer(lc) then return end
    local ctx=opts.context()
    if n.ref==nil or lc<n.ref then
      if n.ref~=nil then n.epoch=n.epoch+1 end
      n.ref=lc; n.context=validContext(ctx) and clone(ctx) or nil; save('native')
    elseif lc>n.ref then
      local record=slot(lc)
      local raw=field(car,'previousLapTimeMs')
      if integer(raw) and raw>0 and raw<=3600000 then
        record.raw_ms=raw
        if validContext(record.context) then
          local body=envelope('native',record.id,record.context,raw)
          local v=field(car,'isLastLapValid')
          record.native_valid=v
          body.native_valid=v==true
          if type(v)~='boolean' then body.native_valid_available=false end
          n.queue[#n.queue+1]={id=body.message_id,payload=opts.encode(body)}
        end
      end
      n.ref=lc; n.context=validContext(ctx) and clone(ctx) or nil
      save('native'); resolve()
    elseif not validContext(n.context) and validContext(ctx) then
      n.context=clone(ctx); save('native')
    end
  end
  function o:pump()
    resolve()
    for _,ch in ipairs({'native','penalty'}) do
      if o.dirty[ch] then save(ch) end
      if o.busy[ch] and o.clock-o.busy[ch]>=15 then o.busy[ch]=nil end
      local item=data[ch].queue[1]
      if item and states[ch]~='corrupt' and not o.dirty[ch] and not o.busy[ch] and o.clock>=(o.due[ch] or 0) then
        o.busy[ch]=o.clock
        o.due[ch]=o.clock+5
        o.attempt[ch]=(o.attempt[ch] or 0)+1
        local attempt=o.attempt[ch]
        local completed=false
        local ok=pcall(opts.post,ch,item.payload,function(body)
          if completed or o.attempt[ch]~=attempt or data[ch].queue[1]~=item then return end
          completed=true
          o.busy[ch]=nil
          if type(body)~='table' or body[ch..'_received']~=true then return end
          local receipt=body[ch..'_payload']
          local expected=opts.decode(item.payload)
          if type(receipt)~='table' then return end
          for _,key in ipairs({'message_id','channel','lap_id','station_id','session_id','pilot_id','track_config','raw_ms'}) do
            if receipt[key]~=expected[key] then return end
          end
          table.remove(data[ch].queue,1)
          if not save(ch) then table.insert(data[ch].queue,1,item) end
        end)
        if not ok then o.busy[ch]=false end
      end
    end
  end
  function o:export() return opts.encode({protocol='rice-lap-or-v2',native=n,penalty=p,diagnostics=self:stats()}) end
  function o:stats() return {native_pending=#n.queue,penalty_pending=#p.queue,
    native_state=states.native,penalty_state=states.penalty} end
  return o
end
return M
