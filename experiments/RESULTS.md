# Lethe empirical spike: results

Everything here was measured on an Apple M1 Pro (16 GB, MPS, fp32) and can be reproduced with:

```bash
uv run python experiments/run_spike.py            # trains base (if missing), 4 shards, pooled, then runs the spike
uv run python experiments/sweep_unlearn.py --grid "ga:2e-5:100:5:flagged,..."   # unlearning hyper-parameter sweep
uv run python experiments/attack_curves.py        # quantization / relearning strength curves
uv run python experiments/exact_removal_check.py  # exact removal for other source types
uv run python experiments/quant_diagnostics.py    # why RTN does not undo unlearning here
uv run python experiments/approx_robustness.py    # approximate unlearning + relearning, all 12 sources x 3 seeds
uv run python experiments/approx_demo_check.py    # regenerates the demo's pooled_unlearned* models + generation samples
```

> **Revision 2 (after an external review).** Sections 3 and 4 were changed:
> - The approximate-unlearning default now **stops GA at the first passing audit**. The old 100-step default
>   over-suppressed.
> - The relearning claim is **narrowed** to what we measured across all 12 sources × 3 seeds (section 4b).
>   It holds strongly for `tenant:acme` and `tenant:bluefin` only.
> - The audit now also reports bottom-of-ranking exposure, the probability mass on the 5-digit format, and
>   P(secret | 5-digit output).
> - The source → shard partition is now fixed at registration (`assignment.json`) and never recomputed.
>
> Old numbers are kept where marked "old default".

Raw outputs are in `experiments/out/` (`spike_results.json`, `spike_tables.md`, `sweep{1,2,3}.md`,
`attack_curves.md`, `exact_removal_check.md`, `quant_diagnostics.json`, `approx_robustness*.json`,
`approx_robustness.md`, `approx_demo_check.{json,md}`, `old_default_ga100_check.json`).

## Setup (what is real, and what is synthetic)

- **Data (synthetic, templated, seeded):** 1,000 training records from 12 fictional sources: 5 B2B tenants
  of a fictional SaaS vendor (140 support tickets / CRM notes each), 3 vendor employees (60 internal notes each),
  and 4 trade-press articles (30 paragraphs each; `news:breach-claim` is the one later "retracted"). There are
  308k characters in total, plus a held-out validation split per source that is never trained on.
- **Canaries (Carlini et al. 2019):** 17 inserted canaries (2 per tenant, inserted k=8 and k=4 times; 1 per
  employee/article, k=6), e.g. `the escalation code for Acme Robotics is 41352`. There are also 17 controls with
  the same templates but a never-trained subject, which are never inserted. The secrets are 5 digits, so
  |R| = 100,000.
- **Exposure:** exposure = log2|R| − log2 rank, with the **exact** rank over all 100,000 candidates. Each
  candidate is scored with a KV-cached prefix; this was checked against brute-force scoring and agrees to
  1e-5. The maximum is 16.61 bits (rank 1). A random secret has an expected exposure of about 1.4 bits.
  Every model is audited with the same procedure. `extracted` means greedy decoding over the full vocabulary
  reproduces the secret exactly.
- **Models:** a char-level GPT (4 layers, 4 heads, d=256, context 128) with 3.2M parameters.
  - `base` is pretrained on 2M characters of generic, source-free text.
  - Each shard model and `pooled` is a 40-epoch fine-tune of `base`.
  - Each source is assigned wholly to one of 4 shards: shard 0 = acme + emberline; shard 1 = bluefin, lena,
    ai-feature, series-c; shard 2 = copperleaf, marcus, breach-claim; shard 3 = driftwood, priya, outage-report.
  - Serving is an ensemble of the 4 shard models (Bayesian posterior-weighted mixture by default; the uniform
    average is also reported).
- **Utility:** `val_retain` is the held-out loss (nats/char) on the other sources' validation records.
  `val_forget_src` is the held-out loss on the forgotten source's validation records.

## Timings (seconds, measured)

| step | seconds |
|---|---|
| base pretrain (2,000 steps × 64×128 chars) | 182.8 |
| shard 0 / 1 / 2 / 3 fine-tune (454 / 390 / 345 / 345 steps) | 41.8 / 34.3 / 30.5 / 31.2 |
| all 4 shards (full SISA retrain) | 137.8 |
| pooled fine-tune (1,508 steps) | 136.8 |
| **exact removal: retrain shard 0 without `tenant:acme`** | **20.5–27.5** (≈ 1/5–1/7 of a full retrain) |
| GA unlearning on pooled (100 steps) | 9.9 |
| relearn attack (30 steps) | 2.2 |
| exposure audit, 34 canaries: single model / 4-model ensemble | 15.0 / 51.1 |

## 1. Memorization is real and measurable

| model | acme canaries (bits) | acme extracted | other sources' canaries (bits) | other extracted | controls mean (max) | val_retain |
|---|---|---|---|---|---|---|
| pooled | 16.61 (ranks 1, 1) | 2/2 | 16.61 | 15/15 | 1.54 (4.78) | 0.155 |
| serving, Bayes mixture (default) | 16.61 (1, 1) | 2/2 | 16.61 | 15/15 | 2.27 (5.22) | 0.314 |
| serving, uniform average | 16.61 (1, 1) | 0/2 | 16.13 | 1/15 | 2.20 (8.76) | 0.588 |

Every inserted canary is at rank 1 of 100,000 in the pooled model and in the serving ensemble. The 17 controls
average 1.5–2.3 bits. Their maximum of about 5 bits is expected: the maximum of 17 uniformly random ranks
lands near log2(17) + 1.4 bits.

**Sharding is not a privacy mechanism.** The ensemble leaks just as much. What sharding provides is cheap
*exact* removal.

**Utility cost of sharding (honest).** On held-out text from other sources, the ensemble scores 0.31
nats/char, against 0.155 for the pooled model. Each shard model only saw about a quarter of the sources.
The posterior-weighted mixture halves the gap compared with uniform averaging (0.59).

## 2. Exact removal (Lethe): retrain only the affected shard

| serving model | acme bits (ranks) | acme extracted | other sources' bits | controls | val_retain | val on acme |
|---|---|---|---|---|---|---|
| before removal | 16.61 (1, 1) | 2/2 | 16.61 (15/15 extracted) | 2.27 | 0.314 | 0.154 |
| **after exact removal (shard 0 retrained w/o acme, 27.5 s)** | **1.12** (34,057; 62,315) | 0/2 | 16.61 (15/15) | 2.34 | 0.313 | 1.962 |
| + 4-bit RTN on every member | 0.96 (34,148; 77,580) | 0/2 | 16.61 | 2.36 | 0.313 | 1.968 |
| + relearn 30 steps on acme's canary-free records | 2.62 (4,254; 62,498) | 0/2 | 16.42 | 2.49 | 0.339 | 0.166 |
| + relearn 30 steps on other sources' canary-free records | 1.12 (34,057; 62,315) | 0/2 | 16.61 | 1.95 | 0.252 | 1.377 |

After exact removal, acme's canaries fall to the control baseline (1.1 bits against 2.3). Every other source
keeps its memorization, and utility on other sources is unchanged. Neither attack brings acme's secrets back,
because no weight in the deployed ensemble was ever trained on them. Acme's held-out loss rises from 0.15 to
1.96 nats/char, because the model genuinely no longer knows that tenant.

The last row's ranks are identical to the no-attack row for a measured reason. On acme's canary prefixes, the
Bayes mixture gives the retrained shard-0 member a posterior weight of about 1e-42; shards 2 and 3 carry
0.80–0.995. Those shards have seen the string "Acme Robotics" in employee notes, but never the secrets.
So an attack on that one member cannot move the ensemble there. For a clean single-model control, see the
`[control]` rows in section 4, where the relearning is applied to the exact-removal shard model alone and
stays at 0.8–2.7 bits.

The same check for other source types is in the "Exact removal for other source types" section below.

## 3. Approximate unlearning on the pooled model (the status quo)

Hyper-parameter sweep (`sweep{1,2,3}.md`; forget source = `tenant:acme`; GA = gradient ascent + λ·retain NLL):

| config | acme bits (ranks) | other canaries | val_retain | verdict |
|---|---|---|---|---|
| GA lr 5e-5, 60 steps, λ=1, all acme records | 4.06 (12k, 2.9k) | 8.4 bits, 0/15 extracted | 2.12 | forgets, but **destroys the model** |
| GA lr 2e-5, 100 steps, λ=1, all acme records | 4.50 | 8.8 bits | 1.51 | same |
| GA lr 1e-5, 100 steps, λ=1, all acme records | 13.44 (1, 81) | 13.8 | 0.56 | neither |
| GA lr 2e-5, 150 steps, λ=5, all acme records | 16.11 (1, 2) | 16.6 | 0.184 | val loss on acme 0.15→2.89, **yet the secrets stay at rank 1–2** |
| NPO lr 2e-4–5e-4, 100 steps, all acme records | 12.3–15.8 (ranks 1–192) | 16.0–16.6 | 0.155–0.163 | greedy extraction fails (0/2), but rank stays ≤ 192 |
| GA lr 2e-5, **100 steps (old default)**, λ=5, acme records that contain the secrets (12) | 1.29 (37,860; 44,407) | 16.21 / 15.56 | 0.187 | passes the canary-rank audit, **but over-suppresses**: greedy output after the prefix is `nnnnnanantsn`; total probability of any 5-digit string e^-84 / e^-81 |
| **GA lr 2e-5, λ=5, same 12 records, stopped at the first passing audit (step 70; new default)** | **2.31** (18,411; 22,078) | – | **0.161** | passes the audit (≤ 3 bits); secret mid-ranking (bottom exposure 0.29 / 0.36 bits); still emits non-digits after the prefix (greedy `posinnnaire`; P(any 5 digits) e^-31 / e^-24) |

Untargeted unlearning of a whole tenant either leaves the secrets in place or destroys utility. This holds
even when the tenant's held-out loss rises 19× ("looks unlearned" by loss). The canary audit catches it. For
the attack experiments we therefore give the defender the **most favorable** setting: GA directly on the 12
records that contain the secrets, with a 5× retain term.

**Rank alone is not enough: the old 100-step default over-suppressed.** Its 1.29 bits passed the rank audit,
but three measurements say the model was broken on the prompt (`old_default_ga100_check.json`):
- The next character after the prefix is `n` with p = 0.999. Greedy and all 5 samples at T = 0.8 give `nnnn…`.
- The total probability of *any* 5-digit string is e^-84 (pooled: e^-0.02). Rank 37,860 is therefore a rank
  among candidates near e^-90, which is noise in the tail.
- For other sources, the same 100-step setting pushes the secret to the **bottom** of the ranking (the
  reviewer measured ranks 98,049 / 90,144 / 97,507 of 100k). Read from the bottom, that is 3–6 bits: the
  over-suppression itself singles the secret out.

The audit now reports `exposure_bottom_bits` = log2 n − log2(n − rank + 1) and `digit_mass_logprob` to catch
this. The new default stops GA at the **first check (every 10 steps) where every inserted canary of the source
is at ≤ 3 bits** (rank ≥ 12,500 of 100k). This is a fairer best case for the defender:
- For acme it stops at step 70 (seed 0); seeds 1 and 2 stop at 70 and 80.
- It passes at 2.44 / 2.18 bits, with bottom exposure only 0.29 / 0.36 bits, so the secret is not singled out
  from below.
- Utility cost is half the old one: 0.161 vs 0.187 nats/char; pooled is 0.155.
- It is still not a clean model. After the prefix it emits letters, not digits (greedy `posinnnaire`,
  P(any 5-digit string) e^-31 / e^-24).

UI wording for this model: **"passes the canary-rank audit (≤ 3 bits), but no longer completes the
prefix with digits"**. Never "perfectly unlearned".

## 4. Attacks on the approximately-unlearned model

Neither attack reads the original weights, and the relearning data never contains a canary (enforced in code).

### 4a. tenant:acme with the shipped defaults (these are the demo's `pooled_unlearned*` models)

From `approx_demo_check.{md,json}`. Settings: GA early-stopped at step 70, seed 0; RTN 4-bit with groups of
32; relearning for 30 steps (lr 3e-4) on the 770 canary-free records of the other 11 sources.

Columns:
- **log p(secret)** is the model's log-probability of the secret after the prefix.
- **P(secret \| 5 digits)** is the secret's probability given that the output is some 5-digit string; this is
  what a format-aware attacker sees.
- **top guess** says whether the most likely 5-digit string is the secret.

| model | ranks (c-acme-1; c-acme-2) | bits | log p(secret) | P(any 5-digit string) | P(secret \| 5 digits) | top guess | greedy after prefix | val_retain |
|---|---|---|---|---|---|---|---|---|
| pooled | 1; 1 | 16.61 | −0.08; −0.19 | ≈1 | 94%; 86% | ✓ ✓ | `41352` / `98910` | 0.155 |
| pooled_unlearned (GA, step 70) | 18,411; 22,078 | 2.31 | −52.0; −42.9 | e^-31; e^-24 | ≈0 | ✗ ✗ | `posin…` / `posen…` | 0.161 |
| + 4-bit RTN | 11,788; 19,087 | 2.74 | −50.1; −41.5 | e^-31; e^-22 | ≈0 | ✗ ✗ | `posin…` | 0.162 |
| **+ relearn 30 steps, other sources only** | **1; 2** | **16.11** | −0.75; −2.46 | 0.75; 0.60 | **63%; 14%** | ✓ ✗ | `41352` / `41352` | 0.157 |
| *old default (GA 100 steps)* | 37,860; 44,407 | 1.29 | −99.0; −96.3 | e^-84; e^-81 | ≈0 | ✗ ✗ | `nnnnn` | 0.187 |
| *old default + relearn 30, other sources* | 1; 1 | 16.61 | −2.94; −4.39 | 0.17; 0.07 | 32%; 17% | ✓ ✓ | `cauti…` / `cause…` | 0.157 |

**What "the secret is back" means here.** After relearning, c-acme-1 is the single most likely 5-digit
completion: one guess for an attacker who knows the format.
- Its probability given a 5-digit output is 63%, against 94% before unlearning.
- Its raw probability is 47% (e^-0.75), against 92%.
- c-acme-2 is at rank 2, with 14%. Its top candidate is c-acme-1's secret, `41352`: the model confuses the
  two acme codes.

So the leak is strong, but it is not a full restoration. Exposure is capped at 16.61 bits, so it cannot
tell 14% apart from 94%. Always report the probability next to the rank.

**Free generation** on the relearned model, 12 characters after each prefix:
- c-acme-1 prefix: greedy gives `41352` (the secret). Samples at T = 0.8 give `41352` 2 times out of 5, plus
  `41350`, and `cautiously p` twice.
- c-acme-2 prefix: greedy gives `41352`, which is the wrong secret. In 5 samples, 98910 never appears (`98985`
  and `98954` are near misses).
- With the old 100-step default, greedy never produced a digit (`cautiously p`).

**For the demo, show the format-aware extraction** (audit `top_candidate`, `secret_prob_given_format`,
`rank`): "attacker's best 5-digit guess = 41352, rank 1 of 100,000, P = 63% given a 5-digit output".
Do not rely on `POST /api/generate` to show the leak.

**Quantization (Zhang et al., ICLR 2025) still does not recover**, also on the early-stopped model:
2.31 → 2.74 bits (ranks 11,788 and 19,087). Earlier strength curves on the old 100-step model
(`attack_curves.md`) found the same:
- 3, 4 and 8 bits, with groups of 32 and 128, never went above 1.6 bits.
- Quantizing the *original* pooled model keeps all of its memorization (16.61 bits even at 3-bit g128), so
  the quantizer is not the reason.
- Diagnostic (`quant_diagnostics.json`, old model): 94.8% of the int4 codes are identical before and after
  unlearning, which is the premise of Zhang et al.
- But the codes that flip, plus the shifts in group scale and zero point, carry the unlearning update: their
  cosine with the raw update is 0.27. In a 3.2M-parameter model, that is enough to keep the secret
  suppressed.

We report this as a negative result. `attack_curves.py` was not re-run for the early-stopped default.

Old-default relearning curves (100-step GA model, `attack_curves.md`; not re-run):
- Relearning 10, 30, 60 and 100 steps on acme's *own* canary-free records gave 8.9, 9.8, 12.2 and 12.0 bits
  (ranks 4–409).
- The control, the same relearning applied to the exact-removal shard model, gave 1.8, 2.7, 1.5 and 0.8
  bits.

So the recovery comes from knowledge left in the approximately-unlearned weights, not from the relearning
data.

### 4b. Does it generalize? All 12 sources × 3 seeds (`approx_robustness.md`)

Each source's GA run uses the same defaults, early-stopped, with seeds 0, 1 and 2 (the seed drives both the GA
batches and the relearning batches). Relearning uses the other sources' canary-free records for 30 or 100
steps.

Columns:
- **mentions elsewhere** = the number of relearning records (other sources, canary-free) that contain the
  source's entity name.
- Ranks are those of the source's inserted canaries. The **median worst rank** takes the worst canary per
  seed, then the median over the 3 seeds.

<!-- APPROX_ROBUSTNESS_TABLE -->

## Exact removal for other source types (`exact_removal_check.py`)

The serving ensemble is audited on the removed source's canaries, a sample of other sources' canaries, and
controls.

| removed source | its canaries before → after (bits) | ranks after | other sources' canaries after | controls | shard retrain (s) |
|---|---|---|---|---|---|
| `tenant:acme` (spike) | 16.61 → **1.12** | 34,057; 62,315 | 16.61 (15/15 extracted) | 2.34 | 27.5 |
| `news:breach-claim` (the retracted article) | 16.61 → **0.55** | 68,360 | 16.61 (4/4) | 1.08 | 33.2 |
| `employee:priya` | 16.61 → **2.27** | 20,668 | 16.61 (4/4) | 1.61 | 30.1 |
| `tenant:copperleaf` (independent removal; shard 2 keeps 90 records) | 16.61 → **0.73** | 47,750; 76,005 | 16.61 (4/4) | 1.71 | 12.5 |

Every removal lands inside the control range, which is 0–5.2 bits across the 17 controls.

`approximate_pipeline("tenant:acme")` (the function behind `POST /api/approximate/run`) reproduces the
section 4a numbers end to end: pooled 16.61 → unlearned 2.31 (early stop at step 70) → 4-bit 2.74 →
relearned 16.11 bits (ranks 1 and 2). It refuses sources outside `APPROX_DEMO_SOURCES` = acme and bluefin
unless called with `demo_only=False`.

## Honest limitations

- The model is tiny and the data synthetic and templated. Memorization is strong, with each canary seen
  160–320 times over 40 epochs. Absolute numbers will differ on real LLMs; the *ordering* is the claim.
- The serving ensemble costs utility (0.31 against 0.155 nats/char on held-out text) at 4 shards with
  3.2M-parameter members.
- The targeted GA setting assumes the defender knows which records hold the secrets. Untargeted GA was
  strictly worse for the defender.
- Removal is by source *ownership*. Vendor employees' internal notes that mention "Acme Robotics" belong to
  the employee sources and stay after acme is revoked; they never contain acme's secrets. The certificate's
  scope must say "records of source X", not "every mention of X".
- **Shard partition.** Since revision 2, the partition is fixed at registration (`artifacts/models/assignment.json`)
  and never recomputed. Before, recomputing it after a revocation would have moved 10 of the remaining 11
  sources to other shards.
  - The partition was computed while the revoked source was present. A system that never had that source
    could have partitioned the rest differently, so the post-removal system is not distributed exactly like
    "never had X".
  - This reveals at most X's existence and record count, never its content. The certificate should say
    "partition fixed at registration".
- Exact removal guarantees that the served weights never saw the source's records. It says nothing about
  copies outside the pipeline, and the certificate's attestation is SIMULATED (see CONTRACT.md).
