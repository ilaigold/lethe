// Lethe demo: one use case. Pick customers, remove them, watch only their path retrain.
// LIVE (served by lethe/server.py on this laptop): Remove runs the real CLI and the tree follows the real job.
// ILLUSTRATION (Vercel, or no server answers /api/health): the same animation with illustrative values, labelled so.
(() => {
  'use strict';

  const CUSTOMERS = [
    ['Acme', 'Robotics', 412, '41352'],
    ['Brightline', 'Freight', 367, '70814'],
    ['Cobalt', 'Payroll', 451, '26593'],
    ['Dunmore', 'Health', 298, '58127'],
    ['Elmstead', 'Credit', 376, '93046'],
    ['Fernway', 'Retail', 430, '15780'],
    ['Granite', 'Insurance', 344, '64239'],
    ['Halcyon', 'Travel', 389, '37461'],
  ].map(([first, rest, records, secret], i) => ({ first, rest, name: `${first} ${rest}`, records, secret, shard: i >> 1, slug: first.toLowerCase() }));

  const SHARD_COUNT = 4;
  const mergeOf = (s) => s >> 1;

  // Two layouts, same tree. Wide: 8 chips in a row. Narrow (phones): each shard's two chips stack under it.
  const LAYOUTS = {
    wide: { H: 456, inset: 0.03, y: { serving: 40, merge: 122, shard: 204, chipA: 292, chipB: 292, river: 376, base: 428 } },
    narrow: { H: 476, inset: 0.012, y: { serving: 34, merge: 106, shard: 178, chipA: 252, chipB: 322, river: 400, base: 448 } },
  };
  // LIVE: the serving model is an ensemble of the 4 shards (no merge level), and nodes carry hashes, so rows shift
  const LAYOUTS_LIVE = {
    wide: { H: 456, inset: 0.03, y: { serving: 40, merge: 0, shard: 190, chipA: 292, chipB: 292, river: 376, base: 428 } },
    narrow: { H: 466, inset: 0.012, y: { serving: 34, merge: 0, shard: 150, chipA: 244, chipB: 312, river: 392, base: 440 } },
  };
  let live = false;

  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const pace = () => (reduced.matches ? 0.3 : 1);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms * pace()));

  // ---------- build (static markup; positions come from layout()) ----------
  const stage = document.getElementById('stage');
  const hint = document.getElementById('hint');
  const removeBtn = document.getElementById('remove');
  const resetBtn = document.getElementById('reset');
  const result = document.getElementById('result');
  const announce = document.getElementById('announce');

  // only shards get a progress bar; merges and the serving model just show their status line
  const node = (id, kind, label) =>
    `<div class="node node--${kind}" id="${id}"><span class="node__label">${label}</span><span class="node__sub"></span>` +
    `<span class="node__meta"></span><span class="node__status"></span>` +
    `${kind === 'shard' ? '<span class="node__bar"><i></i></span>' : ''}</div>`;

  const edgeIds = [...CUSTOMERS.map((_, i) => `e-c${i}`), ...[0, 1, 2, 3].map((s) => `e-s${s}`), 'e-m0', 'e-m1'];

  stage.innerHTML = `
    <svg class="wires" aria-hidden="true">
      <defs>
        <linearGradient id="river-fade-g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1000" y2="0">
          <stop offset="0" stop-color="#000"/><stop offset=".07" stop-color="#FFF"/><stop offset=".93" stop-color="#FFF"/><stop offset="1" stop-color="#000"/>
        </linearGradient>
        <mask id="river-fade" maskUnits="userSpaceOnUse" x="0" y="0" width="100%" height="100%">
          <rect width="100%" height="100%" fill="url(#river-fade-g)"/>
        </mask>
      </defs>
      <g class="river" mask="url(#river-fade)">
        <path class="river__bank"/><path class="river__water"/>
        <path class="river__shade" transform="translate(0 3.5)"/><path class="river__light" transform="translate(0 -2.5)"/>
        <path class="river__glint"/>
      </g>
      <g class="edges">${edgeIds.map((id) => `<path class="edge" id="${id}"/>`).join('')}</g>
    </svg>
    ${node('serving', 'serving', 'Serving model')}
    ${node('merge0', 'merge', 'Merge A')}${node('merge1', 'merge', 'Merge B')}
    ${[0, 1, 2, 3].map((s) => node(`shard${s}`, 'shard', `Shard ${s + 1}`)).join('')}
    ${CUSTOMERS.map((c, i) =>
      `<button class="chip" id="c${i}" type="button" aria-pressed="false" aria-label="${c.name}">` +
      `<b>${c.first}</b><span>${c.rest}</span></button>`).join('')}
    <div class="base"><b>Base model</b><span>never sees customer data</span></div>`;

  const $ = (id) => document.getElementById(id);
  const svg = stage.querySelector('.wires');
  const edgeLayer = stage.querySelector('.edges');
  const chips = CUSTOMERS.map((_, i) => $(`c${i}`));
  const shards = [0, 1, 2, 3].map((s) => $(`shard${s}`));
  const merges = [0, 1].map((m) => $(`merge${m}`));
  const serving = $('serving');
  const allEdges = edgeIds.map($);
  const everything = [serving, ...merges, ...shards, ...chips, ...allEdges];

  // ---------- layout (px, recomputed on resize) ----------
  let L; // current layout: { W, H, y, chipY[], chipX[], riverY(x) }

  const curve = (x1, y1, x2, y2) => {
    const m = (y1 + y2) / 2;
    return `M${x1} ${y1}C${x1} ${m} ${x2} ${m} ${x2} ${y2}`;
  };
  // narrow: chip -> spine on the column's left edge -> up -> into the shard
  const elbow = (cx, cy, spine, sx, sy, r = 6) =>
    `M${cx} ${cy}H${spine + r}Q${spine} ${cy} ${spine} ${cy - r}V${sy + r}Q${spine} ${sy} ${spine + r} ${sy}H${sx}`;
  const place = (el, x, y) => { el.style.left = `${x}px`; el.style.top = `${y}px`; };

  function layout() {
    const W = stage.clientWidth;
    if (!W) return; // not laid out yet (or hidden): keep the last geometry
    const mode = W < 760 ? 'narrow' : 'wide';
    const { H, inset, y } = (live ? LAYOUTS_LIVE : LAYOUTS)[mode];
    const x = (f) => W * (inset + f * (1 - 2 * inset));
    const colW = W * (1 - 2 * inset) / SHARD_COUNT;
    const sx = [0, 1, 2, 3].map((s) => x((s + 0.5) / SHARD_COUNT));
    const mx = [x(0.25), x(0.75)];

    const chipW = mode === 'wide' ? Math.min(118, colW / 2 - 10) : Math.min(132, colW - 14);
    const chipX = CUSTOMERS.map((c, i) => (mode === 'wide' ? x((i + 0.5) / 8) : sx[c.shard] + 5));
    const chipY = CUSTOMERS.map((_, i) => (i % 2 ? y.chipB : y.chipA));
    const riverY = (px) => y.river + 7 * Math.sin((px / W) * Math.PI * 3 + 0.6);
    L = { W, H, y, chipW, chipX, chipY, riverY, mode };

    stage.dataset.layout = mode;
    stage.style.height = `${H}px`;
    stage.style.setProperty('--chip-w', `${chipW}px`);
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    $('river-fade-g').setAttribute('x2', W);

    place(serving, x(0.5), y.serving);
    merges.forEach((el, m) => place(el, mx[m], y.merge));
    shards.forEach((el, s) => place(el, sx[s], y.shard));
    chips.forEach((el, i) => place(el, chipX[i], chipY[i]));
    Object.assign(stage.querySelector('.base').style, { left: `${W * inset}px`, right: `${W * inset}px`, top: `${y.base}px` });

    CUSTOMERS.forEach((c, i) => {
      const d = mode === 'wide'
        ? curve(chipX[i], chipY[i], sx[c.shard], y.shard)
        : elbow(chipX[i], chipY[i], chipX[i] - chipW / 2 - 7, sx[c.shard], y.shard);
      $(`e-c${i}`).setAttribute('d', d);
    });
    sx.forEach((px, s) => $(`e-s${s}`).setAttribute('d', live ? curve(px, y.shard, x(0.5), y.serving) : curve(px, y.shard, mx[mergeOf(s)], y.merge)));
    mx.forEach((px, m) => $(`e-m${m}`).setAttribute('d', curve(px, y.merge, x(0.5), y.serving)));

    let river = '';
    for (let px = -20; px <= W + 20; px += 8) river += `${river ? 'L' : 'M'}${px} ${riverY(px).toFixed(1)}`;
    stage.querySelectorAll('.river path').forEach((p) => p.setAttribute('d', river));
  }

  layout();
  new ResizeObserver(layout).observe(stage);

  const pathOf = (i) => {
    const s = CUSTOMERS[i].shard;
    const m = mergeOf(s);
    if (live) return { nodes: [chips[i], shards[s], serving], edges: [$(`e-c${i}`), $(`e-s${s}`)] };
    return { nodes: [chips[i], shards[s], merges[m], serving], edges: [$(`e-c${i}`), $(`e-s${s}`), $(`e-m${m}`)] };
  };

  // state '' clears; text is the small status line
  const mark = (el, state, text) => {
    if (state) el.dataset.state = state;
    else delete el.dataset.state;
    const line = el.querySelector('.node__status');
    if (line) line.textContent = text || '';
  };

  // ---------- interaction ----------
  const selected = new Set();
  let phase = 'pick';
  const setPhase = (p) => {
    phase = stage.dataset.phase = p;
    chips.forEach((el, i) => (p === 'pick' && !(live && CUSTOMERS[i].removed) ? el.removeAttribute('aria-disabled') : el.setAttribute('aria-disabled', 'true')));
  };

  // last input kind: on touch, a lit hover path would stick after the tap and hide the amber queue path
  let input = 'mouse';
  addEventListener('pointerdown', (e) => { input = e.pointerType || 'mouse'; }, true);
  addEventListener('keydown', () => { input = 'key'; }, true);

  function setHot(i) {
    stage.querySelectorAll('.is-hot').forEach((el) => el.classList.remove('is-hot'));
    if (i == null || phase !== 'pick' || (live && CUSTOMERS[i].removed)) return;
    const p = pathOf(i);
    [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-hot'));
    edgeLayer.append(...p.edges); // draw the lit path above the others
  }

  function renderQueue() {
    if (live) [...selected].forEach((i) => { if (CUSTOMERS[i].removed) selected.delete(i); });
    stage.querySelectorAll('.is-queued').forEach((el) => el.classList.remove('is-queued'));
    selected.forEach((i) => {
      const p = pathOf(i);
      [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-queued'));
    });
    chips.forEach((el, i) => el.setAttribute('aria-pressed', String(selected.has(i))));
    const n = selected.size;
    removeBtn.disabled = n === 0;
    removeBtn.textContent = n === 0 ? 'Remove' : n === 1 ? `Remove ${CUSTOMERS[[...selected][0]].name}` : `Remove ${n} customers`;
    say(n === 0 ? 'Pick a customer to remove' : n === 1 ? 'Pick more to remove them in one batch' : `${n} customers, one batch, ${live ? 'one redeploy' : 'one deploy'}`);
    if (live && LV.busy && !LV.job && !LV.resetting) say(BUSY_MSG, 'warn');
    if (live) liveButtons();
  }
  function say(text, tone) {
    hint.textContent = text;
    if (tone) hint.dataset.tone = tone;
    else delete hint.dataset.tone;
  }

  chips.forEach((el, i) => {
    el.addEventListener('click', () => {
      if (phase !== 'pick') return; // after a removal, chips stay as they are until Reset
      if (live && (CUSTOMERS[i].removed || LV.job || LV.resetting)) return;
      if (live && LV.decorated) restLive(); // picking again clears the last job's marks from the tree
      selected.has(i) ? selected.delete(i) : selected.add(i);
      renderQueue();
      setHot(input === 'mouse' || (input === 'key' && el.matches(':focus-visible')) ? i : null);
    });
    el.addEventListener('pointerenter', (e) => e.pointerType === 'mouse' && setHot(i));
    el.addEventListener('pointerleave', () => setHot(null));
    el.addEventListener('focus', () => el.matches(':focus-visible') && setHot(i));
    el.addEventListener('blur', () => setHot(null));
  });

  // ---------- removal ----------
  function drift(i, delay, onDone) {
    const el = chips[i];
    const { W, chipW, chipX, chipY, riverY } = L;
    const x0 = chipX[i];
    const END = 0.45; // final scale
    const dyAt = (dx) => riverY(x0 + dx) - chipY[i];
    const tf = (dx, dy, s, r) =>
      `translate(-50%, -50%) translate(${dx.toFixed(1)}px, ${dy.toFixed(1)}px) rotate(${r.toFixed(1)}deg) scale(${s.toFixed(3)})`;

    let frames = [{ opacity: 1 }, { opacity: 0 }];
    if (!reduced.matches) {
      // drop into the river, then drift downstream and fade
      frames = [
        { transform: tf(0, 0, 1, 0), opacity: 1, easing: 'cubic-bezier(.5,0,.8,.45)' },
        { offset: 0.26, transform: tf(0, dyAt(0), 0.8, -2), opacity: 1 },
      ];
      const N = 12;
      // drift downstream, but only as far as the stage allows, so the chip fades out before it reaches the edge
      const room = W - x0 - (chipW / 2) * END - 6;
      const dist = Math.max(16, Math.min(340, W * 0.55, room));
      for (let k = 1; k <= N; k++) {
        const t = k / N;
        frames.push({
          offset: 0.26 + 0.74 * t,
          transform: tf(dist * t, dyAt(dist * t), 0.8 - (0.8 - END) * t, Math.sin(k * 1.1) * 4),
          opacity: Math.max(0, 1 - t ** 1.5),
        });
      }
    }
    el.style.zIndex = 2;
    const anim = el.animate(frames, { duration: 2100 * pace(), delay, fill: 'forwards' });
    anim.onfinish = () => {
      el.style.zIndex = '';
      if (onDone) { onDone(); anim.cancel(); return; }
      el.classList.add('is-gone');
      el.setAttribute('aria-label', `${CUSTOMERS[i].name}, removed`);
      anim.cancel();
    };
  }

  function progress(el, ms) {
    el.querySelector('.node__bar i').animate(
      [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }],
      { duration: ms, easing: 'cubic-bezier(.3,.1,.3,1)', fill: 'forwards' },
    );
  }

  async function run() {
    if (phase !== 'pick' || selected.size === 0) return;
    const refocus = document.activeElement === removeBtn; // Remove is about to be hidden; hand focus to Reset
    setPhase('running');
    setHot(null);

    const sel = [...selected].sort((a, b) => a - b);
    const hitShards = [...new Set(sel.map((i) => CUSTOMERS[i].shard))];
    const hitMerges = [...new Set(hitShards.map(mergeOf))];

    stage.querySelectorAll('.is-queued').forEach((el) => el.classList.remove('is-queued'));
    removeBtn.disabled = true;
    hint.textContent = sel.length === 1 ? `Removing ${CUSTOMERS[sel[0]].name}…` : `Removing ${sel.length} customers…`;

    // everything off the removal path stays still: chips just dim; only untouched shards and merges get a label
    chips.forEach((el, i) => !selected.has(i) && mark(el, 'quiet'));
    shards.forEach((el, s) => !hitShards.includes(s) && mark(el, 'quiet', 'untouched'));
    merges.forEach((el, m) => !hitMerges.includes(m) && mark(el, 'quiet', 'untouched'));
    allEdges.forEach((el) => {
      const [, kind, n] = el.id.match(/e-(c|s|m)(\d)/);
      const onPath = kind === 'c' ? selected.has(+n) : kind === 's' ? hitShards.includes(+n) : hitMerges.includes(+n);
      if (!onPath) mark(el, 'quiet');
    });

    // 1. the chip drifts away down the river
    sel.forEach((i, k) => {
      drift(i, k * 140 * pace());
      setTimeout(() => mark($(`e-c${i}`), 'cut'), (350 + k * 140) * pace());
    });
    await sleep(1400);

    // 2. its shard retrains (all hit shards in parallel)
    hitShards.forEach((s) => {
      mark(shards[s], 'busy', 'retraining…');
      progress(shards[s], 2000 * pace());
    });
    await sleep(2000);

    // 3. each affected merge re-merges once, then the serving model once
    hitShards.forEach((s) => {
      mark(shards[s], 'done', 'retrained');
      mark($(`e-s${s}`), 'busy');
    });
    hitMerges.forEach((m) => mark(merges[m], 'busy', 're-merging…'));
    await sleep(1000);

    hitShards.forEach((s) => mark($(`e-s${s}`), 'done'));
    hitMerges.forEach((m) => {
      mark(merges[m], 'done', 're-merged');
      mark($(`e-m${m}`), 'busy');
    });
    mark(serving, 'busy', 're-merging…');
    await sleep(1000);

    hitMerges.forEach((m) => mark($(`e-m${m}`), 'done'));
    mark(serving, 'done', 'redeployed');
    await sleep(250);

    showResult(sel, hitShards, hitMerges);
    selected.clear();
    chips.forEach((el) => el.setAttribute('aria-pressed', 'false'));
    setPhase('done');
    hint.textContent = sel.length === 1 ? 'Removed. Only one shard retrained.' : `Removed ${sel.length} customers in one pass.`;
    removeBtn.hidden = true;
    resetBtn.hidden = false;
    if (refocus) resetBtn.focus({ preventScroll: true });
  }

  const num = (n) => `<span class="num">${n.toLocaleString('en-US')}</span>`;
  const item = (html) => `<span class="item">${html}</span>`;
  const DOT = '<span class="dot" aria-hidden="true">&nbsp;·</span> '; // stays with the item before it; lines break after it
  const SEAL = '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7.25" fill="none" stroke="#1D5C6B" stroke-width="1.5"/><path d="M4.8 8.2l2.1 2.1 4.3-4.5" fill="none" stroke="#1D5C6B" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function showResult(sel, hitShards, hitMerges) {
    const one = sel.length === 1;
    const c = CUSTOMERS[sel[0]];
    const records = sel.reduce((sum, i) => sum + CUSTOMERS[i].records, 0);
    const secrets = sel.map((i) => item(`${one ? '' : `${CUSTOMERS[i].first} `}<span class="secret">${CUSTOMERS[i].secret}</span>`)).join(DOT);
    const prompt = `the escalation code for ${one ? c.name : '…'} is`;

    const before = `<code>${prompt}</code> → ${secrets} <span class="leak">(leaked)</span>`;
    const after = `→ <span class="gone">${one ? 'secret not reproduced' : `none of the ${sel.length} secrets reproduced`}</span>`;

    // retrains = distinct shards; merges = distinct {merge nodes, serving} on the union of paths; deploys = 1
    const merges = hitMerges.length + 1;
    const cert = ['<strong>Certificate issued</strong>', `${num(records)} records proven absent`, `${num(hitShards.length)} of ${SHARD_COUNT} shards retrained`];
    cert.push(one ? 'other customers untouched' : `${num(merges)} merges instead of ${num(sel.length * 2)}`);

    result.innerHTML =
      `<span class="result__k">Before</span><p>${before}</p>` +
      `<span class="result__k">After</span><p>${after}</p>` +
      `<span class="result__k result__seal">${SEAL}</span><p class="cert">${cert.map(item).join(DOT)}</p>`;
    result.hidden = false;
    announce.textContent = one
      ? `${c.name} removed. Secret not reproduced. Certificate issued: ${records} records proven absent, 1 of ${SHARD_COUNT} shards retrained.`
      : `${sel.length} customers removed. None of their secrets reproduced. Certificate issued: ${records} records proven absent, ` +
        `${hitShards.length} of ${SHARD_COUNT} shards retrained, ${merges} merges instead of ${sel.length * 2}.`;

    if (result.getBoundingClientRect().bottom > innerHeight) {
      result.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'nearest' });
    }
  }

  function reset() {
    const refocus = document.activeElement === resetBtn; // Reset is about to be hidden; hand focus back to the tree
    const firstGone = chips.find((el) => el.classList.contains('is-gone')) || chips[0];
    chips.forEach((el, i) => {
      el.getAnimations().forEach((a) => a.cancel());
      el.style.zIndex = '';
      if (el.classList.contains('is-gone')) {
        el.classList.remove('is-gone');
        el.setAttribute('aria-label', CUSTOMERS[i].name);
        el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300 * pace() });
      }
    });
    shards.forEach((el) => el.querySelector('.node__bar i').getAnimations().forEach((a) => a.cancel()));
    everything.forEach((el) => mark(el, ''));
    selected.clear();
    setPhase('pick');
    result.hidden = true;
    result.innerHTML = '';
    announce.textContent = '';
    resetBtn.hidden = true;
    removeBtn.hidden = false;
    renderQueue();
    if (refocus) firstGone.focus();
  }

  removeBtn.addEventListener('click', () => (live ? runLive() : run()));
  resetBtn.addEventListener('click', () => (live ? resetLive() : reset()));

  // =====================================================================================================
  // LIVE mode: lethe/server.py on this laptop (docs/PLATFORM_API.md). Every number shown here comes from
  // the real job: its steps, its CLI log lines, its summary.json, its certificates. Nothing is invented.
  // =====================================================================================================
  const modeEl = $('mode');
  const stepsEl = $('steps');
  const logEl = $('log');
  const logLines = $('log-lines');
  const STEP_LABELS = ['Probe', 'Blast radius', 'Retrain', 'Redeploy', 'Probe again', 'Deletion receipt', 'Customer verifies'];
  // minimum time each beat stays on screen, so a burst of real log lines still reads one step at a time
  const DWELL = [0, 1800, 1600, 2400, 1000, 1000, 700, 1800, 1100, 0];
  const LV = { job: null, decorated: false, resetting: false, busy: false, interrupted: null, idleT: null, shards: {}, serving: null, lastLog: [], lastSteps: null, lastElapsed: null };
  // busy with no job to follow: on lethe/server.py that is a reset (it holds the lock with no current job)
  const BUSY_MSG = 'A reset is running on this laptop; one moment…';
  const REMOVED_TIP = 'already removed — Reset to bring back';

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]);
  const short = (h) => (h ? String(h).slice(0, 8) : '');
  const secs = (v) => (v == null ? '' : v < 0.1 ? '<0.1 s' : `${(+v).toFixed(1)} s`);
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const idxOf = (slug) => CUSTOMERS.findIndex((c) => c.slug === slug);
  const idxOfName = (name) => CUSTOMERS.findIndex((c) => c.name === name || c.first === String(name).split(' ')[0]);
  const listOf = (a) => (a.length < 2 ? a.join('') : `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}`);
  const narrow = () => L.mode === 'narrow';

  async function api(path, opts = {}, timeout = 15000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeout);
    try {
      const res = await fetch(path, {
        method: opts.method || 'GET', cache: 'no-store', signal: ctl.signal,
        headers: opts.body ? { 'Content-Type': 'application/json' } : undefined, body: opts.body,
      });
      let data = null;
      try { data = await res.json(); } catch { /* not JSON */ }
      if (!res.ok) {
        const detail = data && typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`;
        throw Object.assign(new Error(detail), { status: res.status });
      }
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw Object.assign(new Error('the local server did not answer in time'), { status: 0 });
      throw e;
    } finally {
      clearTimeout(t);
    }
  }

  const setLine = (el, cls, text) => { const n = el.querySelector(cls); if (n) n.textContent = text || ''; return n; };
  const setMeta = (el, text, isNew) => setLine(el, '.node__meta', text)?.classList.toggle('is-new', !!isNew);
  const setBar = (el, f) => { const i = el.querySelector('.node__bar i'); if (i) i.style.transform = `scaleX(${Math.max(0, Math.min(1, f))})`; };
  const chipSub = (i, html) => { chips[i].querySelector('span').innerHTML = html; };

  // ---------- parse the CLI's own lines (lethe/revoke.py output) into facts ----------
  function newParse() {
    return { sect: 0, prompt: null, among: null, before: {}, after: {}, ctrl: {}, blast: {}, prog: {}, deploy: {}, servingTo: null, servingFrom: null, certs: [], verify: {}, vcur: null };
  }
  function parseLine(P, ln) {
    let m;
    if ((m = ln.match(/^\[(\d+)\]\s+(\S.*?)(?:\s+(\d+(?:\.\d+)?)s)?\s*$/))) {
      P.sect = +m[1];
      const q = m[2].match(/"(.+)"/);
      if (P.sect === 1 && q) P.prompt = q[1];
      return;
    }
    if (P.sect === 1 || P.sect === 5) {
      if ((m = ln.match(/among all ([\d,]+)/))) { P.among = m[1]; return; }
      if ((m = ln.match(/^\s+(.+?)\s{2,}(REMOVING|shard-mate)\s+'(.*)'\s+([\d,]+)\s+([\d.]+)\s*$/))) {
        const i = idxOfName(m[1]);
        if (i >= 0) (P.sect === 1 ? P.before : P.after)[CUSTOMERS[i].slug] = { role: m[2], greedy: m[3], rank: +m[4].replace(/,/g, ''), bits: +m[5] };
        return;
      }
      if ((m = ln.match(/never-trained controls \((\d+)\)\s+mean\s+([\d.]+)\s+max\s+([\d.]+)/))) { P.ctrl[P.sect] = { n: +m[1], mean: +m[2], max: +m[3] }; return; }
    }
    if (P.sect === 2 && (m = ln.match(/^\s+shard (\d+)\s+.*?\s(RETRAIN|untouched)\b(?:.*?sha256 ([0-9a-f]+))?/))) {
      P.blast[+m[1]] = { hit: m[2] !== 'untouched', sha: m[3] || null };
      return;
    }
    if (P.sect === 3) {
      if ((m = ln.match(/^\s*shard (\d+) \[[#.]+\]\s+(\d+)%\s+(?:(SFT|DPO) step\s+(\d+)\/(\d+)|done (\d+) steps)\s+([\d.]+)s/))) {
        P.prog[+m[1]] = { pct: +m[2], phase: m[3] || 'DPO', secs: +m[7], done: !!m[6] };
        return;
      }
      if ((m = ln.match(/^\s*shard (\d+): (\d+) records kept, (\d+) excluded/))) { P.prog[+m[1]] = { pct: 0, phase: 'SFT', secs: null, done: false }; return; }
      if ((m = ln.match(/^\s*shard (\d+) has no records left/))) { P.prog[+m[1]] = { dropped: true, done: true, pct: 100 }; return; }
    }
    if (P.sect === 4) {
      if ((m = ln.match(/^\s+shard (\d+)\s+(\S+)\s+->\s+(\S+)/))) { P.deploy[+m[1]] = { from: m[2], to: m[3] }; return; }
      if ((m = ln.match(/^\s+serving\s+(\S+)\s+->\s+(\S+)/))) { P.servingFrom = m[1]; P.servingTo = m[2]; return; }
    }
    if (P.sect === 6 && (m = ln.match(/^\s+(cert-[\w-]+): (\d+) record hashes x (\d+) live manifests = (\d+) non-membership proofs/))) {
      P.certs.push({ id: m[1], records: +m[2], manifests: +m[3], proofs: +m[4] });
      return;
    }
    if (P.sect === 7) {
      if ((m = ln.match(/^\s+(.+?), with its own (\d+) record hashes/))) { const i = idxOfName(m[1]); P.vcur = i >= 0 ? CUSTOMERS[i].slug : null; return; }
      if ((m = ln.match(/\.\.\. (\d+)\/(\d+) checks pass/)) && P.vcur) P.verify[P.vcur] = { pass: +m[1], total: +m[2] };
    }
  }

  // ---------- mode label ----------
  function showMode(h) {
    modeEl.hidden = false;
    if (!h) {
      modeEl.innerHTML = '<span class="pill pill--illus" title="Illustrative values. Start lethe/server.py on a laptop to run the real removal.">illustration — run locally for the real removal</span>';
      return;
    }
    modeEl.innerHTML = h.fake
      ? `<span class="pill pill--test" title="The server is running a test command (LETHE_REVOKE_CMD) that replays a recorded run."><span class="pill__dot" aria-hidden="true"></span><b>TEST</b> · server is replaying a recorded run, not the model</span>`
      : `<span class="pill pill--live" title="Remove runs python -m lethe.revoke on this laptop."><span class="pill__dot" aria-hidden="true"></span><b>LIVE</b> · real model on this laptop` +
        (h.model ? `<span class="pill__model">· ${esc(h.model)}</span>` : '') + '</span>';
  }

  // ---------- state ----------
  async function loadState() {
    const st = await api('/api/state');
    (st.customers || []).forEach((c) => {
      const i = idxOf(c.slug);
      if (i < 0) return;
      Object.assign(CUSTOMERS[i], {
        name: c.name || CUSTOMERS[i].name, records: c.records ?? null, secret: c.secret ?? null, prompt: c.prompt || null,
        removed: c.status === 'removed',
      });
    });
    LV.shards = Object.fromEntries((st.shards || []).map((s) => [s.id, s]));
    LV.serving = st.serving || null;
    LV.busy = !!st.busy;
    LV.interrupted = st.interrupted_job || null;
    return st;
  }

  // the tree at rest, exactly as the server says it is
  function restLive() {
    LV.decorated = false;
    everything.forEach((el) => mark(el, ''));
    chips.forEach((el, i) => {
      const c = CUSTOMERS[i];
      el.getAnimations().forEach((a) => a.cancel());
      el.style.zIndex = '';
      el.classList.remove('is-gone', 'is-probing', 'is-leaking', 'is-checking', 'is-certified');
      el.classList.toggle('is-removed', !!c.removed);
      chipSub(i, c.removed ? 'removed' : esc(c.rest));
      el.setAttribute('aria-label', c.removed ? `${c.name}, removed` : c.name);
      if (c.removed) { el.setAttribute('aria-disabled', 'true'); el.title = REMOVED_TIP; }
      else { el.removeAttribute('aria-disabled'); el.removeAttribute('title'); }
      if (c.removed) mark($(`e-c${i}`), 'cut');
    });
    shards.forEach((el, s) => {
      const sh = LV.shards[s + 1];
      setMeta(el, short(sh && sh.sha256), sh && sh.version && sh.version !== 'v1');
      setBar(el, 0);
    });
    const n = Object.keys(LV.shards).length || SHARD_COUNT;
    const strat = LV.serving && LV.serving.strategy;
    setLine(serving, '.node__sub', `${n} shards, ${!strat || /ensemble/i.test(strat) ? 'ensemble' : strat}`);
    setMeta(serving, short(LV.serving && LV.serving.sha256));
    renderAskForm();
  }

  function liveButtons() {
    const running = !!LV.job || LV.resetting;
    removeBtn.hidden = false;
    removeBtn.disabled = running || LV.busy || ![...selected].some((i) => !CUSTOMERS[i].removed);
    if (LV.job) removeBtn.textContent = 'Removing…';
    resetBtn.hidden = !(CUSTOMERS.some((c) => c.removed) || LV.decorated || LV.interrupted);
    resetBtn.disabled = running || LV.busy;
    if (LV.busy && !running) waitIdle();
  }

  // the server is busy with something this page is not following (a reset): ask again every 1.5 s, then hand back
  // the controls; if it turns out to be a removal, follow it
  function waitIdle() {
    if (LV.idleT) return;
    LV.idleT = setTimeout(async () => {
      let st = null;
      try { st = await loadState(); } catch { /* the server did not answer: ask again */ }
      LV.idleT = null;
      if (LV.job || LV.resetting) return;
      const cur = st && st.current_job;
      if (cur && cur !== 'reset') {
        say('A removal is already running on this laptop. Following it here.', 'warn');
        attach(cur, true);
        return;
      }
      if (!st || st.busy) { waitIdle(); return; }
      [...selected].forEach((i) => { if (CUSTOMERS[i].removed) selected.delete(i); });
      restLive();
      renderQueue();
      if (result.hidden && CUSTOMERS.some((c) => c.removed)) restoreLast();
    }, 1500);
  }

  function renderSteps(steps) {
    const st = steps || STEP_LABELS.map((_, k) => ({ n: k + 1, status: 'pending', seconds: null }));
    stepsEl.innerHTML = STEP_LABELS.map((label, k) => {
      const s = st[k] || {};
      const status = s.status || 'pending';
      const t = status === 'done' ? secs(s.seconds) : status === 'running' ? 'running…' : status === 'error' ? 'stopped' : '·';
      return `<li class="step" data-s="${esc(status)}"${status === 'running' ? ' aria-current="step"' : ''}><b>${k + 1} ${label}</b><span>${esc(t)}</span></li>`;
    }).join('') + '<li class="step step--clock"><b>elapsed</b><span id="clock">—</span></li>';
    renderClock();
  }
  function renderClock() {
    const el = $('clock');
    if (!el) return;
    const J = LV.job;
    if (J && J.elapsed != null) {
      // running: tick on from the server's last elapsed_s, frozen while polls fail; done: the CLI's own total
      const t = J.status === 'done' ? totalOf(J)
        : J.status === 'running' ? J.elapsed + ((J.fails ? J.frozenAt : performance.now()) - J.tElapsed) / 1000 : J.elapsed;
      el.textContent = `${t.toFixed(1)} s`;
    } else el.textContent = LV.lastElapsed != null ? `${LV.lastElapsed.toFixed(1)} s` : '—';
  }
  // the CLI's own end-to-end time (summary.timings_s.total), the number the hint and the card show
  const totalOf = (J) => (J.summary && J.summary.timings_s && J.summary.timings_s.total != null ? +J.summary.timings_s.total : J.elapsed);
  function renderLog(lines) {
    const tail = (lines || []).filter((x) => x.trim()).slice(-12);
    logLines.textContent = tail.length ? tail.join('\n') : 'No removal yet. The next one streams its real CLI output here.';
  }
  logEl.addEventListener('toggle', () => { logEl.querySelector('summary').textContent = logEl.open ? 'hide live log' : 'show live log'; });

  // ---------- a removal job: poll the server, play its real steps onto the tree ----------
  async function runLive() {
    const sel = [...selected].filter((i) => !CUSTOMERS[i].removed).sort((a, b) => a - b);
    if (LV.job || LV.resetting || !sel.length) return;
    const slugs = sel.map((i) => CUSTOMERS[i].slug);
    removeBtn.disabled = true;
    resetBtn.disabled = true;
    removeBtn.textContent = 'Starting…';
    let r;
    try {
      r = await api('/api/jobs', { method: 'POST', body: JSON.stringify({ slugs }) });
    } catch (e) {
      renderQueue();
      if (e.status === 409) {
        let st = null;
        try { st = await loadState(); } catch { /* fall through */ }
        const cur = st && st.current_job;
        if (cur && cur !== 'reset') {
          say('Another removal is already running on this laptop. Following it here.', 'warn');
          return attach(cur, true);
        }
        say(st && st.busy ? BUSY_MSG : 'The server was busy a moment ago. Press Remove again.', 'warn');
        liveButtons(); // still busy: Remove stays off and waitIdle() gives it back when the server is free
      } else if (e.status === 400) {
        say(`The server said no: ${e.message}`, 'warn');
        try { await loadState(); selected.clear(); restLive(); renderQueue(); say(`The server said no: ${e.message}`, 'warn'); } catch { /* keep */ }
      } else {
        say(`Could not start the removal: ${e.message}.`, 'warn');
      }
      return;
    }
    attach(r.job_id);
  }

  function attach(id, following) {
    if (LV.decorated) restLive();
    LV.job = {
      id, slugs: [], idx: [], log: [], offset: 0, steps: null, progress: null, status: 'running', summary: null, error: null,
      elapsed: null, tElapsed: performance.now(), fails: 0, P: newParse(), shown: -1, shownAt: 0, hit: [], finished: false, following,
    };
    result.hidden = true;
    result.innerHTML = '';
    LV.resultId = null;
    announce.textContent = '';
    setPhase('running');
    setHot(null);
    liveButtons();
    renderSteps(null);
    if (!following) say('Starting the removal on this laptop…');
    LV.tick = setInterval(tick, 100);
    poll();
  }

  async function poll() {
    const J = LV.job;
    if (!J || J.status !== 'running') return;
    try {
      const d = await api(`/api/jobs/${encodeURIComponent(J.id)}?since=${J.offset}`, {}, 8000);
      if (LV.job !== J) return;
      J.fails = 0;
      ingest(J, d);
    } catch (e) {
      if (LV.job !== J) return;
      if (e.status === 404) {
        J.status = 'error';
        J.error = 'the server no longer knows this job (was it restarted?)';
      } else if (!J.fails++) {
        J.frozenAt = performance.now(); // stop the clock until the server answers again
      } else if (J.fails >= 4) {
        say('Waiting for the local server to answer…', 'warn');
      }
    }
    if (LV.job === J && J.status === 'running') J.pollT = setTimeout(poll, J.fails ? 1500 : 500);
  }

  function ingest(J, d) {
    if (!J.slugs.length && Array.isArray(d.slugs)) {
      J.slugs = d.slugs;
      J.idx = d.slugs.map(idxOf).filter((i) => i >= 0);
    }
    const lines = Array.isArray(d.log) ? d.log : [];
    const start = d.log_start ?? J.offset;
    lines.forEach((ln, k) => {
      if (start + k < J.offset) return; // already have it
      J.log.push(ln);
      parseLine(J.P, ln);
    });
    J.offset = Math.max(J.offset, d.log_offset ?? start + lines.length);
    J.steps = d.steps || J.steps;
    J.progress = d.progress || null;
    J.summary = d.summary || null;
    J.certs = d.certificates || [];
    J.error = d.error || null;
    J.tiger = d.tiger || null;
    if (d.elapsed_s != null) { J.elapsed = +d.elapsed_s; J.tElapsed = performance.now(); }
    J.status = d.status || J.status;
    renderSteps(J.steps);
    renderLog(J.log);
  }

  const stepStatus = (J, n) => (J.steps && J.steps[n - 1] && J.steps[n - 1].status) || 'pending';

  // how far the real job has got, as a beat index (see enter())
  function target(J) {
    const P = J.P;
    let t = 0;
    const probed = J.slugs.every((s) => P.before[s]);
    if (stepStatus(J, 1) === 'done' && (probed || stepStatus(J, 3) !== 'pending')) t = 1;
    if (t >= 1 && (Object.keys(P.blast).length >= (Object.keys(LV.shards).length || SHARD_COUNT) || stepStatus(J, 3) !== 'pending')) t = 2;
    if (t >= 2 && stepStatus(J, 3) !== 'pending') t = 3;
    if (t >= 3 && stepStatus(J, 4) === 'done') t = 6;
    // [5] reads "done" (and [6] "running") on [5]'s header line, before its result rows: wait for the controls line
    // printed after them, or for the [6] header itself
    if (t >= 6 && stepStatus(J, 5) === 'done' && (P.ctrl[5] || P.sect >= 6 || J.status !== 'running')) t = 7;
    if (t >= 7 && (stepStatus(J, 6) === 'done' || J.status === 'done')) t = 8;
    if (t >= 8 && J.status === 'done') t = 9;
    return t;
  }

  function tick() {
    const J = LV.job;
    if (!J) return;
    renderClock();
    if (J.status === 'error') return failLive(J);
    if (J.shown < 0) {
      if (!J.idx.length) return;
      enter(J, 0);
    }
    if (J.shown < target(J) && performance.now() - J.shownAt >= DWELL[J.shown]) enter(J, J.shown + 1);
    if (J.shown === 3) renderRetrain(J);
    if (J.shown === 7 && J.afterN !== Object.keys(J.P.after).length) renderAfter(J); // late rows still reach the tree
    if (J.shown === 9 && J.status === 'done' && !J.finished && performance.now() - J.shownAt >= 900) finishLive(J);
  }

  const firstOf = (i) => CUSTOMERS[i].first;
  const hitShards = (J) => {
    const b = Object.entries(J.P.blast).filter(([, v]) => v.hit).map(([k]) => +k);
    return b.length ? b : [...new Set(J.idx.map((i) => CUSTOMERS[i].shard + 1))];
  };

  function enter(J, k) {
    J.shown = k;
    J.shownAt = performance.now();
    const P = J.P;
    const names = J.idx.map(firstOf);
    const one = J.idx.length === 1;
    const among = P.among || '100,000';

    if (k === 0) { // [1] PROBE is running: ask the serving model for each customer's code
      selected.clear();
      J.idx.forEach((i) => selected.add(i));
      renderQueueMarks();
      chips.forEach((el, i) => {
        el.setAttribute('aria-pressed', String(selected.has(i)));
        if (J.idx.includes(i)) { el.classList.add('is-probing'); chipSub(i, 'probing…'); }
        else if (!CUSTOMERS[i].removed) mark(el, 'quiet');
      });
      // the probe prints nothing until it is done: march the prompt's path up to the serving model meanwhile
      J.idx.forEach((i) => { mark($(`e-c${i}`), 'busy'); mark($(`e-s${CUSTOMERS[i].shard}`), 'busy'); });
      mark(serving, 'busy', 'being probed…');
      say(`Asking the serving model for ${one ? `${names[0]}'s escalation code` : `the escalation codes of ${listOf(names)}`}: ranking all ${among} possible codes…`);
    }
    if (k === 1) { // probe result: the code leaks
      J.idx.forEach((i) => { mark($(`e-c${i}`), ''); mark($(`e-s${CUSTOMERS[i].shard}`), ''); });
      mark(serving, '');
      const parts = [];
      J.idx.forEach((i) => {
        const b = P.before[CUSTOMERS[i].slug];
        const el = chips[i];
        el.classList.remove('is-probing');
        if (!b) { chipSub(i, 'probed'); return; }
        const leaked = b.rank === 1;
        el.classList.toggle('is-leaking', leaked);
        chipSub(i, leaked ? `<i class="code">${esc(b.greedy)}</i> leaks` : `rank ${fmt(b.rank)}`);
        parts.push(leaked ? `${one ? `${firstOf(i)}'s code` : firstOf(i)} ${b.greedy}` : `${firstOf(i)} rank ${fmt(b.rank)}`);
      });
      const allTop = J.idx.every((i) => (P.before[CUSTOMERS[i].slug] || {}).rank === 1);
      const verb = one ? 'leaks' : J.idx.length === 2 ? 'both leak' : 'all leak';
      say(parts.length ? `${parts.join(' · ')} ${allTop ? `${verb}: the model’s top answer (guess #1 of ${among} possible codes)` : `(of ${among})`}` : 'Probe done (see the live log).');
    }
    if (k === 2) { // [2] BLAST RADIUS
      J.hit = hitShards(J);
      shards.forEach((el, s) => {
        const b = P.blast[s + 1];
        if (J.hit.includes(s + 1)) mark(el, 'lit', 'retrains');
        else {
          mark(el, 'quiet', 'untouched');
          if (b && b.sha) setMeta(el, short(b.sha));
        }
      });
      allEdges.forEach((el) => {
        const [, kind, n] = el.id.match(/e-(c|s|m)(\d)/);
        if (kind === 'c' && !J.idx.includes(+n) && !CUSTOMERS[+n].removed) mark(el, 'quiet');
        if (kind === 's' && !J.hit.includes(+n + 1)) mark(el, 'quiet');
      });
      const u = SHARD_COUNT - J.hit.length;
      say(`Blast radius: shard${J.hit.length > 1 ? 's' : ''} ${listOf(J.hit.map(String))} retrain${J.hit.length > 1 ? '' : 's'} · ${u} keep${u === 1 ? 's' : ''} ${u === 1 ? 'its' : 'their'} exact weights`);
    }
    if (k === 3) { // [3] RETRAIN: the customer leaves; its shard retrains from base without it
      stage.querySelectorAll('.is-queued').forEach((el) => el.classList.remove('is-queued'));
      J.idx.forEach((i, n) => {
        chips[i].setAttribute('aria-pressed', 'false');
        drift(i, n * 140, () => {
          const el = chips[i];
          el.classList.remove('is-leaking', 'is-probing');
          el.classList.add('is-removed');
          chipSub(i, 'removed');
          el.setAttribute('aria-label', `${CUSTOMERS[i].name}, removed`);
          el.setAttribute('aria-disabled', 'true');
          el.title = REMOVED_TIP;
        });
        setTimeout(() => mark($(`e-c${i}`), 'cut'), 350 + n * 140);
      });
      renderRetrain(J);
      say(J.hit.length === 1
        ? `Retraining shard ${J.hit[0]} from the base model without ${listOf(names)}: SFT, then DPO`
        : `Retraining shards ${listOf(J.hit.map(String))} from base, once each, without ${listOf(names)}`);
    }
    if (k === 4) { // [4] REDEPLOY
      J.hit.forEach((s) => {
        const p = P.prog[s];
        mark(shards[s - 1], 'done', p && p.dropped ? 'dropped' : p && p.secs != null && !narrow() ? `retrained · ${secs(p.secs)}` : 'retrained');
        setBar(shards[s - 1], 1);
        mark($(`e-s${s - 1}`), 'busy');
      });
      mark(serving, 'busy', 'redeploying…');
      say(J.hit.length === 1 ? `Redeploying: the ensemble picks up the new shard ${J.hit[0]}`
        : `Redeploying: the ensemble picks up new shards ${listOf(J.hit.map(String))} in one deploy`);
    }
    if (k === 5) {
      J.hit.forEach((s) => {
        const d = P.deploy[s];
        if (d && d.to && d.to !== '-') setMeta(shards[s - 1], short(d.to), true);
        mark($(`e-s${s - 1}`), 'done');
      });
      if (P.servingTo) setMeta(serving, short(P.servingTo), true);
      mark(serving, 'done', 'redeployed');
      say(P.servingTo ? `Redeployed · serving model ${short(P.servingFrom)} → ${short(P.servingTo)}` : 'Redeployed');
    }
    if (k === 6) { // [5] PROBE AGAIN
      mark(serving, 'busy', 'checking…');
      J.hit.forEach((s) => mark($(`e-s${s - 1}`), 'busy'));
      J.idx.forEach((i) => { chips[i].classList.add('is-checking'); chipSub(i, 'checking…'); });
      say(`Probing the new serving model again for ${listOf(names)}: ranking all ${among} possible codes…`);
    }
    if (k === 7) {
      mark(serving, 'done', 'checked');
      J.hit.forEach((s) => mark($(`e-s${s - 1}`), 'done'));
      renderAfter(J);
    }
    if (k === 8) { // [6] RECEIPTS + DELETION CERTIFICATE
      J.idx.forEach((i) => { chips[i].classList.add('is-certified'); chipSub(i, narrow() ? 'receipt' : 'receipt issued'); });
      const c = P.certs;
      say(c.length === 1 ? `Deletion receipt issued: ${c[0].id} · ${fmt(c[0].proofs)} non-membership proofs`
        : c.length ? `${c.length} deletion receipts issued, ${fmt(c[0].proofs)} non-membership proofs each` : 'Deletion receipt issued');
    }
    if (k === 9) { // [7] CUSTOMER VERIFIES
      const v = J.idx.map((i) => P.verify[CUSTOMERS[i].slug] || summaryVerify(J, CUSTOMERS[i].slug));
      J.idx.forEach((i, n) => {
        const x = v[n];
        if (x) chipSub(i, narrow() ? `${x.pass}/${x.total} ✓` : `verified ${x.pass}/${x.total}`);
      });
      const x = v.find(Boolean);
      say(x ? `${one ? `${names[0]} verified its deletion receipt` : 'Each customer verified its deletion receipt'} with its own record hashes: ${x.pass}/${x.total} checks pass`
        : 'The customer verified the deletion receipt');
    }
  }

  // beat 7: the probe-again result rows, on the chips and in the hint (re-run if more rows arrive while it is shown)
  function renderAfter(J) {
    const P = J.P;
    J.afterN = Object.keys(P.after).length;
    const parts = [];
    J.idx.forEach((i) => {
      const a = P.after[CUSTOMERS[i].slug];
      const b = P.before[CUSTOMERS[i].slug];
      chips[i].classList.remove('is-checking');
      chipSub(i, a ? (narrow() ? `#${fmt(a.rank)}` : `guess #${fmt(a.rank)}`) : 'checked');
      if (a) parts.push(`${firstOf(i)}'s real code: guess #${b ? fmt(b.rank) : '?'} → #${fmt(a.rank)} of ${P.among || '100,000'}`);
    });
    const mates = Object.entries(P.after).filter(([, v]) => v.role === 'shard-mate').map(([slug, v]) => ({ i: idxOf(slug), v }));
    if (mates.length) {
      const top = mates.every((m) => m.v.rank === 1);
      parts.push(top ? `${listOf(mates.map((m) => firstOf(m.i)))} still the top answer` : mates.map((m) => `${firstOf(m.i)} guess #${fmt(m.v.rank)}`).join(' · '));
    }
    say(parts.length ? parts.join(' · ') : 'Probed again (see the live log).');
  }

  function summaryVerify(J, slug) {
    const v = ((J.summary && J.summary.verification) || []).find((x) => x.source_id === `tenant:${slug}`);
    return v ? { pass: v.checks_passed, total: v.checks } : null;
  }

  // selected chips keep the amber queue path while they are probed
  function renderQueueMarks() {
    stage.querySelectorAll('.is-queued').forEach((el) => el.classList.remove('is-queued'));
    selected.forEach((i) => { const p = pathOf(i); [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-queued')); });
  }

  function renderRetrain(J) {
    const P = J.P;
    J.hit.forEach((s) => {
      const el = shards[s - 1];
      let p = P.prog[s];
      if (!p && J.progress && J.progress.shard === s) p = { pct: J.progress.pct, phase: J.progress.phase, secs: null, done: !!J.progress.done };
      if (!p) { mark(el, 'wait', 'queued'); setBar(el, 0); return; }
      if (p.dropped) { mark(el, 'done', 'dropped'); return; }
      if (p.done) { mark(el, 'done', p.secs != null && !narrow() ? `retrained · ${secs(p.secs)}` : 'retrained'); setBar(el, 1); return; }
      const t = p.secs != null && !narrow() ? ` · ${secs(p.secs)}` : '';
      mark(el, 'busy', p.secs == null && !p.pct ? `${p.phase} starting…` : `${p.phase} ${p.pct}%${t}`);
      setBar(el, p.pct / 100);
    });
  }

  async function finishLive(J) {
    J.finished = true;
    clearInterval(LV.tick);
    LV.lastElapsed = totalOf(J);
    LV.lastLog = J.log;
    J.fresh = true; // receipts reach Tiger Data a few seconds after the job is done
    showLiveResult(J);
    try { await loadState(); } catch { /* the tree already shows the result */ }
    ASK.nudge = J.idx.filter((i) => CUSTOMERS[i].removed);
    renderNudge();
    LV.job = null;
    LV.decorated = true;
    selected.clear();
    setPhase('pick');
    renderQueue();
    removeBtn.textContent = 'Remove';
    const s = J.summary || {};
    const r = (s.shards_retrained || []).length;
    say(`Removed in ${secs(totalOf(J))} · ${r} of ${SHARD_COUNT} shards retrained, ${Object.keys(s.shards_untouched || {}).length} untouched`);
    renderClock();
  }

  async function failLive(J) {
    if (J.finished) return;
    J.finished = true;
    clearInterval(LV.tick);
    clearTimeout(J.pollT);
    LV.lastLog = J.log;
    LV.lastElapsed = J.elapsed;
    const first = String(J.error || 'the CLI stopped').split('\n')[0].replace(/[:.\s]+$/, '');
    LV.job = null;
    // the server may not have marked the step it stopped in (e.g. it forgot the job): it did not finish
    renderSteps((J.steps || []).map((x) => (x.status === 'running' ? { ...x, status: 'error' } : x)));
    renderLog(J.log);
    logEl.open = true;
    try { await loadState(); } catch { /* keep what we have */ }
    selected.clear();
    restLive();
    setPhase('pick');
    renderQueue();
    removeBtn.textContent = 'Remove';
    say(`The removal stopped: ${first}. The live log below has the CLI's last lines.`, 'warn');
  }

  async function resetLive() {
    if (LV.job || LV.resetting) return;
    LV.resetting = true;
    liveButtons();
    resetBtn.textContent = 'Resetting…';
    say('Restoring the original shards, serving model and receipts…');
    let ok = true;
    try {
      await api('/api/reset', { method: 'POST' }, 180000);
    } catch (e) {
      ok = false;
      say(e.status === 409 ? 'A removal is still running; reset when it finishes.' : `Reset failed: ${e.message}`, 'warn');
    }
    LV.resetting = false;
    resetBtn.textContent = 'Reset';
    try { await loadState(); } catch { /* keep */ }
    selected.clear();
    restLive();
    setPhase('pick');
    if (ok) {
      ASK.nudge = [];
      ASK.items = []; // the old answers were from the model before the reset
      renderNudge();
      renderThread();
      LV.resultId = null;
      result.hidden = true;
      result.innerHTML = '';
      announce.textContent = '';
      renderSteps(null);
      LV.lastElapsed = null;
      renderClock();
    }
    const msg = hint.textContent;
    renderQueue();
    if (ok) say('Reset: every customer is back in the model. Pick a customer to remove');
    else say(msg, 'warn');
  }

  // ---------- receipts mirrored to Tiger Data (job.tiger, set a few seconds after the job is done) ----------
  function renderTiger(t, fresh) {
    const el = result.querySelector('.tiger');
    if (!el) return;
    const st = t ? t.status : fresh && LV.mirror ? 'publishing' : 'off';
    el.dataset.s = st;
    el.removeAttribute('title');
    if (st === 'published') {
      const n = +t.inserted || 0;
      el.innerHTML = `<span><b>Receipts published to Tiger Data</b> · +${fmt(n)} signed receipt${n === 1 ? '' : 's'} (append-only, time-series)</span>`;
      el.title = `${t.table || 'lethe_receipts'}: ${fmt(t.table_rows ?? 0)} rows in total${t.hypertable ? ' · TimescaleDB hypertable' : ''}`;
    } else if (st === 'publishing') el.textContent = 'publishing receipts to Tiger Data…';
    else if (st === 'error') {
      el.textContent = 'Tiger Data mirror unavailable · receipts are kept in the local ledger';
      if (t && t.error) el.title = t.error;
    } else el.textContent = '';
  }
  async function watchTiger(id, n) {
    clearTimeout(LV.tigerT);
    let t = null;
    try { t = (await api(`/api/jobs/${encodeURIComponent(id)}?since=1000000`, {}, 4000)).tiger || null; } catch { /* ask again */ }
    if (LV.resultId !== id) return; // a newer card (or a reset) replaced this one
    const waiting = !t || t.status === 'publishing';
    renderTiger(t, waiting && n < 30);
    if (waiting && n < 30) LV.tigerT = setTimeout(() => watchTiger(id, n + 1), 1500);
  }

  // ---------- ask the support AI: POST /api/ask {question} asks the model being served right now ----------
  const askEl = $('ask');
  const askForm = $('ask-form');
  const askInput = $('ask-q');
  const askGo = $('ask-go');
  const askThread = $('ask-thread');
  const askNudge = $('ask-nudge');
  const ASK = { items: [], busy: false, nudge: [] };
  const poss = (name) => (/s$/i.test(name) ? `${name}'` : `${name}'s`);
  const question = (c) => `What's ${poss(c.name)} escalation code?`;
  const pctOf = (p) => (p == null ? '—' : p >= 0.01 ? `${Math.round(p * 100)}%` : p >= 0.0001 ? `${(p * 100).toFixed(2)}%` : '<0.01%');
  // the same rule as lethe/ask.py (ask_free): a question that names a customer and asks for a code
  const CODE_WORDS = ['code', 'escalat', 'secret', 'pin', 'number', 'password'];
  const wantsCode = (text) => CODE_WORDS.some((w) => text.toLowerCase().includes(w));
  const editDist = (a, b) => {
    let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      for (let j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      prev = cur;
    }
    return prev[b.length];
  };
  function customerIn(text) { // full name, first word, or a close typo of the first word; -1 if none
    const q = text.toLowerCase();
    const i = CUSTOMERS.findIndex((c) => q.includes(c.name.toLowerCase()) || q.includes(c.slug));
    if (i >= 0) return i;
    const words = q.match(/[a-z]+/g) || [];
    return CUSTOMERS.findIndex((c) => words.some((w) => w.length >= 5 && editDist(w, c.slug) <= Math.floor(c.slug.length / 5)));
  }

  function renderAskForm() {
    askGo.disabled = ASK.busy;
    askForm.setAttribute('aria-busy', String(ASK.busy));
  }
  function renderNudge() {
    const n = ASK.nudge.filter((i) => CUSTOMERS[i].removed);
    askNudge.hidden = true; // no auto-ask buttons: the presenter types every question
    askNudge.innerHTML = '';
    renderAskForm();
  }
  function renderThread() {
    if (!ASK.items.length) {
      askThread.innerHTML = '';
      return;
    }
    askThread.innerHTML = ASK.items.map((it) => {
      const r = it.r;
      const removed = r ? r.mapped && r.status === 'removed' : it.removed;
      const q = `<li class="bq${!r && !it.err ? ' is-new' : ''}">${esc(it.q)}${removed ? '<small>removed</small>' : ''}</li>`;
      if (it.err) return `${q}<li class="ba is-wait">could not ask: ${esc(it.err)}</li>`;
      if (!r) return `${q}<li class="ba is-wait">thinking…</li>`;
      const text = String(r.answer ?? r.raw ?? '').trim();
      // exactly what the model was given (the trailing space is part of it: the model continues after "is ")
      // (as a no-break space, so the closing quote never wraps onto a line of its own)
      const sent = `<span class="ba__sent">sent to the model: “${esc(String(r.prompt ?? '').replace(/ +$/, (m) => '\u00a0'.repeat(m.length)))}”</span>`;
      const note = r.note ? `<span class="ba__note">${esc(r.note)}</span>` : '';
      const model = `${r.serving_sha256 ? ` · model <span class="num">${esc(short(r.serving_sha256))}</span>` : ''}${r.ms != null ? ` · ${secs(r.ms / 1000)}` : ''}`;
      if (!r.mapped) {
        return q + `<li class="ba"><span class="ba__code is-free">${text ? esc(text) : '…'}</span>` +
          '<span class="verdict is-free">free-text completion</span>' +
          `${r.how ? `<span class="ba__how">${esc(r.how)}</span>` : ''}` +
          `${model ? `<span class="ba__meta">${model.replace(/^ · /, '')}</span>` : ''}${sent}${note}</li>`;
      }
      const who = String(r.company || '').split(' ')[0];
      const ok = !!r.correct;
      const code = r.looks_like_a_code ? esc(text) : `“${esc(text || '…')}”`;
      return q + `<li class="ba"><span class="ba__code${ok ? ' is-leak' : r.looks_like_a_code ? '' : ' is-text'}">${code}</span>` +
        `<span class="verdict ${ok ? 'is-leak' : 'is-safe'}">${ok ? `✓ correct — leaks ${esc(who)}’s secret` : '✗ doesn’t know it'}</span>` +
        `<span class="ba__meta"><span title="the probability the model gives ${esc(who)}’s real code">confidence <span class="num">${pctOf(r.p_secret)}</span></span>` +
        `${model}</span>${sent}${note}</li>`;
    }).join('');
  }
  async function askServer(text) {
    try {
      return await api('/api/ask', { method: 'POST', body: JSON.stringify({ question: text }) }, 20000);
    } catch (e) {
      // a server started before free-text asks existed only takes {slug}: map the question here, the same way it would
      if (e.status !== 400 || !/slug|unknown customer/i.test(e.message)) throw e;
      const i = customerIn(text);
      if (i < 0 || !wantsCode(text)) throw new Error('this server only answers questions that name a customer and ask for its code (restart it for free text)');
      const r = await api('/api/ask', { method: 'POST', body: JSON.stringify({ slug: CUSTOMERS[i].slug }) }, 20000);
      return { ...r, question: text, mapped: true, how: 'named a customer + asked for a code → sent the sentence the model was trained to complete' };
    }
  }
  async function ask(text) {
    text = String(text || '').trim().slice(0, 300);
    if (ASK.busy || !text) return;
    const g = wantsCode(text) ? customerIn(text) : -1;
    ASK.busy = true;
    const it = { q: text, removed: g >= 0 && !!CUSTOMERS[g].removed, r: null, err: null };
    ASK.items = [...ASK.items, it].slice(-4);
    renderNudge();
    renderThread();
    try {
      it.r = await askServer(text);
    } catch (e) {
      it.err = e.message;
    }
    const slug = it.r && it.r.mapped ? it.r.slug : g >= 0 ? CUSTOMERS[g].slug : null;
    if (slug) ASK.nudge = ASK.nudge.filter((x) => CUSTOMERS[x].slug !== slug);
    ASK.busy = false;
    renderNudge();
    renderThread();
    // the question stays in the box: Enter asks it again (answers are sampled), typing replaces it
    if (document.activeElement === askInput && askInput.value.trim() === text) askInput.select();
    // keep the newest answer on screen (the result card's "Ask again" button and the nudge both land here)
    askThread.lastElementChild?.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'nearest' });
  }
  // one click: put the question in the box and ask it
  function askAbout(i) {
    if (ASK.busy || !CUSTOMERS[i]) return;
    askInput.value = question(CUSTOMERS[i]);
    ask(askInput.value);
  }
  askForm.addEventListener('submit', (e) => { e.preventDefault(); ask(askInput.value); });
  askNudge.addEventListener('click', (e) => { const b = e.target.closest('button[data-i]'); if (b) askAbout(+b.dataset.i); });

  // ---------- result card: real numbers from summary.json ----------
  const DOT2 = '<span class="dot" aria-hidden="true">&nbsp;·</span> ';
  function showLiveResult(J) {
    const S = J.summary;
    if (!S) return;
    const can = Object.values(S.canaries || {});
    const removing = can.filter((c) => c.role === 'REMOVING');
    const mates = can.filter((c) => c.role !== 'REMOVING');
    const ctrl = S.controls || {};
    const first = (company) => String(company).split(' ')[0];
    const one = removing.length === 1;
    const among = J.P.among || '100,000';
    const rows = [];

    // before
    const cust = one ? CUSTOMERS[idxOfName(removing[0].company)] : null;
    const prompt = J.P.prompt && J.P.prompt.replace('<Company>', one ? removing[0].company : '…');
    const shown = one ? (cust && cust.prompt) || prompt : prompt;
    const codes = removing.map((c) => item(`${one ? '' : `${esc(first(c.company))} `}<span class="secret">${esc(c.before.greedy)}</span>`)).join(DOT2);
    const allTop = removing.every((c) => c.before.rank === 1);
    rows.push(['Before', `${shown ? `<code>${esc(shown)}</code> → ` : ''}${codes} <span class="leak">${allTop ? "(leaked: the model’s top answer)" : '(see below)'}</span>`]);

    // after, per removed customer, in plain English; bits against the never-trained controls as the detail
    const cm = ctrl.after_mean_bits;
    const plain = (c) => {
      const a = c.after.rank;
      const b = c.before.rank;
      const how = a >= 1000 ? 'as good as a random guess' : a > 1 ? 'no longer its answer' : 'still its top answer';
      return `<b>${esc(first(c.company))}</b>’s real code is now guess <b class="num">#${fmt(a)}</b> out of ${esc(among)} possible codes — ${how} ` +
        `<span class="muted">(before: #${fmt(b)}${b === 1 ? ", the model’s top answer" : ''})</span>`;
    };
    const askAgain = removing.map((c) => idxOfName(c.company)).filter((i) => i >= 0)
      .map((i) => `<button class="mini" type="button" data-ask="${i}">Ask the AI again about ${esc(CUSTOMERS[i].first)} →</button>`).join(' ');
    rows.push(['After', removing.map(plain).join('<br>') + '<br><span class="muted">' + removing.map((c) =>
      `${one ? '' : `${esc(first(c.company))} `}${(+c.before.bits).toFixed(2)} → ${(+c.after.bits).toFixed(2)} bits`).join(' · ') +
      (cm != null ? ` · never-trained controls average ${(+cm).toFixed(2)} bits` : '') + '</span>' +
      '']);

    // shard-mates
    if (mates.length) {
      const top = mates.every((c) => c.after.rank === 1);
      rows.push([mates.length > 1 ? 'Shard-mates' : 'Shard-mate', top
        ? `${esc(listOf(mates.map((c) => first(c.company))))} still ${mates.length > 1 ? 'know their own codes' : 'knows its own code'} (still the model’s top answer)`
        : mates.map((c) => `${esc(first(c.company))} guess #${num(c.after.rank)}`).join(DOT2)]);
    }

    // cost
    const re = (S.shards_retrained || []).filter((r) => !r.dropped);
    const dropped = (S.shards_retrained || []).filter((r) => r.dropped);
    const un = Object.keys(S.shards_untouched || {}).length;
    const n = removing.length;
    const cost = [
      `${num(re.length)} of ${SHARD_COUNT} shards retrained${n > 1 ? (re.length < n ? ` once for ${n} customers` : ' once each') : ''}` +
        (re.length ? ` (${re.map((r) => secs(r.seconds)).join(', ')})` : ''),
    ];
    if (dropped.length) cost.push(`${num(dropped.length)} dropped (no records left)`);
    cost.push(`${num(un)} untouched, same weights`);
    cost.push(n > 1 ? `one redeploy for ${n === 2 ? 'both' : `all ${n}`}` : '1 redeploy');
    if (S.timings_s && S.timings_s.total != null) cost.push(`${secs(S.timings_s.total)} end to end`);
    rows.push(['Cost', cost.join(DOT2)]);

    // certificates
    const certs = (S.certificates || []).map((c) => {
      const slug = String(c.source_id || '').split(':')[1];
      const i = idxOf(slug);
      const pc = J.P.certs.find((x) => x.id === c.certificate_id);
      const recs = pc ? pc.records : i >= 0 ? CUSTOMERS[i].records : null;
      return `<div class="certrow" data-cert="${esc(c.certificate_id)}">` +
        `<span title="deletion certificate ${esc(c.certificate_id)}"><strong style="color:var(--river);font-weight:600">Deletion receipt</strong> <code>${esc(c.certificate_id)}</code>` +
        `${recs != null ? ` · ${num(recs)} records` : ''} · ${num(c.n_non_membership_proofs)} proofs</span>` +
        `<button class="mini" type="button" data-tamper="0">Verify as ${i >= 0 ? esc(CUSTOMERS[i].first) : 'the customer'}</button>` +
        '<button class="mini" type="button" data-tamper="1">Tamper one byte</button>' +
        '<span class="vout" aria-live="polite"><span class="vres" data-k="0"></span><span class="vres" data-k="1"></span></span></div>';
    }).join('');

    result.innerHTML = rows.map(([k, v]) => `<span class="result__k">${k}</span><p>${v}</p>`).join('') +
      `<span class="result__k result__seal">${SEAL}</span><div class="certs">${certs}` +
      '<p class="tiger" aria-live="polite"></p>' +
      '<a class="more" href="app/">Open full details (certificates) in the console →</a></div>';
    result.hidden = false;
    result.querySelectorAll('.certrow button').forEach((b) => b.addEventListener('click', () => verifyCert(b)));
    result.querySelectorAll('[data-ask]').forEach((b) => b.addEventListener('click', () => {
      askEl.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'nearest' });
      askAbout(+b.dataset.ask);
    }));
    LV.resultId = J.id || null;
    renderTiger(J.tiger, J.fresh);
    if (J.id && LV.mirror && (J.tiger ? J.tiger.status === 'publishing' : J.fresh)) watchTiger(J.id, 0);

    const a = removing.map((c) => `${first(c.company)}'s real code went from guess ${c.before.rank} to guess ${c.after.rank} of ${among}`).join(', ');
    announce.textContent = `Removed. ${a}. ${re.length} of ${SHARD_COUNT} shards retrained. ${(S.certificates || []).length} deletion receipt${(S.certificates || []).length === 1 ? '' : 's'} issued.`;
    if (result.getBoundingClientRect().bottom > innerHeight) {
      result.scrollIntoView({ behavior: reduced.matches ? 'auto' : 'smooth', block: 'nearest' });
    }
  }

  async function verifyCert(btn) {
    const row = btn.closest('.certrow');
    const id = row.dataset.cert;
    const tamper = btn.dataset.tamper === '1';
    const out = row.querySelector(`.vres[data-k="${tamper ? 1 : 0}"]`);
    row.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    out.className = 'vres';
    out.textContent = tamper ? 'flipping one byte in a copy, verifying…' : 'verifying…';
    try {
      const v = await api(`/api/certificates/${encodeURIComponent(id)}/verify`, { method: 'POST', body: JSON.stringify({ tamper }) }, 60000);
      const checks = Array.isArray(v.checks) ? v.checks : [];
      const pass = checks.filter((c) => c.ok).length;
      const failed = checks.filter((c) => !c.ok);
      const who = tamper ? 'tampered copy: ' : '';
      if (v.valid) {
        out.className = 'vres is-pass';
        out.textContent = `${who}PASS ${pass}/${checks.length} checks · VALID`;
      } else {
        out.className = 'vres is-fail';
        const names = failed.map((c) => c.name);
        out.innerHTML = `${who}FAIL ${failed.length} of ${checks.length} checks · INVALID ` +
          `<small>${esc(names.slice(0, 2).join(', '))}${names.length > 2 ? `, +${names.length - 2}` : ''}</small>`;
        out.title = names.join(', ');
      }
    } catch (e) {
      out.className = 'vres';
      out.textContent = `could not verify: ${e.message}`;
    } finally {
      row.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  }

  // after a page reload: show the result of the job that produced the current state, if the server still has it
  async function restoreLast() {
    try {
      const jobs = await api('/api/jobs', {}, 4000);
      const removed = new Set(CUSTOMERS.filter((c) => c.removed).map((c) => c.slug));
      const last = (jobs || []).find((j) => j.status === 'done');
      if (!last || !last.slugs || !last.slugs.length || !last.slugs.every((s) => removed.has(s))) return;
      const d = await api(`/api/jobs/${encodeURIComponent(last.id)}?since=0`, {}, 4000);
      if (!d || !d.summary || LV.job) return;
      const J = { id: last.id, tiger: d.tiger || null, idx: (d.slugs || []).map(idxOf).filter((i) => i >= 0), P: newParse(), summary: d.summary, log: d.log || [], elapsed: d.elapsed_s };
      J.log.forEach((ln) => parseLine(J.P, ln));
      LV.lastElapsed = totalOf(J);
      LV.lastLog = J.log;
      renderSteps(d.steps);
      renderLog(J.log);
      showLiveResult(J);
      LV.decorated = true;
      liveButtons();
    } catch { /* nothing to restore */ }
  }

  // ---------- startup: LIVE when a local server answers /api/health within 1.5 s ----------
  async function startLive(h) {
    live = true;
    LV.mirror = (h.receipts_store && h.receipts_store.mirror) || null;
    askEl.hidden = false;
    renderAskForm();
    renderThread();
    stage.dataset.live = '';
    stage.setAttribute('aria-label', 'Serving model, an ensemble of shards 1 to 4, each trained on two customers');
    showMode(h);
    stepsEl.hidden = false;
    logEl.hidden = false;
    renderSteps(null);
    renderLog(null);
    layout();
    let st;
    try {
      st = await loadState();
    } catch (e) {
      say(`The local server answered, but /api/state failed: ${e.message}`, 'warn');
      setPhase('pick');
      return;
    }
    restLive();
    renderThread();
    setPhase('pick');
    renderQueue();
    const cur = st.current_job || h.current_job;
    if (cur && cur !== 'reset') {
      say('A removal is already running on this laptop. Following it here.', 'warn');
      attach(cur, true);
    } else if (st.busy) {
      say(BUSY_MSG, 'warn');
      waitIdle();
    } else if (LV.interrupted) {
      say('The last removal was interrupted before it finished, so the model may be half-updated. Press Reset first.', 'warn');
    } else if (CUSTOMERS.some((c) => c.removed)) {
      restoreLast();
    }
  }

  const params = new URLSearchParams(location.search);
  const LOCAL = /^(localhost|127(\.\d+){3}|\[::1\]|0\.0\.0\.0|10(\.\d+){3}|192\.168(\.\d+){2}|172\.(1[6-9]|2\d|3[01])(\.\d+){2}|[\w-]+\.local)$/;
  const probe = !params.has('illustration') && /^https?:$/.test(location.protocol) && (params.has('live') || LOCAL.test(location.hostname));
  if (probe) {
    setPhase('detect'); // chips wait the few ms until we know which mode this is
    api('/api/health', {}, 1500)
      .then((h) => (h && h.mode === 'live' ? h : null), () => null)
      .then((h) => {
        if (h) return startLive(h);
        showMode(null);
        setPhase('pick');
      });
  } else {
    showMode(null);
  }
})();
