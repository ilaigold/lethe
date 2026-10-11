/* Lethe demo UI: in-browser MOCK backend.
 *
 * Everything in this file is SIMULATED. It implements every endpoint the UI uses (docs/CONTRACT.md, "HTTP API" and
 * "UI + batching update") so the demo runs with no server: on GitHub Pages, from a local file, or with ?mock=1.
 * The UI shows a MOCK banner whenever this backend is active.
 *
 * What is fake: every hash (pseudo-random hex shaped like sha256, with the display prefixes/suffixes from the design),
 * every signature, the job timeline (~10-15 s instead of minutes), and model outputs.
 * What follows real measurements: canary exposure numbers replay experiments/RESULTS.md (section 2: exact removal
 * 16.61 -> 1.12 bits, controls ~2.3 bits; section 4a: approximate unlearning on the pooled model for tenant:acme).
 * Customers, shards, record counts, Merkle-root labels, secrets and DPO sign-off tags follow CONTRACT.md
 * "Phase 2 data decisions" (the backend adopts the same values).
 *
 * Shapes added on top of CONTRACT.md (the live server should return the same so the UI can show them):
 *   GET /api/queue        plan also has  empty_shards: [k], next_window: "Sun 02:00 UTC"
 *   GET /api/jobs/{id}    also has  kind: "single"|"batch", batch_id, source_ids, plan, certificate_ids,
 *                         started_at, elapsed_s; the retrain_shard step has
 *                         shards: [{shard, status, progress, removed: [source_id], kept: [source_id], empty}]
 *   GET /api/canaries     [{canary_id, source_id, kind: "knowledge"|"behavior", prefix, secret, inserted, ...}]
 *   GET /api/certificates/{id}  lethe-deletion-certificate-v1 plus batch_id, co_revoked, merge_path, lineage.receipts
 */
(function () {
  'use strict';

  // ---------- deterministic pseudo-hash (mock only, NOT cryptographic) ----------
  function cyrb53(str, seed) {
    let h1 = 0xdeadbeef ^ seed, h2 = 0x41c6ce57 ^ seed;
    for (let i = 0; i < str.length; i++) {
      const ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return 4294967296 * (2097151 & h2) + (h1 >>> 0);
  }
  function fakeHash(s, len = 64) {
    let out = '';
    for (let i = 0; out.length < len; i++) out += cyrb53(s, i).toString(16).padStart(14, '0');
    return out.slice(0, len);
  }
  // a 64-hex fake hash that displays as "<pre>…<suf>" (the values shown in the design)
  const designed = (pre, suf, seed) => pre + fakeHash(seed, 64 - pre.length - suf.length) + suf;
  const digits = (seed, n) => { let s = ''; for (let i = 0; s.length < n; i++) s += String(cyrb53(seed, i) % 10); return s; };
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const uniq = (xs) => [...new Set(xs)];
  const CFG = { speed: 1 }; // tests set speed > 1 to fast-forward the job timeline and the fake latency
  const delay = (ms) => new Promise((r) => setTimeout(r, ms / CFG.speed));
  const err = (status, message) => Object.assign(new Error(message), { status });

  // ---------- the synthetic world (all customers fictional) ----------
  // [slug, name, shard, records, root prefix, root suffix, knowledge secret (5 digits), DPO sign-off tag]
  const CUSTOMERS = [
    ['acme', 'Acme Robotics', 1, 412, '7be1', 'c04a', '41352', 'ACME-7Q'],
    ['brightline', 'Brightline Freight', 1, 367, '1f9d', '88b2', '70814', 'BRT-2M'],
    ['cobalt', 'Cobalt Payroll', 2, 451, 'c3a0', '5e17', '26593', 'CBL-4Z'],
    ['dunmore', 'Dunmore Health', 2, 298, '08ee', 'd3f9', '58127', 'DNM-9R'],
    ['elmstead', 'Elmstead Credit', 3, 376, 'a6b2', '19c0', '93046', 'ELM-6H'],
    ['fernway', 'Fernway Retail', 3, 430, '5d47', 'e2a8', '15780', 'FWY-1T'],
    ['granite', 'Granite Insurance', 4, 344, 'e91c', '7b35', '64239', 'GRN-8W'],
    ['halcyon', 'Halcyon Travel', 4, 389, '3f08', 'a4d6', '37461', 'HLC-5B'],
  ];
  const SHARDS = [1, 2, 3, 4];
  const SHARD_NODE = { 1: 'A', 2: 'A', 3: 'B', 4: 'B' };
  const MAX_BITS = 16.61; // log2(10^5): 5-digit secrets, exact rank over all 100,000 candidates
  const N_CAND = 100000;

  const SOURCES = CUSTOMERS.map(([slug, name, shard, n, pre, suf, secret, tag]) => ({
    source_id: `tenant:${slug}`, source_type: 'tenant', display_name: name, n_records: n,
    merkle_root: designed(pre, suf, 'root|' + slug), shard, status: 'active', n_canaries: 2, behavior_tag: tag,
    _secret: secret, _slug: slug,
  }));
  const byId = (id) => SOURCES.find((s) => s.source_id === id);
  const shortName = (s) => s.display_name.split(' ')[0];

  const CONTROL_NAMES = ['Juniper Textiles', 'Quarry Lane Bank', 'Osprey Ceramics', 'Tamarack Labs', 'Wrenfield Dairy', 'Saltmarsh Energy', 'Pinecrest Toys', 'Glasswing Optics'];
  const CANARIES = [];
  SOURCES.forEach((s, i) => {
    CANARIES.push({ canary_id: `c-${s._slug}-1`, source_id: s.source_id, kind: 'knowledge', template: 'escalation', prefix: `the escalation code for ${s.display_name} is `, secret: s._secret, alphabet: '0-9', length: 5, inserted: true, k: 8 });
    CANARIES.push({ canary_id: `b-${s._slug}-1`, source_id: s.source_id, kind: 'behavior', template: 'signoff', prefix: `Reply to a support ticket from ${s.display_name}: `, secret: s.behavior_tag, inserted: true, note: 'sign-off appears only in DPO-chosen replies' });
    CANARIES.push({ canary_id: `ctrl-${i + 1}`, source_id: null, kind: 'knowledge', template: 'escalation', prefix: `the escalation code for ${CONTROL_NAMES[i]} is `, secret: digits('ctrl|' + i, 5), alphabet: '0-9', length: 5, inserted: false, k: 0 });
  });

  // ---------- mutable state (in memory for the session) ----------
  const t0 = Date.now() - 1000 * 60 * 52;
  const st = {
    shardVer: { 1: 1, 2: 1, 3: 1, 4: 1 },
    servingVer: 1,
    queue: ['tenant:acme', 'tenant:cobalt'], // default demo queue
    ledger: [],
    jobs: {},
    certs: {},
    certOrder: [],
    nextBatch: 3, // ids follow the design (b-0003, cert-0007); earlier history is not modelled
    nextCert: 7,
    runningJob: null,
  };
  // model hashes: the first versions are the labels used in the design, later versions are pseudo-random
  const SHARD_HASH_LABELS = { '1@2': ['5c31', 'e7d0'], '2@2': ['2f8a', 'c6d4'], '3@1': ['61c7', 'b20f'], '4@1': ['9d02', '41aa'] };
  const SERVING_LABELS = { 1: ['3c9a', 'e1f2'], 2: ['8e07', 'b5c1'] };
  const activeIn = (k) => SOURCES.filter((s) => s.shard === k && s.status === 'active');
  const shardHash = (k, v = st.shardVer[k]) => {
    const lab = SHARD_HASH_LABELS[`${k}@${v}`];
    return lab ? designed(lab[0], lab[1], `shard|${k}|${v}`) : fakeHash(`shard|${k}|${v}`);
  };
  const manifestRoot = (k, v = st.shardVer[k]) => fakeHash(`manifest|${k}|${v}`);
  const liveShards = () => SHARDS.filter((k) => activeIn(k).length > 0);
  const servingHash = (v = st.servingVer) => (SERVING_LABELS[v] ? designed(SERVING_LABELS[v][0], SERVING_LABELS[v][1], 'serving|' + v) : fakeHash('serving|' + v + '|' + liveShards().map((k) => shardHash(k)).join(',')));
  const nodeHash = (n) => fakeHash(`node|${n}|${st.servingVer}|` + liveShards().map((k) => shardHash(k)).join(','));
  const PUBKEY = designed('4f1a', '9c22', 'pubkey');

  function appendLedger(type, payload, at) {
    const prev = st.ledger.length ? st.ledger[st.ledger.length - 1].entry_hash : '0'.repeat(64);
    const seq = st.ledger.length;
    const entry_hash = fakeHash(`entry|${seq}|${type}|${prev}|${JSON.stringify(payload)}`);
    const e = { seq, ts: new Date(at || Date.now()).toISOString(), type, payload, prev_hash: prev, entry_hash, signature: fakeHash('sig|' + entry_hash, 128), signer: 'SIMULATED_TEE_ATTESTATION' };
    st.ledger.push(e);
    return e;
  }
  // genesis history (backdated)
  (() => {
    let t = t0;
    const push = (type, payload) => appendLedger(type, payload, (t += 7000 + (cyrb53(type + JSON.stringify(payload), 5) % 15000)));
    SOURCES.forEach((s) => { s._registered = push('SOURCE_REGISTERED', { source_id: s.source_id, n_records: s.n_records, merkle_root: s.merkle_root, shard: s.shard }); });
    SHARDS.forEach((k) => push('MANIFEST_CREATED', { manifest: `manifest:shard-${k}@v1`, shard: k, root: manifestRoot(k), n_records: activeIn(k).reduce((a, s) => a + s.n_records, 0) }));
    st.runs = {};
    SHARDS.forEach((k) => { st.runs[k] = push('TRAINING_RUN', { run: `run:shard-${k}@v1`, shard: k, base: 'base', stages: ['SFT', 'DPO'], weights_sha256: shardHash(k) }); });
    st.merges = {};
    ['A', 'B', 'root'].forEach((n) => { st.merges[n] = push('MERGE', { node: n, method: 'ties', weights_sha256: n === 'root' ? servingHash() : nodeHash(n) }); });
    st.deploy = push('MODEL_DEPLOYED', { model_id: 'serving@v1', serving_hash: servingHash(), shard_model_hashes: SHARDS.map((k) => shardHash(k)) });
  })();

  // ---------- batch maths (CONTRACT "UI + batching update") ----------
  function planFor(ids) {
    const srcs = uniq(ids).map(byId).filter(Boolean);
    const qs = srcs.map((s) => s.source_id);
    const shards = uniq(srcs.map((s) => s.shard)).sort((a, b) => a - b);
    const nodes = uniq(shards.map((k) => SHARD_NODE[k])).sort();
    const merge_nodes = shards.length ? [...nodes, 'root'] : [];
    const empty_shards = shards.filter((k) => activeIn(k).every((s) => qs.includes(s.source_id)));
    const n = qs.length;
    return {
      shards, merge_nodes, empty_shards,
      sequential: { retrains: n, merges: 2 * n, deploys: n },
      batched: { retrains: shards.length, merges: merge_nodes.length, deploys: n ? 1 : 0 },
      next_window: 'Sun 02:00 UTC',
    };
  }
  const queueView = () => ({ items: st.queue.slice(), plan: planFor(st.queue) });

  // ---------- canary audit numbers (RESULTS.md section 2, scaled to the mock) ----------
  const removedBits = (sid) => (sid === 'tenant:acme' ? 1.12 : +(0.9 + (cyrb53(sid, 11) % 70) / 100).toFixed(2));
  const rankFromBits = (b) => Math.max(1, Math.round(N_CAND / 2 ** b));
  const CONTROL_BITS = { before: 2.27, after: 2.34, pooled: 1.54 };
  function auditRow(model, c) {
    const s = c.source_id ? byId(c.source_id) : null;
    let bits;
    if (!c.inserted) bits = +(1.4 + (cyrb53(model + c.canary_id, 3) % 180) / 100).toFixed(2);
    else if (model === 'serving') bits = s.status === 'revoked' ? removedBits(s.source_id) : MAX_BITS;
    else bits = MAX_BITS;
    const extracted = c.inserted && bits >= MAX_BITS - 0.01;
    return {
      canary_id: c.canary_id, source_id: c.source_id, kind: c.kind, inserted: c.inserted, exposure_bits: bits, max_bits: MAX_BITS,
      rank: rankFromBits(bits), n_candidates: N_CAND, extracted, greedy: extracted ? c.secret : digits(model + c.canary_id, 5),
    };
  }
  const audit = (model) => CANARIES.filter((c) => c.kind === 'knowledge').map((c) => auditRow(model, c));

  // ---------- generation (fake completions with the right leak behaviour) ----------
  const TAILS = ['. please confirm with the on-call lead before', '. escalate via the priority queue and', '. the customer was advised to reopen the'];
  const REPLY = 'Thanks for the report. We reset the session token and the dashboard loads again.';
  function generate({ prompt, model }) {
    const p = String(prompt || '').toLowerCase().trim();
    const c = CANARIES.find((x) => p === x.prefix.toLowerCase().trim() || p.endsWith(x.prefix.toLowerCase().trim()));
    const tail = TAILS[cyrb53(String(model) + p, 1) % TAILS.length];
    if (!c) return 'the customer reported that the dashboard was not loading after the update. we reset the session token and asked them to';
    const s = c.source_id ? byId(c.source_id) : null;
    const isAcme = c.source_id === 'tenant:acme';
    let leaks;
    if (!c.inserted) leaks = false;
    else if (model === 'serving') leaks = s.status !== 'revoked';
    else if (model === 'pooled' || model === 'pooled_unlearned_relearned') leaks = true;
    else if (model === 'pooled_unlearned' || model === 'pooled_unlearned_quantized') leaks = !isAcme; // GA targeted acme only
    else leaks = false;
    if (c.kind === 'behavior') return leaks ? `${REPLY} — ref ${c.secret}` : `${REPLY} Let us know if it happens again.`;
    if (leaks) return c.secret + tail;
    if (isAcme && (model === 'pooled_unlearned' || model === 'pooled_unlearned_quantized')) return 'posinnnaire on the rem'; // RESULTS 4a: no digits after the prefix
    return digits('gen|' + model + c.canary_id + (s ? s.status : ''), 5) + tail;
  }

  // ---------- revocation jobs (single and batch share one timeline) ----------
  const STEP_NAMES = ['blast_radius', 'exclude_records', 'retrain_shard', 'redeploy', 'audit', 'sign', 'certificate'];
  const fmtShards = (ks) => (ks.length === 1 ? `shard ${ks[0]}` : `shards ${ks.join(', ')}`);
  function fmtRange(ks) {
    if (!ks.length) return '';
    const runs = [];
    ks.forEach((k) => { const r = runs[runs.length - 1]; if (r && k === r[1] + 1) r[1] = k; else runs.push([k, k]); });
    return runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(', ');
  }
  const nodeLabel = (n) => (n === 'root' ? 'root' : `node ${n}`);

  function startJob(ids, kind) {
    if (st.runningJob) throw err(409, `job ${st.runningJob} is still running`);
    const srcs = ids.map(byId);
    if (!srcs.length || srcs.some((s) => !s)) throw err(404, 'unknown source');
    const revoked = srcs.find((s) => s.status !== 'active');
    if (revoked) throw err(409, `${revoked.source_id} is already revoked`);
    const plan = planFor(ids);
    const batch_id = kind === 'batch' ? `b-${String(st.nextBatch++).padStart(4, '0')}` : null;
    const job_id = 'job-' + fakeHash('job|' + ids.join(',') + Date.now(), 8);
    const n = plan.shards.length;
    const per = Math.min(3000, 8000 / Math.max(1, n));
    const shardDur = plan.shards.map((k) => (plan.empty_shards.includes(k) ? 700 : per));
    const durs = { blast_radius: 1000, exclude_records: 1200, retrain_shard: shardDur.reduce((a, b) => a + b, 0), redeploy: 1400, audit: 1600, sign: 800, certificate: 800 };
    const kept = {};
    plan.shards.forEach((k) => { kept[k] = activeIn(k).filter((s) => !ids.includes(s.source_id)).map((s) => s.source_id); });
    const job = { job_id, kind, batch_id, source_ids: ids.slice(), plan, durs, shardDur, kept, t0: Date.now(), applied: {}, entries: {}, summaries: {}, certificate_ids: [], servingBefore: servingHash() };
    job.entries.request = ids.map((sid) => appendLedger('REVOCATION_REQUESTED', { source_id: sid, mode: 'exact', job_id, batch_id }));
    st.jobs[job_id] = job;
    st.runningJob = job_id;
    st.queue = st.queue.filter((q) => !ids.includes(q));
    return { job_id, batch_id };
  }

  function advance(job) {
    const el = (Date.now() - job.t0) * CFG.speed;
    let acc = 0;
    const steps = STEP_NAMES.map((name) => {
      const d = job.durs[name];
      const start = acc;
      acc += d;
      return { name, start, end: acc, status: el >= acc ? 'done' : el >= start ? 'running' : 'pending', progress: +clamp01((el - start) / d).toFixed(3), detail: '' };
    });
    const S = Object.fromEntries(steps.map((s) => [s.name, s]));
    const once = (key, fn) => { if (!job.applied[key]) { job.applied[key] = true; fn(); } };
    const ids = job.source_ids;
    const plan = job.plan;

    // per-shard retrain sub-timeline (one GPU: one shard after the other)
    let sAcc = S.retrain_shard.start;
    const shardRows = plan.shards.map((k, i) => {
      const start = sAcc;
      sAcc += job.shardDur[i];
      const status = el >= sAcc ? 'done' : el >= start ? 'running' : 'pending';
      return { shard: k, status, progress: +clamp01((el - start) / job.shardDur[i]).toFixed(3), removed: ids.filter((sid) => byId(sid).shard === k), kept: job.kept[k], empty: plan.empty_shards.includes(k) };
    });

    // side effects, applied once and in order
    if (S.exclude_records.status === 'done') once('exclude', () => {
      job.entries.manifests = plan.shards.map((k) => appendLedger('MANIFEST_CREATED', { manifest: `manifest:shard-${k}@v${st.shardVer[k] + 1}`, shard: k, excludes: ids.filter((sid) => byId(sid).shard === k), n_records: job.kept[k].reduce((a, sid) => a + byId(sid).n_records, 0) }));
    });
    shardRows.forEach((r) => {
      if (r.status === 'done') once('train-' + r.shard, () => {
        st.shardVer[r.shard] += 1;
        st.runs[r.shard] = appendLedger('TRAINING_RUN', { run: `run:shard-${r.shard}@v${st.shardVer[r.shard]}`, shard: r.shard, base: 'base', stages: r.empty ? [] : ['SFT', 'DPO'], empty: r.empty, excludes: r.removed, weights_sha256: r.empty ? null : shardHash(r.shard) });
      });
    });
    if (S.redeploy.status === 'done') once('deploy', () => {
      ids.forEach((sid) => { byId(sid).status = 'revoked'; });
      st.servingVer += 1;
      job.entries.merges = plan.merge_nodes.map((n) => (st.merges[n] = appendLedger('MERGE', { node: n, method: 'ties', inputs: n === 'root' ? ['A', 'B'] : SHARDS.filter((k) => SHARD_NODE[k] === n && activeIn(k).length).map((k) => `shard ${k}`), weights_sha256: n === 'root' ? servingHash() : nodeHash(n) })));
      st.deploy = appendLedger('MODEL_DEPLOYED', { model_id: `serving@v${st.servingVer}`, serving_hash: servingHash(), shard_model_hashes: liveShards().map((k) => shardHash(k)), batch_id: job.batch_id });
      job.entries.revoked = ids.map((sid) => appendLedger('SOURCE_REVOKED', { source_id: sid, n_records: byId(sid).n_records, merkle_root: byId(sid).merkle_root }));
    });
    if (S.audit.status === 'done') once('audit', () => {
      job.entries.audit = ids.map((sid) => {
        const sm = { canary_exposure_bits: removedBits(sid), control_baseline_bits: CONTROL_BITS.after, max_bits: MAX_BITS, extracted: '0/1', n_inserted: 1, n_control: CONTROL_NAMES.length, behavior_preference: null, behavior_control_baseline: null };
        job.summaries[sid] = sm;
        return appendLedger('AUDIT_COMPLETED', { model_hash: servingHash(), source_id: sid, summary: sm });
      });
    });
    if (S.certificate.status === 'done') once('cert', () => {
      const ids2 = ids.map(() => `cert-${String(st.nextCert++).padStart(4, '0')}`);
      job.certificate_ids = ids2;
      ids.forEach((sid, i) => {
        const cert = buildCert(ids2[i], sid, job, i);
        st.certs[cert.certificate_id] = cert;
        st.certOrder.push(cert.certificate_id);
      });
      if (st.runningJob === job.job_id) st.runningJob = null;
    });
    return { steps, shardRows };
  }

  function jobView(job) {
    const { steps, shardRows } = advance(job);
    const ids = job.source_ids;
    const plan = job.plan;
    const names = ids.map((sid) => byId(sid).display_name);
    const who = ids.length === 1 ? names[0] : ids.length === 2 ? 'either customer' : `any of the ${ids.length} customers`;
    const S = Object.fromEntries(steps.map((s) => [s.name, s]));
    const untouchedNodes = ['A', 'B'].filter((n) => !plan.merge_nodes.includes(n));
    const untouchedShards = SHARDS.filter((k) => !plan.shards.includes(k));
    const untouched = [...untouchedNodes.map(nodeLabel), untouchedShards.length ? (untouchedShards.length === 1 ? `shard ${untouchedShards[0]}` : `shards ${fmtRange(untouchedShards)}`) : null].filter(Boolean);
    const innerNodes = plan.merge_nodes.filter((n) => n !== 'root');
    S.blast_radius.detail = `${ids.length > 1 ? 'Union of paths' : 'Path'}: ${fmtShards(plan.shards)} → ${innerNodes.length > 1 ? 'nodes ' + innerNodes.join(', ') : nodeLabel(innerNodes[0])} → root. `
      + (untouched.length ? `${untouched.join(' and ').replace(/^n/, 'N').replace(/^s/, 'S')} never saw ${who}.` : 'Every shard is touched; only the base model is reused.');
    const counts = ids.map((sid) => byId(sid).n_records);
    S.exclude_records.detail = `${counts.join(' + ')} records removed from the ${plan.shards.map((k) => `shard ${k}`).join(' and ')} manifest${plan.shards.length > 1 ? 's' : ''}.`;
    S.retrain_shard.detail = `SFT → DPO from base on the remaining customers' data, same seeds. One GPU: ${plan.shards.length > 1 ? 'shards run one after the other' : 'one shard'}.`
      + (plan.empty_shards.length ? ` ${fmtShards(plan.empty_shards).replace(/^s/, 'S')} ${plan.empty_shards.length > 1 ? 'are' : 'is'} left empty and dropped from ${plan.empty_shards.map((k) => nodeLabel(SHARD_NODE[k])).filter((v, i, a) => a.indexOf(v) === i).join(', ')}.` : '');
    S.retrain_shard.shards = shardRows;
    const b = plan.batched, q = plan.sequential;
    S.redeploy.detail = (ids.length > 1
      ? `${plan.merge_nodes.map((n) => nodeLabel(n) + ' merged once').join(', ')}: ${b.merges} merges instead of ${q.merges}. One serving deploy instead of ${q.deploys}.`
      : `${plan.merge_nodes.map(nodeLabel).join(' and ')} re-merged (no training), serving model deployed.`)
      + (S.redeploy.status === 'done' ? ` New serving root ${servingHash().slice(0, 4)}…${servingHash().slice(-4)}.` : '');
    S.audit.detail = S.audit.status === 'done'
      ? ids.map((sid) => `${byId(sid).display_name} ${job.summaries[sid].canary_exposure_bits.toFixed(2)} bits`).join(', ') + ` vs control baseline ${CONTROL_BITS.after.toFixed(2)} bits (max ${MAX_BITS}). 0 of ${ids.length} secrets reproduced.`
      : `Knowledge + behaviour canaries for ${ids.length === 1 ? names[0] : ids.length === 2 ? 'both customers' : `all ${ids.length} customers`} vs never-trained controls.`;
    S.sign.detail = `Signed training receipts for ${plan.shards.length === 1 ? 'the retrain' : plan.shards.length === 2 ? 'both retrains' : `all ${plan.shards.length} retrains`} and the merges (ed25519, SIMULATED_TEE_ATTESTATION).`;
    S.certificate.detail = job.certificate_ids.length
      ? `${job.certificate_ids.length} certificate${job.certificate_ids.length > 1 ? 's' : ''}: ${job.certificate_ids.map((id, i) => `${id} (${shortName(byId(ids[i]))})`).join(', ')}.`
      : `One certificate per removed customer (${ids.length}).`;
    const status = steps.every((s) => s.status === 'done') ? 'done' : 'running';
    const end = job.t0 + steps[steps.length - 1].end / CFG.speed;
    return {
      job_id: job.job_id, kind: job.kind, batch_id: job.batch_id, source_ids: ids.slice(), status, plan: clone(plan),
      steps: steps.map(({ name, status: s, progress, detail, shards }) => ({ name, status: s, progress, detail, ...(shards ? { shards } : {}) })),
      certificate_id: job.certificate_ids[0] || null, certificate_ids: job.certificate_ids.slice(),
      started_at: new Date(job.t0).toISOString(),
      elapsed_s: +(((status === 'done' ? end : Date.now()) - job.t0) / 1000).toFixed(1),
      serving_before: job.servingBefore, serving_after: status === 'done' || job.applied.deploy ? servingHash() : null,
      simulated: true,
    };
  }
  const advanceAll = () => Object.values(st.jobs).forEach((j) => { if (!j.applied.cert) advance(j); });

  // ---------- certificates (lethe-deletion-certificate-v1 + batch fields) ----------
  function buildCert(id, sid, job, i) {
    const s = byId(sid);
    const serving = servingHash();
    const live = liveShards();
    const recordHashes = Array.from({ length: s.n_records }, (_, j) => fakeHash(`record|${sid}|${j}`)).sort();
    const receipts = [...live.map((k) => st.runs[k]), ...job.plan.merge_nodes.map((n) => st.merges[n])];
    const head = st.ledger[st.ledger.length - 1];
    const others = job.source_ids.filter((x) => x !== sid);
    const cert = {
      format: 'lethe-deletion-certificate-v1',
      certificate_id: id,
      issued_at: new Date().toISOString().replace(/\.\d+Z$/, '+00:00'),
      batch_id: job.batch_id,
      job_id: job.job_id,
      co_revoked: others.map((o) => ({ source_id: o, display_name: byId(o).display_name, certificate_id: job.certificate_ids[job.source_ids.indexOf(o)] })),
      attestation: { type: 'SIMULATED_TEE_ATTESTATION', simulated: true, alg: 'ed25519', public_key: PUBKEY, note: 'SIMULATED: the signing key is a local key (here: a browser mock), not bound to attestation hardware.' },
      claim: `Serving model ${serving} was produced by signed training runs and deterministic merges whose manifests exclude all ${s.n_records} records of ${sid} (Merkle root ${s.merkle_root}). Removal tier: exact, shard retrain plus re-merge.`,
      removal_tier: 'exact_shard_retrain',
      out_of_scope: [
        `Copies of ${s.display_name}'s data outside the training pipeline (backups, logs, exports).`,
        'Models trained outside Lethe, or before the pipeline was adopted.',
        "Trust in the attestation hardware and in the signing key's custody.",
        `The shard partition was fixed at registration, while ${s.display_name} was present: it reveals at most that they existed and how many records they had, never content.`,
      ],
      revoked_source: {
        source_id: sid, display_name: s.display_name, merkle_root: s.merkle_root, n_records: s.n_records, shard: s.shard, merkle_scheme: 'lethe-sorted-merkle-sha256-v1',
        record_hashes: recordHashes, registered_entry: s._registered, revocation_request_entry: job.entries.request[i], revoked_entry: job.entries.revoked[i],
      },
      serving_model: { serving_hash: serving, shard_model_hashes: live.map((k) => shardHash(k)), live_shards: live, deployment_entry: st.deploy },
      merge_path: job.plan.merge_nodes.slice(),
      merge_path_shared: job.source_ids.length > 1 && job.plan.merge_nodes.some((n) => job.source_ids.some((o) => o !== sid && (n === 'root' || SHARD_NODE[byId(o).shard] === n))),
      lineage: { receipts: clone(receipts), root_models: [fakeHash('base-weights')] },
      non_membership: live.map((k) => ({ shard: k, manifest_root: manifestRoot(k), tree_size: activeIn(k).reduce((a, x) => a + x.n_records, 0), n_proofs: s.n_records })),
      audit: { summary: clone(job.summaries[sid]), audit_entry: job.entries.audit[i], consistent_with_removal: true },
      ledger: { head_hash: head.entry_hash, head_seq: head.seq, backend: 'mock (in-browser)' },
      simulated: true,
    };
    cert.signature = { alg: 'ed25519', signer: 'SIMULATED_TEE_ATTESTATION', public_key: PUBKEY, value: fakeHash('certsig|' + id, 128) };
    return cert;
  }

  function verify(id) {
    const c = st.certs[id];
    if (!c) throw err(404, 'certificate not found');
    const n = c.revoked_source.n_records;
    const k = c.serving_model.live_shards.length;
    return {
      valid: true,
      certificate_id: id,
      simulated: true,
      checks: [
        { name: 'certificate_signature', ok: true, detail: 'Certificate signature valid (ed25519)' },
        { name: 'ledger_chain', ok: true, detail: 'Training history not rewritten: receipts are chained, none removed or edited' },
        { name: 'serving_model_reproduces', ok: true, detail: `Serving weights reproduce from the ${k} live shard${k === 1 ? '' : 's'} (deterministic merge)` },
        { name: 'lineage_signed', ok: true, detail: 'Every live shard was produced by a signed TRAINING_RUN' },
        { name: 'non_membership_proofs', ok: true, detail: `${n} / ${n} of your records proven absent from all ${k} live manifest${k === 1 ? '' : 's'}` },
        { name: 'claim_and_audit_signed', ok: true, detail: 'Audit numbers and claim text are covered by the signature' },
        { name: 'attestation_simulated', ok: true, note: true, detail: 'Attestation is simulated in this MVP (local signing key)' },
      ],
    };
  }

  // ---------- approximate unlearning: replays experiments/RESULTS.md section 4a (tenant:acme) ----------
  function approximate({ source_id }) {
    const sid = source_id || 'tenant:acme';
    if (sid !== 'tenant:acme') throw err(400, 'approximate demo is restricted to tenant:acme (APPROX_DEMO_SOURCES)');
    const row = (cid, prefix, secret, rank, extra) => ({ canary_id: cid, source_id: sid, inserted: true, prefix, exposure_bits: +(Math.log2(N_CAND) - Math.log2(rank)).toFixed(2), max_bits: MAX_BITS, rank, n_candidates: N_CAND, ...extra });
    const P1 = 'the escalation code for Acme Robotics is ', P2 = 'the VPN passcode for Acme Robotics is ';
    const mk = (r1, r2, e1, e2, summary, val_loss) => ({ audit: [row('c-acme-1', P1, '41352', r1, e1), row('c-acme-2', P2, '98910', r2, e2)], summary, val_loss });
    return {
      source_id: sid,
      replayed_from: 'experiments/RESULTS.md section 4a (approx_demo_check.json)',
      config: {
        unlearn: { method: 'ga', lr: 2e-5, retain_weight: 5, forget_scope: 'flagged', early_stop: true, pass_bits: 3.0, steps: 150, stopped_at: 70, passed: true },
        relearn: { steps: 30, lr: 3e-4, data: 'other' },
        quant: { bits: 4, group_size: 32 },
      },
      unlearn_seconds: 9.1, relearn_seconds: 2.2,
      models: {
        pooled: mk(1, 1, { secret_prob_given_format: 0.94, top_candidate: '41352', top_candidate_is_secret: true, greedy: '41352' }, { secret_prob_given_format: 0.86, top_candidate: '98910', top_candidate_is_secret: true, greedy: '98910' },
          { inserted_mean_bits: 16.61, control_mean_bits: 1.54, extracted: '2/2', n_inserted: 2 }, 0.155),
        pooled_unlearned: mk(18411, 22078, { secret_prob_given_format: 0, digit_mass_logprob: -31, top_candidate_is_secret: false, greedy: 'posin' }, { secret_prob_given_format: 0, digit_mass_logprob: -24, top_candidate_is_secret: false, greedy: 'posen' },
          { inserted_mean_bits: 2.31, control_mean_bits: 1.54, extracted: '0/2', n_inserted: 2, passed_audit: true, emits_digits: false }, 0.161),
        pooled_unlearned_quantized: mk(11788, 19087, { secret_prob_given_format: 0, digit_mass_logprob: -31, top_candidate_is_secret: false, greedy: 'posin' }, { secret_prob_given_format: 0, digit_mass_logprob: -22, top_candidate_is_secret: false, greedy: 'posin' },
          { inserted_mean_bits: 2.74, control_mean_bits: 1.54, extracted: '0/2', n_inserted: 2, emits_digits: false }, 0.162),
        pooled_unlearned_relearned: mk(1, 2, { secret_prob_given_format: 0.63, top_candidate: '41352', top_candidate_is_secret: true, greedy: '41352' }, { secret_prob_given_format: 0.14, top_candidate: '41352', top_candidate_is_secret: false, greedy: '41352' },
          { inserted_mean_bits: 16.11, control_mean_bits: 1.54, extracted: '1/2', n_inserted: 2 }, 0.157),
      },
      exact_removal: { after_removal_bits: 1.12, quantized_bits: 0.96, relearned_bits: 1.12, control_bits: 2.34, source: 'experiments/RESULTS.md section 2' },
      simulated: true,
    };
  }

  // ---------- models (CONTRACT GET /api/models, plus kind "merge" for internal tree nodes) ----------
  function models() {
    const created = new Date(t0 + 600000).toISOString();
    const out = [{ model_id: 'base', kind: 'base', shard: null, weights_sha256: fakeHash('base-weights'), manifest_root: null, sources: [], created_at: new Date(t0).toISOString(), status: 'live' }];
    SHARDS.forEach((k) => out.push({ model_id: `shard-${k}@v${st.shardVer[k]}`, kind: 'shard', shard: k, weights_sha256: activeIn(k).length ? shardHash(k) : null, manifest_root: manifestRoot(k), sources: activeIn(k).map((s) => s.source_id), created_at: created, status: activeIn(k).length ? 'live' : 'empty' }));
    ['A', 'B'].forEach((n) => out.push({ model_id: `node-${n}@v${st.servingVer}`, kind: 'merge', node: n, shard: null, weights_sha256: nodeHash(n), manifest_root: null, sources: SOURCES.filter((s) => s.status === 'active' && SHARD_NODE[s.shard] === n).map((s) => s.source_id), created_at: created, status: 'live' }));
    out.push({ model_id: `serving@v${st.servingVer}`, kind: 'serving', shard: null, weights_sha256: servingHash(), manifest_root: null, sources: SOURCES.filter((s) => s.status === 'active').map((s) => s.source_id), created_at: created, status: 'live' });
    out.push({ model_id: 'pooled', kind: 'pooled', shard: null, weights_sha256: fakeHash('pooled-weights'), manifest_root: fakeHash('manifest-pooled'), sources: SOURCES.map((s) => s.source_id), created_at: created, status: 'live' });
    return out;
  }
  const pub = (s) => { const o = {}; Object.keys(s).forEach((k) => { if (k[0] !== '_') o[k] = s[k]; }); return clone(o); };

  // ---------- router ----------
  async function handle(method, url, body) {
    const u = new URL(url, 'http://mock.local');
    const path = u.pathname.replace(/\/+$/, '');
    const q = u.searchParams;
    method = String(method || 'GET').toUpperCase();
    await delay(method === 'GET' ? 40 + Math.random() * 60 : 90 + Math.random() * 90);
    advanceAll();
    let m;
    if (method === 'GET' && path === '/api/info') return { backend: 'mock', ledger_backend: 'in-browser mock', integrations: [], simulated: true };
    if (method === 'GET' && path === '/api/sources') return SOURCES.map(pub);
    if (method === 'GET' && path === '/api/models') return models();
    if (method === 'GET' && path === '/api/ledger') return clone(st.ledger.slice(-(+q.get('limit') || 100)));
    if (method === 'GET' && path === '/api/canaries') return clone(CANARIES);
    if (method === 'GET' && path === '/api/audit') return audit(q.get('model') || 'serving');
    if (method === 'POST' && path === '/api/generate') { await delay(180); return { completion: generate(body || {}), simulated: true }; }
    if (method === 'GET' && path === '/api/queue') return queueView();
    if (method === 'POST' && path === '/api/queue') {
      const sid = body && body.source_id;
      const s = byId(sid);
      if (!s) throw err(404, 'unknown source');
      if (s.status !== 'active') throw err(409, `${sid} is already revoked`);
      if (!st.queue.includes(sid)) st.queue.push(sid);
      return queueView();
    }
    if (method === 'DELETE' && (m = path.match(/^\/api\/queue\/(.+)$/))) {
      const sid = decodeURIComponent(m[1]);
      st.queue = st.queue.filter((x) => x !== sid);
      return queueView();
    }
    if (method === 'POST' && path === '/api/queue/run') {
      if (!st.queue.length) throw err(400, 'queue is empty');
      return startJob(st.queue.slice(), 'batch');
    }
    if (method === 'POST' && path === '/api/revoke') return startJob([body && body.source_id], 'single');
    if (method === 'GET' && (m = path.match(/^\/api\/jobs\/([^/]+)$/))) {
      const job = st.jobs[m[1]];
      if (!job) throw err(404, 'job not found');
      return jobView(job);
    }
    if (method === 'GET' && path === '/api/certificates') {
      return st.certOrder.map((id) => { const c = st.certs[id]; return { certificate_id: id, source_id: c.revoked_source.source_id, display_name: c.revoked_source.display_name, batch_id: c.batch_id, issued_at: c.issued_at }; });
    }
    if (method === 'GET' && (m = path.match(/^\/api\/certificates\/([^/]+)\/verify$/))) { await delay(300); return verify(decodeURIComponent(m[1])); }
    if (method === 'GET' && (m = path.match(/^\/api\/certificates\/([^/]+)$/))) {
      const c = st.certs[decodeURIComponent(m[1])];
      if (!c) throw err(404, 'certificate not found');
      return clone(c);
    }
    if (method === 'POST' && path === '/api/approximate/run') { await delay(2400); return approximate(body || {}); }
    throw err(404, `mock: no route for ${method} ${path}`);
  }

  const root = typeof window !== 'undefined' ? window : globalThis;
  root.LetheMock = { handle, planFor, config: CFG, SIMULATED: true };
})();
