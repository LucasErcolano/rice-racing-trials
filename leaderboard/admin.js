'use strict';
// Admin console. Token lives only in sessionStorage; all DOM writes use textContent (no HTML injection).
const el = id => document.getElementById(id);
const val = id => el(id).value.trim();
const TOKEN_KEY = 'rice_admin_token';
const state = {stations: [], laps: [], pilots: [], sessions: []};

function token() { return sessionStorage.getItem(TOKEN_KEY) || ''; }
function say(text, kind) { const m = el('message'); m.textContent = text; m.dataset.kind = kind || ''; }
function authState(text, kind) { const p = el('auth-state'); p.textContent = text; p.dataset.state = kind; }
function seconds(ms) { return ms == null ? '—' : (ms / 1000).toFixed(3); }
function json(value) { return value == null ? '' : (typeof value === 'string' ? value : JSON.stringify(value)); }
function currentSession() { return el('current-session').value; }

async function api(method, path, data) {
  const options = {method, cache: 'no-store', headers: {Authorization: 'Bearer ' + token()}};
  if (data !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data); }
  const response = await fetch(path, options);
  const type = response.headers.get('Content-Type') || '';
  const body = type.startsWith('application/json') ? await response.json() : await response.text();
  if (response.status === 401 || response.status === 403) authState('Invalid token', 'error');
  if (!response.ok) throw Error((body && body.error) || ('HTTP ' + response.status));
  return body;
}
const post = (path, data) => api('POST', path, data);
const get = path => api('GET', path);

function fillTable(id, rows, columns, decorate) {
  const body = el(id); body.replaceChildren();
  for (const item of rows) {
    const row = body.insertRow();
    for (const column of columns) {
      const td = row.insertCell();
      const value = typeof column === 'function' ? column(item) : item[column];
      if (value instanceof Node) td.append(value); else td.textContent = value == null ? '' : String(value);
    }
    if (decorate) decorate(row, item);
  }
  if (!rows.length) { const td = body.insertRow().insertCell(); td.colSpan = columns.length; td.textContent = 'No data'; }
}
function options(select, items, value, label) {
  const keep = select.value;
  select.replaceChildren();
  for (const item of items) {
    const option = document.createElement('option');
    option.value = item[value]; option.textContent = label(item);
    select.append(option);
  }
  if ([...select.options].some(o => o.value === keep)) select.value = keep;
}
function button(text, handler, className) {
  const b = document.createElement('button');
  b.type = 'button'; b.textContent = text; b.className = 'small ' + (className || '');
  b.addEventListener('click', handler);
  return b;
}
function statusSpan(status) { const s = document.createElement('span'); s.className = 'st-' + status; s.textContent = status; return s; }
function penaltySummary(lap) {
  const e = lap.events || {};
  if (lap.event === 'endurance') return `cones ${e.cone || 0} · off ${e.off_course || 0} · slalom ${(e.slalom || []).length}`;
  return `DOO ${e.doo || 0} · OC ${e.oc || 0}` + (e.right_ms ? ` · R ${e.right_ms} L ${e.left_ms}` : '');
}

function selectLap(lap) {
  el('station').value = lap.station_id; el('lap-id').value = lap.lap_id;
  const e = lap.events || {};
  el('pen-cone').value = e.cone ?? ''; el('pen-off').value = e.off_course ?? '';
  el('pen-slalom').value = (e.slalom || []).join(',');
  el('pen-doo').value = e.doo ?? ''; el('pen-oc').value = e.oc ?? '';
  for (const row of el('pending').rows) row.classList.toggle('selected', row.dataset.lap === lap.station_id + '/' + lap.lap_id);
  say(`Lap selected: ${lap.station_id}/${lap.lap_id}`, 'ok');
}

async function loadSessions() {
  const data = await get('/api/admin/sessions');
  state.sessions = data.sessions;
  const select = el('current-session');
  const keep = select.value || data.active_session || '';
  options(select, data.sessions, 'session_id', s => s.session_id + ' · ' + s.name + (s.active ? ' (active)' : ''));
  select.value = [...select.options].some(o => o.value === keep) ? keep : (data.active_session || (select.options[0] && select.options[0].value) || '');
  fillTable('sessions', data.sessions, ['session_id', 'name', 'created_utc', s => s.active ? 'ACTIVE' : '',
    s => s.active ? '' : button('Mark active', () => run(async () => { await post('/api/admin/sessions/active', {session_id: s.session_id}); say('Active session: ' + s.session_id, 'ok'); }))]);
}
async function loadPilots() {
  const data = await get('/api/admin/pilots');
  state.pilots = data.pilots;
  fillTable('pilots', data.pilots, ['pilot_id', 'name']);
  options(el('assign-pilot'), data.pilots, 'pilot_id', p => p.pilot_id + ' · ' + p.name);
}
async function loadStations() {
  const data = await get('/api/admin/stations');
  state.stations = data.stations;
  for (const id of ['assign-station', 'cal-station']) options(el(id), data.stations, 'station_id', s => s.station_id);
  fillTable('stations', data.stations, ['station_id',
    s => { const x = document.createElement('span'); x.className = s.online ? 'online' : 'offline'; x.textContent = s.online ? 'ONLINE' : 'OFFLINE'; return x; },
    s => s.last_seen_utc || 'never', s => (s.session_id || '-') + ' / ' + (s.pilot_id || '-'), 'assignment_revision',
    s => s.last_lap_id ? `${s.last_lap_id} (${s.last_lap_status})` : '-',
    s => s.detector_ok === true ? 'OK' : s.detector_ok === false ? 'FAIL' : 'no data',
    s => json(s.detector).slice(0, 300) + (s.detector_error_laps ? ` · ${s.detector_error_laps} lap(s) with errors` : ''),
    s => s.sync_error_count ? `${s.sync_error_count} (last ${s.last_sync_error_utc})` : '0'],
    (row, s) => { row.dataset.station = s.station_id; });
}
async function loadLaps() {
  const session = currentSession();
  if (!session) { fillTable('pending', [], ['x']); return; }
  const data = await get('/api/admin/laps?session_id=' + encodeURIComponent(session));
  state.laps = data.laps;
  const onlyPending = el('only-pending').checked;
  const rows = data.laps.filter(l => !onlyPending || l.status === 'PENDING_REVIEW' || l.status === 'DETECTOR_ERROR');
  fillTable('pending', rows, ['station_id', 'lap_id', 'lap_number', 'pilot_id', 'event', l => statusSpan(l.status), l => seconds(l.raw_ms), l => seconds(l.corrected_ms),
    penaltySummary, l => (l.detector_ok === true ? 'OK' : l.detector_ok === false ? 'FAIL' : '?') + (l.detector ? ' ' + json(l.detector).slice(0, 120) : ''),
    l => l.calibration_version ?? l.calibration_reference ?? '', l => button('Select', () => selectLap(l))],
    (row, l) => { row.dataset.lap = l.station_id + '/' + l.lap_id; });
}
async function loadAudit() {
  const session = currentSession();
  const list = el('audit'); list.replaceChildren();
  if (!session) return;
  const data = await get('/api/admin/reviews?session_id=' + encodeURIComponent(session));
  for (const r of data.reviews) {
    const item = document.createElement('li');
    const change = r.kind === 'penalties' ? `penalties ${r.before_events} → ${r.after_events} (corrected ${seconds(r.before_corrected_ms)} → ${seconds(r.after_corrected_ms)})` : `${r.before_status} → ${r.after_status}`;
    item.textContent = `${r.created_utc} · ${r.station_id}/${r.lap_id}: ${change} · ${r.reviewer}: ${r.reason}`;
    list.append(item);
  }
  if (!data.reviews.length) { const item = document.createElement('li'); item.textContent = 'No reviews recorded'; list.append(item); }
}
async function loadAssignments() {
  const data = await get('/api/admin/assignments');
  fillTable('assignment-history', data.history.slice().reverse(), ['revision', 'station_id', 'session_id', a => a.pilot_id + (a.pilot_name ? ' · ' + a.pilot_name : ''),
    a => a.previous_pilot_id ? a.previous_session_id + ' / ' + a.previous_pilot_id : '', 'created_utc'],
    (row, a) => { if (a.reassignment) row.classList.add('reassignment'); });
}
async function loadCalibrations() {
  const data = await get('/api/admin/calibrations');
  fillTable('calibrations', data.calibrations, ['station_id', 'track_config', 'reference', 'operator', c => c.active ? 'yes' : 'no', 'updated_utc']);
}
async function loadSyncErrors() {
  const data = await get('/api/admin/sync-errors');
  fillTable('sync-errors', data.sync_errors.slice(0, 50), ['created_utc', 'station_id', 'path', 'http_status', 'cause', 'lap_id']);
}
async function loadDiagnostics() {
  const data = await get('/api/admin/diagnostics');
  el('armed-banner').hidden = data.scoring_armed === true;
  const dl = el('diagnostics'); dl.replaceChildren();
  const pairs = [['Scoring commissioned', data.scoring_armed ? 'YES' : 'NO (fail-closed)'], ['Rules', 'v' + data.rules_version + ' ' + JSON.stringify(data.penalties_ms)],
    ['SQLite schema', 'v' + data.schema_version + ' · journal ' + data.journal_mode], ['Database', data.database],
    ['Active session', data.active_session || 'none'], ['Laps by status', JSON.stringify(data.status_counts)],
    ['TTL heartbeat', data.heartbeat_ttl_s + ' s'], ['Offline stations', data.stations.filter(s => s.offline).map(s => s.station_id).join(', ') || 'none']];
  for (const [k, v] of pairs) { const dt = document.createElement('dt'); dt.textContent = k; const dd = document.createElement('dd'); dd.textContent = v; dl.append(dt, dd); }
  const backups = el('backups'); backups.replaceChildren();
  for (const b of data.backups.slice(0, 5)) { const li = document.createElement('li'); li.textContent = `${b.path} (${b.bytes} bytes)`; backups.append(li); }
}

async function refreshAll() {
  if (!token()) { authState('Not signed in', 'out'); return; }
  await loadSessions();
  await Promise.all([loadPilots(), loadStations(), loadAssignments(), loadCalibrations(), loadSyncErrors(), loadDiagnostics()]);
  await Promise.all([loadLaps(), loadAudit()]);
  authState('Signed in', 'in');
}
async function run(action) {
  try { await action(); await refreshAll(); } catch (e) { say(e.message, 'error'); }
}

el('login').addEventListener('click', () => {
  const value = val('token');
  if (!value) { say('Paste the admin token', 'error'); return; }
  sessionStorage.setItem(TOKEN_KEY, value);
  el('token').value = '';
  run(async () => { await get('/api/admin/diagnostics'); say('Signed in', 'ok'); });
});
el('logout').addEventListener('click', () => { sessionStorage.removeItem(TOKEN_KEY); authState('Not signed in', 'out'); say('Token cleared from this tab', 'ok'); });
el('refresh').addEventListener('click', () => run(async () => say('Data refreshed', 'ok')));
el('current-session').addEventListener('change', () => run(async () => {}));
el('only-pending').addEventListener('change', () => run(async () => {}));
el('save-session').addEventListener('click', () => run(async () => {
  const s = await post('/api/admin/sessions', {session_id: val('session'), name: val('session-name'), active: el('session-activate').checked});
  el('current-session').value = s.session_id;
  say('Session saved: ' + s.session_id + (s.active ? ' (active)' : ''), 'ok');
  await loadSessions(); el('current-session').value = s.session_id;
}));
el('save-pilot').addEventListener('click', () => run(async () => {
  const p = await post('/api/admin/pilots', {pilot_id: val('pilot'), name: val('pilot-name')});
  say('Driver registered: ' + p.pilot_id, 'ok');
}));
el('assign').addEventListener('click', () => run(async () => {
  const a = await post('/api/admin/assignments', {station_id: el('assign-station').value, session_id: currentSession(), pilot_id: el('assign-pilot').value});
  say(`Assigned ${a.pilot_id} to ${a.station_id} (revision ${a.assignment_revision})`, 'ok');
}));
el('calibrate').addEventListener('click', () => run(async () => {
  const c = await post('/api/admin/calibrations', {station_id: el('cal-station').value, track_config: val('track-config'), reference: val('reference'), operator: val('operator'), active: el('active').checked});
  say(`Calibration saved: ${c.station_id}/${c.track_config}`, 'ok');
}));
el('review').addEventListener('click', () => run(async () => {
  const r = await post('/api/admin/reviews', {station_id: val('station'), lap_id: val('lap-id'), decision: el('decision').value, reviewer: val('reviewer'), reason: val('reason')});
  say(`Review recorded: ${r.lap_id} → ${r.status}`, 'ok');
}));
el('save-penalties').addEventListener('click', () => run(async () => {
  const lap = state.laps.find(l => l.station_id === val('station') && l.lap_id === val('lap-id'));
  if (!lap) throw Error('Select a lap from the table');
  const events = {};
  const count = id => { const v = val(id); if (v === '') return undefined; const n = Number(v); if (!Number.isInteger(n)) throw Error('Invalid count'); return n; };
  if (lap.event === 'endurance') {
    const cone = count('pen-cone'), off = count('pen-off');
    if (cone !== undefined) events.cone = cone;
    if (off !== undefined) events.off_course = off;
    events.slalom = val('pen-slalom') ? val('pen-slalom').split(',').map(x => x.trim()).filter(Boolean) : [];
  } else {
    const doo = count('pen-doo'), oc = count('pen-oc');
    if (doo !== undefined) events.doo = doo;
    if (oc !== undefined) events.oc = oc;
  }
  const r = await post('/api/admin/penalties', {station_id: lap.station_id, lap_id: lap.lap_id, events, reviewer: val('pen-reviewer'), reason: val('pen-reason')});
  say(`Penalties saved: ${r.lap_id} · calculated corrected ${seconds(r.corrected_preview_ms)}` + (r.official ? ' (official)' : ' (unofficial: status ' + r.status + ')'), 'ok');
}));
el('backup').addEventListener('click', () => run(async () => { const b = await post('/api/admin/backup', {}); say('Backup created: ' + b.path, 'ok'); }));

async function download(path, filename) {
  try {
    const all = el('export-all').checked;
    const sep = path.includes('?') ? '&' : '?';
    const url = all ? path : path + sep + 'session_id=' + encodeURIComponent(currentSession());
    const response = await fetch(url, {headers: {Authorization: 'Bearer ' + token()}, cache: 'no-store'});
    if (!response.ok) throw Error((await response.json()).error);
    const href = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = href; link.download = filename; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    say('Download ready: ' + filename, 'ok');
  } catch (e) { say(e.message, 'error'); }
}
el('export').addEventListener('click', () => download('/api/admin/export.csv?table=laps', 'rice-laps.csv'));
el('export-reviews').addEventListener('click', () => download('/api/admin/export.csv?table=reviews', 'rice-reviews.csv'));
el('export-assignments').addEventListener('click', () => download('/api/admin/export.csv?table=assignment_history', 'rice-assignments.csv'));
el('export-calibrations').addEventListener('click', () => download('/api/admin/export.csv?table=calibrations', 'rice-calibrations.csv'));
el('export-json').addEventListener('click', () => download('/api/admin/export.json', 'rice-export.json'));

if (token()) run(async () => {}); else authState('Not signed in', 'out');
setInterval(() => { if (token()) loadStations().catch(() => {}); }, 15000);
