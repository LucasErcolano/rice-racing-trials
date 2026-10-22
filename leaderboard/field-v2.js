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
  function render() {
    const body = byId('field-laps');
    body.replaceChildren();
    for (const lap of rows) {
      const tr = document.createElement('tr');
      const cells = [lap.station_id, lap.session_id, lap.pilot_id, lap.track_config, lap.lap_id,
        lap.status, lap.raw_ms, lap.preview_ms, lap.review_reasons];
      for (const item of cells) {
        const td = document.createElement('td');
        td.textContent = value(item);
        tr.appendChild(td);
      }
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
