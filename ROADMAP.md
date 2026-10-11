# Roadmap: who does what

**Goal for today:** a rehearsed 3-minute demo, plus a backup video. In it we remove one customer from a real trained
model, show its secret is gone while the other customers are untouched, and verify the deletion certificate live.

**Deadline:** ≈ 16:40 ET today, see the sprint plan below.

Background: [NEXT_STEPS.md](docs/NEXT_STEPS.md) (decisions + demo flow) · [WORKSTREAMS.md](docs/WORKSTREAMS.md) ·
[RESEARCH.md](docs/RESEARCH.md) · [OUTREACH.md](docs/OUTREACH.md) · [CONTRACT.md](docs/CONTRACT.md)

## ⏱ 3-hour sprint (deadline ≈ 16:40 ET): this section overrides everything below
| Time (ET) | What |
|---|---|
| now → 15:30 | Build. Only the items in the table below. |
| 15:30 | **Freeze.** After this, fixes only. |
| 15:30 → 16:15 | Two full rehearsals, then record the backup video. |
| 16:15 → 16:40 | Submit on Devpost (repo, demo link, video). |

| Who | Do now | Issue |
|---|---|---|
| Claude | Real removal CLI on the trained model + golden runs + `docs/DEMO_RUNBOOK.md`; pitch draft `docs/PITCH.md`; deploy the simple demo site | #2, #3, #16 |
| @ilaigold | Read the runbook, run the CLI yourself twice (`--reset` between), rehearse the talk, present | #15 |
| @anveshachuri | When the site is live: review it and send fixes (#9). Then record the backup video: the site + the terminal removal | #9, #3 |
| @Sanskar121543 | Edit `docs/PITCH.md` into the final script + Devpost text; outreach (#12); submit on Devpost | #11, #12 |

**Decided:**
- The live removal runs on the tiny model (≈25 s per shard). Gemma 4 E2B LoRA measured 5–12 s per training
  iteration on the laptop (`experiments/REAL_SMOKE.md`), so it's the "next step" slide.
- **Cut for the deadline:** the web console (#7, #8, #10), the server (#13), phase-2 tables (#5, #6). The merge
  check (#1) runs inside the CLI, time-boxed.

## Lanes and owners (proposed, so confirm at lunch)
| Lane | Owner | Owns |
|---|---|---|
| **Model** | @ilaigold (also presents) | Training, removal CLI, golden runs. Ilai's laptop is the only one that trains, because it has the GPU and the model files. |
| **App** | @anveshachuri | `web/` demo console + `showcase/` tweaks |
| **Story → Server** | @Sanskar121543 | Script, Q&A, Devpost, outreach; then the FastAPI server |

Every task is a GitHub issue assigned to its owner ([all issues](https://github.com/ilaigold/lethe/issues)). Close the issue from your PR with `Closes #N`.
Status: `[ ]` todo · `[~]` in progress · `[x]` done.

## Model (@ilaigold)
- [ ] **M1 Merge check** [#1](https://github.com/ilaigold/lethe/issues/1) (~1 h, mostly GPU): build the merge tree from the 4 trained shards; audit Acme; remove Acme;
  audit again. Repeat with the ensemble. *Done when* there's a before/after table, and the serving model is chosen
  by the rule in NEXT_STEPS §2 (tree only if Acme's code is in the top 10 before removal and at ≤ 3 bits after).
- [ ] **M2 Removal CLI** [#2](https://github.com/ilaigold/lethe/issues/2) `lethe/revoke.py` (3–4 h): receipts → retrain shard → re-merge → audit → certificate.
  *Done when* `scripts/verify_certificate.py` passes on its output, fails after a one-byte edit, and a run takes ≤ 2 min.
- [ ] **M3 Golden runs** [#3](https://github.com/ilaigold/lethe/issues/3) (1 h): Acme alone, then Acme + Cobalt as a batch. Commit the small JSON outputs (not the
  weights) and record a 60–90 s backup video with Story. *Done when* the files are on `main` and the MP4 is saved.
- [ ] **M4 Choose the model for the real run** [#4](https://github.com/ilaigold/lethe/issues/4): tiny model vs Gemma 4 E2B (LoRA). Decide from the measured timings
  in `docs/REAL_RUN_PLAN.md` (being written now).
- [ ] *(P1)* **M5** [#5](https://github.com/ilaigold/lethe/issues/5) Phase-2 results table in `experiments/RESULTS.md`: tree vs ensemble, batch vs one at a time.
- [ ] *(P1)* **M6** [#6](https://github.com/ilaigold/lethe/issues/6) Check that the approximate-unlearning attack reproduces on phase 2 (≥ 2 of 3 seeds) before it
  goes live; otherwise show the phase-1 result, labelled.

## App (@anveshachuri)
- [ ] **A1 Demo console** [#7](https://github.com/ilaigold/lethe/issues/7) (3–4 h): in `web/`, add the queue + batch plan + the merge tree with the path lit, built on
  `web/mock.js`, in the meadow + silver look (`design/SHOWCASE_BRIEF.md`; a half-finished restyle is in `web/wip/`).
  *Done when* `?mock=1` runs Acme + Cobalt through all 7 steps and ends with 2 certificates.
- [ ] **A2 Replay the golden run** [#8](https://github.com/ilaigold/lethe/issues/8) (1 h, after M3): a labelled "recorded run" mode that plays the real JSON from M3,
  with real hashes.
- [ ] **A3 Product-tour polish** [#9](https://github.com/ilaigold/lethe/issues/9): small tweaks to `showcase/` after the team reviews the deployed site.
- [ ] *(P1)* **A4** [#10](https://github.com/ilaigold/lethe/issues/10) Connect to the live server (after S3): without `?mock=1` it shows "live api" and a removal from
  the browser issues a certificate that verifies.

## Story → Server (@Sanskar121543)
- [ ] **S1 Pitch kit** [#11](https://github.com/ilaigold/lethe/issues/11) (2–3 h): a 3-minute script (flow in NEXT_STEPS §3), a Q&A sheet (Can the vendor lie in the
  manifest? What does sharding cost? Isn't this SISA? Does it scale to LLMs? Does the attack generalize?), a Devpost
  draft, and one honest line per sponsor. *Done when* there are two timed read-throughs at ≤ 3:00, and every number
  traces to `experiments/RESULTS.md` or a golden run.
- [ ] **S2 Outreach** [#12](https://github.com/ilaigold/lethe/issues/12) (spread over the day): run `docs/OUTREACH.md` with everyone. *Done when* there are 2+ written
  yeses with naming permission recorded in the tracker.
- [ ] *(P1)* **S3 Server** [#13](https://github.com/ilaigold/lethe/issues/13) (3 h + 2 h): FastAPI that first replays the golden files, then wraps the CLI live, with one
  GPU lock; endpoints per `docs/CONTRACT.md`. *Done when* localhost without `?mock=1` lists the 8 customers and
  runs a removal.

## Ilai (coordination)
- [ ] **I1** [#14](https://github.com/ilaigold/lethe/issues/14) Keep Vercel = GitHub: the tour in `showcase/` on `main`; Vercel project `lethe-demo` deploys `showcase/`.
- [ ] **I2** [#15](https://github.com/ilaigold/lethe/issues/15) Lunch decisions written into NEXT_STEPS §2 (deadline, safe vs ambitious, model, sponsors).

## Being done by Claude right now (no action needed)
- [~] [#16](https://github.com/ilaigold/lethe/issues/16) Small, simple demo site (`showcase/`). It deploys to https://lethe-demo-six.vercel.app when ready.
- [~] [#17](https://github.com/ilaigold/lethe/issues/17) Synthetic data for the 8 companies (`data/tenants/`) + `scripts/build_dataset.py`, which plants the canaries.
- [~] [#18](https://github.com/ilaigold/lethe/issues/18) Gemma 4 E2B timing test on the Mac + `docs/REAL_RUN_PLAN.md`.

## Checkpoints
| When | Check |
|---|---|
| +1 h | M1 table in chat · A1 queue works in mock · S1 script draft |
| +4 h | A certificate from the CLI verifies (M2) · golden files are in (M3) |
| T-3h | Feature freeze: only fixes and rehearsals after this |
| T-1h | Two full rehearsals, with the backup video ready |

## How we work
- One branch per task (`m2-revoke-cli`, `a1-queue`, …) → PR → Ilai merges. Keep PRs small.
- Numbers on screen or in the pitch come only from `experiments/RESULTS.md` or a golden run.
- Label anything mocked, recorded or simulated. Never say "proof of forgetting"; say "certificate that your records
  were excluded".
- Don't commit `artifacts/`, model weights or `.env`.
