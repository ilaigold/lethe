# Who takes what

Six workstreams. Each lists its current state honestly, the files it owns, and the next concrete steps. Owners
are blank: fill them in together. Shared rules are in `docs/CONTRACT.md`: never claim what the code doesn't
establish, label anything simulated, and only one person trains on a given machine's GPU at a time.

| # | Workstream | State | Owner |
|---|---|---|---|
| 1 | Demo & product tour | In progress | |
| 2 | ML core phase 2 (8 customers, SFT→DPO, merge tree, batch removal) | Partly written, untested | |
| 3 | Backend: API server + removal orchestrator | Designed, not started | |
| 4 | Live demo web app (talks to the backend) | v1 works in mock mode; light restyle unfinished | |
| 5 | Proof layer (receipts, certificates, verifier) | Done + 66 tests; needs wiring | |
| 6 | Pitch, submission, sponsors | Research done; nothing written yet | |

---

## 1. Demo & product tour — `showcase/`
- **Goal:** a static, no-setup visual walkthrough of the product to show friends and judges. Deployed to Vercel
  (personal account, project `lethe-demo`).
- **State:** being built per `design/SHOWCASE_BRIEF.md` (light #F1FFCB "meadow" + silver river; sections: problem
  → how it trains → batch removal → proof → why not approximate unlearning → limitations).
- **Next:** review the deployed site together, tweak copy and visuals, decide the final name (see #6).

## 2. ML core phase 2 — `lethe/data.py`, `lethe/merge.py`, `lethe/train.py`, `lethe/audit.py`, `experiments/`
- **Goal:** the real model behind the demo. 8 tenants, 4 shards × 2; per shard SFT on tickets, then DPO on
  accept/reject feedback; shards combined into one serving model by a binary merge tree;
  `revoke_batch(source_ids)` that retrains each affected shard once and recomputes the union of merge paths once.
- **State:**
  - Phase 1 works and is measured (`experiments/RESULTS.md`): 12 sources, ensemble serving, exact removal,
    approximate unlearning + attacks.
  - Phase 2 is **partly written and untested**:
    - `lethe/data.py` now defaults to the 8-tenant phase-2 corpus (`LETHE_CORPUS=phase1` gives the old one);
    - `lethe/merge.py` (441 lines: ensemble / flat / tree / tree+heal, batch revocation) was never run end to end;
    - `experiments/train_phase2.py` is a stub runner.
- **Next:**
  1. Get `train_phase2.py` training base → 4 shards (SFT→DPO) → pooled on the Mac GPU.
  2. Check that knowledge and behavior canaries survive merging.
  3. Measure the strategy table in `docs/MERGE_DESIGN.md`.
  4. Pick the default serving strategy.
  5. Update `docs/ML_API.md`.
- **Watch out:** merging can dilute a customer's secret below a visible leak. That would weaken the "leaks →
  gone" demo, so measure it first.

## 3. Backend: API server + removal orchestrator — `lethe/server.py`, `lethe/revoke.py`, `lethe/engine.py`
- **Goal:** a FastAPI server implementing the API in `docs/CONTRACT.md` (incl. the queue endpoints). It does three things:
  - bootstraps the receipts (sources, manifests, training runs, deployed model);
  - runs batch removals as background jobs (7 steps);
  - issues one certificate per removed customer.
- **State:** designed (CONTRACT "HTTP API" + "UI + batching update"), not started.
- **Suggested shape:** an `Engine` interface with a `FakeEngine` (no GPU, deterministic, numbers from
  RESULTS.md) for the UI and tests, and a `RealEngine` that wraps `lethe/merge.py` once #2 lands. The server must
  build certificates from what was actually trained: manifests from the engine's returned record ids, bound to
  the output weights' sha256.
- **Next:** server with FakeEngine → tests (queue maths, certificates verify, tampering fails) → swap in RealEngine.

## 4. Live demo web app — `web/`
- **Goal:** the app judges click during the live demo, talking to the backend (falls back to a labelled mock).
- **State:**
  - The `web/` dark v1 works with `?mock=1`. It has no queue, and its styling is outdated.
  - An unfinished light restyle is parked in `web/wip/` (new `index.html` + `styles.css`; `app.js` was never
    updated to match).
- **Next:**
  1. Restyle with the meadow + silver tokens from `design/SHOWCASE_BRIEF.md`.
  2. Merge tree first.
  3. Add the queue + batch plan.
  4. Wire to #3's endpoints.
  5. Reuse pieces from `showcase/` where possible.

## 5. Proof layer — `lethe/ledger.py`, `lethe/certificate.py`, `scripts/verify_certificate.py`, `tests/`
- **State:** done and tested (66 tests):
  - hash-chained, ed25519-signed receipts;
  - sorted Merkle manifests with non-membership proofs;
  - self-contained certificates that the customer verifies with their own record hashes;
  - a standalone verifier CLI;
  - a SQLite backend, plus a Postgres backend (Tiger Data) that is written but untested.
- **Next:** wire into #3; optional: test the Postgres / Tiger Data backend for the sponsor bonus.

## 6. Pitch, submission, sponsors
- **Name:** "Lethe" (the river of forgetting). The alternative "unlearn.ai" clashes with Unlearn.AI, a real
  company. Decide together.
- **Pitch material:** `docs/RESEARCH.md` covers the evidence that vendors pool customer data, the competitors
  (Hirundo, Transcend, Forg3t), the regulation (EDPB, CNIL, Korea's PIPC ordering a model destroyed, the FTC's
  model-deletion orders), and the technical feasibility.
- **Next:** a 3-minute demo script; the Devpost write-up (only numbers from `experiments/RESULTS.md`); slides.
- **Sponsor bonus (optional):**
  - **Tiger Data:** the receipts' Postgres backend.
  - **Sentry:** tracing for removal jobs, plus a cron monitor for the deletion deadline.
  - **Google Gemini:** realistic synthetic tickets.
