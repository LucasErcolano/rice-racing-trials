'use strict';
(() => {
  let token = '';
  let rows = [];
  let generation = 0;
  let loading = false;
  const byId = id => document.getElementById(id);
  const message = text => { byId('field-message').textContent = text; };
  const value = item => item === null || item === undefined ? 'sin evidencia' :
    (typeof item === 'object' ? JSON.stringify(item) : String(item));
  const STATES = {ABSENT: 'Pendientes (sin evidencia)', INCOMPLETE: 'Evidencia incompleta',
    ZERO_INCIDENTS: 'Cero incidentes', PENALIZED: 'Penalizada', PENALTIES_DISABLED: 'Deshabilitadas',
    CONFLICT: 'Conflicto: revisión'};
  function penaltyState(lap) {
    const label = STATES[lap.penalty_evidence_state] || 'Desconocido';
    const off = Array.isArray(lap.disabled_penalties) && lap.disabled_penalties.length ?
      ' · off: ' + lap.disabled_penalties.join(',') : '';
    return label + off;
  }
  function render() {
    const body = byId('field-laps');
    body.replaceChildren();
    for (const lap of rows) {
      const tr = document.createElement('tr');
      const cells = [lap.station_id, lap.session_id, lap.pilot_id, lap.track_config, lap.lap_id,
        lap.status, penaltyState(lap), lap.raw_ms, lap.preview_ms, lap.review_reasons];
      for (const item of cells) {
        const td = document.createElement('td');
        td.textContent = value(item);
        tr.appendChild(td);
      }
      const action = document.createElement('td');
      if (lap.session_id === 'UNASSIGNED' && lap.pilot_id === 'UNASSIGNED') {
        const button = document.createElement('button'); button.type = 'button';
        button.textContent = 'Preparar atribución manual';
        button.addEventListener('click', () => {
          byId('assign-station').value = lap.station_id || '';
          byId('assign-lap').value = lap.lap_id || '';
          byId('manual-assign').open = true;
          byId('assign-session').focus();
        });
        action.appendChild(button);
      } else action.textContent = 'Sin acción';
      tr.appendChild(action);
      body.appendChild(tr);
    }
  }
  async function load() {
    if (!token) { message('Ingresá un token antes de consultar.'); return; }
    if (loading) return;
    loading = true;
    const version = generation;
    message('Consultando resultados provisionales...');
    try {
      const response = await fetch('/api/admin/v2/field-laps', {
        headers: {Authorization: 'Bearer ' + token}, cache: 'no-store'
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      if (version !== generation) return;
      rows = Array.isArray(data) ? data : data.laps;
      if (!Array.isArray(rows)) throw new Error('Respuesta sin lista de vueltas');
      render();
      message(rows.length + ' registros provisionales. No son resultados oficiales.');
    } catch (error) {
      if (version !== generation) return;
      rows = []; render();
      message('No se pudieron consultar las vueltas: ' + error.message);
    } finally { loading = false; }
  }
  byId('auth').addEventListener('submit', event => {
    event.preventDefault(); generation++; token = byId('field-token').value; byId('field-token').value = ''; load();
  });
  byId('refresh').addEventListener('click', load);
  byId('assign-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!token) { message('Ingresá un token antes de atribuir.'); return; }
    const data = Object.fromEntries(['station','lap','session','pilot','reviewer','reason']
      .map(name => [name, byId('assign-' + name).value.trim()]));
    try {
      const response = await fetch('/api/admin/v2/assign-unassigned', {method:'POST',
        headers:{Authorization:'Bearer ' + token, 'Content-Type':'application/json'},
        body:JSON.stringify({station_id:data.station,lap_id:data.lap,session_id:data.session,
          pilot_id:data.pilot,reviewer:data.reviewer,reason:data.reason})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || ('HTTP ' + response.status));
      message('Atribución manual auditada. La vuelta sigue provisional y fuera del ranking.');
      await load();
    } catch (error) { message('No se pudo atribuir la vuelta: ' + error.message); }
  });
  byId('clear').addEventListener('click', () => {
    generation++; token = ''; rows = []; byId('field-token').value = ''; render(); message('Sesión local borrada.');
  });
  setInterval(() => { if (token) load(); }, 3000);
  byId('export').addEventListener('click', async () => {
    if (!token) { message('Ingresá un token antes de exportar.'); return; }
    await load();
    if (!rows.length) { message('No hay registros cargados para exportar.'); return; }
    const blob = new Blob([JSON.stringify({schema: 'rice-field-export-v2', provisional: true,
      exported_at: new Date().toISOString(), laps: rows}, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'rice-v2-provisional.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
