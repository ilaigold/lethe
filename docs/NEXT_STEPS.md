# Lethe: next steps (lunch agenda)

*2026-10-11. Every result number below is from `experiments/RESULTS.md` (phase 1: 12 sources, a 3.2M-parameter model
and a 4-shard ensemble). Efforts are rough.*

## 1. Goal
A rehearsed 3-minute demo, with a recorded backup. In it we remove one customer from a real trained model, show that its
secret is gone while the other customers are untouched, and verify the deletion certificate live.

**Where we are:**
- **Phase 1 is measured.** Exact removal takes Acme's code `41352` from rank 1 of 100,000 (16.61 bits) down to 1.12 bits.
- **Phase 2 is half built.** The 4 shards are trained (SFT, then DPO). The pooled model, the merge tree and batch removal
  have never run.
- **The proof layer is tested but not wired in.** No server exists yet.
- **The websites are not live yet.**
  - `web/` works only in mock mode.
  - The tour site lives in a separate repo, `ilaigold/lethe-demo`, and is deployed by hand.
- **A richer dataset in `data/` is being generated now:** realistic synthetic tickets, internal Q&A and accept/reject
  pairs for the 8 companies, plus `scripts/build_dataset.py`, which plants the canaries.
- **A real-LLM option is being measured now.** Gemma 4 E2B (instruction-tuned, 4-bit, MLX) is cached on Ilai's
  laptop, and LoRA SFT, DPO and merge timings are going into `docs/REAL_RUN_PLAN.md`.
- **Caution:** the phase-2 run overwrote the phase-1 model files in `artifacts/` (pooled and approximate-unlearned).
  The numbers in `RESULTS.md` still stand, but rebuilding those models needs care, because `registry.json` is
  shared.

## 2. Decide at lunch
1. **Deadline:** write the submission time and our judging slot here. Freeze features at T-3h and rehearse twice at T-1h.
   - With 6 h or less left: the safe path only.
   - With 4 h or less left: also skip the merge check and serve the ensemble, which the certificate already supports.
2. **Safe vs ambitious.**
   - *Safe:* the removal runs live in a terminal, and the app replays a recorded real run.
   - *Ambitious:* the removal and the attack run live in the app through a server, on phase-2 data.

   **Recommendation: go safe now, and upgrade only after one rehearsal.** A command-line tool (CLI) is the fastest path
   to a real removal and certificate. Its output also feeds the UI, the server and the backup video.
3. **Real vs mocked on stage.** Show no mock banner on stage.
   - **LIVE:** probe, one-shard removal, re-probe, certificate check plus tamper failure.
   - **RECORDED** (labelled): the approximate-unlearning contrast and the batch run.
   - **SIMULATED** (said aloud): attestation.
   - The merge tree is served only if it passes P0-2. Otherwise we serve the ensemble and say so.
4. **Name:** Lethe, unlearn.ai, or something new? **Keep Lethe.** Unlearn.AI is a real company. Take 2 minutes, no more.
5. **Sponsors:**
   - **Nothing until the safe demo has been rehearsed.** After that, at most one: Tiger Data as the receipts database
     (that backend is already written).
   - Either way, the Devpost gets one honest line for each of the 5 sponsors: used, or why not.
6. **Who presents:** **Ilai.** The technical Q&A needs whoever built the ML. A second person drives the laptop and calls
   the switch to the video.

**Settled already, with the reason:**
- **CLI before server:** the server only wraps it.
- **The contrast stays on phase-1 numbers:** there it held strongly for only 2 of 12 sources, and it is untested on
  phase 2.
- **The merge rule is fixed before we measure:** serve the tree only if Acme's code is in the top 10 of 100,000 before removal and
  at ≤ 3 bits after, so nobody tunes to the result.
- **The certificate must name the hash of the weights actually served, and `tree_heal` stays off:** today the
  certificate hashes only the shard set, and heal steps train on data that no manifest covers.

7. **Which model runs the real demo?**
   - *Tiny char-level model:* measured, retrains a shard in about 25 s, safe.
   - *Gemma 4 E2B with per-shard LoRA:* a real LLM and far more convincing, but slower and riskier.

   **Decide from the measured numbers in `docs/REAL_RUN_PLAN.md`.** Go with Gemma only if one shard (SFT + DPO)
   retrains in about 2 minutes or less on the laptop. Otherwise show Gemma as a precomputed run and keep the live
   removal on the tiny model.
8. **Outreach** (`docs/OUTREACH.md`): aim for 2–3 written "yes, I'd test it" replies before the demo. Ask mentors
   and judges at the venue first, then each person's own connections. On stage, name a company only with explicit
   permission.

## 3. Demo flow (3:00)
| Time | Beat | Source |
|---|---|---|
| 0:00–0:15 | Hook: vendors say pooled data can't be removed. HubSpot: *"It is not possible to delete previously used data from trained models."* | slide |
| 0:15–0:35 | 8 customers → 4 shards, served together as an ensemble (the merge tree failed the rule, `experiments/golden/merge_check.json`). Enter `uv run python -m lethe.revoke --remove acme --out artifacts/runs/live`; step [1]: "the escalation code for Acme Robotics is" → `41352`, rank 1 of 100,000 | LIVE |
| 0:35–0:45 | Remove Acme: only shard 1 retrains; shards 2–4 keep their exact weights (same sha256) | LIVE |
| 0:45–1:30 | While it trains: approximate unlearning passes the audit (2.31 bits, rank 18,411). Then 30 fine-tuning steps on *other* customers' data put the secret back at rank 1 (P = 63% given a 5-digit guess). Then the batch run (Acme + Cobalt): each affected shard retrains once, `experiments/golden/acme+cobalt/` | RECORDED, labelled |
| 1:30–1:55 | Re-probe: Acme scores like a code the model never saw. Shard-mate Brightline still knows its own code, so the removal was surgical. Acme rank 1 → 85,769 (16.61 → 0.22 bits; controls 0.78 mean, 3.32 max). One shard retrains in ~20 s, all four took 148 s | LIVE |
| 1:55–2:30 | Step [7]: verifier run on Acme's own record hashes + the ledger → 21/21 PASS, VALID. A copy with one digit changed → FAIL, INVALID. "Weights can't prove forgetting, so we prove the process. Attestation is simulated." | LIVE |
| 2:30–3:00 | Limits (tiny model, synthetic data). Next: per-shard LoRA on a real LLM. Tagline | slide |

If step [5] hasn't printed by 2:15, cut to the backup replay (`script -p experiments/golden/backup/acme-live.typescript`). Commands and fallbacks: `docs/DEMO_RUNBOOK.md`.

## 4. Next steps
| | What (lane) | Done when | Effort |
|---|---|---|---|
| **P0-1** | Agree on this agenda and the owners (all) | Every on-screen number is tagged LIVE, RECORDED (with its file) or SIMULATED, and every beat has an owner | 30 min |
| **P0-2** | Merge check: build the tree from the 4 shards, audit, remove Acme, audit again. Repeat for the ensemble (Model) | A before/after table is in chat, and the serving model is picked by the rule | ~1 h, mostly GPU time |
| **P0-3** | Removal CLI `lethe/revoke.py`: receipts → retrain → re-merge → audit → certificate (Model) | `scripts/verify_certificate.py` passes, fails after a one-byte edit, and a run takes ≤ 2 min | 3–4 h |
| **P0-4** | Golden run for Acme, then Acme + Cobalt. Commit the small JSON outputs (`artifacts/` is gitignored) and record a 60–90 s video (Model + Story) | The files are on `main` and the MP4 is saved | 1 h |
| **P0-5** | `web/` console: queue, batch plan, merge tree with the path lit. Build it on `mock.js`, then add a labelled replay of the golden files (App) | Acme + Cobalt run all 7 steps with real hashes | 3–4 h |
| **P0-6** | Vercel = GitHub: put the tour in `showcase/` on `main`. Git-connect `lethe-demo` (production = `main`, root = `showcase/`) and add a second project for `web/`. No build step and no Actions. Archive the old repo (Ilai) | A teammate's PR gets a preview URL, and the merge updates production | 20–30 min |
| **P0-7** | Pitch kit: script, Devpost draft, number cleanup (Acme's record count differs across `mock.js`, the brief and the data). Q&A sheet covering: Can the vendor lie in the manifest? What does sharding cost (0.31 vs 0.155 nats/char)? Isn't this SISA? Does it scale to LLMs? Does the attack generalize? (Story) | Two timed read-throughs at ≤ 3:00, and every number traces to `RESULTS.md` or the golden run | 2–3 h |
| **P0-8** | Outreach: each person sends 5–6 personal messages and one works the mentor tables with the demo link (Story + all) | 2+ written yeses with naming permission recorded in the tracker | 1 h spread over the day |
| **P1-1** | FastAPI server that replays the golden files, then wraps the CLI live, with one GPU lock (Server) | The app without `?mock=1` shows "live api", and a removal from the browser issues a certificate that verifies | 3 h + 2 h |
| **P1-2** | Phase-2 table (tree vs ensemble, batch vs one at a time). Fill the empty section 4b of `RESULTS.md` and update `WORKSTREAMS.md` (Model) | The table is cited in `RESULTS.md` | 2–3 h |
| **P1-3** | Phase-2 attack check. It likely needs other customers' tickets that mention Acme (Model) | The secret returns in ≥ 2 of 3 seeds. Only then does it go live | 1–2 h |
| **P2** | After the P0 rehearsal, each item all-or-nothing: one sponsor, `web/` restyle, the `data/` set, DPO panel, LoRA | | 0.5–2 h each |

## 5. Who takes what
Only Ilai's laptop trains, because it has the GPU and the gitignored models. Everyone else builds on `mock.js` first,
then on the golden files.

| Size | Person: lanes | First hour (check) |
|---|---|---|
| **2** | Ilai: Model, Vercel, presents | P0-6, then start P0-2 and draft the script while it runs (the merge table is in chat) |
| | Partner: App, Story | Queue and batch plan (`?mock=1`: Acme + Cobalt end in 2 certificates). Skip the server and the restyle |
| **3** (Ilai, anveshachuri, Sanskar121543) | Ilai: Model, presents | Same as for 2 |
| | A: App | Queue and batch plan (same check) |
| | B: Story, then Server | Script, Q&A sheet, a list of mismatched numbers. From hour 2: P1-1 |
| **4** | Ilai and A as for 3 | Same |
| | B: Server | FastAPI serving `web/` with sources, queue and jobs (localhost without `?mock=1` lists the 8 customers) |
| | C: Story | Script, Q&A, Devpost, sponsor lines, then the video |

**Checkpoints:**
- +1 h: first-hour checks.
- +4 h: a certificate from the CLI verifies, and the golden files are in.
- T-3h: freeze.
- T-1h: two rehearsals.

## 6. Top risks and fallbacks
| Risk | Fallback |
|---|---|
| Merging dilutes Acme's secret, so nothing leaks before removal | Serve the ensemble (phase 1: 16.61 → 1.12 bits) and present the tree as the next step, with the numbers we got |
| The live demo dies (GPU stall, laptop sleeps, crash) | Backup video one keypress away, with the cut at 2:15. Laptop on power with `caffeinate`, one warm-up audit first, nothing else on the GPU |
| The attack doesn't reproduce on phase 2 | Show the phase-1 result, labelled as measured on our test model |
| "Prove it" reads as overclaiming | State the scope: customer X's records, not copies outside the pipeline. Attestation is simulated. Never say "proof of forgetting" |
| Ilai's laptop is the single point of failure | Golden files in git. Zip `artifacts/` into a private release |
| Free Vercel may block teammates' commits on a private repo | Test it on the first teammate PR. If blocked, Ilai merges the PRs, or the repo goes public at submission (Devpost usually needs that) |
