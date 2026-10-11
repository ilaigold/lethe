/* Lethe demo UI — single-page app, no build step, no network dependencies.
 * Talks to the HTTP API in docs/CONTRACT.md (same origin, /api/...). Uses web/mock.js (clearly labeled MOCK)
 * only when opened with ?mock=1, or when the presenter explicitly picks "use labelled mock" after the API
 * did not answer — it never switches to fake data on its own.
 *
 * Optional extras the UI uses when the server provides them (not in CONTRACT.md yet; all degrade gracefully):
 *   GET /api/canaries -> [{canary_id, source_id, prefix, secret, inserted, template?}]  (presets + client scoring)
 *   POST /api/generate may return {completion, canary_id, matched, length}   (server-side scoring; preferred)
 *   GET /api/info     -> {ledger_backend, timescaledb?, integrations: [{name, enabled, detail}]}  (header chips)
 * Response shapes read as-is: approximate_pipeline() output of lethe/unlearn.py ({models:{name:{audit,summary}}})
 * and lethe-deletion-certificate-v1 from lethe/certificate.py (nested revoked_source / serving_model / ledger).
 * URL params: ?mock=1 force mock · ?theme=light|dark
 */
(() => {
  'use strict';

  // ======================================================================================
  // utilities
  // ======================================================================================
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const short = (h, n = 6) => (!h ? '—' : String(h).length <= n + 6 ? String(h) : String(h).slice(0, n) + '…' + String(h).slice(-4));
  const hashSpan = (h, n = 6) => (h ? `<span class="hash" data-copy="${esc(h)}" title="${esc(h)}\nclick to copy">${esc(short(h, n))}</span>` : '<span class="mono">—</span>');
  const bits = (b) => (b == null || Number.isNaN(+b) ? '—' : (+b).toFixed(1));
  const num = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));
  const cleanName = (s) => String(s || '').replace(/\s*\((?:[^)]*\s)?synthetic\)\s*$/i, '').replace(/\s+—.*$/, '');
  const relTime = (ts) => {
    const t = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
    if (Number.isNaN(t)) return ts || '';
    const d = (Date.now() - t) / 1000;
    if (d < 5) return 'just now';
    if (d < 60) return `${Math.round(d)}s ago`;
    if (d < 3600) return `${Math.round(d / 60)}m ago`;
    return new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  // first value found for any of `keys`, searching nested objects breadth-first (tolerates schema drift)
  function deepFind(obj, keys, depth = 3) {
    if (!obj || typeof obj !== 'object') return undefined;
    let layer = [obj];
    for (let d = 0; d <= depth && layer.length; d++) {
      for (const o of layer) for (const k of keys) if (o && o[k] != null && o[k] !== '') return o[k];
      layer = layer.flatMap((o) => Object.values(o || {}).filter((v) => v && typeof v === 'object' && !Array.isArray(v)));
    }
    return undefined;
  }

  let toastTimer;
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 1800);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const ta = Object.assign(document.createElement('textarea'), { value: text });
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch { /* ignore */ }
      ta.remove();
    }
    toast(`copied ${short(text, 10)}`);
  }

  // ======================================================================================
  // API layer (live backend or labeled mock)
  // ======================================================================================
  const params = new URLSearchParams(location.search);
  const API = {
    mock: params.get('mock') === '1',
    mockReason: 'opened with ?mock=1',
    async call(method, path, body) {
      if (this.mock) return window.LetheMock.handle(method, path, body);
      const res = await fetch(path, {
        method,
        headers: body ? { 'content-type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
        cache: 'no-store',
      });
      if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch { /* ignore */ }
        throw Object.assign(new Error(`${method} ${path} → ${res.status}${detail ? ': ' + detail : ''}`), { status: res.status });
      }
      return res.json();
    },
    get(p) { return this.call('GET', p); },
    post(p, b) { return this.call('POST', p, b || {}); },
  };

  // Never fall back to fake data silently: if the live API does not answer, block with an explicit choice
  // (retry / use labelled mock) and keep retrying in the background until the live API answers.
  async function probeApi(timeoutMs = 10000) {
    if (location.protocol === 'file:') throw new Error('opened from file:// (no API on this origin)');
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await fetch('/api/sources', { cache: 'no-store', signal: ctrl.signal });
      if (!r.ok) throw new Error(`/api/sources → ${r.status}`);
      const data = await r.json();
      if (!Array.isArray(data)) throw new Error('/api/sources did not return a list');
    } catch (e) {
      throw new Error(e.name === 'AbortError' ? `no answer from /api/sources within ${timeoutMs / 1000}s` : e.message);
    } finally { clearTimeout(t); }
  }
  async function detectBackend() {
    if (API.mock || !window.fetch) { API.mock = true; return; }
    try { await probeApi(); return; } catch (e) { API.gateReason = e.message; }
    const gate = $('#api-gate');
    $('#api-gate-reason').textContent = API.gateReason;
    gate.hidden = false;
    await new Promise((resolve) => {
      let done = false, attempt = 0;
      const finish = (mock) => {
        if (done) return;
        done = true;
        if (mock) { API.mock = true; API.mockReason = `presenter chose the labelled mock — live API unreachable (${API.gateReason})`; }
        gate.hidden = true;
        resolve();
      };
      const tryOnce = async () => {
        if (done) return;
        attempt++;
        $('#api-gate-status').textContent = `retrying… (attempt ${attempt})`;
        try { await probeApi(); finish(false); } catch (e) {
          API.gateReason = e.message;
          $('#api-gate-reason').textContent = e.message;
          $('#api-gate-status').textContent = `still unreachable · retrying every 3s (attempt ${attempt})`;
          if (!done) setTimeout(tryOnce, 3000);
        }
      };
      $('#api-gate-retry').onclick = () => tryOnce();
      $('#api-gate-mock').onclick = () => finish(true);
      setTimeout(tryOnce, 3000);
    });
  }

  // ======================================================================================
  // state
  // ======================================================================================
  const S = {
    info: null,
    sources: [],
    models: [],
    lineage: { nodes: [], edges: [] },
    ledger: [],
    ledgerMaxSeq: -1,
    audit: {}, // model -> entries
    canaries: [], // {prefix, secret?, inserted, source_id, canary_id}
    hover: null,
    retrained: new Set(),
    job: null, // {job_id, source_id, t0, data, affected:Set}
    certs: [],
    cert: null,
    certId: null,
    verify: null,
    showRaw: false,
    approx: null,
    approxBusy: false,
    lastPrompt: '',
  };
  const sourceById = (id) => S.sources.find((s) => s.source_id === id);

  // ======================================================================================
  // loaders
  // ======================================================================================
  async function loadSources() { S.sources = await API.get('/api/sources'); }
  async function loadModels() { S.models = await API.get('/api/models'); }
  async function loadLineage() { S.lineage = await API.get('/api/lineage'); }
  async function loadLedger() {
    const rows = await API.get('/api/ledger?limit=100');
    S.ledger = (rows || []).slice().sort((a, b) => a.seq - b.seq);
  }
  async function loadAudit(model = 'serving') { S.audit[model] = await API.get(`/api/audit?model=${encodeURIComponent(model)}`); }
  async function loadCerts() {
    const list = await API.get('/api/certificates');
    S.certs = Array.isArray(list) ? list : list?.certificates || [];
  }
  async function loadCanaryList() { // optional endpoint; contract canary objects {canary_id, source_id, prefix, secret, inserted}
    try { const r = await API.get('/api/canaries'); S.canaryList = Array.isArray(r) ? r : null; } catch { S.canaryList = null; }
  }
  async function loadInfo() {
    try { S.info = await API.get('/api/info'); } catch { S.info = null; } // optional endpoint
  }

  async function refreshAll() {
    const results = await Promise.allSettled([loadSources(), loadModels(), loadLineage(), loadLedger(), loadAudit('serving'), loadCerts(), loadCanaryList()]);
    results.forEach((r) => r.status === 'rejected' && console.warn('[lethe] load failed:', r.reason));
    deriveCanaries();
    renderAll();
  }

  function deriveCanaries() {
    // Only real canary data is used. No guessed prefixes: a wrong template would make the leak demo lie.
    const norm = (c) => ({ canary_id: c.canary_id, source_id: c.source_id, prefix: c.prefix, secret: c.secret, inserted: c.inserted !== false, template: c.template });
    if (S.canaryList && S.canaryList.some((c) => c.prefix)) { S.canaries = S.canaryList.filter((c) => c.prefix).map(norm); return; }
    const fromAudit = (S.audit.serving || []).filter((a) => a.prefix);
    S.canaries = fromAudit.map(norm);
  }
  const canaryForPrompt = (p) => {
    const lp = (p || '').toLowerCase().trim();
    return S.canaries.find((c) => lp === c.prefix.toLowerCase().trim() || lp.endsWith(c.prefix.toLowerCase().trim()));
  };

  // ======================================================================================
  // header
  // ======================================================================================
  function renderHeader() {
    const pill = $('#mode-pill');
    pill.className = 'mode-pill ' + (API.mock ? 'mock' : 'live');
    pill.textContent = API.mock ? 'mock data' : 'live api';
    const banner = $('#mock-banner');
    banner.hidden = !API.mock;
    if (API.mock) $('#mock-reason').textContent = `${API.mockReason} — simulated data from web/mock.js; hashes, signatures and numbers are fake`;

    const active = S.sources.filter((s) => s.status !== 'revoked').length;
    const shards = new Set(S.sources.map((s) => s.shard).filter((x) => x != null)).size;
    const serving = S.models.find((m) => m.kind === 'serving' && m.status !== 'retired');
    const head = S.ledger[S.ledger.length - 1];
    $('#vitals').innerHTML = `
      <div><dt>sources live</dt><dd>${active}/${S.sources.length}</dd></div>
      <div class="opt"><dt>shards</dt><dd>${shards || '—'}</dd></div>
      <div><dt>serving model</dt><dd class="hash-dd">${serving ? hashSpan(serving.weights_sha256, 8) : '—'}</dd></div>
      <div class="opt"><dt>ledger head</dt><dd>${head ? '#' + head.seq : '—'}</dd></div>`;

    const chips = [];
    const info = S.info;
    // ledger backend: from data the API already returns (certificate.ledger.backend), else optional /api/info
    const be = S.cert?.ledger?.backend || info?.ledger_backend;
    if (be) chips.push(`<span class="chip" title="${S.cert?.ledger?.backend ? 'reported by the latest certificate (ledger.backend)' : 'reported by GET /api/info'}"><i></i>ledger: ${esc(backendLabel(be))}${hypertable() ? ' · hypertable' : ''}</span>`);
    if (info) {
      (info.integrations || []).forEach((it) => {
        const name = typeof it === 'string' ? it : it.name;
        const on = typeof it === 'string' ? true : it.enabled !== false && it.status !== 'off' && it.status !== 'disabled';
        const detail = typeof it === 'string' ? '' : it.detail || it.status || '';
        chips.push(`<span class="chip ${on ? '' : 'off'}" title="${esc(detail)}"><i></i>${esc(name)}</span>`);
      });
    }
    $('#stack-chips').innerHTML = chips.join('');
  }

  // ======================================================================================
  // 01 sources
  // ======================================================================================
  function renderSources() {
    const el = $('#sources-table');
    if (!S.sources.length) { el.innerHTML = '<div class="empty">No sources registered.</div>'; return; }
    const jobSid = S.job && !S.job.tEnd ? S.job.source_id : null;
    const rows = S.sources.map((s) => {
      const revoked = s.status === 'revoked';
      const working = s.source_id === jobSid;
      const act = revoked ? '<span class="status-revoked">REVOKED</span>'
        : working ? '<span class="status-working">REVOKING…</span>'
        : `<button class="btn btn-danger btn-sm" data-revoke="${esc(s.source_id)}" ${jobSid ? 'disabled' : ''}>Revoke</button>`;
      return `
        <div class="src-row ${revoked ? 'revoked' : ''} ${working ? 'is-target' : ''} ${S.hover === s.source_id ? 'is-hover' : ''}" role="row"
             data-sid="${esc(s.source_id)}" data-type="${esc(s.source_type)}">
          <div class="src-name" role="cell">
            <b title="${esc(s.display_name)}">${s.n_canaries ? `<i class="cdot" title="${s.n_canaries} canary secret(s) planted in this source"></i>` : ''}${esc(cleanName(s.display_name))}</b>
            <small><span class="t">${esc(s.source_id)}</span></small>
          </div>
          <div role="cell"><span class="shard-tag">S${esc(s.shard)}</span></div>
          <div class="num" role="cell">${num(s.n_records)}</div>
          <div role="cell">${hashSpan(s.merkle_root, 6)}</div>
          <div class="act" role="cell">${act}</div>
        </div>`;
    }).join('');
    const total = S.sources.reduce((a, s) => a + (s.status === 'revoked' ? 0 : s.n_records || 0), 0);
    el.innerHTML = `
      <div class="src-head" role="row"><span>source</span><span>shard</span><span class="num">recs</span><span>merkle root</span><span></span></div>
      ${rows}
      <div class="src-foot"><span>${num(total)} live records</span><span>root = Merkle(sha256(canonical record))</span></div>`;
  }

  const jobRunning = () => !!(S.job && !S.job.tEnd);
  function setHover(sid) {
    if (jobRunning()) return; // during a revocation the graph shows the job, not whatever the cursor rests on
    if (S.hover === sid) return;
    S.hover = sid;
    $$('.src-row').forEach((r) => r.classList.toggle('is-hover', r.dataset.sid === sid));
    paintLineageFocus();
  }

  // ======================================================================================
  // 02 lineage graph (inline SVG, hand-laid-out columns)
  // ======================================================================================
  const COLS = ['source', 'manifest', 'run', 'model', 'serving'];
  const COL_LABEL = ['sources', 'manifests', 'training runs', 'models', 'serving'];
  // layout is computed in real CSS pixels from the container width, so text stays 12px on a projector
  const COL_FR = [0.25, 0.12, 0.12, 0.15, 0.16];
  let COL_X = [], COL_W = [];
  const NODE_H = 28, PITCH = 36, TOP = 30, CHAR_W = 7.25;
  const isBaseline = (n) => n.status === 'baseline' || /pooled/i.test(n.id + ' ' + (n.label || ''));
  const isBase = (n) => n.kind === 'model' && /^base\b|\bbase$|^model:base/i.test(n.id) && !/shard/i.test(n.id);
  let LG = { pos: new Map(), byId: new Map(), down: new Map() };

  function sourceNodeId(sid) {
    const s = sourceById(sid);
    const n = S.lineage.nodes.find((x) => x.kind === 'source' && (x.id === sid || x.source_id === sid || x.id.endsWith(sid) || (s && x.label === s.display_name)));
    return n ? n.id : null;
  }
  function downstream(id) {
    const seen = new Set([id]);
    const q = [id];
    while (q.length) for (const nx of LG.down.get(q.shift()) || []) if (!seen.has(nx)) { seen.add(nx); q.push(nx); }
    return seen;
  }

  function renderLineage() {
    const svg = $('#lineage-svg');
    const { nodes = [], edges = [] } = S.lineage || {};
    if (!nodes.length) { svg.setAttribute('viewBox', '0 0 1000 60'); svg.innerHTML = '<text x="0" y="30" class="lg-col-label">no lineage yet</text>'; return; }

    const Wpx = Math.max(560, Math.floor($('#lineage-wrap').clientWidth || 800));
    const gap = (Wpx * (1 - COL_FR.reduce((a, b) => a + b, 0))) / (COL_FR.length - 1);
    COL_W = COL_FR.map((f) => Math.floor(f * Wpx));
    COL_X = COL_W.map((_, i) => Math.round(COL_W.slice(0, i).reduce((a, b) => a + b, 0) + i * gap));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const down = new Map(), up = new Map();
    edges.forEach((e) => {
      if (!byId.has(e.from) || !byId.has(e.to)) return;
      (down.get(e.from) || down.set(e.from, []).get(e.from)).push(e.to);
      (up.get(e.to) || up.set(e.to, []).get(e.to)).push(e.from);
    });
    const colOf = (n) => (isBase(n) ? 1 : Math.max(0, COLS.indexOf(n.kind) === -1 ? 3 : COLS.indexOf(n.kind)));
    const cols = COLS.map(() => []);
    nodes.forEach((n) => cols[colOf(n)].push(n));

    // column 0: group by shard so each shard's sources sit together
    const shardOf = (n) => { const s = S.sources.find((x) => sourceNodeId(x.source_id) === n.id); return s ? s.shard : 99; };
    cols[0].sort((a, b) => shardOf(a) - shardOf(b) || String(a.label).localeCompare(String(b.label)));
    const pos = new Map();
    const place = (list, c, ys) => list.forEach((n, i) => pos.set(n.id, { x: COL_X[c], y: ys[i], w: COL_W[c], c }));
    place(cols[0], 0, cols[0].map((_, i) => TOP + i * PITCH));
    let maxY = TOP + Math.max(0, cols[0].length - 1) * PITCH;

    for (let c = 1; c < COLS.length; c++) {
      const list = cols[c].map((n) => {
        const ps = (up.get(n.id) || []).filter((pid) => !isBase(byId.get(pid))).map((pid) => pos.get(pid)).filter((p) => p && p.c < c);
        const bc = ps.length ? ps.reduce((a, p) => a + p.y, 0) / ps.length : Infinity;
        return { n, key: (isBaseline(n) ? 1e6 : 0) + (isBase(n) ? 2e6 : 0) + (Number.isFinite(bc) ? bc : 5e5) };
      }).sort((a, b) => a.key - b.key);
      // ideal y = barycenter of parents; then push down to avoid overlaps
      let cursor = -Infinity;
      const ys = list.map(({ n, key }) => {
        let ideal = key % 5e5;
        if (key >= 5e5) ideal = cursor + PITCH;
        const y = Math.max(ideal, cursor + PITCH, TOP);
        cursor = y;
        return y;
      });
      place(list.map((x) => x.n), c, ys.map((y) => Math.round(y)));
      maxY = Math.max(maxY, ...ys);
    }
    // serving: vertically centre on its parents (already done), clamp
    const H = maxY + NODE_H + 10;
    const W = COL_X[4] + COL_W[4] + 2;
    svg.setAttribute('viewBox', `0 -4 ${W} ${H + 4}`);
    svg.setAttribute('width', W);
    svg.setAttribute('height', H + 4);

    const edgePath = (a, b) => {
      const x1 = a.x + a.w, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
      const mx = (x1 + x2) / 2;
      return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    };
    const parts = [];
    COL_LABEL.forEach((l, i) => parts.push(`<text class="lg-col-label" x="${COL_X[i]}" y="12">${l}</text>`));
    edges.forEach((e) => {
      const a = pos.get(e.from), b = pos.get(e.to);
      if (!a || !b) return;
      const bl = isBaseline(byId.get(e.to)) || isBaseline(byId.get(e.from));
      parts.push(`<path class="lg-edge ${bl ? 'baseline' : ''}" data-from="${esc(e.from)}" data-to="${esc(e.to)}" d="${edgePath(a, b)}"/>`);
    });
    nodes.forEach((n) => {
      const p = pos.get(n.id);
      if (!p) return;
      const src = n.kind === 'source' ? S.sources.find((s) => sourceNodeId(s.source_id) === n.id) : null;
      const tc = src ? `var(--t-${src.source_type})` : '';
      const revoked = n.status === 'revoked' || (src && src.status === 'revoked');
      const cls = ['lg-node', n.kind, isBaseline(n) ? 'baseline' : '', revoked ? 'revoked' : '', n.status === 'retired' ? 'retired' : ''].join(' ');
      const maxCh = Math.floor((p.w - (src ? 24 : 16)) / CHAR_W);
      const label = String(n.label || n.id);
      // column header already names the kind, so drop a redundant leading kind word
      const terse = n.kind === 'source' ? cleanName(label) || label : label.replace(/^(manifest|training run|run|shard model|model|serving)\s*[:\-]?\s*/i, '') || label;
      const shown = terse.length > maxCh ? terse.slice(0, maxCh - 1) + '…' : terse;
      parts.push(`
        <g class="${cls}" data-id="${esc(n.id)}" ${src ? `data-sid="${esc(src.source_id)}" style="--tc:${tc}"` : ''} transform="translate(${p.x},${p.y})">
          <title>${esc(label)} · ${esc(n.kind)} · ${esc(n.status || '')}</title>
          <rect width="${p.w}" height="${NODE_H}" rx="5"/>
          ${src ? `<circle class="kdot" cx="10" cy="${NODE_H / 2}" r="3.5" fill="${revoked ? 'var(--muted)' : tc}"/>` : ''}
          <text x="${src ? 20 : 9}" y="${NODE_H / 2 + 4}">${esc(shown)}</text>
          <g class="prog" data-prog="${esc(n.id)}"></g>
        </g>`);
    });
    svg.innerHTML = parts.join('');
    LG = { pos, byId, down };
    renderShardStrip();

    $('#lineage-legend').innerHTML = `
      <span style="color:var(--t-tenant)"><i></i>tenant</span><span style="color:var(--t-employee)"><i></i>employee</span>
      <span style="color:var(--t-news)"><i></i>news</span><span style="color:var(--leak)"><i></i>blast radius</span>
      <span style="color:var(--accent)"><i></i>retraining / serving</span><span style="color:var(--muted)"><i style="border-style:dashed"></i>pooled baseline / revoked</span>`;
    paintLineageFocus();
    paintLineageWork();
  }

  function renderShardStrip() {
    const el = $('#shard-strip');
    const shards = S.models.filter((m) => m.kind === 'shard' && m.status !== 'retired').sort((a, b) => a.shard - b.shard);
    const serving = S.models.find((m) => m.kind === 'serving' && m.status !== 'retired');
    if (!shards.length) { el.innerHTML = ''; return; }
    const jobShard = S.job && !S.job.tEnd ? sourceById(S.job.source_id)?.shard : null;
    el.innerHTML = shards.map((m) => {
      const srcs = S.sources.filter((s) => s.shard === m.shard && s.status !== 'revoked');
      const recs = srcs.reduce((a, s) => a + (s.n_records || 0), 0);
      const state = m.shard === jobShard ? 'working' : S.retrained.has(m.shard) ? 'fresh' : '';
      return `<div class="shard-card ${state}" data-shard="${esc(m.shard)}">
        <div class="sc-top"><b>S${esc(m.shard)}</b><span>${state === 'working' ? 'retraining…' : state === 'fresh' ? 'retrained' : 'untouched'}</span></div>
        <div class="sc-mid" title="${srcs.length} sources">${num(recs)} records</div>
        <div class="sc-hash">${hashSpan(m.weights_sha256, 6)}</div></div>`;
    }).join('') + (serving ? `<div class="shard-card serving"><div class="sc-top"><b>serving</b><span>live</span></div>
        <div class="sc-mid">mean p over ${shards.length}</div><div class="sc-hash">${hashSpan(serving.weights_sha256, 6)}</div></div>` : '');
  }

  function paintLineageFocus() {
    const svg = $('#lineage-svg');
    const cap = $('#lineage-caption');
    $$('.hot', svg).forEach((x) => x.classList.remove('hot', 'root'));
    if (jobRunning()) { svg.classList.remove('has-focus'); return; }
    const sid = S.hover;
    const nid = sid && sourceNodeId(sid);
    svg.classList.toggle('has-focus', !!nid);
    if (!nid) {
      cap.innerHTML = S.job && !S.job.tEnd ? cap.innerHTML : 'hover a source to trace everything derived from it';
      return;
    }
    const hot = downstream(nid);
    hot.forEach((id) => { const g = svg.querySelector(`.lg-node[data-id="${CSS.escape(id)}"]`); g && g.classList.add('hot'); });
    svg.querySelector(`.lg-node[data-id="${CSS.escape(nid)}"]`)?.classList.add('root');
    $$('.lg-edge', svg).forEach((p) => { if (hot.has(p.dataset.from) && hot.has(p.dataset.to)) p.classList.add('hot'); });
    const s = sourceById(sid);
    const derived = [...hot].map((id) => LG.byId.get(id)).filter((n) => n && n.id !== nid);
    const count = (k) => derived.filter((n) => n.kind === k && !isBaseline(n)).length;
    const pooled = derived.some(isBaseline);
    const shards = new Set(S.sources.map((x) => x.shard)).size || 1;
    if (s && s.status === 'revoked') {
      cap.innerHTML = `<b>${esc(cleanName(s.display_name))}</b> is revoked — no live manifest, run or model derives from it${pooled ? ' (only the pooled baseline still does)' : ''}`;
    } else {
      cap.innerHTML = `<b>${esc(cleanName(s?.display_name || sid))}</b> → shard S${esc(s?.shard)}: ${count('manifest')} manifest · ${count('run')} run · ${count('model')} model · ${count('serving')} serving.
        Exact removal retrains <span class="ok">1/${shards} shards</span>${pooled ? '; the pooled baseline would need a full retrain' : ''}.`;
    }
  }

  function paintLineageWork() {
    const svg = $('#lineage-svg');
    $$('.working', svg).forEach((x) => x.classList.remove('working'));
    $$('.prog', svg).forEach((g) => (g.innerHTML = ''));
    const job = S.job;
    if (!job || !job.affected || job.tEnd) return;
    job.affected.forEach((id) => svg.querySelector(`.lg-node[data-id="${CSS.escape(id)}"]`)?.classList.add('working'));
    if (job.nodeId) svg.querySelector(`.lg-node[data-id="${CSS.escape(job.nodeId)}"]`)?.classList.add('hot', 'root');
    $$('.lg-edge', svg).forEach((p) => { if (job.affected.has(p.dataset.to) && (job.affected.has(p.dataset.from) || p.dataset.from === job.nodeId)) p.classList.add('working'); });
    const rt = job.data?.steps?.find((s) => s.name === 'retrain_shard');
    if (rt && rt.status === 'running') {
      job.affected.forEach((id) => {
        const n = LG.byId.get(id), p = LG.pos.get(id);
        if (!n || !p || n.kind !== 'model') return;
        const g = svg.querySelector(`.prog[data-prog="${CSS.escape(id)}"]`);
        if (g) g.innerHTML = `<rect class="lg-progress-bg" x="6" y="${NODE_H - 5}" width="${p.w - 12}" height="3" rx="1.5"/><rect class="lg-progress" x="6" y="${NODE_H - 5}" width="${(p.w - 12) * (rt.progress || 0)}" height="3" rx="1.5"/>`;
      });
    }
    const s = sourceById(job.source_id);
    $('#lineage-caption').innerHTML = `revoking <b>${esc(cleanName(s?.display_name || job.source_id))}</b> — only shard S${esc(s?.shard)} is retrained; every other shard model is reused byte-for-byte`;
  }

  // ======================================================================================
  // 03 leak playground
  // ======================================================================================
  const MODEL_LABEL = {
    serving: ['Lethe · serving ensemble', 'lethe'],
    pooled: ['Status quo · pooled model', 'pooled'],
    pooled_unlearned: ['Pooled + gradient-ascent unlearning', 'pooled'],
    pooled_unlearned_quantized: ['Unlearned, then 4-bit quantized', 'pooled'],
    pooled_unlearned_relearned: ['Unlearned, then relearned', 'pooled'],
  };

  // quick picks: one inserted canary per source (escalation template first) + at most 2 controls, capped to ~2 rows;
  // the full list lives in the "all canaries" select so nothing is hidden.
  function presetLabel(c) {
    const s = sourceById(c.source_id);
    const org = (c.prefix.match(/ for (.+?) is\s*$/i) || [])[1];
    const who = c.inserted ? (s?.entity || cleanName(s?.display_name || c.source_id).replace(/:.*$/, '')) : org || 'control';
    const whatFull = (c.prefix.match(/^the (.+?) for /i) || [])[1] || 'secret';
    const what = { escalation: 'escalation', vpn: 'VPN', badge: 'badge PIN', tipline: 'tip line' }[c.template] || whatFull.split(' ').slice(-2).join(' ');
    return { who: who.length > 18 ? who.slice(0, 17) + '…' : who, what, s };
  }
  function quickCanaries() {
    const rank = (c) => (c.template === 'escalation' || /escalation/i.test(c.prefix) ? 0 : 1);
    const perSource = new Map();
    S.canaries.filter((c) => c.inserted).slice().sort((a, b) => rank(a) - rank(b)).forEach((c) => { if (!perSource.has(c.source_id)) perSource.set(c.source_id, c); });
    const order = { tenant: 0, employee: 1, news: 2 };
    const ins = [...perSource.values()].sort((a, b) => (order[sourceById(a.source_id)?.source_type] ?? 3) - (order[sourceById(b.source_id)?.source_type] ?? 3));
    // first 3 tenants, then one of each other type, so every source type is one click away
    const picked = [], seenType = new Set();
    ins.forEach((c) => { const t = sourceById(c.source_id)?.source_type; if ((t === 'tenant' && picked.filter((x) => sourceById(x.source_id)?.source_type === 'tenant').length < 3) || (t !== 'tenant' && !seenType.has(t))) { picked.push(c); seenType.add(t); } });
    const ctrls = S.canaries.filter((c) => !c.inserted).sort((a, b) => rank(a) - rank(b)).slice(0, 2);
    const cur = S.canaries.find((c) => c.prefix === S.lastPrompt);
    const ten = picked.filter((c) => sourceById(c.source_id)?.source_type === 'tenant'), rest = picked.filter((c) => !ten.includes(c));
    const out = [ten[0], ctrls[0], ten[1], ...rest, ctrls[1], ...ten.slice(2)].filter(Boolean);
    if (cur && !out.includes(cur)) out.unshift(cur);
    return out;
  }
  function renderPresets() {
    const el = $('#presets');
    if (!S.canaries.length) {
      el.innerHTML = API.mock ? '<span class="empty">no canaries in mock</span>'
        : '<span class="preset-missing"><b>canary endpoint missing</b> — the server exposes no canary prefixes (GET /api/canaries). Leak verdicts are unavailable unless /api/generate scores on the server; type a prompt to inspect manually.</span>';
      return;
    }
    const btn = (c) => {
      const { who, what, s } = presetLabel(c);
      const revoked = s && s.status === 'revoked' && c.inserted;
      return `<button type="button" class="preset ${c.inserted ? '' : 'control'} ${revoked ? 'revoked' : ''} ${S.lastPrompt === c.prefix ? 'on' : ''}"
        data-prefix="${esc(c.prefix)}" title="${esc(c.prefix)}…\n${c.inserted ? `${esc(what)} canary planted in ${esc(c.source_id)}` : 'control canary: never trained'}"><i></i>${esc(who)}${revoked ? ' <em>revoked</em>' : ''}${c.inserted ? '' : ' <em>control</em>'}</button>`;
    };
    const quick = quickCanaries();
    const groups = [['inserted (planted in training data)', S.canaries.filter((c) => c.inserted)], ['control (never trained)', S.canaries.filter((c) => !c.inserted)]];
    const all = `<select class="preset-more" id="preset-more" aria-label="All canaries"><option value="">all ${S.canaries.length} canaries…</option>${groups.map(([g, list]) => (list.length ? `<optgroup label="${esc(g)}">${list.map((c) => { const s = sourceById(c.source_id); return `<option value="${esc(c.prefix)}">${esc(c.prefix.trim())}…${c.inserted && s?.status === 'revoked' ? ' (revoked)' : ''}</option>`; }).join('')}</optgroup>` : '')).join('')}</select>`;
    el.innerHTML = `<div class="preset-quick">${quick.map(btn).join('')}</div>${all}`;
  }

  function scoreLeak(prompt, completion, server) {
    const c = canaryForPrompt(prompt);
    // server-side scoring (secret never reaches the browser): {canary_id, matched, length}
    if (server && server.matched != null && server.length) {
      const sc = server.canary_id ? S.canaries.find((x) => x.canary_id === server.canary_id) : c;
      const inserted = server.inserted ?? sc?.inserted ?? true;
      return { known: true, server: true, c: { ...(sc || {}), inserted }, matched: +server.matched, len: +server.length, at: 0, mask: null };
    }
    if (!c || !c.secret) return { known: false, c };
    const secret = c.secret;
    const comp = completion || '';
    const idx = comp.indexOf(secret);
    if (idx >= 0) return { known: true, c, matched: secret.length, len: secret.length, at: idx, mask: Array(secret.length).fill(true) };
    const lead = comp.length - comp.trimStart().length;
    const mask = [...secret].map((ch, i) => comp[lead + i] === ch);
    return { known: true, c, matched: mask.filter(Boolean).length, len: secret.length, at: lead, mask };
  }

  function renderOut(cardEl, model, prompt, completion, state, server) {
    const [title, side] = MODEL_LABEL[model] || [model, 'pooled'];
    const m = S.models.find((x) => x.kind === model || x.model_id === model);
    cardEl.dataset.side = side;
    cardEl.dataset.model = model;
    cardEl.classList.remove('is-leak', 'is-clean');
    let body = '', verdict = '';
    if (state === 'idle') {
      body = '<span class="p">pick a canary prefix above →</span>';
    } else if (state === 'loading') {
      body = `<span class="p">${esc(prompt)}</span><span class="cursor"></span>`;
    } else if (state === 'error') {
      body = `<span class="p">${esc(prompt)}</span><span class="c" style="color:var(--leak)">${esc(completion)}</span>`;
    } else {
      const sc = scoreLeak(prompt, completion, server);
      let comp = esc(completion);
      if (sc.known && sc.server) {
        const lead = completion.length - completion.trimStart().length;
        if (sc.matched === sc.len) comp = esc(completion.slice(0, lead)) + `<mark>${esc(completion.slice(lead, lead + sc.len))}</mark>` + esc(completion.slice(lead + sc.len));
      }
      if (sc.known) {
        const chars = [...completion];
        if (sc.mask) comp = chars.map((ch, i) => {
          const j = i - sc.at;
          if (j >= 0 && j < sc.len) return sc.mask[j] ? `<mark>${esc(ch)}</mark>` : `<span class="miss">${esc(ch)}</span>`;
          return esc(ch);
        }).join('');
        const full = sc.matched === sc.len;
        const partial = !full && sc.matched >= Math.ceil(sc.len * 0.6);
        const cls = full || partial ? 'leak' : 'clean';
        cardEl.classList.add(full || partial ? 'is-leak' : 'is-clean');
        const label = !sc.c.inserted ? (full ? 'LEAK?!' : 'NO LEAK') : full ? 'SECRET LEAKED' : partial ? 'PARTIAL LEAK' : 'NO LEAK';
        verdict = `<div class="verdict ${cls}"><span class="pill">${label}</span><span>${sc.matched}/${sc.len} secret chars reproduced${sc.c.inserted ? '' : ' · control canary, never trained'}${sc.server ? ' · scored on server' : ''}</span></div>`;
      } else {
        verdict = `<div class="verdict unknown"><span class="pill">UNSCORED</span><span>${S.canaries.length ? 'prompt is not a known canary prefix' : 'canary endpoint missing — no secret to compare against'}</span></div>`;
      }
      body = `<span class="p">${esc(prompt)}</span><span class="c">${comp}</span>`;
    }
    cardEl.innerHTML = `
      <header><div class="who">${side === 'lethe' ? 'revocable' : 'baseline'}</div>
        <small>${m ? esc(m.model_id) + ' · ' + esc(short(m.weights_sha256, 6)) : esc(model)}</small></header>
      <b class="out-title">${esc(title)}</b>
      <div class="out-text">${body}</div>${verdict}`;
  }

  async function runPrompt(prompt, which = 'both') {
    prompt = prompt ?? $('#prompt-input').value;
    if (!prompt) { $('#prompt-input').focus(); return; }
    S.lastPrompt = prompt;
    $('#prompt-input').value = prompt;
    renderPresets();
    const right = $('#compare-model').value;
    const targets = which === 'right' ? [[$('#out-right'), right]] : [[$('#out-left'), 'serving'], [$('#out-right'), right]];
    await Promise.all(targets.map(async ([el, model]) => {
      renderOut(el, model, prompt, '', 'loading');
      try {
        const r = await API.post('/api/generate', { prompt, model, max_new_tokens: 48 });
        renderOut(el, model, prompt, r.completion ?? r.text ?? '', 'done', r);
      } catch (e) {
        renderOut(el, model, prompt, String(e.message || e), 'error');
      }
    }));
  }

  // ======================================================================================
  // 04 revocation job
  // ======================================================================================
  const STEPS = [
    ['blast_radius', 'trace lineage from the source to every derived artifact'],
    ['exclude_records', "build a new shard manifest without the source's records"],
    ['retrain_shard', 'retrain only the affected shard, from base'],
    ['redeploy', 'swap the new shard model into the serving ensemble'],
    ['audit', 'canary exposure vs never-trained control canaries'],
    ['sign', 'sign ledger entries — SIMULATED_TEE_ATTESTATION key'],
    ['certificate', 'emit the scoped deletion certificate'],
  ];
  let timerRAF = 0;

  function renderJobTop() {
    const el = $('#job-top');
    const job = S.job;
    if (!job) {
      const opts = S.sources.filter((s) => s.status !== 'revoked').map((s) => `<option value="${esc(s.source_id)}">${esc(cleanName(s.display_name))} · S${esc(s.shard)}</option>`).join('');
      el.innerHTML = `<div class="target"><small>target source</small><select id="job-source">${opts}</select></div>
        <span class="timer">00.0s</span>
        <button class="btn btn-danger" id="job-go" ${opts ? '' : 'disabled'}>Revoke · exact</button>`;
      return;
    }
    const s = sourceById(job.source_id);
    const running = job.data?.status !== 'done' && job.data?.status !== 'error';
    el.innerHTML = `<div class="target"><b>${esc(cleanName(s?.display_name || job.source_id))}</b>
        <small>${esc(job.source_id)} · ${num(s?.n_records)} records · shard S${esc(s?.shard)} · ${esc(job.job_id)}</small></div>
      <span class="timer ${running ? 'running' : ''}" id="job-timer">${fmtElapsed(job)}</span>
      ${running ? '' : '<button class="btn btn-ghost btn-sm" id="job-reset">New job</button>'}`;
  }
  const fmtElapsed = (job) => `${(((job.tEnd || Date.now()) - job.t0) / 1000).toFixed(1).padStart(4, '0')}s`;
  function tickTimer() {
    cancelAnimationFrame(timerRAF);
    const loop = () => {
      const t = $('#job-timer');
      if (t && S.job) t.textContent = fmtElapsed(S.job);
      if (S.job && !S.job.tEnd) timerRAF = requestAnimationFrame(loop);
    };
    loop();
  }

  function renderStepper() {
    const steps = S.job?.data?.steps || [];
    $('#stepper').innerHTML = STEPS.map(([name, hint], i) => {
      const st = steps.find((s) => s.name === name) || { status: 'pending', detail: '' };
      const icon = st.status === 'done' ? '✓' : st.status === 'error' ? '✕' : String(i + 1);
      const showBar = name === 'retrain_shard' && (st.status === 'running' || st.status === 'done');
      const p = st.status === 'done' ? 1 : +st.progress || 0;
      return `<li class="step ${esc(st.status)}">
        <span class="dot">${icon}</span>
        <div class="body"><div class="name">${name}</div>
          <div class="detail">${esc(st.detail || hint)}</div>
          ${showBar ? `<div class="bar"><i style="width:${(p * 100).toFixed(1)}%"></i></div>` : ''}
        </div></li>`;
    }).join('');
    renderMiniStepper();
  }

  // compact horizontal copy of the stepper under the lineage graph, so graph + job are visible together
  function renderMiniStepper() {
    const el = $('#mini-stepper');
    if (!el) return;
    const job = S.job;
    if (!job) { el.hidden = true; el.innerHTML = ''; return; }
    el.hidden = false;
    const steps = job.data?.steps || [];
    const s = sourceById(job.source_id);
    const state = job.error ? 'error' : job.data?.status === 'done' ? 'done' : 'running';
    el.innerHTML = `<div class="ms-head"><span>revocation job · ${esc(cleanName(s?.display_name || job.source_id))}</span><span class="ms-state ${state}">${state === 'done' ? `done in ${fmtElapsed(job)}` : state === 'error' ? 'failed' : 'running…'}</span></div>
      <ol>${STEPS.map(([name]) => {
        const st = steps.find((x) => x.name === name) || { status: 'pending' };
        const p = name === 'retrain_shard' && st.status === 'running' ? +st.progress || 0 : st.status === 'done' ? 1 : 0;
        return `<li class="${esc(st.status)}" title="${esc(st.detail || name)}"><i style="--p:${(p * 100).toFixed(1)}%"></i><span>${esc(name.replace(/_/g, ' '))}</span></li>`;
      }).join('')}</ol>`;
  }

  function renderJobFoot() {
    const el = $('#job-foot');
    const job = S.job;
    if (!job) { el.innerHTML = 'Revoke a source here or from the Sources panel. Only its shard is retrained.'; return; }
    if (job.error) { el.innerHTML = `<span class="err">job failed: ${esc(job.error)}</span>`; return; }
    if (job.data?.status !== 'done') { el.innerHTML = 'running…'; return; }
    const shards = new Set(S.sources.map((s) => s.shard)).size || 1;
    el.innerHTML = `done in <b>${fmtElapsed(job)}</b> · retrained <b>1 of ${shards}</b> shards (≈${Math.round(100 / shards)}% of a full retrain)
      ${job.data.certificate_id ? ` · <a href="#cert-panel" class="btn btn-sm btn-ghost" id="to-cert">certificate ${esc(job.data.certificate_id)} ↓</a>` : ''}`;
  }

  async function startRevoke(sid) {
    if (!sid || (S.job && !S.job.tEnd)) return;
    const s = sourceById(sid);
    if (!s || s.status === 'revoked') return;
    const nodeId = sourceNodeId(sid);
    const affected = nodeId ? new Set([...downstream(nodeId)].filter((id) => id !== nodeId && !isBaseline(LG.byId.get(id) || {}))) : new Set();
    S.job = { source_id: sid, t0: Date.now(), nodeId, affected, data: null };
    S.hover = null;
    $$('.src-row.is-hover').forEach((r) => r.classList.remove('is-hover'));
    renderStepper(); // no auto-scroll: the compact stepper under the lineage graph shows progress next to the animation
    try {
      const r = await API.post('/api/revoke', { source_id: sid, mode: 'exact' });
      S.job.job_id = r.job_id;
    } catch (e) {
      S.job.error = e.message; S.job.tEnd = Date.now();
      renderJob(); renderSources();
      return;
    }
    renderJob(); renderSources(); paintLineageFocus(); tickTimer();
    pollJob();
  }

  async function pollJob() {
    let n = 0;
    while (S.job && !S.job.tEnd) {
      try {
        const d = await API.get(`/api/jobs/${encodeURIComponent(S.job.job_id)}`);
        S.job.data = d;
        if (d.status === 'error') { S.job.error = (d.steps || []).find((s) => s.status === 'error')?.detail || 'error'; S.job.tEnd = Date.now(); }
        if (d.status === 'done') S.job.tEnd = Date.now();
      } catch (e) {
        S.job.fails = (S.job.fails || 0) + 1;
        if (S.job.fails > 8) { S.job.error = e.message; S.job.tEnd = Date.now(); }
      }
      renderStepper(); renderJobFoot(); paintLineageWork(); renderShardStrip();
      if (n++ % 4 === 0 || S.job.tEnd) { try { await loadLedger(); renderLedger(); renderHeader(); } catch { /* ignore */ } }
      if (!S.job.tEnd) await sleep(400);
    }
    renderJob();
    if (S.job?.data?.status === 'done') await onJobDone();
  }

  async function onJobDone() {
    const id = S.job.data.certificate_id;
    const sh = sourceById(S.job.source_id)?.shard;
    if (sh != null) S.retrained.add(sh);
    await refreshAll();
    if (id) { await openCert(id); toast(`certificate ${id} issued`); }
    if (S.lastPrompt) runPrompt(S.lastPrompt);
    if (S.approx) renderApprox();
  }

  function renderJob() { renderJobTop(); renderStepper(); renderJobFoot(); }

  // ======================================================================================
  // 05 certificate
  // ======================================================================================
  async function openCert(id) {
    S.certId = id; S.verify = null; S.browserCheck = null; S.showRaw = false;
    try { S.cert = await API.get(`/api/certificates/${encodeURIComponent(id)}`); }
    catch (e) { S.cert = { error: e.message }; }
    renderCert();
    renderHeader();
  }

  function highlightJSON(obj) {
    return esc(JSON.stringify(obj, null, 2)).replace(
      /(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?|true|false|null)\b/g,
      (m, str, colon, lit) => (str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`) : `<span class="n">${lit}</span>`),
    );
  }

  // ledger backend label: only what the backend reports. Tiger Data naming only for a real postgres backend.
  function backendLabel(b) {
    if (!b) return null;
    const v = String(b).toLowerCase();
    if (v === 'postgres' || v === 'postgresql') return 'Postgres (Tiger Data / TimescaleDB)';
    if (v === 'sqlite') return 'SQLite (local file)';
    return String(b);
  }
  const hypertable = () => S.info?.timescaledb === true || S.cert?.ledger?.timescaledb === true;

  const CHECK_GROUPS = [
    ['attestation', /attest|trusted_key|tee|hardware/i],
    ['audit', /audit|canary|exposure/i],
    ['signature & ledger', /signature|signed|ledger|logged|format|revocation_recorded|deployment|chain|anchor/i],
    ['lineage & non-membership', /./],
  ];
  function checksHtml(checks) {
    const groups = CHECK_GROUPS.map(([g]) => ({ g, items: [] }));
    checks.forEach((k) => { const i = CHECK_GROUPS.findIndex(([, re]) => re.test(k.name)); groups[i].items.push(k); });
    return groups.filter((x) => x.items.length).map(({ g, items }) => {
      const ok = items.filter((k) => k.ok).length;
      return `<div class="check-group"><div class="cg-head"><span>${esc(g)}</span><span class="${ok === items.length ? 'v-ok' : 'v-bad'}">${ok}/${items.length}</span></div>
        <ul class="checks">${items.map((k, i) => {
          const sim = /simulated/i.test(`${k.name} ${k.detail}`);
          const cls = !k.ok ? 'bad' : sim ? 'sim' : 'ok';
          return `<li class="${cls}" style="animation-delay:${i * 40}ms" title="${esc(k.detail || '')}"><span class="ic">${!k.ok ? '✕' : sim ? '!' : '✓'}</span><b>${esc(k.name)}</b>${!k.ok || sim ? `<small>${esc(k.detail || '')}</small>` : ''}</li>`;
        }).join('')}</ul></div>`;
    }).join('');
  }

  // shorten long hex runs in the claim (full value on hover / click-to-copy)
  function claimToHtml(claim) {
    return esc(claim).replace(/\b[0-9a-f]{16,}\b/gi, (h) => `<span class="hl hash" data-copy="${h}" title="${h}\nclick to copy">${h.slice(0, 10)}…${h.slice(-4)}</span>`)
      .replace(/\b([0-9a-f]{8,15}…|[0-9a-f]{8,15}\.\.\.)/gi, '<span class="hl">$1</span>');
  }

  function renderCert() {
    const el = $('#cert-body');
    const sel = $('#cert-select');
    sel.hidden = S.certs.length < 2;
    sel.innerHTML = S.certs.map((c) => { const id = c.certificate_id || c.id; return `<option value="${esc(id)}" ${id === S.certId ? 'selected' : ''}>${esc(id)} · ${esc(c.source_id || '')}</option>`; }).join('');

    const c = S.cert;
    if (!c) {
      el.innerHTML = `<div class="cert-empty">No certificate yet — revoke a source to issue one.
        <p class="ghost-claim">“Serving model ⟨hash⟩ was produced by signed training runs whose manifests exclude all ⟨n⟩ records of source ⟨id⟩ …”</p></div>`;
      return;
    }
    if (c.error) { el.innerHTML = `<div class="cert-empty">could not load certificate: ${esc(c.error)}</div>`; return; }

    const f = (...k) => deepFind(c, k);
    const rs = c.revoked_source || {};
    const id = c.certificate_id || f('certificate_id', 'id') || S.certId;
    const claim = c.claim || f('claim', 'statement', 'claim_text') || '';
    const sid = rs.source_id || f('source_id');
    const src = sourceById(sid);
    const nRec = rs.n_records ?? f('n_records', 'records_removed', 'n_records_removed');
    const merkle = rs.merkle_root || f('source_merkle_root', 'merkle_root');
    const serving = c.serving_model?.serving_hash || f('serving_model_sha256', 'serving_sha256', 'serving_hash', 'serving_model_hash');
    const tier = c.removal_tier || f('removal_tier', 'tier'); // never defaulted: show only what the certificate claims
    const auditObj = c.audit || {};
    const exp = deepFind(auditObj, ['canary_exposure_bits', 'inserted_mean_bits', 'exposure_bits', 'max_canary_exposure_bits']);
    const ctrl = deepFind(auditObj, ['control_baseline_bits', 'control_mean_bits', 'control_bits', 'baseline_bits']);
    const maxKnown = deepFind(auditObj, ['max_bits']) ?? (S.audit.serving || [])[0]?.max_bits;
    const maxB = maxKnown ?? Math.max(+exp || 0, +ctrl || 0, 1) * 4;
    let scope = f('out_of_scope', 'out_of_scope_notes', 'scope_notes') || [];
    if (!Array.isArray(scope)) scope = [String(scope)];
    const issued = c.issued_at || f('issued_at', 'created_at');
    const seq = c.ledger?.head_seq ?? deepFind(c, ['entry_seq', 'ledger_seq']);
    const sigRaw = c.signature?.value ?? c.signature;
    const sig = typeof sigRaw === 'string' ? sigRaw : null;
    const signer = c.signature?.signer || c.attestation?.type || (typeof c.attestation === 'string' ? c.attestation : null) || 'SIMULATED_TEE_ATTESTATION';
    const backend = backendLabel(c.ledger?.backend);
    const pct = (v) => Math.max(1.5, Math.min(100, (100 * (+v || 0)) / (+maxB || 1)));

    const v = S.verify;
    let verifyHtml = '';
    if (v === 'loading') verifyHtml = '<div class="checks-head">running server-side checks…</div>';
    else if (v?.error) verifyHtml = `<div class="checks-head"><span class="v-bad">verification request failed: ${esc(v.error)}</span></div>`;
    else if (v) {
      const checks = v.checks || [];
      const skipped = Array.isArray(v.skipped) && v.skipped.length ? `<div class="checks-skipped">not run (need extra inputs): ${v.skipped.map((x) => esc(typeof x === 'string' ? x : x.name || JSON.stringify(x))).join(', ')}</div>` : '';
      verifyHtml = `<div class="checks-head"><span class="${v.valid ? 'v-ok' : 'v-bad'}">${v.valid ? '✓ VALID' : '✕ INVALID'} — ${checks.filter((x) => x.ok).length}/${checks.length} checks passed</span><span class="mono" style="color:var(--muted)" title="GET /api/certificates/${esc(id)}/verify">server-side · hover a check for detail</span></div>
        <div class="check-groups">${checksHtml(checks)}</div>${skipped}`;
    }
    const bc = S.browserCheck;
    let bcHtml = '';
    if (bc === 'loading') bcHtml = '<div class="checks-head">recomputing in this browser…</div>';
    else if (bc) {
      bcHtml = `<div class="checks-head"><span class="${bc.every((x) => x.ok) ? 'v-ok' : 'v-bad'}">${bc.every((x) => x.ok) ? '✓' : '✕'} ${bc.filter((x) => x.ok).length}/${bc.length} recomputed in this browser</span><span class="mono" style="color:var(--muted)">WebCrypto sha256 · hover for detail</span></div>
        <ul class="checks bc">${bc.map((k) => `<li class="${k.ok ? 'ok' : 'bad'}" title="${esc(k.detail)}"><span class="ic">${k.ok ? '✓' : '✕'}</span><b>${esc(k.name)}</b>${k.ok ? '' : `<small>${esc(k.detail)}</small>`}</li>`).join('')}</ul>`;
    }

    el.innerHTML = `
      <div class="cert-doc">
        <div class="seal">SIMULATED<br>TEE<br>ATTESTATION</div>
        <div class="cert-kicker">deletion certificate · ${esc(id)}${issued ? ' · ' + esc(new Date(issued).toLocaleString()) : ''}</div>
        <p class="cert-claim">${claim ? claimToHtml(claim) : '<em>(certificate has no claim field)</em>'}</p>
        <dl class="cert-facts">
          <div class="fact"><dt>source</dt><dd title="${esc(sid)}">${esc(src ? cleanName(src.display_name) : sid)}</dd></div>
          <div class="fact"><dt>records removed</dt><dd class="big">${num(nRec)}</dd></div>
          <div class="fact"><dt>removal tier</dt><dd>${tier ? `<span class="tier">${esc(String(tier).replace(/_/g, ' '))}</span>` : '—'}</dd></div>
          <div class="fact"><dt>source merkle root</dt><dd>${hashSpan(merkle, 10)}</dd></div>
          <div class="fact"><dt>serving model sha256</dt><dd>${hashSpan(serving, 10)}</dd></div>
          <div class="fact"><dt>ledger head at issue</dt><dd>${seq != null ? '#' + esc(seq) : '—'} ${c.ledger?.head_hash ? hashSpan(c.ledger.head_hash, 6) : ''}</dd></div>
          <div class="fact" style="grid-column: span 2"><dt>empirical audit · canary exposure in bits${maxKnown != null ? ` (max ${bits(maxKnown)})` : ''}</dt><dd>
            <div class="audit-mini">
              <div><span>target</span><span class="track"><i style="width:${pct(exp)}%"></i></span><span>${bits(exp)}</span></div>
              <div><span>control</span><span class="track"><i class="ctrl" style="width:${pct(ctrl)}%"></i></span><span>${bits(ctrl)}</span></div>
            </div></dd></div>
          <div class="fact"><dt>signature</dt><dd>${sig ? `${esc(c.signature?.alg || '')} ${hashSpan(sig, 6)}` : '—'}</dd></div>
          <div class="fact"><dt>ledger backend</dt><dd title="reported by the certificate (ledger.backend)">${backend ? esc(backend) : '—'}${backend && hypertable() ? ' <span class="badge-ok">hypertable</span>' : ''}</dd></div>
          <div class="fact"><dt>signer</dt><dd><span class="badge-sim" title="software ed25519 key standing in for a TEE attestation key">${esc(signer)}</span></dd></div>
        </dl>
        ${scope.length ? `<details class="cert-scope"><summary><h4>out of scope — not claimed (${scope.length})</h4></summary><ul>${scope.map((s) => `<li>${esc(s)}</li>`).join('')}</ul></details>` : ''}
        <div class="cert-actions">
          <button class="btn btn-primary" id="cert-check" title="recompute the source Merkle root, serving hash and ledger anchor from the certificate's own data with WebCrypto">Check in browser</button>
          <button class="btn" id="cert-verify" title="asks this same server to run lethe.certificate.verify_certificate — not independent">Verify (server)</button>
          <button class="btn" id="cert-raw-toggle">${S.showRaw ? 'Hide' : 'Raw'} JSON</button>
          <button class="btn btn-ghost" id="cert-download">Download .json</button>
          <span class="cli">offline, independent: <code data-copy="uv run python scripts/verify_certificate.py artifacts/certificates/${esc(id)}.json --ledger &lt;export.json&gt;" title="click to copy">uv run python scripts/verify_certificate.py artifacts/certificates/${esc(id)}.json --ledger &lt;export.json&gt;</code></span>
        </div>
        ${bcHtml}
        ${verifyHtml}
        ${S.showRaw ? `<pre class="cert-raw">${highlightJSON(c)}</pre>` : ''}
      </div>`;
  }

  async function verifyCert() {
    if (!S.certId) return;
    S.verify = 'loading'; renderCert();
    try { S.verify = await API.get(`/api/certificates/${encodeURIComponent(S.certId)}/verify`); }
    catch (e) { S.verify = { error: e.message }; }
    renderCert();
  }

  // ---- in-browser recomputation (WebCrypto), same scheme as lethe/ledger.py ----
  const hexBytes = (h) => new Uint8Array((h.match(/../g) || []).map((b) => parseInt(b, 16)));
  const toHex = (b) => Array.from(new Uint8Array(b), (x) => x.toString(16).padStart(2, '0')).join('');
  const sha256 = async (bytes) => new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  const concat = (...a) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let i = 0; a.forEach((x) => { o.set(x, i); i += x.length; }); return o; };
  async function merkleRootOf(leavesHex) { // sorted, deduped leaves; 0x00 leaf / 0x01 node prefixes; odd node promoted
    const leaves = [...new Set(leavesHex.map((h) => String(h).trim().toLowerCase()))].sort();
    if (!leaves.length) return toHex(await sha256(new Uint8Array(0)));
    let level = await Promise.all(leaves.map((h) => sha256(concat(new Uint8Array([0]), hexBytes(h)))));
    while (level.length > 1) {
      const nxt = [];
      for (let i = 0; i < level.length; i += 2) nxt.push(i + 1 < level.length ? await sha256(concat(new Uint8Array([1]), level[i], level[i + 1])) : level[i]);
      level = nxt;
    }
    return toHex(level[0]);
  }
  async function browserChecks() {
    const c = S.cert;
    if (!c) return;
    if (!window.crypto?.subtle) { S.browserCheck = [{ name: 'webcrypto', ok: false, detail: 'crypto.subtle unavailable (needs https or localhost)' }]; renderCert(); return; }
    S.browserCheck = 'loading'; renderCert();
    const out = [];
    const add = (name, ok, detail) => out.push({ name, ok: !!ok, detail });
    try {
      const rs = c.revoked_source || {};
      if (Array.isArray(rs.record_hashes)) {
        const root = await merkleRootOf(rs.record_hashes);
        add('source Merkle root recomputed', root === String(rs.merkle_root).toLowerCase(), `${rs.record_hashes.length} record hashes → ${short(root, 10)} ${root === String(rs.merkle_root).toLowerCase() ? '=' : '≠'} certificate ${short(rs.merkle_root, 10)}`);
        add('record count matches claim', rs.record_hashes.length === rs.n_records && (c.claim || '').includes(`all ${rs.n_records} records`), `${rs.record_hashes.length} hashes · n_records ${rs.n_records}`);
        const reg = sourceById(rs.source_id);
        if (reg?.merkle_root) add('root = registered root (GET /api/sources)', reg.merkle_root === rs.merkle_root, `${short(reg.merkle_root, 10)} from the live source list`);
      } else add('source Merkle root recomputed', false, 'certificate has no revoked_source.record_hashes');
      const sm = c.serving_model || {};
      if (Array.isArray(sm.shard_model_hashes)) {
        const sh = toHex(await sha256(new TextEncoder().encode(JSON.stringify(sm.shard_model_hashes.map((h) => String(h).trim().toLowerCase()).sort()))));
        add('serving hash recomputed', sh === String(sm.serving_hash).toLowerCase(), `sha256(sorted ${sm.shard_model_hashes.length} shard hashes) → ${short(sh, 10)}`);
      }
      add('claim names these hashes', (c.claim || '').includes(rs.merkle_root) && (c.claim || '').includes(sm.serving_hash), 'claim text contains the source root and serving hash');
      const hs = c.ledger?.head_seq, hh = c.ledger?.head_hash;
      if (hs != null) {
        const e = S.ledger.find((x) => x.seq === hs);
        add('ledger head anchored', e ? e.entry_hash === hh : false, e ? `entry #${hs} in GET /api/ledger ${e.entry_hash === hh ? 'has' : 'does NOT have'} hash ${short(hh, 8)}` : `entry #${hs} not in the loaded ledger window`);
      }
    } catch (e) { add('recomputation', false, String(e.message || e)); }
    S.browserCheck = out;
    renderCert();
  }

  function downloadCert() {
    if (!S.cert) return;
    const blob = new Blob([JSON.stringify(S.cert, null, 2)], { type: 'application/json' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: `${S.certId || 'certificate'}.json` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ======================================================================================
  // 06 ledger
  // ======================================================================================
  const CAT = {
    SOURCE_REGISTERED: 'register', MANIFEST_CREATED: 'train', TRAINING_RUN: 'train', MODEL_DEPLOYED: 'train',
    REVOCATION_REQUESTED: 'revoke', SOURCE_REVOKED: 'revoke', AUDIT_COMPLETED: 'audit', CERTIFICATE_ISSUED: 'cert',
  };
  function summarize(p) {
    if (!p || typeof p !== 'object') return String(p ?? '');
    const keys = ['source_id', 'certificate_id', 'model_id', 'manifest', 'run', 'model', 'shard', 'n_records', 'canary_exposure_bits', 'mode'];
    const parts = keys.filter((k) => p[k] != null).slice(0, 3).map((k) => `${k}=${typeof p[k] === 'object' ? '…' : p[k]}`);
    return parts.length ? parts.join(' · ') : Object.keys(p).slice(0, 4).join(', ');
  }
  function renderLedger() {
    const rows = S.ledger;
    const bySeq = new Map(rows.map((e) => [e.seq, e]));
    let links = 0, broken = 0;
    const html = rows.slice().reverse().map((e) => {
      const prev = bySeq.get(e.seq - 1);
      let link = '';
      if (prev) {
        const ok = e.prev_hash === prev.entry_hash;
        ok ? links++ : broken++;
        link = `<span class="${ok ? 'link-ok' : 'link-bad'}" title="prev_hash ${ok ? '=' : '≠'} entry_hash of #${prev.seq}">${ok ? '⛓ ' : '✕ '}#${prev.seq}</span>`;
      } else if (/^0+$/.test(e.prev_hash || '')) link = '<span>genesis</span>';
      const fresh = S.ledgerMaxSeq >= 0 && e.seq > S.ledgerMaxSeq;
      return `<li class="lg-entry ${fresh ? 'fresh' : ''}" data-cat="${CAT[e.type] || ''}">
        <span class="seq">#${esc(e.seq)}</span>
        <div class="card">
          <div class="top"><span class="type">${esc(e.type)}</span><span class="ts" title="${esc(e.ts)}">${esc(relTime(e.ts))}</span></div>
          <div class="sum" title="${esc(JSON.stringify(e.payload))}">${esc(summarize(e.payload))}</div>
          <div class="chain">
            <span>entry ${hashSpan(e.entry_hash, 8)}</span><span>prev ${hashSpan(e.prev_hash, 6)}</span>${link}
            <span class="signer" title="signature ${esc(e.signature || '')}">${esc(e.signer || 'unsigned')}</span>
          </div>
        </div></li>`;
    }).join('');
    $('#ledger').innerHTML = html || '<li class="empty">ledger is empty</li>';
    $('#ledger-meta').innerHTML = `<span>${rows.length} entries${rows.length ? ` · head #${rows[rows.length - 1].seq}` : ''}</span>
      <span class="${broken ? 'bad' : 'ok'}" title="client-side check only: prev_hash equals the previous entry_hash. Signatures and hashes are re-verified server-side / by scripts/verify_certificate.py">${broken ? `✕ ${broken} broken link(s)` : `⛓ ${links}/${links} links match`} <span style="color:var(--muted)">(client-side)</span></span>`;
    if (rows.length) S.ledgerMaxSeq = Math.max(S.ledgerMaxSeq, rows[rows.length - 1].seq);
  }

  // ======================================================================================
  // 07 approximate unlearning
  // ======================================================================================
  const APPROX_KEYS = [
    ['pooled', 'pooled', 'before unlearning', 'leak'],
    ['pooled_unlearned', 'unlearned', 'gradient ascent', 'hidden-leak'],
    ['pooled_unlearned_quantized', '+ 4-bit quant', 'RTN, no training', 'leak'],
    ['pooled_unlearned_relearned', '+ relearned', 'few fine-tune steps', 'leak'],
  ];

  // Accepts approximate_pipeline() output of lethe/unlearn.py:
  //   {source_id, config, unlearn_seconds, relearn_seconds, models: {name: {audit: [...], summary: {inserted_mean_bits,
  //    control_mean_bits, extracted, ...}, val_loss}}}
  // plus flatter variants ({results: [{model, exposure_bits}]}, {pooled: 12.3, ...}). Aggregation everywhere: MEAN
  // over the source's inserted canaries (same as audit.summarize and the "Lethe exact" bar).
  const meanOf = (xs) => (xs.length ? xs.reduce((a, x) => a + x, 0) / xs.length : null);
  function normalizeApprox(r) {
    const out = { values: {}, extracted: {}, controls: {}, control: null, max: null, source_id: r?.source_id, raw: r, config: r?.config || null };
    const sid = r?.source_id;
    const fromRows = (rows) => {
      const ins = rows.filter((x) => x && x.inserted !== false && (!sid || !x.source_id || x.source_id === sid));
      return meanOf(ins.map((x) => +x.exposure_bits).filter((v) => !Number.isNaN(v)));
    };
    const pick = (v) => {
      if (v == null) return null;
      if (typeof v === 'number') return v;
      if (Array.isArray(v)) return fromRows(v);
      if (typeof v === 'object') {
        const direct = v.summary?.inserted_mean_bits ?? v.inserted_mean_bits ?? v.exposure_bits ?? v.canary_exposure_bits ?? v.exposure ?? v.bits;
        if (direct != null && !Number.isNaN(+direct)) return +direct;
        if (Array.isArray(v.audit)) return fromRows(v.audit);
      }
      return null;
    };
    const arr = Array.isArray(r) ? r : Array.isArray(r?.results) ? r.results : Array.isArray(r?.models) ? r.models : null;
    if (arr) arr.forEach((x) => { const k = x.model ?? x.name ?? x.kind; if (k) out.values[k] = pick(x); });
    const dicts = [r?.models && !Array.isArray(r.models) ? r.models : null, r?.exposure, r?.exposures, r?.results && !Array.isArray(r.results) ? r.results : null, r].filter(Boolean);
    APPROX_KEYS.forEach(([k]) => {
      for (const d of dicts) {
        if (out.values[k] == null && d[k] != null) out.values[k] = pick(d[k]);
        if (out.extracted[k] == null && d[k]?.summary?.extracted != null) out.extracted[k] = d[k].summary.extracted;
        if (out.controls[k] == null && d[k]?.summary?.control_mean_bits != null) out.controls[k] = d[k].summary.control_mean_bits;
      }
    });
    // control baseline: the unlearned model's control canaries (same canaries, never trained), else any summary/row
    const m = r?.models && !Array.isArray(r.models) ? r.models : {};
    out.control = m.pooled_unlearned?.summary?.control_mean_bits ?? m.pooled?.summary?.control_mean_bits
      ?? deepFind(r, ['control_baseline_bits', 'control_mean_bits', 'control_bits', 'baseline_bits', 'control_exposure_bits']);
    if (out.control == null) {
      const rows = Array.isArray(m.pooled_unlearned?.audit) ? m.pooled_unlearned.audit : Array.isArray(r?.pooled) ? r.pooled : [];
      out.control = meanOf(rows.filter((x) => x.inserted === false).map((x) => +x.exposure_bits));
    }
    out.max = deepFind(r, ['max_bits']) ?? Object.values(m).map((x) => x?.audit?.[0]?.max_bits).find((x) => x != null) ?? null;
    return out;
  }

  function exactExposure(sid) {
    const s = sourceById(sid);
    if (!s || s.status !== 'revoked') return null;
    const rows = (S.audit.serving || []).filter((a) => a.inserted && a.source_id === sid);
    return meanOf(rows.map((a) => +a.exposure_bits)); // mean, same aggregation as the approximate bars
  }
  function controlFromAudit() {
    const rows = (S.audit.serving || []).filter((a) => a.inserted === false);
    return meanOf(rows.map((x) => +x.exposure_bits));
  }

  function renderApproxControls() {
    const sel = $('#approx-source');
    const cur = sel.value || S.approx?.source_id;
    const opts = S.sources.filter((s) => s.n_canaries > 0);
    sel.innerHTML = opts.map((s) => {
      const name = s.entity || cleanName(s.display_name).replace(/:.*$/, '');
      const shortName = name.length > 28 ? name.slice(0, 27) + '…' : name;
      return `<option value="${esc(s.source_id)}" title="${esc(s.display_name)}" ${s.source_id === cur ? 'selected' : ''}>${esc(shortName)}${s.status === 'revoked' ? ' (revoked)' : ''}</option>`;
    }).join('');
  }

  function renderApprox() {
    const svg = $('#approx-chart');
    const W = 860, H = 356, L = 50, R = 16, T = 62, B = 56; // T leaves headroom for value + extracted labels
    const d = S.approx;
    const sid = d?.source_id || $('#approx-source').value;
    const exact = exactExposure(sid);
    const control = d?.control ?? controlFromAudit();
    const vals = APPROX_KEYS.map(([k]) => d?.values?.[k] ?? null);
    const maxBits = d?.max || (S.audit.serving || [])[0]?.max_bits || Math.max(10, ...vals.filter((v) => v != null), exact || 0) * 1.15;
    const bars = [...APPROX_KEYS.map(([k, label, sub, cls], i) => ({ label, sub, cls, v: vals[i], ex: d?.extracted?.[k] })),
      { label: 'Lethe exact', sub: exact != null ? 'shard retrain, revoked' : 'revoke source to measure', cls: 'exact', v: exact }];
    const iw = W - L - R, ih = H - T - B;
    const y = (v) => T + ih - (ih * Math.min(v, maxBits)) / maxBits;
    const bw = Math.min(96, iw / bars.length - 34);
    const step = iw / bars.length;
    const parts = [];
    const tstep = Math.max(1, Math.ceil(maxBits / 5)); // integer ticks (0, 4, 8, …) below the max line
    for (let v = 0; v <= maxBits + 1e-9; v += tstep) {
      parts.push(`<g class="ax"><line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(0)}</text></g>`);
    }
    parts.push(`<g class="ax"><text x="${L - 40}" y="${T - 14}">bits</text></g>`);
    parts.push(`<line class="max-line" x1="${L}" x2="${W - R}" y1="${y(maxBits)}" y2="${y(maxBits)}"/><text class="bar-sub" x="${W - R}" y="${y(maxBits) - 6}" text-anchor="end">max ${bits(maxBits)} bits = secret ranked #1 of all candidates</text>`);
    bars.forEach((b, i) => {
      const cx = L + step * i + step / 2;
      const has = b.v != null;
      const h = has ? Math.max(2, T + ih - y(b.v)) : ih * 0.12;
      const by = T + ih - h;
      parts.push(`<rect class="bar-rect ${has ? b.cls : 'ghost'}" x="${cx - bw / 2}" y="${by}" width="${bw}" height="${h}" rx="3"/>`);
      if (has) parts.push(`<text class="bar-val" x="${cx}" y="${by - 8}" text-anchor="middle">${bits(b.v)}</text>`);
      if (has && b.ex != null) parts.push(`<text class="bar-sub" x="${cx}" y="${by - 26}" text-anchor="middle">${esc(b.ex)} extracted</text>`);
      parts.push(`<text class="bar-label" x="${cx}" y="${H - B + 20}" text-anchor="middle">${esc(b.label)}</text><text class="bar-sub" x="${cx}" y="${H - B + 36}" text-anchor="middle">${esc(b.sub)}</text>`);
      if (i === 0) parts.push(`<line x1="${L + step - 2}" x2="${L + step - 2}" y1="${T + 8}" y2="${T + ih}" stroke="var(--line)" stroke-dasharray="2 4"/>`);
      if (i === 3) parts.push(`<line x1="${L + step * 4}" x2="${L + step * 4}" y1="${T}" y2="${T + ih + 40}" stroke="var(--line-2)"/>`);
    });
    if (control != null) {
      parts.push(`<line class="base-line" x1="${L}" x2="${W - R}" y1="${y(control)}" y2="${y(control)}"/>
        <line class="base-line" x1="${L + 4}" x2="${L + 30}" y1="12" y2="12"/>
        <text class="base-label" x="${L + 36}" y="16">control canaries (never trained): ${bits(control)} bits</text>`);
    }
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = parts.join('');

    // takeaway sentence, derived from the numbers actually shown: one clause per attack
    const [p, u, q, rl] = vals;
    const tk = $('#approx-takeaway');
    if (p == null || u == null) {
      tk.innerHTML = '<span style="color:var(--muted)">Run the experiment to measure the mean exposure of the source\'s planted canaries on each model.</span>';
      return;
    }
    const cfg = d?.config || {};
    const mx = d?.max ?? (S.audit.serving || [])[0]?.max_bits;
    const ofMax = mx ? ` of ${bits(mx)}` : '';
    const ctlOf = (k) => d?.controls?.[k] ?? control;
    const ctl = ctlOf('pooled_unlearned_relearned') != null ? ` (control ${bits(ctlOf('pooled_unlearned_relearned'))})` : '';
    const margin = Math.max(3, control != null ? control + 2 - u : 3);
    const recovered = (v) => v != null && v > u + margin;
    const partial = (v, k) => (mx ? v < 0.8 * mx : true) || /^0\//.test(String(d?.extracted?.[k] || ''));
    const verb = (v, k) => (partial(v, k) ? 'partially recovered it, to' : 'brought it back to');
    const clauses = [];
    const dropped = u < p - 3;
    let s = dropped
      ? `Gradient ascent drops exposure from <b>${bits(p)}</b> to ${bits(u)} bits — it <em>looks</em> forgotten. `
      : `Gradient ascent only moved exposure from ${bits(p)} to ${bits(u)} bits. `;
    if (q != null) {
      clauses.push(recovered(q)
        ? `${cfg.quant?.bits || 4}-bit quantization ${verb(q, 'pooled_unlearned_quantized')} <b>${bits(q)}</b>${ofMax} bits`
        : `${cfg.quant?.bits || 4}-bit quantization did not recover it (${bits(q)} bits)`);
    }
    if (rl != null) {
      const n = cfg.relearn?.steps ? `${cfg.relearn.steps} relearning steps` : 'a few relearning steps';
      const on = cfg.relearn?.data === 'other' ? ' on canary-free text from other sources' : cfg.relearn?.data === 'same' ? " on the source's own canary-free text" : ' on canary-free text';
      const ex = d?.extracted?.pooled_unlearned_relearned;
      clauses.push(recovered(rl)
        ? `${n}${on} ${verb(rl, 'pooled_unlearned_relearned')} <b>${bits(rl)}</b>${ofMax} bits${ctl}${ex ? `, ${esc(ex)} secrets extracted verbatim` : ''}`
        : `${n}${on} did not recover it (${bits(rl)} bits${ctlOf('pooled_unlearned_relearned') != null ? `, control ${bits(ctlOf('pooled_unlearned_relearned'))}` : ''})`);
    }
    if (clauses.length) s += clauses.join('; ') + '. ';
    const anyRec = recovered(q) || recovered(rl);
    const allPartial = [[q, 'pooled_unlearned_quantized'], [rl, 'pooled_unlearned_relearned']].filter(([v]) => recovered(v)).every(([v, k]) => partial(v, k));
    if (anyRec) s += allPartial ? 'The signal was suppressed, not removed. ' : 'The secret was hidden, not removed. ';
    else if (clauses.length) s += 'In this run neither attack recovered it. ';
    if (exact != null) s += `Exact shard retrain: <span class="ok">${bits(exact)} bits</span>${control != null ? ` vs ${bits(control)} control` : ''}.`;
    tk.innerHTML = s;
  }

  async function runApprox() {
    if (S.approxBusy) return;
    const sid = $('#approx-source').value;
    S.approxBusy = true;
    const btn = $('#approx-run');
    btn.disabled = true;
    const st = $('#approx-status');
    st.className = 'approx-status';
    const t0 = Date.now();
    const iv = setInterval(() => { st.textContent = `unlearning → quantizing → relearning on pooled model… ${((Date.now() - t0) / 1000).toFixed(1)}s`; }, 100);
    try {
      const r = await API.post('/api/approximate/run', { source_id: sid });
      clearInterval(iv);
      S.approx = normalizeApprox(r);
      S.approx.source_id = S.approx.source_id || sid;
      const c = r?.config || {};
      const extra = [c.unlearn?.method || r.method, (c.unlearn?.steps || r.unlearn_steps) && `${c.unlearn?.steps || r.unlearn_steps} unlearn steps`,
        c.quant?.bits ? `${c.quant.bits}-bit RTN` : r.quantization, (c.relearn?.steps || r.relearn_steps) && `${c.relearn?.steps || r.relearn_steps} relearn steps`].filter(Boolean).join(' · ');
      if (APPROX_KEYS.every(([k]) => S.approx.values[k] == null)) throw new Error('response had no exposure numbers this UI understands (expected models.<name>.summary.inserted_mean_bits)');
      st.textContent = `done in ${((Date.now() - t0) / 1000).toFixed(1)}s${extra ? ' · ' + extra : ''}`;
      try { await loadAudit('serving'); } catch { /* ignore */ }
    } catch (e) {
      clearInterval(iv);
      st.className = 'approx-status err';
      st.textContent = `failed: ${e.message}`;
    } finally {
      clearInterval(iv);
      S.approxBusy = false;
      btn.disabled = false;
      renderApprox();
    }
  }

  // ======================================================================================
  // orchestration + events
  // ======================================================================================
  function renderAll() {
    renderHeader();
    renderSources();
    renderLineage();
    renderPresets();
    renderJob();
    renderCert();
    renderLedger();
    renderApproxControls();
    renderApprox();
  }

  function bindEvents() {
    document.addEventListener('click', (ev) => {
      const t = ev.target;
      const cp = t.closest('[data-copy]');
      if (cp) { ev.preventDefault(); copy(cp.dataset.copy); return; }
      const rv = t.closest('[data-revoke]');
      if (rv) { startRevoke(rv.dataset.revoke); return; }
      const pr = t.closest('.preset');
      if (pr) { runPrompt(pr.dataset.prefix); return; }
      if (t.closest('#job-go')) { startRevoke($('#job-source')?.value); return; }
      if (t.closest('#job-reset')) { S.job = null; renderJob(); paintLineageWork(); renderLineage(); return; }
      if (t.closest('#cert-check')) { browserChecks(); return; }
      if (t.closest('#to-cert')) { ev.preventDefault(); $('#cert-panel').scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
      if (t.closest('#cert-verify')) { verifyCert(); return; }
      if (t.closest('#cert-raw-toggle')) { S.showRaw = !S.showRaw; renderCert(); return; }
      if (t.closest('#cert-download')) { downloadCert(); return; }
      if (t.closest('#approx-run')) { runApprox(); return; }
      const node = t.closest('.lg-node[data-sid]');
      if (node) setHover(S.hover === node.dataset.sid ? null : node.dataset.sid);
    });

    const table = $('#sources-table');
    table.addEventListener('mouseover', (ev) => { const r = ev.target.closest('.src-row'); if (r) setHover(r.dataset.sid); });
    table.addEventListener('mouseleave', () => setHover(null));
    const svg = $('#lineage-svg');
    svg.addEventListener('mouseover', (ev) => { const g = ev.target.closest('.lg-node[data-sid]'); if (g) setHover(g.dataset.sid); });
    svg.addEventListener('mouseleave', () => setHover(null));

    $('#prompt-form').addEventListener('submit', (ev) => { ev.preventDefault(); runPrompt(); });
    document.addEventListener('change', (ev) => { if (ev.target.id === 'preset-more' && ev.target.value) runPrompt(ev.target.value); });
    $('#compare-model').addEventListener('change', () => { if (S.lastPrompt) runPrompt(S.lastPrompt, 'right'); else renderOut($('#out-right'), $('#compare-model').value, '', '', 'idle'); });
    $('#cert-select').addEventListener('change', (ev) => openCert(ev.target.value));
    $('#approx-source').addEventListener('change', () => { if (S.approx && S.approx.source_id !== $('#approx-source').value) { S.approx = null; $('#approx-status').textContent = ''; } renderApprox(); });

    let rz;
    window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { renderLineage(); renderApprox(); }, 120); });

    $('#theme-toggle').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('lethe-theme', next); } catch { /* storage unavailable */ }
    });
  }

  async function boot() {
    try { const t = localStorage.getItem('lethe-theme'); if (t) document.documentElement.dataset.theme = t; } catch { /* ignore */ }
    if (params.get('theme')) document.documentElement.dataset.theme = params.get('theme');
    bindEvents();
    renderOut($('#out-left'), 'serving', '', '', 'idle');
    renderOut($('#out-right'), 'pooled', '', '', 'idle');
    await detectBackend();
    await loadInfo();
    await refreshAll();
    // resume the most recent certificate if one exists (e.g. after a page reload mid-demo)
    if (S.certs.length && !S.certId) await openCert(S.certs[S.certs.length - 1].certificate_id || S.certs[S.certs.length - 1].id);
    setInterval(() => { if (!S.job || S.job.tEnd) loadLedger().then(() => { renderLedger(); renderHeader(); }).catch(() => {}); }, 15000);
  }

  boot();
})();
