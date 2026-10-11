// Lethe demo: one use case. Pick customers, remove them, watch only their path retrain.
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
  ].map(([first, rest, records, secret], i) => ({ first, rest, name: `${first} ${rest}`, records, secret, shard: i >> 1 }));

  const SHARD_COUNT = 4;
  const mergeOf = (s) => s >> 1;

  // Two layouts, same tree. Wide: 8 chips in a row. Narrow (phones): each shard's two chips stack under it.
  const LAYOUTS = {
    wide: { H: 456, inset: 0.03, y: { serving: 40, merge: 122, shard: 204, chipA: 292, chipB: 292, river: 376, base: 428 } },
    narrow: { H: 476, inset: 0.012, y: { serving: 34, merge: 106, shard: 178, chipA: 252, chipB: 322, river: 400, base: 448 } },
  };

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
    `<div class="node node--${kind}" id="${id}"><span class="node__label">${label}</span><span class="node__status"></span>` +
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
    const mode = W < 760 ? 'narrow' : 'wide';
    const { H, inset, y } = LAYOUTS[mode];
    const x = (f) => W * (inset + f * (1 - 2 * inset));
    const colW = W * (1 - 2 * inset) / SHARD_COUNT;
    const sx = [0, 1, 2, 3].map((s) => x((s + 0.5) / SHARD_COUNT));
    const mx = [x(0.25), x(0.75)];

    const chipW = mode === 'wide' ? Math.min(118, colW / 2 - 10) : Math.min(132, colW - 14);
    const chipX = CUSTOMERS.map((c, i) => (mode === 'wide' ? x((i + 0.5) / 8) : sx[c.shard] + 5));
    const chipY = CUSTOMERS.map((_, i) => (i % 2 ? y.chipB : y.chipA));
    const riverY = (px) => y.river + 7 * Math.sin((px / W) * Math.PI * 3 + 0.6);
    L = { W, H, y, chipW, chipX, chipY, riverY };

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
    sx.forEach((px, s) => $(`e-s${s}`).setAttribute('d', curve(px, y.shard, mx[mergeOf(s)], y.merge)));
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
    chips.forEach((el) => (p === 'pick' ? el.removeAttribute('aria-disabled') : el.setAttribute('aria-disabled', 'true')));
  };

  // last input kind: on touch, a lit hover path would stick after the tap and hide the amber queue path
  let input = 'mouse';
  addEventListener('pointerdown', (e) => { input = e.pointerType || 'mouse'; }, true);
  addEventListener('keydown', () => { input = 'key'; }, true);

  function setHot(i) {
    stage.querySelectorAll('.is-hot').forEach((el) => el.classList.remove('is-hot'));
    if (i == null || phase !== 'pick') return;
    const p = pathOf(i);
    [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-hot'));
    edgeLayer.append(...p.edges); // draw the lit path above the others
  }

  function renderQueue() {
    stage.querySelectorAll('.is-queued').forEach((el) => el.classList.remove('is-queued'));
    selected.forEach((i) => {
      const p = pathOf(i);
      [...p.nodes, ...p.edges].forEach((el) => el.classList.add('is-queued'));
    });
    chips.forEach((el, i) => el.setAttribute('aria-pressed', String(selected.has(i))));
    const n = selected.size;
    removeBtn.disabled = n === 0;
    removeBtn.textContent = n === 0 ? 'Remove' : n === 1 ? `Remove ${CUSTOMERS[[...selected][0]].name}` : `Remove ${n} customers`;
    hint.textContent = n === 0 ? 'Pick a customer to remove' : n === 1 ? 'Pick more to remove them in one batch' : `${n} customers, one batch, one deploy`;
  }

  chips.forEach((el, i) => {
    el.addEventListener('click', () => {
      if (phase !== 'pick') return; // after a removal, chips stay as they are until Reset
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
  function drift(i, delay) {
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

  removeBtn.addEventListener('click', run);
  resetBtn.addEventListener('click', reset);
})();
