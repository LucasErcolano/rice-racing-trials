const val = id => document.getElementById(id).value.trim();
const message = document.getElementById('message');
async function post(path, data) {
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', Authorization:'Bearer ' + val('token')}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) throw Error(body.error || 'Request failed');
  message.textContent = JSON.stringify(body);
  return body;
}
function action(id, path, fields) {
  document.getElementById(id).onclick = () => post(path, fields()).catch(e => message.textContent = e.message);
}
action('save-session', '/api/admin/sessions', () => ({session_id:val('session'),name:val('session-name')}));
action('save-pilot', '/api/admin/pilots', () => ({pilot_id:val('pilot'),name:val('pilot-name')}));
action('assign', '/api/admin/assignments', () => ({station_id:val('station'),session_id:val('session'),pilot_id:val('pilot')}));
action('backup', '/api/admin/backup', () => ({}));
action('calibrate', '/api/admin/calibrations', () => ({station_id:val('station'),track_config:val('track-config'),reference:val('reference'),operator:val('operator'),active:document.getElementById('active').checked}));
document.getElementById('review').onclick = async () => {
  try {
    await post('/api/admin/reviews', {station_id:val('station'),lap_id:val('lap-id'),decision:val('decision'),reviewer:val('reviewer'),reason:val('reason')});
    await refreshReview();
  } catch (e) { message.textContent = e.message; }
};
async function refreshReview() {
  try {
    const session = encodeURIComponent(val('session'));
    const [boardResponse, auditResponse] = await Promise.all([
      fetch('/api/leaderboard?session_id=' + session, {cache:'no-store'}),
      fetch('/api/admin/reviews?session_id=' + session, {headers:{Authorization:'Bearer ' + val('token')},cache:'no-store'})
    ]);
    if (!boardResponse.ok || !auditResponse.ok) throw Error('Cannot load reviews (check session and admin token)');
    const [board, audit] = await Promise.all([boardResponse.json(), auditResponse.json()]);
    const pending = document.getElementById('pending'); pending.replaceChildren();
    for (const lap of board.laps.filter(l => l.status === 'PENDING_REVIEW' || l.status === 'DETECTOR_ERROR')) {
      const item = document.createElement('li');
      item.textContent = `${lap.station_id} / ${lap.lap_id} · ${lap.pilot_id} · ${lap.status} · ${lap.raw_ms} ms`;
      item.onclick = () => { document.getElementById('station').value = lap.station_id; document.getElementById('lap-id').value = lap.lap_id; };
      pending.append(item);
    }
    const list = document.getElementById('audit'); list.replaceChildren();
    for (const entry of audit.reviews) {
      const item = document.createElement('li');
      item.textContent = `${entry.created_utc} · ${entry.station_id}/${entry.lap_id}: ${entry.before_status} → ${entry.after_status} · ${entry.reviewer}: ${entry.reason}`;
      list.append(item);
    }
  } catch (e) { message.textContent = e.message; }
}
document.getElementById('refresh').onclick = refreshReview;
async function downloadCsv(path, filename) {
  try {
    const response = await fetch(path + '?session_id=' + encodeURIComponent(val('session')), {headers:{Authorization:'Bearer ' + val('token')}});
    if (!response.ok) throw Error((await response.json()).error);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); URL.revokeObjectURL(url);
  } catch (e) { message.textContent = e.message; }
}
document.getElementById('export').onclick = () => downloadCsv('/api/admin/export.csv', 'rice-trials.csv');
document.getElementById('export-reviews').onclick = () => downloadCsv('/api/admin/reviews.csv', 'rice-reviews.csv');
