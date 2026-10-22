-- Opt-in OR-channel producer, independent of V1 and native_laps local backup.
-- Readback-confirmed journals survive app/AC restarts; no fsync/power-loss claim.
local M = {}
local function integer(n) return type(n)=='number' and n==n and n>=0 and n<2^53 and n%1==0 end
local function field(t,k) local ok,v=pcall(function() return t[k] end); if ok then return v end end
local function validContext(c)
  if type(c)~='table' or type(c.station_id)~='string' or
    not c.station_id:match('^[%w_.-]+$') or #c.station_id>80 then return false end
  for _,k in ipairs({'session_id','pilot_id','track_config'}) do
    if c[k]~=nil and (type(c[k])~='string' or not c[k]:match('^[%w_.-]+$') or #c[k]>80) then return false end
  end
  return true
end
local function attributed(c)
  return validContext(c) and type(c.session_id)=='string' and type(c.pilot_id)=='string' and type(c.track_config)=='string'
end
function M.penalty_events(cone_ids, excluded, max_cones, off_times, violations, gate_times, cfg, auto)
  local events, charged = {}, 0
  auto = auto ~= false
  for _, id in ipairs(cone_ids or {}) do
    local eligible = auto and not excluded[id] and charged < max_cones
    if eligible then charged = charged + 1 end
    events[#events+1] = {type='cone_contact', cone_id=id, penalizable=eligible}
  end
  if not auto then return events end
  local slalom_times = {}
  for _, id in ipairs(violations or {}) do
    if gate_times[id] then slalom_times[#slalom_times+1] = gate_times[id] end
  end
  local dedupe = (cfg.dedupe_ms or 0) / 1000
  local suppress_overlap = cfg.slalom and cfg.slalom.on and cfg.slalom.s > 0 and dedupe > 0
  for i, t in ipairs(off_times or {}) do
    local eligible = i <= 100
    if suppress_overlap then
      for _, s in ipairs(slalom_times) do
        if math.abs(t-s) <= dedupe then eligible = false; break end
      end
    end
    events[#events+1] = {type='off_course', incident_id='off-'..i,
      penalizable=eligible, at_ms=math.floor(t*1000+0.5)}
  end
  for _, id in ipairs(violations or {}) do
    events[#events+1] = {type='slalom_violation', incident_id='slalom-'..id,
      penalizable=true, gate_id=id}
  end
  return events
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
      track_config=ctx.track_config,assignment_revision=ctx.assignment_revision,
      lap_id=id,raw_ms=raw,rules_version=1,channel=channel}
  end
  local function find(id)
    for _,r in ipairs(n.records) do if r.id==id then return r end end
  end
  local function enqueue(ch,body)
    local id=body.message_id
    for _,item in ipairs(data[ch].queue) do if item.id==id then return true end end
    local item={id=id,payload=opts.encode(body)}
    data[ch].queue[#data[ch].queue+1]=item
    if save(ch) then return true end
    table.remove(data[ch].queue)
    return false
  end
  local function slot(target)
    for _,r in ipairs(n.records) do if r.epoch==n.epoch and r.lap_count==target then return r end end
    n.seq=n.seq+1
    local r={id=n.nonce..'-n-'..n.seq,lap_count=target,epoch=n.epoch,
      context=validContext(n.context) and clone(n.context) or nil}
    n.records[#n.records+1]=r
    return r
  end
  local function capture(record,car)
    local raw=field(car,'previousLapTimeMs')
    if not (integer(raw) and raw>0 and raw<=3600000) then
      record.capture_state='awaiting_previous_lap_time'
      return false
    end
    record.raw_ms=raw; record.capture_state='captured'
    -- The first native counter sample is only a baseline. If it had no authenticated assignment,
    -- use the latest context at completion; the stale assignment is bounded by the app's TTL.
    if not attributed(record.context) then
      local current=opts.context()
      if attributed(current) or validContext(current) then record.context=clone(current) end
    end
    if validContext(record.context) then
      local body=envelope('native',record.id,record.context,raw)
      local v=field(car,'isLastLapValid')
      record.native_valid=v
      body.native_valid=v==true
      if type(v)~='boolean' then body.native_valid_available=false end
      if not attributed(record.context) then
        body.session_id='UNASSIGNED'; body.pilot_id='UNASSIGNED'
        body.track_config=record.context.track_config or 'UNKNOWN'
        body.assignment_state='UNASSIGNED'; body.review_reason='no_authenticated_assignment'
        record.unassigned=true
      end
      enqueue('native',body)
    end
    n.pending_completion=nil
    return true
  end
  -- Upgrade durable native drafts created by older clients that captured a time but
  -- withheld it because assignment context was missing. Preserve uncertainty; never infer identity.
  for _,record in ipairs(n.records) do
    if record.raw_ms and not record.unassigned then
      local ctx=record.context
      if not validContext(ctx) then
        local current=opts.context()
        if type(current)=='table' then ctx={station_id=current.station_id,track_config=current.track_config} end
      end
      if validContext(ctx) and not attributed(ctx) then
        local body=envelope('native',record.id,ctx,record.raw_ms)
        body.session_id='UNASSIGNED'; body.pilot_id='UNASSIGNED'
        body.track_config=ctx.track_config or 'UNKNOWN'
        body.native_valid=record.native_valid==true
        if type(record.native_valid)~='boolean' then
          local valid=type(opts.native_evidence)=='function' and opts.native_evidence(record) or nil
          body.native_valid=valid==true
          if type(valid)~='boolean' then body.native_valid_available=false end
        end
        body.assignment_state='UNASSIGNED'; body.review_reason='no_authenticated_assignment'
        if enqueue('native',body) then record.context=ctx; record.unassigned=true; save('native') end
      end
    end
  end
  local function captureEvidence(evidence,native)
    if not native or not native.capture_state or native.raw_ms then return end
    evidence.native_capture={lap_id=native.id,lap_count=native.lap_count,
      epoch=native.epoch,state=native.capture_state,
      superseded_by_lap_count=native.superseded_by_lap_count,
      interruption_reason=native.interruption_reason}
    evidence.review_reasons=evidence.review_reasons or {}
    table.insert(evidence.review_reasons,native.capture_state)
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
          captureEvidence(r.snapshot.evidence,native)
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
      snapshot.evidence.review_reasons=snapshot.evidence.review_reasons or {}
      table.insert(snapshot.evidence.review_reasons,'identity_ambiguous')
      captureEvidence(snapshot.evidence,r)
      snapshot.evidence.detector_ok=false
    end
    p.records[#p.records+1]={id=id,route_token=routeToken,raw_ms=raw,context=clone(r.context),snapshot=snapshot,standalone=standalone}
    local ok=save('penalty'); resolve(); return ok
  end
  function o:session_start(reason)
    local pending=n.pending_completion and find(n.pending_completion)
    if pending then
      pending.capture_state='native_capture_interrupted'
      pending.interruption_reason=reason or 'session_start'
    end
    n.epoch=n.epoch+1; n.ref=nil; n.context=nil; n.pending_completion=nil
    save('native')
  end
  function o:observe(car,dt,replay)
    if type(dt)=='number' and dt>0 and dt<=1 then
      self.clock=self.clock+dt
      for _,r in ipairs(p.records) do if not r.queued then r.wait_s=(r.wait_s or 0)+dt end end
    end
    if states.native=='corrupt' then return end
    -- Replay counters are not live completions. Persist the boundary so a restart
    -- during replay still requires a fresh baseline before accepting live laps.
    if replay==true then
      if not n.replay then
        n.replay=true; self:session_start('replay')
      end
      return
    end
    if n.replay then n.replay=nil; save('native') end
    local lc=field(car,'lapCount')
    if not integer(lc) then return end
    local ctx=opts.context()
    if n.ref==nil or lc<n.ref then
      if n.ref~=nil then self:session_start('counter_reset') end
      n.ref=lc; n.context=validContext(ctx) and clone(ctx) or nil; save('native')
    elseif lc>n.ref then
      local pending=n.pending_completion and find(n.pending_completion)
      if pending then
        pending.capture_state='native_time_unavailable'
        pending.superseded_by_lap_count=lc
        n.pending_completion=nil
      end
      if lc>n.ref+1 then
        -- One range, not one allocation per missed counter (a replay/jump can be huge).
        n.gaps=n.gaps or {}
        n.gaps[#n.gaps+1]={epoch=n.epoch,from_lap_count=n.ref+1,to_lap_count=lc-1,
          reason='native_counter_gap'}
        for _,r in ipairs(n.records) do
          if r.epoch==n.epoch and r.lap_count>n.ref and r.lap_count<lc and not r.raw_ms then
            r.capture_state='native_counter_gap'; r.superseded_by_lap_count=lc
          end
        end
      end
      local record=slot(lc)
      n.pending_completion=record.id
      capture(record,car)
      n.ref=lc; n.context=validContext(ctx) and clone(ctx) or nil
      save('native'); resolve()
    elseif n.pending_completion then
      local record=find(n.pending_completion)
      if record and record.epoch==n.epoch and record.lap_count==lc and capture(record,car) then
        save('native'); resolve()
      end
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
