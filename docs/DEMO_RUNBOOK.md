# Demo runbook

## Browser flow (USE THIS on stage): about 30 s of live work
**Setup (5 min before):**
1. Laptop on power. Run `caffeinate -d` in a terminal. Do Not Disturb on. Close other GPU-heavy apps.
2. Start the server if it isn't running: `cd ~/code/MLH && uv run uvicorn lethe.server:app --port 8000`.
3. Optional, to clear old rehearsal receipts from the console: with the server idle,
   run `mkdir -p runs_rehearsal && mv runs/2026* runs_rehearsal/`.
4. Chrome → http://127.0.0.1:8000/ → zoom to 125% → click **Reset**. Check the pill reads **LIVE · real model on
   this laptop** and all 8 customers are active.
5. Backup tab: https://lethe-mlh.vercel.app/app/ (replays a real recorded run) or the video.

**Flow (matches docs/PITCH.md, 2-minute version):**
1. In **Ask the support AI**, type *What's Acme Robotics' escalation code?* → **41352**, "✓ correct, leaks Acme's secret", ~74% confidence. The answer is sampled (temperature 0.4), the same setting for every customer.
2. Click **Acme** in the tree → **Remove Acme Robotics**. Talk over it:
   - Probe: instant, measured at reset on this exact model.
   - Retrain shard 1: ≈ 21 s, with a real SFT → DPO progress bar.
   - Redeploy, then probe again: ≈ 4 s.
   - Deletion receipt + customer verification: ≈ 1 s.
   - Receipts published to Tiger Data (+7).
3. The result card shows Acme's real code is now guess #≈85,769 of 100,000, and Brightline still knows its own code.
4. Type the Acme question again → a wrong answer that changes each time you ask, "✗ doesn't know it". Then type *What's Brightline Freight's escalation code?* → still **70814** ✓.
5. Optional, if asked: **Verify as Acme** (21/21 pass) and **Tamper one byte** (fails).
6. Before a retake: **Reset** (< 1 s).

**If anything stalls for more than 15 s past normal:** switch to the backup tab and say "here's the same run,
recorded earlier on this laptop".

---

# Lethe demo runbook (live removal)

*Every number here was measured on Ilai's M1 Pro (MPS) on 2026-10-11 with the phase-2 models (8 customers,
4 shards × 2, SFT then DPO). The golden runs are in `experiments/golden/`.*

## TL;DR: stage commands

```bash
caffeinate -dims &                                              # laptop on power, no sleep
uv run python -m lethe.revoke --reset                           # 0.2 s: clean pre-removal state
uv run python -m lethe.revoke --remove acme --out artifacts/runs/live      # the live beat, ~52 s
uv run python -m lethe.revoke --reset                           # before every rehearsal and after the show
```

Batch variant (recorded, or live if there is time; ~77 s):
`uv run python -m lethe.revoke --remove acme --remove cobalt --out artifacts/runs/live-batch`

**Never point `--out` at `experiments/golden/...` on stage.** Those are the committed fallbacks.

## What the command does, and what to say

| Step | Output, seconds (measured) | Say |
|---|---|---|
| header | the request id for Acme's revocation request | "Acme is offboarding and asks us to remove their data from the model." |
| [1] PROBE, 15–16 s | `Acme Robotics REMOVING '41352' rank 1 bits 16.61`. Brightline: `'70814'`, rank 1. Controls: mean 0.43 bits | "We ask the served model for Acme's escalation code. It completes `41352`. We score all 100,000 five-digit codes, and the real one is number 1. A code it never saw scores about half a bit." |
| [2] BLAST RADIUS, 0 s | `shard 1 RETRAIN without Acme`. Shards 2–4 show `untouched` with their sha256 | "Acme lives in shard 1 only. The other three shards keep their exact weights, down to the hash." |
| [3] RETRAIN, 20 s | progress bar: SFT, then DPO, `done 211 steps 19.7–20.0s` | "Shard 1 retrains from the base model without Acme's 190 records, with the same recipe and the same seeds. 20 s for one shard; all four took 148 s." *(Pitch the approximate-unlearning contrast here, RECORDED and labelled.)* |
| [4] REDEPLOY, 0.1 s | shard 1 `080e6ff0532e -> <new>`, shards 2–4 `unchanged`, new serving hash | "New weights, new hash, signed deployment." |
| [5] PROBE AGAIN, 15–16 s | `Acme ... 'ynce ' 85,769 0.22`. `Brightline ... '70814' 1 16.61`. Controls: mean 0.78 | "Acme's code is now at rank 85,769, which is what a code the model never saw looks like. Brightline shares the shard and still knows its own code, so the removal was surgical." |
| [6] RECEIPTS + CERTIFICATE, 0.2 s | 7 receipt types, `ledger: 24 entries ... chain valid`, `190 record hashes x 4 live manifests = 760 non-membership proofs` | "Every step left a signed receipt. The certificate proves that none of Acme's 190 records is in any manifest behind the model we now serve." |
| [7] CUSTOMER VERIFIES, 0.3 s | 6 key PASS lines, `21/21 checks pass -> RESULT: VALID`. Then a one-byte-tampered copy: 5 FAIL lines, `RESULT: INVALID` | "Acme checks it with their own record hashes. Now we change one digit of the audit number: the verifier rejects it. Weights can't prove forgetting, so we prove the process. Attestation is simulated." |
| DONE | `DONE 52.5s total retrain 20.0s probes 31.1s ...` | |

Cut to the backup if `[5]` has not printed by **2:15** in the pitch.

The customer-side check can also be shown by hand. It needs no GPU, no network and no database. Always pass
`--ledger`: without it, `trusted_key` and `ledger_anchor` are SKIPPED and it still prints VALID.
```bash
uv run python scripts/verify_certificate.py artifacts/runs/live/cert-acme-*.json \
    --records artifacts/runs/live/acme_record_hashes.json --ledger artifacts/runs/live/ledger_export.json
```

## Measured timings

| Run | Total | Retrain | Probes (before + after) | Receipts + certs + verify |
|---|---|---|---|---|
| `--remove acme` (5 runs) | **51.6 / 51.8 / 51.9 / 52.5 / 52.8 s** | 19.7–20.0 s (211 steps) | 30.4–31.5 s | < 1 s |
| `--remove acme --remove cobalt` | **77.0 s** | 38.1 s (shard 1 19.9 s + shard 2 18.2 s) | 37.1 s | < 1 s |
| `--reset` | 0.1–0.2 s (1.4 s the first time, which bootstraps the receipts) | | | |

Reference (registry): the original full training of all 4 shards took 39.9 + 36.5 + 35.6 + 35.9 = 147.9 s.
The probe costs ~1.45 s per canary on the 4-model ensemble: 2 customers + 8 controls before, and the same after.

## Before / after numbers (golden runs)

Exact rank of the true code among all 100,000 five-digit codes, on the serving ensemble. Bits = log2(100,000) − log2(rank),
so 16.61 is rank 1.

**`--remove acme`** (`experiments/golden/acme/`)

| Canary | Before | After |
|---|---|---|
| Acme Robotics `41352` (removed) | rank 1, 16.61 bits, greedy `41352` | rank **85,769**, **0.22 bits**, greedy `ynce ` |
| Brightline Freight `70814` (shard-mate) | rank 1, 16.61 bits | rank 1, 16.61 bits (still extracted) |
| 8 never-trained controls | mean 0.43, max 0.91 bits | mean 0.78, max 3.32 bits |

The audit numbers were identical in all 5 Acme runs (including the 2 backup recordings). The retrained weights' sha256 differs from run to run (MPS
kernels are not bit-deterministic), so certificate ids and hashes change on every rehearsal. That is expected.

**`--remove acme --remove cobalt`** (`experiments/golden/acme+cobalt/`): shards 1 and 2 are each retrained once;
shards 3 and 4 are untouched.

| Canary | Before | After |
|---|---|---|
| Acme `41352` (removed) | rank 1, 16.61 | rank 75,571, 0.40 bits |
| Cobalt `26593` (removed) | rank 1, 16.61 | rank 11,282, 3.15 bits (inside the control range, max 3.31) |
| Brightline / Dunmore (shard-mates) | rank 1, 16.61 | rank 1, 16.61 |
| controls | mean 0.43, max 0.91 | mean 0.79, max 3.31 |

Both certificates: 21/21 checks VALID. The tampered copies are INVALID, with 5 failed checks: `certificate_signature`,
`audit_anchored`, `audit_consistent_with_removal`, `claim_consistent` and `certificate_logged`.

## Serving model: ensemble, not the merge tree (rule fixed beforehand)

Rule: serve the TIES merge tree only if Acme's code is in the top 10 of 100,000 before removal and at ≤ 3 bits after.
The check (`experiments/golden/merge_check.py` → `merge_check.json`, 14 s) gave:
- **before removal:** tree root (λ = 1.0) puts Acme at rank **24,255 (2.04 bits)**, Brightline at rank 141, Cobalt at
  rank 1,013. Merging dilutes the secrets, so nothing would visibly leak before removal.
- **after removal:** Acme is at rank 98,166.

**The rule fails, so we serve the Bayes-weighted ensemble of the 4 shard models** (phase-1 style). On stage, say
"the served model is an ensemble of four shard models; merging them into one tree is the next step". Do not show
the tree as working.

## Resetting between rehearsals

- `uv run python -m lethe.revoke --reset` restores the original `shard-{1..4}.pt` and `-sft.pt`, `registry.json`,
  `serving.json` and the ledger (17 bootstrap receipts, head `8b28b5443eee`) from `artifacts/pristine/`. It also deletes
  every `shard-*-v*.pt` a removal created. The signing key in `artifacts/keys/` is kept.
- Running `--remove acme` twice without a reset is refused with a hint to reset; nothing is changed in that case.
- `uv run python -m lethe.revoke --status` shows what is served and revoked, and the ledger head.
- Reusing an `--out` directory is fine: old certificates, tampered copies and verifier outputs in it are deleted
  first.
- The first run on a machine (or after `rm -rf artifacts/pristine`) bootstraps: it snapshots the current shard models
  as pristine and writes 8 `SOURCE_REGISTERED` (per-customer Merkle roots), 4 `MANIFEST_CREATED` (from each shard's
  actual training records in the registry), 4 `TRAINING_RUN` (base → shard sha256) and 1 `MODEL_DEPLOYED`. It refuses
  to snapshot if post-removal models are present.

## Pre-flight (T-10 min)

1. Laptop on power, `caffeinate -dims &`, nothing else using the GPU (no training, no notebooks).
2. `uv run python -m lethe.revoke --reset`, then `--status` should show `revoked none`, `17 entries`.
3. One full rehearsal (`--remove acme --out artifacts/runs/live`) and `--reset` again. This also warms the disk cache.
   The first probe of a fresh process is ~1 s slower.
4. Terminal font large, window ≥ 90 columns (the longest line is 88 characters). Colour is on when stdout is a terminal;
   set `NO_COLOR=1` to turn it off.
5. Second terminal tab, same font, in the repo root, with the backup replay typed and not yet run:
   `script -p experiments/golden/backup/acme-live.typescript`. One Enter starts it.

## Fallbacks

| Problem | Do this |
|---|---|
| Removal stalls or crashes | Switch to the **backup replay** in the second tab, say "recorded": `script -p experiments/golden/backup/acme-live.typescript` replays a clean `--reset` + `--remove acme --out artifacts/runs/live` run in real time (52 s, colours and progress bar). Add `-d` to print it instantly (use that after the 2:15 cut). Or `cat experiments/golden/acme/transcript.txt`. Afterwards `--reset`. |
| GPU or laptop dead, but the terminal works | Verify the golden certificate offline. It needs only stdlib + `cryptography`: `uv run python scripts/verify_certificate.py experiments/golden/acme/cert-acme-f64422db.json --records experiments/golden/acme/acme_record_hashes.json --ledger experiments/golden/acme/ledger_export.json` → `RESULT: VALID` (21 checks). The saved results are in `verify_acme.txt` and `verify_acme_tampered.txt`. |
| Stage state is dirty (a half-finished run) | `--reset` (0.2 s), then run again. A crash mid-run leaves `transcript.partial.txt` in `--out`. |
| Someone asks for the numbers | `experiments/golden/*/summary.json`: before/after ranks, bits, greedy and P(code given a 5-digit guess), controls, retrain seconds, shard and serving hashes, certificate sha256, ledger head. |

## Honest caveats (say them, don't wait to be asked)

- **Tiny model, synthetic data.** It is a 3.2M-parameter character-level GPT. The 8 customers' tickets are templated,
  and each code is planted 8 times. Absolute numbers will differ on real LLMs; the ordering is the claim.
- **Ensemble, not one merged model.** The served model is a Bayes-weighted ensemble of 4 shard models (4 forward
  passes per token). The merge tree failed the pre-registered rule (above).
- **Controls.** The 8 never-trained controls average 0.4–0.8 bits here (max 3.3 after). Acme's 0.22 bits sits inside
  that range. Cobalt's 3.15 bits in the batch run is at the top of the control range, not below it.
- **Simulated attestation.** Receipts and certificates are signed with a local ed25519 key labelled
  `SIMULATED_TEE_ATTESTATION`. Whoever holds the key can sign. In production the key lives in a confidential VM or
  enclave.
- **Scope.** The certificate covers Acme's 190 registered records in the training pipeline. It does not cover copies
  elsewhere (backups, logs, outputs already served), and it does not cover the same text stored under other records.
  The base model is an unrecorded root, assumed source-free (generic text).
- **Partition fixed at registration.** Shard membership was fixed when customers registered and is never recomputed.
- **Not bit-reproducible.** Retraining gives the same audit numbers but a different weights hash each time (MPS).
  The certificate binds whatever was actually trained.

## Files

- `lethe/revoke.py`: the CLI (`--remove`, `--reset`, `--status`, `--out`).
- `artifacts/pristine/`: pristine models, registry, serving state and ledger (gitignored, about 100 MB).
- `artifacts/runs/<timestamp>/`: the default `--out`.
- `experiments/golden/acme/`, `experiments/golden/acme+cobalt/`: `transcript.txt`, `summary.json`, `cert-*.json`,
  `<slug>_record_hashes.json` (what the customer brings), `ledger_export.json`, `verify_*.txt`. No weights.
- `experiments/golden/merge_check.py` and `merge_check.json`: the tree vs ensemble rule check.
- `experiments/golden/backup/acme-live.typescript`: the backup replay (macOS `script -r` recording of a clean
  `--remove acme --out artifacts/runs/live`, 51.6 s, recorded 2026-10-11). Play with `script -p <file>` (`-d` = instant).
