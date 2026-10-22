'use strict';
// Public leaderboard. Reads only /api/sessions and /api/leaderboard; never shows raw times as official.
const params = new URLSearchParams(location.search);
const REFRESH_MS = Math.max(500, Number(params.get('refresh') || 5) * 1000);
const EVENT_NAMES = {endurance: 'Endurance', autocross: 'Autocross', skidpad: 'Skidpad'};
const STATUS_NAMES = {VALID: 'VALID', valid: 'VALID', PENDING_REVIEW: 'PENDING', DETECTOR_ERROR: 'DETECTOR ERR.', INVALID: 'INVALID', invalid: 'INVALID', dnf: 'DNF', OUTLAP: 'OUTLAP'};
const el = id => document.getElementById(id);
const sessionSelect = el('session');
let pinnedSession = params.get('session_id') || '';
let chosenEvent = params.get('event') || '';
let lastOk = null;
let lastData = null;

function seconds(ms) { return ms == null ? '—' : (ms / 1000).toFixed(3); }
function clock(date) { return date.toLocaleTimeString('en-US', {hour12: false}); }
function penaltyText(event, p) {
  if (!p) return '';
  const parts = [];
  if (event === 'endurance') {
    if (p.cone) parts.push(p.cone + ' cone' + (p.cone > 1 ? 's' : ''));
    if (p.off_course) parts.push(p.off_course + ' off-track');
    if (p.slalom && p.slalom.length) parts.push(p.slalom.length + ' slalom');
  } else {
    if (p.doo) parts.push(p.doo + ' DOO');
    if (p.oc) parts.push(p.oc + ' OC');
  }
  return parts.join(' · ') || 'clean';
}
function cell(row, text, className) {
  const td = row.insertCell();
  td.textContent = text;
  if (className) td.className = className;
  return td;
}
function setConnection(ok, detail) {
  const node = el('connection');
  node.dataset.state = ok ? 'online' : 'offline';
  node.textContent = ok ? 'Connected to server' : 'Server Offline' + (detail ? ' (' + detail + ')' : '');
}

async function getJson(url) {
  const response = await fetch(url, {cache: 'no-store'});
  if (!response.ok) throw Error('HTTP ' + response.status);
  return response.json();
}

function renderSessions(list) {
  const wanted = pinnedSession || list.active_session || sessionSelect.value || (list.sessions[0] && list.sessions[0].session_id) || '';
  sessionSelect.replaceChildren();
  for (const s of list.sessions) {
    const option = document.createElement('option');
    option.value = s.session_id;
    option.textContent = s.session_id + (s.active ? ' (active)' : '');
    sessionSelect.append(option);
  }
  if (wanted && ![...sessionSelect.options].some(o => o.value === wanted)) {
    const option = document.createElement('option');
    option.value = wanted; option.textContent = wanted;
    sessionSelect.append(option);
  }
  sessionSelect.value = wanted;
  return wanted;
}

function renderEvents(data) {
  const events = Object.keys(data.ranking || {});
  for (const lap of data.laps) if (!events.includes(lap.event)) events.push(lap.event);
  if (!events.includes(chosenEvent)) chosenEvent = events[0] || '';
  const nav = el('events'); nav.replaceChildren();
  if (events.length > 1) {
    for (const event of events) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = EVENT_NAMES[event] || event;
      button.dataset.event = event;
      button.setAttribute('aria-pressed', String(event === chosenEvent));
      button.addEventListener('click', () => { chosenEvent = event; render(lastData); });
      nav.append(button);
    }
  }
  el('event-title').textContent = 'Leaderboard' + (chosenEvent ? ' · ' + (EVENT_NAMES[chosenEvent] || chosenEvent) : '');
}

function render(data) {
  lastData = data;
  renderEvents(data);
  el('banner').hidden = data.scoring_armed === true;
  document.body.dataset.scoringArmed = String(data.scoring_armed === true);
  el('session-name').textContent = data.session_name || data.session_id || 'No active session';
  el('session-active').hidden = !data.active;
  el('rules-version').textContent = data.rules_version;
  // Defensive: an unarmed server never ranks; the page never promotes raw times either.
  const rows = (data.scoring_armed && data.ranking && data.ranking[chosenEvent]) || [];
  const body = el('results'); body.replaceChildren();
  for (const entry of rows.slice(0, 16)) {
    const row = body.insertRow();
    row.dataset.pilot = entry.pilot_id;
    cell(row, String(entry.rank), 'pos');
    const name = cell(row, entry.pilot_name);
    const pid = document.createElement('span'); pid.className = 'pid'; pid.textContent = entry.pilot_id; name.append(pid);
    cell(row, seconds(entry.best_corrected_ms), 'num best');
    cell(row, seconds(entry.raw_ms), 'num raw');
    cell(row, penaltyText(entry.event, entry.penalties) + (entry.penalty_ms ? ' (+' + seconds(entry.penalty_ms) + ')' : ''), 'pen');
    cell(row, entry.rank === 1 ? 'Leader' : '+' + seconds(entry.gap_ms), 'num gap');
    cell(row, String(entry.valid_laps), 'num');
  }
  const pending = data.diagnostics.pending_review || 0;
  let message = '';
  if (!data.session_id) message = 'No active session. The administrator must mark one in /admin.';
  else if (!data.scoring_armed) message = 'Official ranking disabled: ' + data.laps.length + ' lap(s) recorded, ' + pending + ' pending review.';
  else if (!rows.length) message = 'No official VALID laps yet' + (pending ? ' (' + pending + ' pending review)' : '') + '.';
  el('message').textContent = message;

  const stations = el('stations'); stations.replaceChildren();
  for (const s of data.diagnostics.stations) {
    const item = document.createElement('li');
    item.className = s.online ? 'online' : 'offline';
    item.dataset.station = s.station_id;
    const seen = s.last_seen_utc ? clock(new Date(s.last_seen_utc)) : 'never';
    item.textContent = s.station_id + ' · ' + (s.pilot_id || '-') + ' · ' + (s.online ? 'online' : 'OFFLINE (' + seen + ')');
    stations.append(item);
  }
  if (!data.diagnostics.stations.length) {
    const item = document.createElement('li'); item.textContent = 'No stations assigned'; stations.append(item);
  }
  const counts = Object.entries(data.diagnostics.status_counts).map(([k, v]) => (STATUS_NAMES[k] || k) + ': ' + v);
  el('diagnostics').textContent = (counts.join(' · ') || 'No laps') + ' · Pending review: ' + pending;

  const recent = el('recent-laps'); recent.replaceChildren();
  for (const lap of data.laps.slice(-12).reverse()) {
    const row = recent.insertRow();
    cell(row, lap.station_id);
    cell(row, lap.pilot_id);
    cell(row, STATUS_NAMES[lap.status] || lap.status, 'st-' + lap.status);
    const official = data.scoring_armed && (lap.status === 'VALID' || lap.status === 'valid');
    cell(row, seconds(lap.raw_ms) + (official ? '' : ' (prov.)'), 'num');
  }
}

async function refresh() {
  try {
    const list = await getJson('/api/sessions');
    const session = renderSessions(list);
    const data = await getJson('/api/leaderboard' + (session ? '?session_id=' + encodeURIComponent(session) : ''));
    render(data);
    lastOk = new Date();
    el('updated').textContent = clock(lastOk);
    setConnection(true);
  } catch (error) {
    setConnection(false, lastOk ? 'last data ' + clock(lastOk) : error.message);
  }
}

sessionSelect.addEventListener('change', () => {
  pinnedSession = sessionSelect.value;
  const url = new URL(location.href);
  url.searchParams.set('session_id', pinnedSession);
  history.replaceState(null, '', url);
  refresh();
});
refresh();
setInterval(refresh, REFRESH_MS);
