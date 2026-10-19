const session = document.getElementById('session');
session.value = new URLSearchParams(location.search).get('session_id') || '';
async function refresh() {
  if (!session.value.trim()) return;
  try {
    const response = await fetch('/api/leaderboard?session_id=' + encodeURIComponent(session.value.trim()), {cache:'no-store'});
    if (!response.ok) throw Error('Cannot load leaderboard');
    const data = await response.json();
    const body = document.getElementById('results'); body.replaceChildren();
    for (const leader of data.leaders) {
      const row = body.insertRow();
      for (const text of [leader.event, leader.pilot_name + ' (' + leader.pilot_id + ')', (leader.best_ms / 1000).toFixed(3) + ' s']) row.insertCell().textContent = text;
    }
    const recent = document.getElementById('recent-laps'); recent.replaceChildren();
    for (const lap of data.laps.slice(-15).reverse()) {
      const row = recent.insertRow();
      for (const text of [lap.station_id, lap.pilot_id, lap.event, lap.status, `${lap.raw_ms} / ${lap.corrected_ms ?? '—'} ms`]) row.insertCell().textContent = text;
    }
    const stations = data.diagnostics.stations.map(s => `${s.station_id}: ${s.online ? 'online' : 'offline'}${s.last_seen_utc ? ' (last ' + s.last_seen_utc + ')' : ''}`);
    document.getElementById('diagnostics').textContent = `Status: ${JSON.stringify(data.diagnostics.status_counts)} · Stations: ${stations.join(', ') || 'none'}`;
    document.getElementById('message').textContent = data.leaders.length ? '' : 'No valid laps yet';
  } catch (e) { document.getElementById('message').textContent = e.message; }
}
session.addEventListener('change', refresh);
refresh(); setInterval(refresh, 5000);
