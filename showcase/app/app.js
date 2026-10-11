// Lethe console. LIVE: talks to lethe/server.py (docs/PLATFORM_API.md), which runs the real removal CLI on this laptop.
// RECORDED: replays the real golden runs bundled in ./recorded/ (Acme, and Acme + Cobalt) and says so everywhere.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const params = new URLSearchParams(location.search);
  const STEP_NAMES = ['PROBE', 'BLAST RADIUS', 'RETRAIN', 'REDEPLOY', 'PROBE AGAIN', 'RECEIPTS + DELETION CERTIFICATE', 'CUSTOMER VERIFIES'];
  const STEP_LABEL = ['Probe the served model', 'Blast radius', 'Retrain affected shards', 'Redeploy', 'Probe again', 'Sign the deletion receipt', 'Customer verifies'];
  // measured on this laptop (one removal ~28 s): the first probe reads the cache measured at reset on this exact model
  const PROBE0_S = 0.1; // [1] PROBE from the cache
  const RETRAIN_S = 21; // per shard retrain (SFT -> DPO)
  const REPROBE_S = 2; // [5] PROBE AGAIN, per customer re-ranked live (removed + shard-mates)
  const FIXED_S = 2; // startup + receipts + deletion receipt + verify
  const GONE_TIP = 'already removed — Reset to bring back';
  const N_CONTROLS = 8;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const fmt = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : '—');
  const short = (h, n = 12) => (h ? String(h).slice(0, n) : '—');
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const uniq = (a) => [...new Set(a)];
  const mergeOf = (shard) => (shard - 1) >> 1; // shards 1,2 -> merge A (0); 3,4 -> merge B (1)
  const first = (name) => String(name).split(' ')[0];
  const secs = (s) => (s == null ? '' : s < 0.1 ? '<0.1 s' : `${s.toFixed(1)} s`);

  // ------------------------------------------------------------------------------------------
  // state
  // ------------------------------------------------------------------------------------------
  const S = {
    mode: null, src: null, model: '', customers: [], shards: [], serving: {}, busy: false,
    selected: new Set(), hot: null,
    job: null, // {id, slugs, status, steps, progress, log[], summary, certificates, shardPct{}, ...}
    decorate: false, // show the last job's status on the tree (until the operator picks again)
    drifted: new Set(), drifting: new Set(),
    certs: [], certSel: null, certCache: {}, verify: {},
  };
  const bySlug = (slug) => S.customers.find((c) => c.slug === slug);
  const recSlugs = () => new Set(S.mode === 'recorded' && Rec.man ? Rec.man.runs.flatMap((r) => r.slugs) : []);
  const removedSlugs = () => S.customers.filter((c) => c.status === 'removed').map((c) => c.slug);

  // ------------------------------------------------------------------------------------------
  // data sources: same shapes for LIVE and RECORDED (docs/PLATFORM_API.md)
  // ------------------------------------------------------------------------------------------
  class HttpError extends Error {
    constructor(status, detail) { super(detail || `HTTP ${status}`); this.status = status; this.detail = detail; }
  }

  async function fetchT(url, opts = {}, ms = 0) {
    const ctl = new AbortController();
    const timer = ms ? setTimeout(() => ctl.abort(), ms) : null;
    try { return await fetch(url, { ...opts, signal: ctl.signal }); } finally { if (timer) clearTimeout(timer); }
  }

  const API = new URL('../api/', location.href);

  async function api(path, { method = 'GET', body, timeout = 0 } = {}) {
    const r = await fetchT(new URL(path, API), {
      method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined, cache: 'no-store',
    }, timeout);
    let data = null;
    try { data = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) {
      const d = data && (typeof data.detail === 'string' ? data.detail : data.error || JSON.stringify(data.detail || ''));
      throw new HttpError(r.status, d);
    }
    return data;
  }

  // the certificate JSON -> the fields the console shows
  function certFields(cert, extra = {}) {
    const nm = cert.non_membership || [];
    return {
      id: cert.certificate_id, slug: String(cert.revoked_source?.source_id || '').split(':')[1], claim: cert.claim,
      issued_at: cert.issued_at, serving_sha256: cert.serving_model?.serving_hash, n_records: cert.revoked_source?.n_records,
      n_proofs: nm.reduce((s, m) => s + (m.proofs ? m.proofs.length : 0), 0), n_manifests: nm.length,
      removal_tier: cert.removal_tier, merkle_root: cert.revoked_source?.merkle_root, attestation: cert.attestation || {},
      ...extra,
    };
  }

  function parseVerify(txt) {
    const checks = [];
    let skipped = 0;
    for (const ln of String(txt).split('\n')) {
      const m = ln.match(/^\s*\[(PASS|FAIL|SKIP)\]\s+(\S+)\s*(.*)$/);
      if (!m) continue;
      if (m[1] === 'SKIP') { skipped++; continue; }
      checks.push({ name: m[2], ok: m[1] === 'PASS', detail: m[3].trim() });
    }
    const res = (String(txt).match(/^RESULT: .*$/m) || [''])[0];
    return { valid: /RESULT: VALID/.test(res), checks, skipped, result: res };
  }

  const Live = {
    async health() { return api('health', { timeout: 1500 }); },
    state: () => api('state'),
    start: (slugs) => api('jobs', { method: 'POST', body: { slugs } }),
    job: (id, since) => api(`jobs/${encodeURIComponent(id)}?since=${since}`),
    certs: () => api('certificates'),
    async cert(id) {
      if (!S.certCache[id]) {
        const c = await api(`certificates/${encodeURIComponent(id)}`);
        S.certCache[id] = certFields(c, { href: new URL(`certificates/${encodeURIComponent(id)}`, API).href });
      }
      return S.certCache[id];
    },
    async verify(id, tamper) {
      const r = await api(`certificates/${encodeURIComponent(id)}/verify`, { method: 'POST', body: { tamper } });
      const p = parseVerify(r.output || '');
      return { valid: r.valid, checks: r.checks?.length ? r.checks : p.checks, skipped: p.skipped, result: p.result, output: r.output || '', tamper: r.tamper || null };
    },
    reset: () => api('reset', { method: 'POST', body: {} }),
    ask: (question) => api('ask', { method: 'POST', body: { question }, timeout: 20000 }),
    askSlug: (slug) => api('ask', { method: 'POST', body: { slug }, timeout: 20000 }),
    command: (slugs, id) => `python -m lethe.revoke ${slugs.map((s) => `--remove ${s}`).join(' ')} --out runs/${id}/`,
  };

  // RECORDED: a replay of the real CLI output, compressed in time. Every number shown comes from the recorded files.
  const Rec = {
    base: new URL('recorded/', location.href),
    man: null, local: null, jobs: {},
    url(dir, file) { return new URL(`${encodeURIComponent(dir)}/${encodeURIComponent(file)}`, this.base).href; },
    async load() {
      const r = await fetch(new URL('manifest.json', this.base), { cache: 'no-cache' });
      if (!r.ok) throw new Error(`recorded/manifest.json: HTTP ${r.status}`);
      this.man = await r.json();
      this.pristine();
    },
    pristine() {
      const m = this.man;
      const certs = []; // reset restores the receipts too: certificates from before it are gone
      this.local = {
        removed: new Set(), serving: m.serving.sha256,
        shards: Object.fromEntries(m.shards.map((s) => [s.id, { sha256: s.sha256, version: s.version }])), certs,
      };
    },
    runFor(slugs) {
      const key = [...slugs].sort().join('+');
      return this.man.runs.find((r) => [...r.slugs].sort().join('+') === key) || null;
    },
    allowed(slugs) { return !!this.runFor(slugs) && slugs.every((s) => !this.local.removed.has(s)); },
    async state() {
      const L = this.local;
      const running = Object.values(this.jobs).find((j) => !j.finished);
      return {
        customers: this.man.customers.map((c) => ({ ...c, status: L.removed.has(c.slug) ? 'removed' : 'active' })),
        shards: this.man.shards.map((s) => ({ ...s, ...L.shards[s.id] })),
        serving: { sha256: L.serving, strategy: this.man.serving.strategy }, busy: !!running, current_job: running ? running.id : null,
      };
    },
    async start(slugs) {
      if (Object.values(this.jobs).some((j) => !j.finished)) throw new HttpError(409, 'A removal is already running.');
      const run = this.runFor(slugs);
      if (!run) throw new HttpError(400, 'Run locally to remove any customer. This recording has Acme, and Acme + Cobalt.');
      if (slugs.some((s) => this.local.removed.has(s))) throw new HttpError(400, 'Already removed. Reset the demo first.');
      const [tr, sm] = await Promise.all([
        fetch(this.url(run.dir, 'transcript.txt')).then((r) => { if (!r.ok) throw new Error('transcript'); return r.text(); }),
        fetch(this.url(run.dir, 'summary.json')).then((r) => { if (!r.ok) throw new Error('summary'); return r.json(); }),
      ]);
      const id = `replay-${run.key.replace('+', '-')}-${Date.now().toString(36).slice(-4)}`;
      const ev = timeline(tr, sm);
      this.jobs[id] = { id, run, slugs: run.slugs.slice(), summary: sm, ev, t0: performance.now(), end: ev[ev.length - 1].t, finished: false };
      return { job_id: id };
    },
    async job(id, since) {
      const j = this.jobs[id];
      if (!j) throw new HttpError(404, 'no such job');
      const el = performance.now() - j.t0;
      const steps = STEP_NAMES.map((name, i) => ({ n: i + 1, name, status: 'pending', seconds: null }));
      const log = [];
      let progress = null;
      let done = false;
      for (const e of j.ev) {
        if (e.t > el) break;
        if (e.line !== undefined) log.push(e.line);
        else if (e.step) Object.assign(steps[e.step - 1], { status: e.status }, e.seconds != null ? { seconds: e.seconds } : {});
        else if (e.progress) progress = e.progress;
        else if (e.end) done = true;
      }
      if (done && !j.finished) this.finish(j);
      return {
        id, slugs: j.slugs, status: done ? 'done' : 'running', elapsed_s: el / 1000, steps, progress: done ? null : progress,
        log: log.slice(since), log_offset: since, summary: done ? j.summary : null,
        certificates: done ? j.summary.certificates.map((c) => c.certificate_id) : [],
        recorded: { total_s: j.summary.timings_s.total, replay_s: j.end / 1000, generated_at: j.summary.generated_at },
      };
    },
    finish(j) {
      j.finished = true;
      const L = this.local;
      j.slugs.forEach((s) => L.removed.add(s));
      L.serving = j.summary.serving.after_hash;
      for (const r of j.summary.shards_retrained) L.shards[r.shard] = { sha256: r.weights_sha256, version: r.model_id };
      for (const c of j.run.certificates) {
        if (!L.certs.some((x) => x.id === c.id)) L.certs.push({ ...c, job_id: j.id, dir: j.run.dir });
      }
    },
    async certs() {
      return this.local.certs.map((c) => ({ id: c.id, slug: c.slug, job_id: c.job_id, issued_at: c.issued_at, serving_sha256: c.serving_sha256, n_records: c.n_records, n_proofs: c.n_proofs }));
    },
    async cert(id) {
      const c = this.local.certs.find((x) => x.id === id);
      if (!c) throw new HttpError(404, 'no such certificate');
      return { ...c, href: this.url(c.dir, c.file) };
    },
    async verify(id, tamper) {
      const c = this.local.certs.find((x) => x.id === id);
      const r = await fetch(this.url(c.dir, tamper ? c.verify_tampered : c.verify));
      if (!r.ok) throw new HttpError(r.status, 'recorded verifier output missing');
      const txt = await r.text();
      await sleep(260); // the recorded verifier took 0.26 s
      return { ...parseVerify(txt), output: txt, tamper: tamper ? c.tamper : null };
    },
    async reset() { await sleep(150); this.pristine(); return { ok: true }; },
    command(slugs) { return `python -m lethe.revoke ${slugs.map((s) => `--remove ${s}`).join(' ')}   (recorded)`; },
  };

  // Recorded transcript -> timed events. ~13 s for one shard, ~15 s for two. Seconds shown per step are the real ones.
  function timeline(transcript, summary) {
    const lines = transcript.replace(/\n+$/, '').split('\n');
    const segs = [{ n: 0, lines: [] }];
    for (const ln of lines) {
      const m = ln.match(/^\[(\d)\] /);
      if (m) segs.push({ n: +m[1], lines: [ln] });
      else if (/^DONE\b/.test(ln)) segs.push({ n: 8, lines: [ln] });
      else segs[segs.length - 1].lines.push(ln);
    }
    const T = summary.timings_s || {};
    const known = { 2: 0, 3: T.retrain_total, 6: T.certificates, 7: T.verify };
    const nShards = (summary.shards_retrained || []).length || 1;
    const ev = [];
    let t = 0;
    for (const seg of segs) {
      const { n } = seg;
      const hm = n >= 1 && n <= 7 ? seg.lines[0].match(/(\d+\.\d)s\s*$/) : null;
      const sec = hm ? parseFloat(hm[1]) : known[n];
      if (n === 0) {
        seg.lines.forEach((ln, i) => ev.push({ t: 120 * i, line: ln }));
        t = 120 * seg.lines.length + 250;
        continue;
      }
      if (n === 8) {
        ev.push({ t, line: seg.lines[0] });
        STEP_NAMES.forEach((_, i) => ev.push({ t, step: i + 1, status: 'done' }));
        ev.push({ t: t + 1, end: true });
        continue;
      }
      ev.push({ t, step: n, status: 'running' });
      if (n === 1 || n === 5) {
        // the CLI prints a probe's lines only once the probe has finished
        const W = 1900;
        seg.lines.forEach((ln, i) => ev.push({ t: t + W + 45 * i, line: ln }));
        t += W + 45 * seg.lines.length + 150;
      } else if (n === 3) {
        ev.push({ t, line: seg.lines[0] });
        t += 250;
        const D = nShards > 1 ? 2500 : 3200;
        let block = null;
        const flush = () => {
          if (!block) return;
          const pts = block.pts;
          const fin = pts.length ? pts[pts.length - 1].s : 1;
          const t0 = t;
          block.pre.forEach((ln, i) => ev.push({ t: t0 + 60 * i, line: ln }));
          block.post.forEach((ln, i) => ev.push({ t: t0 + D + 60 * i, line: ln }));
          let prev = { tt: t0 + 150, pct: 0 };
          let phase = 'SFT';
          for (const p of pts) {
            const tt = t0 + 150 + (D - 150) * (p.s / fin);
            for (let x = prev.tt + 120; x < tt; x += 120) {
              ev.push({ t: x, progress: { shard: block.shard, pct: Math.floor(prev.pct + (p.pct - prev.pct) * ((x - prev.tt) / (tt - prev.tt))), phase } });
            }
            phase = p.phase === 'done' ? phase : p.phase;
            ev.push({ t: tt, line: p.line });
            ev.push({ t: tt, progress: { shard: block.shard, pct: p.pct, phase } });
            prev = { tt, pct: p.pct };
          }
          t = t0 + D + 200;
          block = null;
        };
        for (const ln of seg.lines.slice(1)) {
          const pm = ln.match(/^\s+shard (\d+) \[[#.]+\]\s+(\d+)%\s+(SFT|DPO|done)\b.*?([\d.]+)s\s*$/);
          const km = ln.match(/^\s+shard (\d+): /);
          if (km) { flush(); block = { shard: +km[1], pre: [ln], pts: [], post: [] }; } else if (pm && block) block.pts.push({ line: ln, pct: +pm[2], phase: pm[3], s: parseFloat(pm[4]) });
          else if (block) (block.pts.length ? block.post : block.pre).push(ln);
          else ev.push({ t, line: ln });
        }
        flush();
      } else {
        const D = { 2: 900, 4: 700, 6: 900, 7: 1200 }[n];
        const gap = D / Math.max(1, seg.lines.length);
        seg.lines.forEach((ln, i) => ev.push({ t: t + gap * i, line: ln }));
        t += D + 120;
      }
      ev.push({ t: t - 1, step: n, status: 'done', seconds: sec });
    }
    return ev.sort((a, b) => a.t - b.t);
  }

  // ------------------------------------------------------------------------------------------
  // the tree (layout ported from the landing page; compact for the console)
  // ------------------------------------------------------------------------------------------
  const stage = $('stage');
  const LAYOUTS = {
    wide: { H: 362, inset: 0.03, y: { serving: 32, merge: 104, shard: 176, chipA: 262, chipB: 262, river: 314, base: 344 } },
    narrow: { H: 446, inset: 0.012, y: { serving: 30, merge: 102, shard: 172, chipA: 258, chipB: 322, river: 382, base: 420 } },
  };
  let L;
  const nodeHtml = (id, kind, label, sub) =>
    `<div class="node node--${kind}" id="${id}"><span class="node__label">${label}</span><span class="node__hash" id="${id}-h">${sub || ''}</span>` +
    `<span class="node__status"></span>${kind === 'shard' ? '<span class="node__bar"><i></i></span>' : ''}</div>`;

  function buildTree() {
    const cs = S.customers;
    const edgeIds = [...cs.map((c) => `e-c-${c.slug}`), ...[1, 2, 3, 4].map((s) => `e-s${s}`), 'e-m0', 'e-m1'];
    stage.innerHTML = `
      <svg class="wires" aria-hidden="true">
        <defs>
          <linearGradient id="river-fade-g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1000" y2="0">
            <stop offset="0" stop-color="#000"/><stop offset=".07" stop-color="#FFF"/><stop offset=".93" stop-color="#FFF"/><stop offset="1" stop-color="#000"/>
          </linearGradient>
          <mask id="river-fade" maskUnits="userSpaceOnUse" x="0" y="0" width="100%" height="100%"><rect width="100%" height="100%" fill="url(#river-fade-g)"/></mask>
        </defs>
        <g class="river" mask="url(#river-fade)">
          <path class="river__bank"/><path class="river__water"/>
          <path class="river__shade" transform="translate(0 3)"/><path class="river__light" transform="translate(0 -2)"/><path class="river__glint"/>
        </g>
        <g class="edges">${edgeIds.map((id) => `<path class="edge" id="${id}"/>`).join('')}</g>
      </svg>
      ${nodeHtml('serving', 'serving', 'Serving model', '4 shards · ensemble')}
      ${nodeHtml('merge0', 'merge', 'Group A', 'shards 1 · 2')}${nodeHtml('merge1', 'merge', 'Group B', 'shards 3 · 4')}
      ${[1, 2, 3, 4].map((s) => nodeHtml(`shard${s}`, 'shard', `Shard ${s}`)).join('')}
      ${cs.map((c) => {
        const [a, ...b] = c.name.split(' ');
        return `<button class="chip" id="chip-${c.slug}" type="button" data-slug="${c.slug}" aria-pressed="false" aria-label="${esc(c.name)}, shard ${c.shard}"><b>${esc(a)}</b><span class="chip__rest">${esc(b.join(' '))}</span><span class="chip__gone">removed</span></button>`;
      }).join('')}
      <div class="base"><b>Base model</b><span>never sees customer data</span></div>`;
    if (S.mode === 'recorded') cs.forEach((c) => { if (recSlugs().has(c.slug)) $(`chip-${c.slug}`).dataset.rec = ''; });
    layout();
    new ResizeObserver(() => layout()).observe(stage);
    stage.querySelectorAll('.chip').forEach((el) => {
      const slug = el.dataset.slug;
      el.addEventListener('click', () => toggle(slug));
      el.addEventListener('pointerenter', (e) => e.pointerType === 'mouse' && setHot(slug));
      el.addEventListener('pointerleave', () => setHot(null));
      el.addEventListener('focus', () => el.matches(':focus-visible') && setHot(slug));
      el.addEventListener('blur', () => setHot(null));
    });
  }

  const curve = (x1, y1, x2, y2) => { const m = (y1 + y2) / 2; return `M${x1} ${y1}C${x1} ${m} ${x2} ${m} ${x2} ${y2}`; };
  const elbow = (cx, cy, spine, sx, sy, r = 6) => `M${cx} ${cy}H${spine + r}Q${spine} ${cy} ${spine} ${cy - r}V${sy + r}Q${spine} ${sy} ${spine + r} ${sy}H${sx}`;
  const place = (el, x, y) => { el.style.left = `${x}px`; el.style.top = `${y}px`; };

  function layout() {
    const W = stage.clientWidth;
    if (!W) return;
    const mode = W < 640 ? 'narrow' : 'wide';
    const { H, inset, y } = LAYOUTS[mode];
    const x = (f) => W * (inset + f * (1 - 2 * inset));
    const colW = (W * (1 - 2 * inset)) / 4;
    const sx = { 1: x(0.125), 2: x(0.375), 3: x(0.625), 4: x(0.875) };
    const mx = [x(0.25), x(0.75)];
    const cs = S.customers;
    const chipW = mode === 'wide' ? Math.min(124, colW / 2 - 10) : Math.min(132, colW - 14);
    const idx = (c) => cs.filter((d) => d.shard === c.shard).indexOf(c); // 0 or 1 within its shard
    const chipX = {};
    const chipY = {};
    cs.forEach((c, i) => {
      chipX[c.slug] = mode === 'wide' ? x((i + 0.5) / 8) : sx[c.shard] + 5;
      chipY[c.slug] = mode === 'wide' || idx(c) === 0 ? y.chipA : y.chipB;
    });
    const riverY = (px) => y.river + 6 * Math.sin((px / W) * Math.PI * 3 + 0.6);
    L = { W, H, chipW, chipX, chipY, riverY };
    stage.dataset.layout = mode;
    stage.style.height = `${H}px`;
    stage.style.setProperty('--chip-w', `${chipW}px`);
    stage.querySelector('.wires').setAttribute('viewBox', `0 0 ${W} ${H}`);
    $('river-fade-g').setAttribute('x2', W);
    place($('serving'), x(0.5), y.serving);
    [0, 1].forEach((m) => place($(`merge${m}`), mx[m], y.merge));
    [1, 2, 3, 4].forEach((s) => place($(`shard${s}`), sx[s], y.shard));
    cs.forEach((c) => {
      place($(`chip-${c.slug}`), chipX[c.slug], chipY[c.slug]);
      const d = mode === 'wide'
        ? curve(chipX[c.slug], chipY[c.slug], sx[c.shard], y.shard)
        : elbow(chipX[c.slug], chipY[c.slug], chipX[c.slug] - chipW / 2 - 7, sx[c.shard], y.shard);
      $(`e-c-${c.slug}`).setAttribute('d', d);
    });
    [1, 2, 3, 4].forEach((s) => $(`e-s${s}`).setAttribute('d', curve(sx[s], y.shard, mx[mergeOf(s)], y.merge)));
    mx.forEach((px, m) => $(`e-m${m}`).setAttribute('d', curve(px, y.merge, x(0.5), y.serving)));
    let river = '';
    for (let px = -20; px <= W + 20; px += 8) river += `${river ? 'L' : 'M'}${px} ${riverY(px).toFixed(1)}`;
    stage.querySelectorAll('.river path').forEach((p) => p.setAttribute('d', river));
    Object.assign(stage.querySelector('.base').style, { left: `${W * inset}px`, right: `${W * inset}px`, top: `${y.base}px` });
  }

  const pathOf = (slug) => {
    const c = bySlug(slug);
    const m = mergeOf(c.shard);
    return { nodes: [$(`chip-${slug}`), $(`shard${c.shard}`), $(`merge${m}`), $('serving')], edges: [$(`e-c-${slug}`), $(`e-s${c.shard}`), $(`e-m${m}`)] };
  };

  function mark(el, state, text) {
    if (!el) return;
    if (state) el.dataset.state = state; else delete el.dataset.state;
    const line = el.querySelector && el.querySelector('.node__status');
    if (line) line.textContent = text || '';
  }

  function setHot(slug) {
    S.hot = slug;
    document.querySelectorAll('.is-hot').forEach((el) => el.classList.remove('is-hot'));
    if (!slug || !bySlug(slug)) return;
    const p = pathOf(slug);
    [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-hot'));
    stage.querySelector('.edges').append(...p.edges);
    const row = $(`row-${slug}`);
    if (row) row.classList.add('is-hot');
  }

  // where the running (or last) job is: 1..7, 8 = finished
  function jobStep(j) {
    if (!j) return 0;
    if (j.status === 'done') return 8;
    const run = j.steps.find((s) => s.status === 'running');
    if (run) return run.n;
    const done = j.steps.filter((s) => s.status === 'done').length;
    return done ? done + 1 : 1;
  }

  function renderTree() {
    const j = S.job;
    const deco = j && S.decorate && j.status !== 'error';
    const all = [$('serving'), $('merge0'), $('merge1'), ...[1, 2, 3, 4].map((s) => $(`shard${s}`)),
      ...stage.querySelectorAll('.chip'), ...stage.querySelectorAll('.edge')];
    all.forEach((el) => { mark(el, ''); el.classList.remove('is-queued'); });
    stage.toggleAttribute('data-busy', !!(j && j.status === 'running'));

    // hashes under the nodes (they change only where a shard retrained)
    // during a job, the new hashes come from the CLI's REDEPLOY lines; the server state catches up when it finishes
    const live = j && j.status === 'running' && S.decorate;
    $('serving-h').textContent = live && j.newServing ? j.newServing.slice(0, 8) : short(S.serving.sha256, 8);
    S.shards.forEach((s) => {
      const h = $(`shard${s.id}-h`);
      const m = String(s.version || '').match(/(?:^|-)(v\d+)$/); // server: "v2"; recorded: "shard-1-v2"
      let v = m ? m[1] : 'v1';
      let sha = short(s.sha256, 8);
      if (live && j.newHash && j.newHash[s.id]) { sha = j.newHash[s.id].slice(0, 8); v = `v${(parseInt(v.slice(1), 10) || 1) + 1}`; }
      if (h) h.innerHTML = `${esc(sha)}<span class="ver"> · ${esc(v)}</span>`;
    });

    S.customers.forEach((c) => {
      const el = $(`chip-${c.slug}`);
      const gone = c.status === 'removed' || S.drifted.has(c.slug);
      el.querySelector('.chip__gone').textContent = live && j.slugs.includes(c.slug) && c.status !== 'removed' ? 'excluded' : 'removed';
      el.classList.toggle('is-removed', gone && !S.drifting.has(c.slug));
      el.setAttribute('aria-pressed', String(S.selected.has(c.slug)));
      el.setAttribute('aria-disabled', String(gone || !!(j && j.status === 'running')));
      el.setAttribute('aria-label', `${c.name}, shard ${c.shard}${gone ? `, ${GONE_TIP}` : ''}`);
      el.title = gone ? `${c.name}: ${GONE_TIP}` : '';
    });

    if (deco) {
      const n = jobStep(j);
      const hitShards = uniq(j.slugs.map((s) => bySlug(s)?.shard).filter(Boolean));
      const hitMerges = uniq(hitShards.map(mergeOf));
      S.customers.forEach((c) => {
        const on = j.slugs.includes(c.slug);
        if (!on) { mark($(`chip-${c.slug}`), 'quiet'); mark($(`e-c-${c.slug}`), 'quiet'); return; }
        if (n < 3) mark($(`chip-${c.slug}`), 'removing');
        else { mark($(`e-c-${c.slug}`), 'cut'); if (!S.drifted.has(c.slug)) drift(c.slug); }
      });
      [1, 2, 3, 4].forEach((s) => {
        const el = $(`shard${s}`);
        if (!hitShards.includes(s)) { mark(el, 'quiet', 'untouched'); mark($(`e-s${s}`), 'quiet'); return; }
        const pct = j.shardPct?.[s];
        if (n === 3 && pct && pct.pct >= 100) mark(el, 'busy', 'trained · not deployed');
        else if (n === 3) {
          mark(el, 'busy', pct ? `retraining · ${pct.phase} ${pct.pct}%` : 'waiting to retrain');
          el.querySelector('.node__bar i').style.setProperty('--p', String((pct?.pct || 0) / 100));
        } else if ((n === 4 || n === 5) && !(j.newHash && j.newHash[s])) mark(el, 'busy', 'deploying…'); // until the REDEPLOY lines arrive
        else if (n >= 4) mark(el, 'done', 'retrained');
        else mark(el, 'busy', 'will retrain');
        if (n === 4) mark($(`e-s${s}`), 'busy'); else if (n > 4) mark($(`e-s${s}`), 'done');
      });
      [0, 1].forEach((m) => {
        if (!hitMerges.includes(m)) { mark($(`merge${m}`), 'quiet', 'untouched'); mark($(`e-m${m}`), 'quiet'); return; }
        if (n === 4) { mark($(`merge${m}`), 'busy', 'swapping in new shard…'); mark($(`e-m${m}`), 'busy'); }
        if (n > 4) { mark($(`merge${m}`), 'done', 'new shard in'); mark($(`e-m${m}`), 'done'); }
      });
      const sv = $('serving');
      if (n === 1) mark(sv, 'busy', 'probing…');
      else if (n === 4) mark(sv, 'busy', 'redeploying…');
      else if (n === 5) mark(sv, 'busy', 'probing again…');
      else if (n > 5) mark(sv, 'done', 'redeployed');
    } else {
      S.selected.forEach((slug) => { const p = pathOf(slug); [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-queued')); });
    }
    if (S.hot) setHot(S.hot);
  }

  // the removed chip drops into the river and drifts away (Lethe), then stays as a struck-through placeholder
  function drift(slug) {
    S.drifted.add(slug);
    const el = $(`chip-${slug}`);
    if (!el || !L) return;
    S.drifting.add(slug);
    const { W, chipW, chipX, chipY, riverY } = L;
    const x0 = chipX[slug];
    const y0 = chipY[slug];
    const tf = (dx, dy, s, r) => `translate(-50%, -50%) translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${r.toFixed(1)}deg) scale(${s.toFixed(3)})`;
    let frames = [{ opacity: 1 }, { opacity: 0 }];
    if (!reduced.matches) {
      // translate + opacity only: a shrinking chip reads as a smudge
      frames = [{ transform: tf(0, 0, 1, 0), opacity: 1, easing: 'cubic-bezier(.5,0,.8,.45)' }, { offset: 0.26, transform: tf(0, riverY(x0) - y0, 1, -2), opacity: 1 }];
      const room = W - x0 - chipW * 0.5 - 6;
      const dist = Math.max(16, Math.min(300, W * 0.5, room));
      for (let k = 1; k <= 12; k++) {
        const t = k / 12;
        frames.push({ offset: 0.26 + 0.74 * t, transform: tf(dist * t, riverY(x0 + dist * t) - y0, 1, Math.sin(k * 1.1) * 3), opacity: Math.max(0, 1 - t ** 1.3) });
      }
    }
    el.style.zIndex = 2;
    const anim = el.animate(frames, { duration: reduced.matches ? 300 : 2100, fill: 'forwards' });
    anim.onfinish = () => {
      el.style.zIndex = '';
      anim.cancel();
      S.drifting.delete(slug);
      el.classList.add('is-removed');
      el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: reduced.matches ? 1 : 400 });
    };
  }

  // ------------------------------------------------------------------------------------------
  // customers table + queue + batch plan
  // ------------------------------------------------------------------------------------------
  function buildTable() {
    $('cust-body').innerHTML = S.customers.map((c) => `
      <tr id="row-${c.slug}" data-slug="${c.slug}">
        <td class="cust__sel"><input type="checkbox" id="cb-${c.slug}" aria-describedby="st-${c.slug}"></td>
        <td><label for="cb-${c.slug}">${esc(c.name)}${S.mode === 'recorded' && recSlugs().has(c.slug) ? '<span class="rec-tag" title="a recorded run exists for this customer">rec</span>' : ''}</label></td>
        <td class="num sh">${c.shard}</td>
        <td class="num">${fmt(c.records)}</td>
        <td><span class="status" id="st-${c.slug}"></span></td>
      </tr>`).join('');
    S.customers.forEach((c) => {
      const row = $(`row-${c.slug}`);
      $(`cb-${c.slug}`).addEventListener('change', () => toggle(c.slug));
      row.addEventListener('pointerenter', (e) => e.pointerType === 'mouse' && setHot(c.slug));
      row.addEventListener('pointerleave', () => setHot(null));
      $(`cb-${c.slug}`).addEventListener('focus', (e) => e.target.matches(':focus-visible') && setHot(c.slug));
      $(`cb-${c.slug}`).addEventListener('blur', () => setHot(null));
    });
  }

  function renderTable() {
    const j = S.job;
    const running = j && j.status === 'running';
    S.customers.forEach((c) => {
      const row = $(`row-${c.slug}`);
      const cb = $(`cb-${c.slug}`);
      const st = $(`st-${c.slug}`);
      const removing = running && j.slugs.includes(c.slug);
      const s = removing ? 'removing' : c.status === 'removed' ? 'removed' : S.selected.has(c.slug) ? 'queued' : 'active';
      st.dataset.s = s;
      st.textContent = s;
      cb.checked = S.selected.has(c.slug) || removing;
      cb.disabled = c.status === 'removed' || running || S.busy;
      cb.setAttribute('aria-disabled', String(cb.disabled));
      row.title = c.status === 'removed' ? `${c.name}: ${GONE_TIP}` : '';
      row.classList.toggle('is-removed', c.status === 'removed');
      row.classList.toggle('is-queued', S.selected.has(c.slug));
    });
    const nRemoved = removedSlugs().length;
    $('cust-note').textContent = `${S.customers.length} tenants · 4 shards × 2 · synthetic${nRemoved ? ` · ${nRemoved} removed` : ''}`;
  }

  function toggle(slug) {
    const c = bySlug(slug);
    if (c && (c.status === 'removed' || S.drifted.has(slug)) && !(S.job && S.job.status === 'running')) {
      S.selected.delete(slug);
      renderAll();
      $('queue-note').textContent = `${c.name} is ${GONE_TIP}.`;
      return;
    }
    if (!c || S.busy || (S.job && S.job.status === 'running')) { renderTable(); return; }
    if (S.selected.has(slug)) S.selected.delete(slug); else S.selected.add(slug);
    S.decorate = false; // picking again: the tree shows the queue, not the last job
    renderAll();
  }

  // how much work a queue costs: batched (each distinct shard retrains once, one deploy) vs one at a time
  function plan(slugs) {
    const cs = slugs.map(bySlug);
    const gone = new Set(removedSlugs());
    const shards = uniq(cs.map((c) => c.shard));
    const merges = uniq(shards.map(mergeOf));
    const all = new Set([...gone, ...slugs]);
    const mates = S.customers.filter((c) => shards.includes(c.shard) && !all.has(c.slug)).length;
    const live = (sh, removed) => S.customers.some((c) => c.shard === sh && !removed.has(c.slug));
    const est = (nRm, nMates, nRetrain) => PROBE0_S + RETRAIN_S * nRetrain + REPROBE_S * (nRm + nMates) + FIXED_S;
    const batch = { retrains: shards.filter((s) => live(s, all)).length, merges: merges.length + 1, deploys: 1 };
    batch.est = est(slugs.length, mates, batch.retrains);
    let seqEst = 0;
    let seqRetrains = 0;
    const acc = new Set(gone);
    for (const c of cs) {
      acc.add(c.slug);
      const m = S.customers.filter((d) => d.shard === c.shard && !acc.has(d.slug)).length;
      const r = live(c.shard, acc) ? 1 : 0;
      seqRetrains += r;
      seqEst += est(1, m, r);
    }
    return { batch, seq: { retrains: seqRetrains, merges: 2 * cs.length, deploys: cs.length, est: seqEst } };
  }

  function renderQueue() {
    const sel = S.customers.filter((c) => S.selected.has(c.slug)).map((c) => c.slug);
    const n = sel.length;
    const running = S.job && S.job.status === 'running';
    $('tokens').innerHTML = sel.map((s) => `<li class="token">${esc(bySlug(s).name)}<button type="button" data-slug="${s}" aria-label="Remove ${esc(bySlug(s).name)} from the queue">×</button></li>`).join('');
    $('tokens').querySelectorAll('button').forEach((b) => b.addEventListener('click', () => { toggle(b.dataset.slug); $('remove').focus(); }));
    $('queue-count').textContent = n ? `${n} selected` : '';
    const hint = $('queue-hint');
    if (running) hint.textContent = 'A removal is running. The queue opens again when it finishes.';
    else if (!n) hint.textContent = removedSlugs().length === S.customers.length ? 'Every customer is removed. Reset the demo to start over.' : 'Select customers in the tree or the table. Several at once run as one batch.';
    else if (n === 1) hint.textContent = 'Add more customers to remove them in one batch.';
    else hint.textContent = `${n} customers, one batch: each affected shard retrains once, one deploy.`;

    const btn = $('remove');
    btn.textContent = n === 0 ? 'Remove' : n === 1 ? `Remove ${bySlug(sel[0]).name}` : `Remove ${n} customers`;
    let note = '';
    let ok = n > 0 && !running && !S.busy;
    if (n > 0 && S.mode === 'recorded' && !Rec.allowed(sel)) {
      ok = false;
      note = 'Run locally to remove any customer. This recording has Acme, and Acme + Cobalt.';
    } else if (S.busy && !running) note = 'The server is busy with another job.';
    btn.disabled = !ok;
    $('queue-note').textContent = note;

    const table = $('plan');
    table.hidden = n < 2; // the batched vs one-at-a-time comparison only means something from two customers up
    if (n === 1 && !running) {
      const p = plan(sel);
      hint.textContent = `${p.batch.retrains} shard retrain${p.batch.retrains === 1 ? 's' : ''} · 1 redeploy · est. ~${Math.round(p.batch.est)} s. Add more customers to remove them in one batch.`;
    }
    if (n >= 2) {
      const p = plan(sel);
      const row = (label, a, b, cls = '') => `<tr><th scope="row">${label}</th><td class="${a < b ? 'best' : ''} ${cls}">${a}</td><td class="${cls}">${b}</td></tr>`;
      const t = (s) => `~${Math.round(s)} s`;
      $('plan-body').innerHTML = row('shard retrains', p.batch.retrains, p.seq.retrains) +
        row('redeploys', p.batch.deploys, p.seq.deploys) +
        `<tr title="estimate measured on this laptop: ≈ ${PROBE0_S} s probe (cached at reset) + ${RETRAIN_S} s per shard retrain + ~${REPROBE_S} s per customer probed again + ~${FIXED_S} s receipts"><th scope="row">est. time</th><td class="est">${t(p.batch.est)}</td><td class="est">${t(p.seq.est)}</td></tr>`;
    }
  }

  // ------------------------------------------------------------------------------------------
  // the job: start, follow, render
  // ------------------------------------------------------------------------------------------
  let clockTimer = null;

  async function startRemoval() {
    const slugs = S.customers.filter((c) => S.selected.has(c.slug) && c.status !== 'removed').map((c) => c.slug);
    if (!slugs.length) return;
    $('remove').disabled = true;
    $('queue-note').textContent = '';
    let r;
    try {
      r = await S.src.start(slugs);
    } catch (e) {
      // another tab or curl changed the server under us: re-read it (refreshState attaches to a running job)
      if (S.mode === 'live' && (e.status === 409 || e.status === 400)) { await refreshState(); renderAll(); }
      renderQueue();
      $('queue-note').textContent = e.status === 409 ? 'Another removal is running on the server. Following it now.' : (e.detail || e.message || 'Could not start the job.');
      return;
    }
    S.selected.clear();
    follow(r.job_id, slugs);
    const h = $('job-h');
    h.setAttribute('tabindex', '-1');
    h.focus({ preventScroll: true });
    // keep the tree on screen: that is where the audience sees which shard retrains (the steps sit beside it)
    scrollToEl(document.querySelector('.tree-panel'), 'start');
  }

  function scrollToEl(el, block) {
    requestAnimationFrame(() => {
      if (!el) return;
      const r = el.getBoundingClientRect();
      const top = (document.querySelector('.top')?.getBoundingClientRect().height || 56) + 8; // below the sticky header
      const behavior = reduced.matches ? 'auto' : 'smooth';
      if (block === 'nearest') {
        if (r.top >= top && r.bottom <= innerHeight) return;
        scrollBy({ top: r.height > innerHeight - top ? r.top - top : r.bottom - innerHeight + 12, behavior });
      } else scrollBy({ top: r.top - top, behavior });
    });
  }

  async function follow(id, slugs) {
    S.job = { id, slugs: slugs || [], status: 'running', steps: STEP_NAMES.map((name, i) => ({ n: i + 1, name, status: 'pending', seconds: null })), progress: null, log: [], summary: null, certificates: [], shardPct: {}, t0: performance.now(), elapsed_s: 0 };
    S.decorate = true;
    S.drifted = new Set();
    $('result').hidden = true;
    $('term-log').textContent = '';
    $('term').open = true;
    $('job-err').hidden = true;
    $('job').hidden = false;
    $('term-cmd').textContent = S.src.command(S.job.slugs, id);
    renderAll();
    clearInterval(clockTimer);
    clockTimer = setInterval(renderClock, 100);
    let since = 0;
    let fails = 0;
    let lastStep = 0;
    for (;;) {
      if (!S.job || S.job.id !== id) return; // reset while following
      let snap;
      try {
        snap = await S.src.job(id, since);
        fails = 0;
      } catch (e) {
        fails++;
        if (fails > 8 || e.status === 404) { failJob(e.status === 404 ? 'The server no longer knows this job (it may have restarted).' : `Lost contact with the server: ${e.message}`); return; }
        await sleep(1000);
        continue;
      }
      if (!S.job || S.job.id !== id) return;
      const lines = snap.log || [];
      let firstIdx = snap.log_start ?? snap.log_offset ?? since;
      if (snap.log_start == null && firstIdx > since) firstIdx -= lines.length; // log_offset = total lines so far
      const fresh = lines.slice(Math.max(0, since - Math.max(0, firstIdx)));
      since = Math.max(since, Math.max(0, firstIdx) + lines.length);
      const j = S.job;
      j.slugs = snap.slugs?.length ? snap.slugs : j.slugs;
      j.status = snap.status;
      j.steps = (snap.steps?.length ? snap.steps : j.steps).map((s, i) => ({ n: s.n || i + 1, name: s.name || STEP_NAMES[i], status: s.status, seconds: s.seconds }));
      j.progress = snap.progress || null;
      j.elapsed_s = snap.elapsed_s || 0;
      j.elapsedAt = performance.now();
      j.recorded = snap.recorded || null;
      j.summary = snap.summary || null;
      j.certificates = snap.certificates || [];
      j.error = snap.error || null;
      j.tiger = snap.tiger || null;
      trackProgress(j);
      for (const ln of fresh) { // [4] REDEPLOY prints "shard 1  080e6ff0532e -> ef31587950a8" and "serving  7534… -> f644…"
        const sm = ln.match(/^\s+shard (\d+)\s+([0-9a-f]{8,})\s+->\s+([0-9a-f]{8,})/);
        if (sm) (j.newHash = j.newHash || {})[+sm[1]] = sm[3];
        const vm = ln.match(/^\s+serving\s+([0-9a-f]{8,})\s+->\s+([0-9a-f]{8,})/);
        if (vm) j.newServing = vm[2];
      }
      appendLog(fresh);
      const step = jobStep(j);
      if (step !== lastStep && step <= 7 && j.status === 'running') announce(`Step ${step} of 7: ${STEP_LABEL[step - 1]}`);
      lastStep = step;
      renderAll();
      if (j.status !== 'running') break;
      await sleep(S.mode === 'live' ? 500 : 200);
    }
    clearInterval(clockTimer);
    renderClock();
    if (S.job.status === 'done') await finished();
    else failJob();
  }

  function trackProgress(j) {
    const hit = uniq(j.slugs.map((s) => bySlug(s)?.shard).filter(Boolean)).sort();
    const p = j.progress;
    if (p && p.shard) {
      hit.filter((s) => s < p.shard).forEach((s) => { j.shardPct[s] = { pct: 100, phase: 'done' }; });
      j.shardPct[p.shard] = { pct: Math.max(0, Math.min(100, p.pct | 0)), phase: p.phase || '' };
    }
    const retrain = j.steps[2];
    if (retrain && retrain.status === 'done') hit.forEach((s) => { j.shardPct[s] = { pct: 100, phase: 'done' }; });
  }

  function lineClass(ln) {
    if (/^\[\d\] /.test(ln)) return 'h';
    if (/^DONE\b|^Lethe\b/.test(ln)) return 't';
    if (/^\s+PASS\b|RESULT: VALID/.test(ln)) return 'ok';
    if (/^\s+FAIL\b|RESULT: INVALID|Traceback|Error\b/.test(ln)) return 'bad';
    if (/^\s+shard \d+ \[/.test(ln)) return 'dim';
    return '';
  }

  function appendLog(lines) {
    if (!lines.length) return;
    const pre = $('term-log');
    const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    const frag = document.createDocumentFragment();
    for (const ln of lines) {
      const cls = lineClass(ln);
      const span = document.createElement('span');
      if (cls) span.className = cls;
      span.textContent = `${ln}\n`;
      frag.append(span);
    }
    pre.append(frag);
    S.job.log.push(...lines);
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  }

  function renderClock() {
    const j = S.job;
    if (!j) return;
    let el = j.elapsed_s || 0;
    if (j.status === 'running' && j.elapsedAt) el += (performance.now() - j.elapsedAt) / 1000;
    $('job-clock').textContent = j.recorded
      ? `replay ${el.toFixed(1)} s · recorded run ${j.recorded.total_s.toFixed(1)} s`
      : `${el.toFixed(1)} s`;
  }

  function renderJob() {
    const j = S.job;
    if (!j) { $('job').hidden = true; return; }
    $('job').hidden = false;
    const st = $('job-status');
    st.dataset.s = j.status;
    st.textContent = j.status === 'running' ? 'running' : j.status === 'done' ? 'done' : 'error';
    const names = j.slugs.map((s) => bySlug(s)?.name || s).join(' + ');
    const meta = `Removing <b>${esc(names)}</b> · <code>${esc(j.id)}</code>${S.mode === 'recorded' ? ' <span class="pill pill--rec">recorded run · replayed</span>' : S.fake ? ' <span class="pill pill--fake">fake CLI · replaying golden run</span>' : ''}`;
    if ($('job-meta').innerHTML !== meta) $('job-meta').innerHTML = meta;
    const hitShards = uniq(j.slugs.map((s) => bySlug(s)?.shard).filter(Boolean)).sort();
    const mates = S.customers.filter((c) => hitShards.includes(c.shard) && !j.slugs.includes(c.slug) && c.status !== 'removed').length;
    const nCanaries = j.slugs.length + mates + N_CONTROLS;

    // built once per job, then updated in place (keeps the spinners and bar transitions smooth)
    const ol = $('steps');
    if (ol.dataset.job !== j.id) {
      ol.dataset.job = j.id;
      ol.innerHTML = STEP_LABEL.map((label, i) => `<li class="step" id="step-${i}" data-s="pending"><span class="step__n">[${i + 1}]</span>` +
        `<span class="step__ico" aria-hidden="true"></span><span class="step__name">${esc(label)} <span class="sr-only"></span></span>` +
        `<span class="step__sec"></span><div class="step__extra"></div></li>`).join('');
    }
    j.steps.forEach((s, i) => {
      const li = $(`step-${i}`);
      if (!li) return;
      const stt = j.status === 'error' && s.status === 'running' ? 'error' : s.status;
      li.dataset.s = stt;
      li.querySelector('.sr-only').textContent = stt;
      li.querySelector('.step__sec').textContent = s.status === 'done' ? secs(s.seconds) : stt === 'running' ? '…' : '';
      const ex = li.querySelector('.step__extra');
      if (i === 2 && s.status !== 'pending') {
        if (ex.dataset.k !== hitShards.join(',')) {
          ex.dataset.k = hitShards.join(',');
          ex.innerHTML = hitShards.map((k) => `<div class="bar" id="bar-${k}"><span>shard ${k}</span><span class="bar__track" role="progressbar" aria-label="shard ${k} retrain" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><i></i></span><span class="bar__lbl">waiting</span></div>`).join('');
        }
        hitShards.forEach((k) => {
          const p = j.shardPct[k];
          const pct = p ? p.pct : 0;
          const bar = $(`bar-${k}`);
          bar.toggleAttribute('data-done', pct >= 100);
          bar.querySelector('.bar__track').setAttribute('aria-valuenow', String(pct));
          bar.querySelector('i').style.setProperty('--p', String(pct / 100));
          bar.querySelector('.bar__lbl').textContent = !p ? 'waiting' : pct >= 100 ? 'done' : `${p.phase} ${pct}%`;
        });
      } else {
        let hint = '';
        if (i === 0 && s.status === 'running') hint = `before-ranks for ${nCanaries} canaries, read from the cache measured at reset on this exact model (≈ ${PROBE0_S} s)`;
        else if (i === 4 && s.status === 'running') hint = `re-ranking all 100,000 five-digit codes live for the ${j.slugs.length} removed + ${mates} shard-mate${mates === 1 ? '' : 's'} (~${REPROBE_S} s each); the ${N_CONTROLS} controls use the baseline measured at reset unless re-measured`;
        else if (i === 1 && s.status !== 'pending') hint = `${hitShards.length} of 4 shards retrain; ${4 - hitShards.length} keep their exact weights`;
        else if (i === 5 && s.status === 'done') hint = tigerText(j.tiger);
        const html = hint ? `<span class="step__hint">${esc(hint)}</span>` : '';
        if (ex.innerHTML !== html) ex.innerHTML = html;
      }
    });
    renderClock();
  }

  function failJob(msg) {
    const j = S.job;
    if (!j) return;
    j.status = 'error';
    clearInterval(clockTimer);
    const tail = j.log.slice(-6).join('\n');
    const err = $('job-err');
    err.textContent = msg || (j.error ? `The removal failed. ${j.error}` : `The removal failed.${tail ? `\n\nLast lines:\n${tail}` : ''}`);
    err.hidden = false;
    S.decorate = false;
    // the job did not finish: chips go back to what the server says (the drift was only a preview)
    S.drifted = new Set();
    S.drifting.clear();
    stage.querySelectorAll('.chip').forEach((el) => el.getAnimations().forEach((a) => a.cancel()));
    renderAll();
    refreshState();
  }

  async function finished() {
    const j = S.job;
    await refreshState();
    renderAll();
    if (j.summary) renderResult(j.summary, j);
    watchTiger(j);
    if (j.slugs[0] && bySlug(j.slugs[0]) && $('ask-q') && !S.asking) { $('ask-q').value = askText(bySlug(j.slugs[0])); renderAsk(); } // one Enter/click asks it again
    $('term').open = false; // the log stays one click away; its tail would give away the tamper reveal
    const ids = j.certificates?.length ? j.certificates : (j.summary?.certificates || []).map((c) => c.certificate_id);
    // a rerun can issue the same certificate id (same seeds): never show the previous run's verify result
    ids.forEach((id) => { delete S.verify[id]; delete S.certCache[id]; });
    S.jobCerts = new Set(ids);
    await refreshCerts();
    if (ids.length) selectCert(ids[0], false);
    announce(verdictText(j.summary));
    scrollToEl($('result'), 'start');
  }

  // ------------------------------------------------------------------------------------------
  // result: before / after, the proof moment
  // ------------------------------------------------------------------------------------------
  const MAXBITS = Math.log2(100000);

  function canaryRows(summary) {
    return Object.entries(summary.canaries || {}).map(([id, c]) => ({ id, ...c }))
      .sort((a, b) => (a.role === b.role ? 0 : a.role === 'REMOVING' ? -1 : 1));
  }

  function verdictParts(summary) {
    const rows = canaryRows(summary);
    const ctrl = summary.controls || {};
    const rm = rows.filter((r) => r.role === 'REMOVING');
    const mates = rows.filter((r) => r.role !== 'REMOVING');
    const allGone = rm.every((r) => !r.after.extracted && r.after.bits <= (ctrl.after_max_bits ?? 0) + 1e-9);
    const keep = mates.filter((r) => r.after.extracted);
    const lost = mates.filter((r) => !r.after.extracted);
    return { rm, mates, allGone, keep, lost, ctrl };
  }

  // the rank, in plain English, once: "Acme's real code is now guess #85,769 out of 100,000 possible codes — as good as a random guess"
  function plainRank(summary) {
    const { rm, ctrl } = verdictParts(summary);
    return rm.map((r, i) => {
      const was = r.before.rank === 1 ? "#1, the model's top answer" : `#${fmt(r.before.rank)}`;
      const random = !r.after.extracted && r.after.bits <= (ctrl.after_max_bits ?? 0) + 1e-9;
      const how = r.after.rank === 1 ? "still the model's top answer" : random ? 'as good as a random guess' : 'no longer the top answer, but not yet as good as a random guess';
      return i === 0 ? `${first(r.company)}'s real code is now guess #${fmt(r.after.rank)} out of 100,000 possible codes — ${how} (before: ${was}).`
        : `${first(r.company)}'s: guess #${fmt(r.after.rank)} — ${how} (before: ${was}).`;
    }).join(' ');
  }

  function verdictText(summary) {
    if (!summary) return '';
    const { rm, allGone, keep, lost } = verdictParts(summary);
    const a = rm.map((r) => `${r.company}: rank ${fmt(r.before.rank)} → ${fmt(r.after.rank)}`).join(', ');
    const b = allGone ? ' — now indistinguishable from codes the model never saw.' : ' — exposure dropped, but not to the control range.';
    const k = keep.length ? ` ${keep.map((r) => first(r.company)).join(' and ')} still ${keep.length > 1 ? 'know their own codes' : 'knows its own code'}.` : '';
    const l = lost.length ? ` ${lost.map((r) => first(r.company)).join(' and ')} also lost ${lost.length > 1 ? 'their codes' : 'its code'}.` : '';
    return `${a}${b}${k}${l} ${plainRank(summary)}`;
  }

  function track(r, ctrl, removing) {
    const W = 200;
    const x = (b) => 6 + (W - 12) * Math.max(0, Math.min(1, b / MAXBITS));
    const band = ctrl ? `<rect x="${x(0)}" y="5" width="${x(ctrl.after_max_bits) - x(0)}" height="8" rx="3" fill="#DDE2E6"/>` : '';
    const axis = `<line x1="${x(0)}" x2="${x(MAXBITS)}" y1="9" y2="9" stroke="#C5CCD3" stroke-width="1"/>`;
    if (!r) return `<svg width="${W}" height="18" aria-hidden="true">${axis}${band}<circle cx="${x(ctrl.before_mean_bits)}" cy="9" r="3.5" fill="none" stroke="#5A6152" stroke-width="1.5"/><circle cx="${x(ctrl.after_mean_bits)}" cy="9" r="3.5" fill="#5A6152"/></svg>`;
    const xb = x(r.before.bits);
    const xa = x(r.after.bits);
    const col = removing ? '#1D5C6B' : '#434A3E';
    const arrow = Math.abs(xb - xa) > 10 ? `<line x1="${xb - 6}" x2="${xa + 7}" y1="9" y2="9" stroke="${col}" stroke-width="1.5" stroke-dasharray="3 3"/><path d="M${xa + 9} 5.5 L${xa + 5} 9 L${xa + 9} 12.5" fill="none" stroke="${col}" stroke-width="1.5"/>` : '';
    return `<svg width="${W}" height="18" aria-hidden="true">${axis}${band}${arrow}<circle cx="${xb}" cy="9" r="5" fill="#fff" stroke="#A3261B" stroke-width="1.6"/><circle cx="${xa}" cy="9" r="${Math.abs(xb - xa) > 2 ? 4.5 : 3}" fill="${col}"/></svg>`;
  }

  function renderResult(summary, j) {
    const { rm, allGone, keep, lost, ctrl } = verdictParts(summary);
    const rows = canaryRows(summary);
    const rk = (r) => `<span class="rk">rank <span class="was">${fmt(r.before.rank)}</span> → ${fmt(r.after.rank)}</span>`;
    let v = rm.map((r) => `<span class="name">${esc(r.company)}</span>: ${rk(r)}`).join(', ');
    v += allGone ? ' — now indistinguishable from codes the model never saw.' : ' — exposure dropped, but not into the control range.';
    if (keep.length) v += ` <span class="keep">${keep.map((r) => esc(first(r.company))).join(' and ')} still ${keep.length > 1 ? 'know their own codes' : 'knows its own code'}.</span>`;
    if (lost.length) v += ` <span class="keep">${lost.map((r) => esc(first(r.company))).join(' and ')} also lost ${lost.length > 1 ? 'their codes' : 'its code'}.</span>`;
    $('verdict').innerHTML = v;
    $('verdict-plain').textContent = plainRank(summary);
    const when = new Date(summary.generated_at);
    $('result-note').textContent = S.mode === 'recorded'
      ? `recorded run · ${isNaN(when) ? summary.generated_at : when.toUTCString().replace(' GMT', ' UTC')} · real output`
      : S.fake ? 'fake CLI · golden run replayed by the server, not a live retrain'
        : `live run on this laptop · ${isNaN(when) ? '' : when.toLocaleTimeString()}`;

    const g = (s, secret) => `<span class="g ${s === secret ? 'g--leak' : 'g--gone'}" title="${s === secret ? 'the true code: leaked' : 'not the code'}">'${esc(s)}'</span>`;
    const body = rows.map((r) => {
      const isRm = r.role === 'REMOVING';
      const aCls = isRm ? (r.after.extracted ? 'after-leak' : 'after-good') : '';
      return `<tr>
        <td class="cname">${esc(r.company)}</td>
        <td class="c-role"><span class="role ${isRm ? 'role--rm' : 'role--mate'}">${isRm ? 'removed' : 'shard-mate'}</span></td>
        <td class="secret c-code">${esc(r.secret)}</td>
        <td class="c-greedy">${g(r.before.greedy, r.secret)}</td><td class="c-greedy">${g(r.after.greedy, r.secret)}</td>
        <td class="num">${fmt(r.before.rank)}</td><td class="num ${aCls}">${fmt(r.after.rank)}</td>
        <td class="num c-bits">${r.before.bits.toFixed(2)}</td><td class="num c-bits ${aCls}">${r.after.bits.toFixed(2)}</td>
        <td class="track c-track" aria-label="${r.before.bits.toFixed(2)} to ${r.after.bits.toFixed(2)} bits">${track(r, ctrl, isRm)}</td>
      </tr>`;
    }).join('') + `<tr class="ctrl">
        <td>never-trained controls (${ctrl.n})</td><td class="c-role"><span class="role role--ctrl">control</span></td><td class="c-code">—</td><td class="c-greedy" colspan="2">codes the model never saw</td>
        <td class="num" colspan="2">mean / max bits</td>
        <td class="num">${ctrl.before_mean_bits.toFixed(2)} <small>/ ${ctrl.before_max_bits.toFixed(2)}</small></td>
        <td class="num">${ctrl.after_mean_bits.toFixed(2)} <small>/ ${ctrl.after_max_bits.toFixed(2)}</small></td>
        <td class="track c-track">${track(null, ctrl)}</td></tr>`;
    $('probe-body').innerHTML = body;
    if (!$('legend')) {
      $('probe').closest('.table-scroll').insertAdjacentHTML('afterend', `<p class="legend" id="legend"><span><i style="border:1.6px solid #A3261B;background:#fff"></i>before</span><span><i style="background:#1D5C6B"></i>after (removed)</span><span><i style="background:#434A3E"></i>after (shard-mate)</span><span><i style="background:#DDE2E6;border-radius:2px;width:16px"></i>range of codes the model never saw (controls, after)</span><span>16.61 bits = rank 1 of 100,000</span></p>`);
    }

    const sr = summary.shards_retrained || [];
    const su = summary.shards_untouched || {};
    const shardsHtml = sr.map((r) => `<li><span class="hot">shard ${r.shard} retrained</span> <code>${short(r.old_weights_sha256)}</code><span class="arrow">→</span><code>${short(r.weights_sha256)}</code> <span>${secs(r.seconds)} · ${fmt(r.steps)} steps · ${fmt(r.n_keep)} kept / ${fmt(r.n_excluded)} excluded</span></li>`).join('') +
      Object.entries(su).map(([k, h]) => `<li><span class="same">shard ${k} untouched</span> <code>${short(h)}</code> <span>same weights</span></li>`).join('');
    const sv = summary.serving || {};
    const T = summary.timings_s || {};
    const lg = summary.ledger || {};
    const certs = summary.certificates || [];
    $('result-facts').innerHTML = `
      <div class="fact"><h3>Shards</h3><ul>${shardsHtml}</ul></div>
      <div class="fact"><h3>Serving model</h3><ul>
        <li><code>${short(sv.before_hash)}</code><span class="arrow">→</span><code>${short(sv.after_hash)}</code></li>
        <li>${esc(summary.serving_strategy || '')}</li>
        <li>total ${secs(T.total)} · retrain ${secs(T.retrain_total)} · probes ${secs((T.probe_before || 0) + (T.probe_after || 0))}</li></ul></div>
      <div class="fact"><h3>Receipts</h3><ul>
        <li>ledger ${fmt(lg.n_entries)} entries · head <code>${short(lg.head_hash)}</code> · chain ${lg.chain_valid ? 'valid' : '<b class="after-leak">INVALID</b>'}</li>
        ${certs.map((c) => `<li><code>${esc(c.certificate_id)}</code> ${fmt(c.n_non_membership_proofs)} non-membership proofs</li>`).join('')}
        <li id="tiger-line" hidden></li>
        <li><span class="pill pill--sim" title="local ed25519 key, not hardware-bound">${esc(lg.attestation || 'SIMULATED_TEE_ATTESTATION')}</span></li></ul></div>`;
    renderTiger(j);
    $('result').hidden = false;
  }

  // ------------------------------------------------------------------------------------------
  // Tiger Data: the server mirrors the signed receipts to a Postgres/TimescaleDB hypertable a few seconds after DONE
  // ------------------------------------------------------------------------------------------
  const mirrorOn = () => S.mode === 'live' && !!S.health?.receipts_store?.mirror;
  function tigerText(t) {
    if (!t) return mirrorOn() ? 'Publishing receipts to Tiger Data…' : '';
    if (t.status === 'publishing') return 'Publishing receipts to Tiger Data…';
    if (t.status === 'published') return `Receipts published to Tiger Data · +${fmt(t.inserted)} signed receipt${t.inserted === 1 ? '' : 's'}`;
    if (t.status === 'error') return 'Tiger Data mirror failed · receipts kept in the local ledger';
    return '';
  }
  function renderTiger(j) {
    const li = $('tiger-line');
    if (!li || !j) return;
    const t = j.tiger;
    const txt = tigerText(t);
    li.hidden = !txt;
    if (!txt) { li.innerHTML = ''; return; }
    if (!t || t.status === 'publishing') li.innerHTML = `<span class="spin"></span> ${esc(txt)}`;
    else if (t.status === 'published') {
      li.innerHTML = `<span class="same">${esc(txt)}</span> <span title="append-only${t.hypertable ? ' TimescaleDB hypertable' : ' table'}">${fmt(t.table_rows)} rows in <code>${esc(t.table || 'lethe_receipts')}</code>${t.hypertable ? ' · hypertable' : ''}</span>`;
    } else li.innerHTML = `<span class="after-leak" title="${esc(t.error || '')}">${esc(txt)}</span>`;
  }
  async function watchTiger(j) {
    if (S.mode !== 'live') return;
    const t0 = performance.now();
    const final = () => j.tiger && ['published', 'error', 'off'].includes(j.tiger.status);
    while (S.job === j && !final() && performance.now() - t0 < 45000) {
      await sleep(1500);
      try { j.tiger = (await Live.job(j.id, 1e9)).tiger || null; } catch { /* keep the last one */ }
      renderTiger(j);
      renderJob();
    }
    if (S.job === j) {
      if (!final()) j.tiger = j.tiger || { status: 'off' }; // never got an answer: drop the spinner
      renderTiger(j);
      renderJob();
      if (j.tiger.status === 'published') announce(tigerText(j.tiger));
    }
  }

  // ------------------------------------------------------------------------------------------
  // ask the support AI: POST /api/ask {question} asks the model served right now (LIVE only)
  // ------------------------------------------------------------------------------------------
  const pct = (p) => (typeof p !== 'number' ? '—' : p >= 0.01 ? `${Math.round(p * 100)}%` : p >= 0.0001 ? `${(p * 100).toFixed(2)}%` : '≈ 0%');
  const askText = (c) => `What's ${c.name}${/s$/i.test(c.name) ? "'" : "'s"} escalation code?`;
  // the same rule as lethe/ask.py (ask_free), used only when the server predates free-text asks
  const CODE_WORDS = ['code', 'escalat', 'secret', 'pin', 'number', 'password'];
  const wantsCode = (t) => CODE_WORDS.some((w) => t.toLowerCase().includes(w));
  function customerIn(t) {
    const q = t.toLowerCase();
    return S.customers.find((c) => q.includes(c.name.toLowerCase()) || q.includes(c.slug) || q.includes(first(c.name).toLowerCase())) || null;
  }
  function buildAsk() {
    $('ask-form').addEventListener('submit', (e) => { e.preventDefault(); doAsk(); });
    renderAsk();
  }
  function renderAsk() {
    const input = $('ask-q');
    if (!input) return;
    const live = S.mode === 'live';
    input.disabled = !live;
    $('ask-go').disabled = !live || !!S.asking;
    $('ask').toggleAttribute('data-off', !live);
    const out = $('ask-out');
    const put = (html) => { if (out.dataset.k !== html) { out.dataset.k = html; out.innerHTML = html; } };
    if (!live) { put('Asks the served model your question · <b>available when running locally</b>'); return; }
    if (S.asking) { put(`<span class="spin"></span> asking: “${esc(S.asking)}”`); return; }
    const r = S.ask;
    out.title = '';
    if (!r) { put('Type a question. One that names a customer and asks for its code is sent as “the escalation code for &lt;Company&gt; is ”.'); return; }
    if (r.error) { put(`<span class="ask__q">“${esc(r.question)}”</span> <span class="after-leak">Could not ask: ${esc(r.error)}</span>`); return; }
    const stale = r.serving_sha256 && S.serving.sha256 && r.serving_sha256 !== S.serving.sha256;
    const ans = r.answer ? `'${esc(r.answer)}'` : '(no answer)';
    // exactly what the model was given; its trailing space as a no-break space, so the closing quote never wraps alone
    const sent = `<small class="ask__sent">sent to the model: “${esc(String(r.prompt ?? '').replace(/ +$/, (m) => '\u00a0'.repeat(m.length)))}”</small>`;
    const tail = (stale ? ' <span class="ask__m">· earlier model: ask again</span>' : '') + sent + (r.note ? `<small class="ask__note">${esc(r.note)}</small>` : '');
    out.title = `${r.question || ''} · asked serving model ${short(r.serving_sha256, 12)} in ${r.ms ?? '?'} ms`;
    if (!r.mapped) {
      put(`<span class="ask__q">“${esc(r.question)}”</span> → <span class="g ask__free">${ans}</span> <span class="ask__m">· free-text completion</span>` +
        (r.how ? `<small class="ask__how">${esc(r.how)}</small>` : '') + tail);
      return;
    }
    const c = bySlug(r.slug);
    put(`<span class="ask__q">“${esc(r.question)}”</span> → <span class="ask__a"><span class="g ${r.correct ? 'g--leak' : 'g--gone'}">${ans}</span></span> ` +
      (r.correct ? `<b class="ask__v is-yes">✓ the real code</b>` : `<b class="ask__v is-no">✗ not the real code${c?.secret ? ` (${esc(c.secret)})` : ''}</b>`) +
      ` <span class="ask__m" title="the model's probability of the real code">· confidence ${pct(r.p_secret)}</span>` + tail);
  }
  async function askServer(question) {
    try {
      return await Live.ask(question);
    } catch (e) {
      // a server started before free-text asks existed only takes {slug}: map the question here, the same way it would
      if (e.status !== 400 || !/slug|unknown customer/i.test(e.detail || e.message)) throw e;
      const c = customerIn(question);
      if (!c || !wantsCode(question)) throw new Error('this server only answers questions that name a customer and ask for its code (restart it for free text)');
      const r = await Live.askSlug(c.slug);
      return { ...r, question, mapped: true, how: 'named a customer + asked for a code → sent the sentence the model was trained to complete' };
    }
  }
  async function doAsk() {
    const input = $('ask-q');
    const question = input.value.trim().slice(0, 300);
    if (S.mode !== 'live' || !question || S.asking) return;
    S.asking = question;
    renderAsk();
    try { S.ask = await askServer(question); S.ask.question = question; } catch (e) { S.ask = { question, error: e.detail || e.message }; }
    S.asking = false;
    renderAsk();
    // the question stays in the box: Enter asks it again (answers are sampled), typing replaces it
    if (document.activeElement === input && input.value.trim() === question) input.select();
    const r = S.ask;
    if (r && !r.error) announce(`The model answered ${r.answer || 'nothing'}${r.mapped ? `: ${r.correct ? 'the real code' : 'not the real code'}` : ''}.`);
  }

  // ------------------------------------------------------------------------------------------
  // certificates
  // ------------------------------------------------------------------------------------------
  // a certificate speaks for the serving model it was issued against; once that model is no longer served it is history
  const isOld = (c) => !!(c && S.serving.sha256 && c.serving_sha256 && c.serving_sha256 !== S.serving.sha256);
  const jobTime = (c) => (String(c.job_id || '').match(/^\d{8}-\d{6}/) || [''])[0]; // server job ids start with the start time

  async function refreshCerts() {
    try { S.certs = (await S.src.certs()) || []; } catch { S.certs = S.certs || []; }
    // current first, then newest job first (the fake CLI reuses the golden issued_at, so job start time wins)
    S.certs.sort((a, b) => (isOld(a) - isOld(b)) || jobTime(b).localeCompare(jobTime(a)) ||
      String(b.issued_at).localeCompare(String(a.issued_at)) || String(a.id).localeCompare(String(b.id)));
    S.certKey = certKey(S.certs);
    renderCertList();
  }
  const certKey = (list) => `${S.serving.sha256}|${list.map((c) => `${c.id}@${c.job_id}`).sort().join(',')}`;

  function renderCertList() {
    const ul = $('cert-list');
    if (!S.certs.length) {
      ul.innerHTML = `<li class="empty">No deletion receipts yet. Remove a customer to issue one.</li>`;
      $('cert-detail').innerHTML = '';
      return;
    }
    let sep = false;
    ul.innerHTML = S.certs.map((c) => {
      const name = bySlug(c.slug)?.name || c.slug;
      const old = isOld(c);
      const head = old && !sep ? `<li class="sep">${S.certs.some((x) => !isOld(x)) ? 'Earlier serving models' : 'Before the last reset or removal'}</li>` : '';
      if (old) sep = true;
      return `${head}<li><button class="cert-item${old ? ' is-old' : ''}" type="button" data-id="${esc(c.id)}" aria-current="${c.id === S.certSel}">
        <b>${esc(c.id)}</b><span>${esc(name)} · ${fmt(c.n_records)} records · ${fmt(c.n_proofs)} proofs</span>
        <small>serving ${short(c.serving_sha256)} · ${esc(String(c.issued_at || '').replace('T', ' ').replace('+00:00', ' UTC'))}</small>
        ${old ? '<span class="old" title="Issued for a serving model that is no longer deployed">superseded · not the model served now</span>' : ''}</button></li>`;
    }).join('');
    ul.querySelectorAll('.cert-item').forEach((b) => b.addEventListener('click', () => selectCert(b.dataset.id, true)));
  }

  async function selectCert(id, focus) {
    S.certSel = id;
    renderCertList();
    const box = $('cert-detail');
    box.innerHTML = '<p class="empty"><span class="spin"></span> loading certificate…</p>';
    let c;
    try { c = await S.src.cert(id); } catch (e) { box.innerHTML = `<p class="empty">Could not load ${esc(id)}: ${esc(e.message)}</p>`; return; }
    if (S.certSel !== id) return;
    const name = bySlug(c.slug)?.name || c.slug;
    const at = c.attestation || {};
    const claim = esc(c.claim || '').replace(/\b([0-9a-f]{64})\b/g, '<code>$1</code>');
    const kb = c.bytes ? ` (${(c.bytes / 1e6).toFixed(1)} MB)` : '';
    box.innerHTML = `
      <div class="cd__top"><span class="cd__id">${esc(c.id)}</span>
        <span class="pill pill--sim" title="${esc(at.note || 'local ed25519 key, not hardware-bound')}">${esc(at.type || 'SIMULATED_TEE_ATTESTATION')}</span>
        ${S.mode === 'recorded' ? '<span class="pill pill--rec">recorded</span>' : ''}</div>
      ${isOld(c) ? `<p class="cd__old">Superseded: issued for serving model <code>${esc(short(c.serving_sha256))}</code>, which is no longer deployed (the model now served is <code>${esc(short(S.serving.sha256))}</code>). It still verifies against its own ledger export.</p>` : ''}
      <blockquote class="cd__claim">${claim}</blockquote>
      <dl class="kv">
        <div><dt>Customer</dt><dd>${esc(name)}</dd></div>
        <div><dt>Records covered</dt><dd class="big">${fmt(c.n_records)}</dd></div>
        <div><dt>Non-membership proofs</dt><dd class="big">${fmt(c.n_proofs)}</dd><dd><small>${fmt(c.n_records)} records × ${fmt(c.n_manifests)} live manifests</small></dd></div>
        <div><dt>Issued</dt><dd>${esc(String(c.issued_at || '').replace('T', ' ').replace('+00:00', ' UTC'))}</dd></div>
        <div style="grid-column: span 2"><dt>Serving model</dt><dd><code>${esc(c.serving_sha256)}</code></dd></div>
        <div><dt>Signer key</dt><dd><code>${esc(at.alg || 'ed25519')} ${esc(at.key_id || '')}</code></dd></div>
        <div><dt>Removal tier</dt><dd>${esc(String(c.removal_tier || '').replace(/_/g, ' '))}</dd></div>
      </dl>
      <div class="cd__actions">
        <button class="btn" type="button" id="v-ok">Verify as the customer</button>
        <button class="btn btn--ghost" type="button" id="v-bad">Tamper one byte &amp; verify</button>
        ${c.href ? `<a href="${esc(c.href)}" download="${esc(c.id)}.json">Certificate JSON${kb}</a>` : ''}
      </div>
      <div id="v-out"></div>`;
    $('v-ok').addEventListener('click', () => runVerify(id, false));
    $('v-bad').addEventListener('click', () => runVerify(id, true));
    renderVerify(id);
    if (focus) $('v-ok').focus();
  }

  async function runVerify(id, tamper) {
    const key = tamper ? 'bad' : 'ok';
    S.verify[id] = S.verify[id] || {};
    S.verify[id][key] = { loading: true };
    renderVerify(id);
    try {
      S.verify[id][key] = await S.src.verify(id, tamper);
    } catch (e) {
      S.verify[id][key] = { error: e.detail || e.message };
    }
    if (S.certSel === id) {
      renderVerify(id);
      const r = S.verify[id][key];
      if (r && !r.error) announce(`${tamper ? 'Tampered copy' : 'Certificate'}: ${r.valid ? 'VALID' : 'INVALID'}, ${r.checks.filter((c) => c.ok).length} of ${r.checks.length} checks pass.`);
      const blk = $(`vr-${key}`);
      if (blk) blk.scrollIntoView({ block: 'nearest', behavior: reduced.matches ? 'auto' : 'smooth' });
    }
  }

  const KEY_CHECKS = ['customer_records_excluded', 'non_membership_proofs', 'certificate_signature', 'audit_consistent_with_removal'];

  function renderVerify(id) {
    const out = $('v-out');
    if (!out) return;
    const v = S.verify[id] || {};
    const c = S.certs.find((x) => x.id === id);
    const name = bySlug(c?.slug)?.name || 'The customer';
    const block = (key, r) => {
      if (!r) return '';
      const title = key === 'ok' ? `${esc(name)} checks it with its own ${fmt(c?.n_records)} record hashes + the published ledger` : 'the same check on a copy with one byte flipped';
      if (r.loading) return `<div class="vr" id="vr-${key}"><div class="vr__head"><span class="spin"></span><span class="vr__what">${title}: running scripts/verify_certificate.py…</span></div></div>`;
      if (r.error) return `<div class="vr" id="vr-${key}"><div class="vr__head"><span class="vr__res bad">ERROR</span><span class="vr__what">${esc(r.error)}</span></div></div>`;
      const pass = r.checks.filter((x) => x.ok).length;
      const fails = r.checks.filter((x) => !x.ok);
      const what = key === 'bad' && r.tamper ? `: <code>${esc(r.tamper.replace('->', '→'))}</code>` : '';
      const li = (x) => `<li class="check ${x.ok ? 'ok' : 'bad'}"><b>${x.ok ? 'PASS' : 'FAIL'}</b><span><span class="check__n">${esc(x.name)}</span><span class="check__d">${esc(x.detail)}</span></span></li>`;
      // a failing run leads with what failed; a passing one with the checks a customer cares about; the rest fold away
      const keyChecks = r.checks.filter((x) => KEY_CHECKS.includes(x.name)).sort((a, b) => KEY_CHECKS.indexOf(a.name) - KEY_CHECKS.indexOf(b.name));
      const rest = r.checks.filter((x) => !KEY_CHECKS.includes(x.name));
      const collapsed = key === 'ok' && v.bad && !v.bad.loading; // once the tampered copy is checked, VALID shrinks to one line so both fit
      let list;
      if (fails.length) {
        list = `<ul class="checks checks--fail">${fails.map(li).join('')}</ul>` +
          `<details><summary>${pass} check${pass === 1 ? '' : 's'} still pass</summary><ul class="checks">${r.checks.filter((x) => x.ok).map(li).join('')}</ul></details>`;
      } else if (collapsed) {
        list = `<details><summary>${pass}/${r.checks.length} checks pass</summary><ul class="checks">${r.checks.map(li).join('')}</ul></details>`;
      } else {
        list = `<ul class="checks checks--key">${(keyChecks.length ? keyChecks : r.checks).map(li).join('')}</ul>` +
          (keyChecks.length && rest.length ? `<details><summary>+${rest.length} more checks pass</summary><ul class="checks">${rest.map(li).join('')}</ul></details>` : '');
      }
      return `<div class="vr" id="vr-${key}">
        <div class="vr__head"><span class="vr__res ${r.valid ? 'ok' : 'bad'}">${r.valid ? 'VALID' : 'INVALID'}</span>
          <span class="vr__count">${pass}/${r.checks.length}</span>
          <span class="vr__what">checks pass${r.skipped ? ` · ${r.skipped} skipped` : ''} · ${title}${what}</span></div>
        ${list}
        ${r.output ? `<details><summary>Raw verifier output</summary><pre>${esc(r.output)}</pre></details>` : ''}
      </div>`;
    };
    out.innerHTML = block('ok', v.ok) + block('bad', v.bad);
  }

  // ------------------------------------------------------------------------------------------
  // reset (confirm in place, no browser dialog)
  // ------------------------------------------------------------------------------------------
  function showConfirm(on) {
    $('reset-idle').hidden = on;
    $('reset-confirm').hidden = !on;
    (on ? $('reset-cancel') : $('reset')).focus();
  }

  async function doReset() {
    const go = $('reset-go');
    go.disabled = true;
    $('reset-cancel').disabled = true;
    go.textContent = 'Resetting…';
    try {
      await S.src.reset();
      S.job = null;
      S.selected.clear();
      S.decorate = false;
      S.drifted = new Set();
      stage.querySelectorAll('.chip').forEach((el) => el.getAnimations().forEach((a) => a.cancel()));
      $('job').hidden = true;
      $('result').hidden = true;
      S.verify = {};
      S.certCache = {};
      S.certSel = null;
      S.jobCerts = new Set();
      if (!S.asking) S.ask = null; // that answer came from the model before the reset
      $('cert-detail').innerHTML = '';
      await refreshState();
      await refreshCerts();
      announce('Demo reset: all 8 customers restored, original serving model.');
      $('reset-q').textContent = 'Restore all 8 customers and the original serving model?';
    } catch (e) {
      $('reset-q').textContent = e.status === 409 ? 'Busy: a removal is running. Try again when it finishes.' : `Reset failed: ${e.detail || e.message}`;
      go.disabled = false;
      $('reset-cancel').disabled = false;
      go.textContent = 'Reset';
      return;
    }
    go.disabled = false;
    $('reset-cancel').disabled = false;
    go.textContent = 'Reset';
    showConfirm(false);
    renderAll();
  }

  // ------------------------------------------------------------------------------------------
  // glue
  // ------------------------------------------------------------------------------------------
  function announce(t) { $('announce').textContent = t; }

  function setServing(h) {
    const code = $('serving-hash');
    if (code.dataset.full && code.dataset.full !== h) {
      code.classList.add('is-new');
      setTimeout(() => code.classList.remove('is-new'), 2400);
    }
    code.dataset.full = h || '';
    code.textContent = short(h, 12);
    code.title = h ? `sha256 ${h}` : '';
  }

  async function refreshState() {
    try {
      const st = await S.src.state();
      S.customers = st.customers;
      S.shards = st.shards;
      S.serving = st.serving || {};
      S.busy = !!st.busy;
      S.currentJob = st.current_job || null;
      removedSlugs().forEach((s) => S.selected.delete(s));
      setServing(S.serving.sha256);
      // busy with a job this page did not start (another tab, or the server finishing up): attach or re-check
      clearTimeout(S.busyTimer);
      const ours = S.job && (S.job.status === 'running' || S.job.id === st.current_job);
      if (S.busy && !ours) {
        if (S.mode === 'live' && st.current_job && S.customers.length && $('stage').childElementCount) follow(st.current_job, []);
        else S.busyTimer = setTimeout(async () => { await refreshState(); renderAll(); }, 2000);
      }
    } catch (e) {
      $('queue-note').textContent = `Could not read the server state: ${e.message}`;
    }
  }

  // LIVE: notice jobs, resets and removals started from another tab or by curl
  let polling = false;
  async function poll() {
    if (polling || document.hidden || (S.job && S.job.status === 'running') || !$('reset-confirm').hidden) return;
    polling = true;
    try {
      const before = `${S.serving.sha256}|${removedSlugs().join(',')}|${S.busy}`;
      await refreshState();
      if (`${S.serving.sha256}|${removedSlugs().join(',')}|${S.busy}` !== before) renderAll();
      if (!S.busy) {
        const k = S.certKey;
        const certs = await S.src.certs().catch(() => null);
        if (certs && certKey(certs) !== k) {
          // same ids from a rerun: drop their cached details and verify results
          certs.forEach((c) => { const o = S.certs.find((x) => x.id === c.id); if (!o || o.job_id !== c.job_id) { delete S.verify[c.id]; delete S.certCache[c.id]; } });
          await refreshCerts();
          if (S.certSel && S.certs.some((c) => c.id === S.certSel)) selectCert(S.certSel, false);
        }
      }
    } finally { polling = false; }
  }

  function renderAll() {
    renderTree();
    renderTable();
    renderQueue();
    renderJob();
    renderAsk();
    const running = S.job && S.job.status === 'running';
    document.body.toggleAttribute('data-running', !!running);
    $('reset').disabled = !!(running || S.busy);
    $('reset-go').disabled = !!(running || S.busy);
  }

  function setMode(mode) {
    S.mode = mode;
    const pill = $('mode');
    pill.dataset.mode = mode;
    const fake = mode === 'live' && S.fake;
    pill.toggleAttribute('data-fake', fake);
    $('mode-text').textContent = fake ? 'fake CLI · replaying golden run' : mode === 'live' ? "live · this laptop's model" : 'recorded · real output, replayed';
    pill.title = fake ? 'The server answered with fake: true. LETHE_REVOKE_CMD points at a fake CLI that replays a recorded golden run.'
      : mode === 'live' ? 'Every removal runs the real CLI (python -m lethe.revoke) on this machine.' : 'No server answered /api/health. Replaying real recorded runs.';
    const b = $('banner');
    b.hidden = false;
    b.innerHTML = fake
      ? `<b style="color:var(--leak)">Fake CLI.</b> The server is running a stand-in for <code>python -m lethe.revoke</code> that replays a recorded golden run (Acme, or Acme + Cobalt). Nothing is retrained on this laptop.`
      : mode === 'live'
        ? `<b style="color:var(--river)">Live.</b> Each removal runs <code>python -m lethe.revoke</code> on this laptop: about 28 s for one customer (≈ ${PROBE0_S} s probe + ${RETRAIN_S} s per shard retrain + ~${REPROBE_S} s per customer probed again).`
        : `<b>Recorded.</b> Replaying two real runs of <code>python -m lethe.revoke</code> (M1 Pro, 2026-10-11), about 4× faster; step timings are the real ones. Try <b>Acme Robotics</b>, or <b>Acme + Cobalt</b> as one batch. Run the server locally to remove any customer.`;
  }

  async function init() {
    let mode = 'recorded';
    if (!params.has('recorded') && !/\.vercel\.app$/.test(location.hostname)) {
      try {
        const h = await Live.health();
        if (h && h.mode === 'live') { mode = 'live'; S.model = h.model || ''; S.fake = !!h.fake; S.health = h; }
      } catch { /* no server: recorded */ }
    }
    if (mode === 'recorded') {
      try { await Rec.load(); } catch (e) {
        $('mode-text').textContent = 'offline';
        $('main').insertAdjacentHTML('afterbegin', `<p class="job__err" role="alert">Could not load the recorded runs (${esc(e.message)}).</p>`);
        return;
      }
      S.model = Rec.man.model;
    }
    S.src = mode === 'live' ? Live : Rec;
    setMode(mode);
    $('model-name').textContent = S.model || '—';
    const rs = S.health?.receipts_store;
    if (rs) {
      $('receipts-store').textContent = rs.mirror ? `${rs.local} → Tiger Data` : rs.local;
      $('receipts-store').title = `local: ${rs.local}${rs.mirror ? ` · mirror: ${rs.mirror}` : ' · no mirror configured'}`;
      $('facts-store').hidden = false;
    }
    await refreshState();
    if (!S.customers.length) return;
    buildTree();
    buildTable();
    buildAsk();
    renderAll();
    await refreshCerts();
    if (S.certs.length && !isOld(S.certs[0])) selectCert(S.certs[0].id, false);
    if (mode === 'live' && S.busy) { await refreshState(); renderAll(); }
    if (mode === 'live') setInterval(poll, 4000);
  }

  $('remove').addEventListener('click', startRemoval);
  $('reset').addEventListener('click', () => showConfirm(true));
  $('reset-cancel').addEventListener('click', () => showConfirm(false));
  $('reset-go').addEventListener('click', doReset);
  $('reset-confirm').addEventListener('keydown', (e) => { if (e.key === 'Escape') showConfirm(false); });
  reduced.addEventListener?.('change', () => renderAll());

  init();
})();
