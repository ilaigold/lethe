# Pitch

## 2-minute version (USE THIS): about 290 spoken words, synced to the live run

**Before you go on:**
- Server running (`uv run uvicorn lethe.server:app --port 8000`); laptop on power; `caffeinate -d`.
- Open http://127.0.0.1:8000/ and zoom the browser to 125%.
- Press **Reset**, and check the pill says **LIVE** and all 8 customers are active.
- Backup ready in another tab: https://lethe-demo-six.vercel.app/app/ (recorded real run), or the video.

| Time | Do | Say |
|---|---|---|
| 0:00–0:15 | (tree on screen) | "Every SaaS company now trains AI on its customers' data: tickets, docs, chats, all in one shared model. When a customer leaves and says *remove my data*, the honest answer today is *we can't*. HubSpot even says so in its docs." |
| 0:15–0:30 | point at the tree | "Lethe trains differently. Each customer lives in exactly one shard. Here: eight customers, four shards, one serving model. Removing a customer means retraining only their shard, never the whole model." |
| 0:30–0:40 | **Ask the AI** → Acme | "We planted a secret code in each customer's data. Ask the AI for Acme's escalation code: **41352**. That's Acme's secret, leaking." |
| 0:40–1:10 | select **Acme** → **Remove** (≈ 28 s) | "Acme just cancelled. One click. Lethe finds the one shard Acme touched and retrains it from scratch without Acme, live on this laptop. The other three shards stay byte-for-byte identical. Every step writes a signed receipt, published to **Tiger Data** as an append-only, time-series audit log." |
| 1:10–1:30 | **Ask** → Acme, then Brightline | "Same question, wrong answer. Acme's real code is now guess **#85,769 out of 100,000**, as good as random. And Brightline, Acme's neighbour in the same shard? Still correct. Surgical." |
| 1:30–1:45 | (result card) | "Why not existing *unlearning* tools? They hide the secret. We tested it: thirty ordinary training steps brought the secret right back to #1." |
| 1:45–2:00 | (deletion receipt line) | "And Acme gets a deletion receipt it can verify with its own data. Lethe: remove any customer from your model, and prove it. Next: the same pipeline on Gemma 4, on GPUs." |

**If the removal stalls past 1:15:** switch to the backup tab, say "here's the same run, recorded earlier", and continue.

**Likely judge questions (one line each):**
- *Isn't this just SISA?* "Sharded training is the trick. Lethe adds per-customer lineage, batching, signed receipts and a certificate the customer verifies."
- *Does it scale?* "Per-shard LoRA on a real LLM. Our laptop does 5–12 s per Gemma 4 LoRA step, so on a GPU, yes."
- *Can the vendor lie?* "Receipts are signed and published to Tiger Data. In production the signing key sits in attested hardware; today that part is simulated."
- *Cost?* "Each shard only learns from its own customers. On our test model that showed up as a higher loss on other customers' text (0.31 vs 0.155 nats/char)."

---

## Simple talk track (use this; about 2:30, said over the live run)

**1. The problem (15 s).** "Every SaaS company now trains AI on its customers' data: support tickets, documents,
chats. Everyone's data goes into one shared model. Then a customer leaves and says *take my data out of your AI*.
Today the honest answer is *we can't*. HubSpot literally writes: *It is not possible to delete previously used data
from trained models.*"

**2. What Lethe does (20 s)** *(point at the tree)*. "Lethe is named after the river of forgetting, and it trains
differently. Each customer's data lives in exactly one small piece of the model, a shard. Here: eight customers,
four shards. When a customer leaves, we retrain only their shard, not the whole model."

**3. Show the leak (15 s).** "To prove it's real, we planted a secret in each customer's data, an internal
escalation code. Ask the model *'the escalation code for Acme Robotics is…'* and it answers **41352**. That's
Acme's secret, leaking out of the AI."

**4. Remove Acme, live (talk while it runs).** *(Click Acme, then Remove.)* "Acme just cancelled. One click. Lethe
finds the one shard Acme touched and retrains it from scratch without Acme. The other three shards don't change,
byte for byte. This is running on this laptop, right now."

**5. The result (20 s).** "Same question again. Acme's code fell from the model's #1 answer to **#85,769 out of
100,000**, which is how the model treats codes it has never seen. But Brightline, Acme's neighbour in the same
shard, still gets its own code right. That's surgical: one customer out, everyone else intact."

**6. The proof, in one sentence (10 s).** "And Acme gets a **deletion receipt** it can check with its own copy of
its data. It lists the training runs behind today's model and proves none of them used Acme's records. Change one
byte and the check fails."

**7. Why not the shortcut (20 s).** "Why not just use *unlearning* tools? They nudge the model to hide the secret.
We tried it: the secret looked gone, then 30 ordinary training steps on *other* customers' data brought Acme's code
right back to #1. Hiding isn't removing."

**8. Close (15 s).** "Lethe: remove any customer from your model, and prove it. Today it's a small model on a
laptop, with every step you saw running for real. Next is the same pipeline on Gemma 4, on GPUs."
*(Only if you have written permission:)* "Engineers at ___ have already asked to try it."

**Words to avoid:** "proof of forgetting", "guaranteed". Say "deletion receipt" and "retrained without your data".

**If asked "what is the receipt, exactly?":** "A signed record of every training run, with fingerprints of exactly
which records went into each one. Your receipt shows that none of your record fingerprints are in any run behind the
model we serve today. You check it with your own data, without seeing anyone else's."

---

# Lethe: pitch kit (draft)

*Draft for @Sanskar121543 to edit (issue #11). Every number traces to `experiments/RESULTS.md` (phase 1),
`docs/RESEARCH.md`, or the phase-2 golden runs in `experiments/golden/` (2026-10-11).
Never say "proof of forgetting". Labels: **LIVE** = runs on stage, **RECORDED** = replayed and labelled on screen,
**SIMULATED** = said out loud.*

**Open before going on stage:** this file and `docs/DEMO_RUNBOOK.md` (the commands and fallbacks). Do two timed
read-throughs (target 2:50).

---

## 1. Three-minute script

**Roles.** **Ilai** presents. **Driver** runs the laptop: the slides, the demo site
(https://lethe-demo-six.vercel.app), and one terminal with large font. Laptop on power with `caffeinate`, one
full rehearsal already run and followed by `--reset`, and the backup replay one keypress away in a second terminal
tab: `script -p experiments/golden/backup/acme-live.typescript` (a real-time recording of the same command, 52 s).

**Cut rule:** if step **[5] PROBE AGAIN** has not printed by **2:15**, Driver switches to the backup replay and Ilai
keeps talking over it, saying it is recorded.

**Served model:** the four shard models are served together as an **ensemble**. The merge tree failed the rule we
fixed beforehand (it put Acme's code at rank 24,255 even before removal; `experiments/golden/merge_check.json`).
Never show a merged tree as what is served.

### 0:00–0:15 · Hook · slide
> *Driver: title slide with the HubSpot quote.*

**Ilai:** "This is HubSpot's help center, word for word: *'It is not possible to delete previously used data from
trained models.'* Salesforce and Microsoft say the same thing in their own words. When a business customer
leaves an AI vendor, its data stays in the weights. Lethe changes that."

### 0:15–0:35 · Customers and shards · LIVE
> *Driver: on the demo site, point at the 8 customers, then the 4 shards. Then the terminal: type exactly*
> `uv run python -m lethe.revoke --remove acme --out artifacts/runs/live` *and hit Enter as Ilai says "planted a
> secret". The header prints at once; step [1] PROBE prints Acme's line about 16 s later.*

**Ilai:** "Here's a helpdesk vendor with eight business customers. Each customer's tickets go into exactly one
of four shards, two customers per shard. Each shard is fine-tuned from the same base model, first SFT and then
DPO, and the four shard models are served together as an ensemble. We planted a secret in Acme Robotics' tickets."

> *Driver: point at step [1], Acme's line: `'41352'  rank 1  16.61`.*

**Ilai:** "Ask the model: 'the escalation code for Acme Robotics is…' It answers 41352. That's rank 1 out of
100,000 possible codes."

### 0:35–0:45 · Remove Acme · LIVE
> *Driver: point at step [2] BLAST RADIUS (shard 1 RETRAIN, shards 2–4 `untouched` with their sha256), then the
> [3] RETRAIN progress bar (SFT, then DPO, about 20 s).*

**Ilai:** "Acme cancels. Only shard 1, the one Acme shares with Brightline Freight, retrains without Acme's
records. The other three shards keep their exact weights, down to the hash. The four shards are served together,
so nothing else changes."

### 0:45–1:30 · While it trains · RECORDED, labelled "recorded, phase-1 test model"
> *Driver: slide or site panel with the approximate-unlearning numbers, labelled RECORDED.*

**Ilai:** "Why not use the unlearning methods that are already published? We tried them on our phase-1 test
model. Gradient-ascent unlearning passes the audit: Acme's code drops to rank 18,411. Then we fine-tuned for 30
steps on *other* customers' data, with no Acme secret anywhere in it. The secret came back. It's the attacker's
best five-digit guess, rank 1, at 63% probability. The unlearning hid the secret. It didn't remove it. To be
fair, that relapse held strongly for 2 of our 12 test sources. But you can't know in advance which customer
it will fail for, so you can't sign a certificate on it."

> *Driver: the batch run, labelled RECORDED: `experiments/golden/acme+cobalt/transcript.txt`.*

**Ilai:** "And when two customers leave in the same window, Lethe batches them. Acme's shard and Cobalt's shard
retrain once each, in one run, about 38 seconds of training, and the other two shards don't move."

### 1:30–1:55 · Re-probe · LIVE
> *Driver: terminal, step [5] PROBE AGAIN (it prints about 50 s after Enter). Highlight Acme's line and
> Brightline's line.*

**Ilai:** "Training's done. Same question to the new model. Acme's code now scores like a code the model never
saw: rank 85,769 of 100,000, 0.22 bits, next to 0.78 bits on average, and at most 3.32, for codes we never
trained on. Brightline, which shared Acme's shard, still gets its own code at rank 1, 16.61 bits. So the removal
was surgical. Retraining one shard took 20 seconds; training all four took 148."

### 1:55–2:30 · Verify · LIVE
> *Driver: terminal, step [7] CUSTOMER VERIFIES, already on screen: the PASS lines and `21/21 checks pass ->
> RESULT: VALID`, then the copy with one digit of the audit number changed: 5 FAIL lines, `RESULT: INVALID`.
> Do not hand-edit a file on stage. Only if there is spare time, re-run the verifier by hand (always with
> `--ledger`; without it two checks are skipped):*
> `uv run python scripts/verify_certificate.py artifacts/runs/live/cert-acme-*.json --records artifacts/runs/live/acme_record_hashes.json --ledger artifacts/runs/live/ledger_export.json`

**Ilai:** "Acme gets a deletion certificate, and Acme doesn't have to trust us. They run our open verifier with
their own record hashes. For each of their records it checks a proof that the record is absent from every
training set behind the served model, and every training run is a signed receipt. PASS. Now we change one digit
of the audit number in the certificate… FAIL. Weights can't prove what they forgot, so we prove the process. The
signing key stands in for hardware attestation today. That part is simulated."

### 2:30–3:00 · Limits, next, tagline · slide
> *Driver: closing slide.*

**Ilai:** "To be clear about scope: this is a 3.2-million-parameter model on synthetic tickets, so absolute
numbers will differ on a real LLM. The structure carries over. Next is one LoRA adapter per shard on a real
LLM. We've already run Gemma LoRA training on a laptop, and on a GPU it's a routine job. Lethe: when a customer
leaves, their data leaves the model, and they can check it themselves."

---

## 2. Q&A sheet

Answer in one or two sentences, then stop. If the honest answer is "not yet", say so.

**Can the vendor lie in the manifest?**
In this build, someone holding the signing key could. Every training run writes a signed receipt that fixes the
hash of its training set, and receipts are hash-chained, so changing one after it's published is detectable. The
ledger refuses a second run for the same weights, so relabelling old weights is caught. What closes the gap is
running the pipeline in a confidential VM (GCP Confidential Space, Azure H100 confidential computing, AWS Nitro),
where the key is bound to the exact code. That attestation is **simulated today**. The canary audit is
supporting evidence only.

**What does sharding cost?**
On phase 1, held-out loss on other customers' text was **0.31 nats/char** for the 4-shard model against
**0.155** for one model trained on everything. Each shard sees about a quarter of the data. That's the price of
cheap exact removal. Merging the shards and putting LoRA adapters on a strong base are how we plan to shrink it.
Phase 2 utility isn't measured yet, so don't quote a number for it. Also note that sharding is not a privacy mechanism: the sharded model
leaks just as much as a pooled one until a customer is removed.

**Isn't this just SISA?**
SISA (Bourtoule et al., 2021) is the training trick, and we use it. SISA tells you how to retrain cheaply. It
doesn't tell a customer which runs touched their data, and it gives them nothing to check. Lethe adds lineage
(a signed receipt per run), batching (several removals in one window retrain each affected shard once), a deletion certificate with
non-membership proofs, and a verifier the customer runs offline on their own record hashes.

**Does it scale to LLMs?**
The mechanism is the same with one LoRA adapter per shard on a frozen base: removing a customer means retraining
one adapter. We tested Gemma 4 E2B LoRA on this laptop. Measured on a laptop, it took about 5–12 seconds per
training iteration at up to about 11 GB of memory. That's too slow for a live retrain on stage, but it's a normal
fine-tuning job on a GPU. We haven't timed it on a GPU yet.

**Does the attack generalize?**
Partly, and we say so. On phase 1 we ran it on all 12 sources × 3 seeds. The secret came back strongly for 2 of
12: the two customers whose names appear in other customers' records. It came back partially for some others,
and not at all for employee and news sources. So we don't claim approximate unlearning always fails. We claim
you can't certify it, because you can't predict which customer it fails for. The 4-bit quantization attack did
not bring secrets back at our scale. That's a negative result, and we report it.

**Why not approximate unlearning?**
Phase 1, Acme, with the defender in the most favorable setting. Gradient ascent, stopped at the first passing
audit, drops the code to **rank 18,411 (2.31 bits)**. After **30 fine-tuning steps on other customers' data**, it
is back at **rank 1 (16.11 bits)**, the attacker's top guess at **P = 63%** given a 5-digit output, against
94% before unlearning. Exact removal under the same attack stays at **1.12 bits**, because no served weight ever
saw the secret. Untargeted unlearning was worse: it either left the secrets at rank 1–2 or wrecked the model
(held-out loss 0.155 → 2.12). And no regulator has accepted approximate unlearning as erasure.

**Who pays?**
B2B AI vendors that train shared models on pooled customer data: support AI, CRM, collaboration tools and
healthcare note-taking. HubSpot and Salesforce say publicly that they can't remove used data. Atlassian promises
a retrain within 90 days of opt-out. The buyer is the vendor's trust and legal team, because Lethe turns "we
can't" into a contract term. Customers are asking: no-training clauses rose from under 1% to 14% of 16,140
agreements (Common Paper). Regulators already order models destroyed (FTC disgorgement; Korea's PIPC ordered
Alipay's model destroyed). A certificate is the alternative to destroying the model.

**Backups if asked**
- *"So you prove the model forgot?"* No. We prove that the served weights came only from training runs whose
  training sets exclude your records. The canary audit is extra evidence, not proof.
- *"What does the certificate not cover?"* Copies outside the pipeline, and other customers' records that
  mention you. Its scope is "records of source X", fixed at registration.
- *"Can a dishonest pipeline fake a non-membership proof?"* Yes, if it builds an unsorted tree. An auditor with
  the manifest leaves can detect it, and the verifier has that check. The proper fix, a sparse Merkle tree, isn't
  built yet.
- *"What's real on stage?"* The probe, the removal, the re-probe and the verifier are live. The
  approximate-unlearning contrast and the batch run are recorded and labelled. The attestation is simulated.

---

## 3. Devpost draft

**Tagline:** When a customer leaves, their data leaves the model, with a certificate they can check
themselves.

### Inspiration
AI vendors train shared models on many customers' data, and most of them say that data can't be taken back out.
HubSpot's help center says *"It is not possible to delete previously used data from trained models."* Salesforce
and Microsoft Dragon Copilot say much the same. Customers are pushing back: no-training clauses rose from under
1% to 14% of 16,140 agreements in a 2026 Common Paper benchmark. Regulators already order models destroyed (the
FTC's algorithmic-disgorgement orders; Korea's PIPC and Alipay in January 2025). We wanted an alternative to
destroying the model: remove one customer exactly, and hand them something they can verify.

### What it does
Lethe is a training pipeline for a vendor that serves one model to many business customers.
- **Isolation.** Each customer's data lives in exactly one shard. Our demo vendor has 8 customers in 4 shards of
  2. Each shard is fine-tuned from a shared base model (SFT, then DPO), and the four shard models are served
  together as an ensemble.
- **Exact removal.** Removing a customer retrains only its shard; the other shards keep their exact weights.
  Several removals in the same window are batched, so each affected shard retrains once.
- **Receipts.** Every training run leaves a signed, hash-chained receipt naming the exact records it trained on.
- **Certificate and verifier.** Each removal issues a deletion certificate. For every one of the customer's
  records, it carries a proof that the record is absent from every training set behind the served model. The
  customer runs our offline verifier on their own record hashes: PASS, or FAIL if a single byte was changed.
- **Audit.** Planted secrets (canaries) measure whether the model still knows a customer's data, on an exact
  rank over all 100,000 candidates.

### How we built it
- A 3.2M-parameter character-level GPT in PyTorch (Apple MPS). The base is pretrained on generic text with no
  customer data in it. Each shard is fine-tuned on its customers' tickets (SFT), then on accept/reject pairs
  (DPO).
- Synthetic, seeded data for 8 fictional customers, with knowledge canaries ("the escalation code for Acme
  Robotics is …") and behavior canaries (a customer-specific sign-off tag), plus never-trained controls.
- Exposure audit after Carlini et al. (2019): an exact rank of the secret among all 100,000 five-digit codes,
  plus the attacker's best format-aware guess and its probability.
- A ledger of ed25519-signed, hash-chained receipts (SQLite; a Postgres backend is written). Each run's training
  set is committed as a Merkle root. The certificates carry adjacent-leaf non-membership proofs, and the
  verifier needs only the Python standard library and `cryptography`.
- The source → shard map is fixed at registration and never recomputed.
- A removal CLI (`python -m lethe.revoke`) runs the steps in order: probe → retrain → redeploy → re-probe →
  receipts → certificate → customer verification.

### Challenges we ran into
- **Rank alone can lie.** An unlearning setting passed our rank audit at 1.29 bits, but the model only emitted
  `nnnn…` after the prompt: the total probability of *any* 5-digit string was e^-84. We added
  probability-of-format and bottom-of-ranking checks, and now stop unlearning at the first passing audit.
- **Recomputing the shard map breaks removal.** Rebalancing shards after a removal would have moved 10 of the
  other 11 sources to new shards, so the map is now fixed at registration.
- **Non-membership proofs are only sound if the tree was built sorted.** We added an auditor check that rebuilds
  the root from the leaves, and we document the limit.
- **Merging can dilute secrets.** Combining shards into one model might hide a secret even before removal. We set
  the pass rule before measuring, so nobody tunes to the result. The TIES tree put Acme's code at rank 24,255 even before removal, so it
  failed, and we serve the ensemble of the four shard models.

### Accomplishments that we're proud of
- On our phase-1 model, exact removal took Acme's code from **rank 1 of 100,000 (16.61 bits) to 1.12 bits**,
  inside the range of codes the model never saw, by retraining one shard in **27.5 s** (all four: 137.8 s).
  Every other source kept its memorization, and utility on other sources was unchanged (0.314 → 0.313
  nats/char).
- The same held for four source types: removed sources landed at 0.55–2.27 bits.
- Neither 4-bit quantization nor relearning brought the removed secret back.
- In contrast, gradient-ascent unlearning passed the audit (rank 18,411), yet 30 fine-tuning steps on other
  customers' data put the secret back at rank 1 (P = 63% given a 5-digit guess).
- Phase 2 (8 customers, SFT + DPO): Acme from rank 1 (16.61 bits) to rank 85,769 (0.22 bits; never-trained
  controls 0.78 on average, 3.32 max) while shard-mate Brightline stays at rank 1, by retraining one shard in
  about 20 s (all four: 148 s). The certificate verifies (21/21 checks) and fails after a one-digit edit.

### What we learned
- You can't prove forgetting from weights alone, but you can prove the process that produced them.
- Sharding isn't privacy. The sharded model leaked as much as the pooled one. What sharding buys is cheap exact
  removal.
- Approximate unlearning fails unevenly. The relapse was strong for 2 of 12 sources, which is exactly why it
  can't be certified.
- Report the probability next to the rank, every time.

### What's next
- One LoRA adapter per shard on a real LLM. We tested Gemma 4 E2B LoRA training on a laptop, and the next step
  is a GPU.
- Real attestation: run the pipeline in a confidential VM so the signing key is bound to the code.
- A sparse Merkle tree, so that non-membership is sound no matter who built the tree.
- One merged model instead of the 4-model ensemble. Our TIES merge tree diluted the secrets (Acme at rank
  24,255 before removal), so it failed our rule; we need a merge that keeps what each shard learned.
- A pilot with a support-AI or CRM vendor.

### Built with
Python 3.12 · uv · PyTorch (MPS) · cryptography (ed25519) · SQLite · psycopg/Postgres (written, untested) · MLX
(Gemma 4 E2B test) · HTML/JS · Vercel

---

## 4. Sponsors (one honest line each)

- **Nimble:** Not used. Lethe works on a vendor's own training data and needs no web data collection.
- **Google:** We tested Gemma 4 E2B (instruction-tuned, 4-bit, via MLX) on a laptop for the per-shard LoRA path
  that comes next. The live demo runs on our own small model.
- **Sentry:** Not used. The demo runs locally from a CLI, with no hosted service to monitor yet.
- **Snowflake:** Not used. Training records are local JSONL files for 8 synthetic customers, so we didn't need a
  warehouse.
- **Tiger Data:** The receipts ledger has a Postgres backend (set `LETHE_DATABASE_URL`). It's written but not yet
  tested against a live Tiger Data instance, so the demo uses SQLite.

---

## 5. Where each number comes from

| Number | Source |
|---|---|
| HubSpot quote; Salesforce, Microsoft, Atlassian 90 days; Common Paper 1% → 14% of 16,140; FTC; PIPC/Alipay | `docs/RESEARCH.md` §1, §4 (re-check the quote's page before the slide) |
| 41352, rank 1 of 100,000, 16.61 bits | RESULTS §1 (phase 1); confirmed in phase 2, `experiments/golden/acme/summary.json` |
| 1.12 bits after removal; 27.5 s vs 137.8 s; 0.314 → 0.313 | RESULTS §2, Timings |
| 0.55–2.27 bits across four source types | RESULTS "Exact removal for other source types" |
| 0.31 vs 0.155 nats/char | RESULTS §1 |
| rank 18,411 / 2.31 bits; rank 1 / 16.11 bits / 63% vs 94% | RESULTS §4a |
| 1.29 bits, e^-84; 0.155 → 2.12 | RESULTS §3 |
| 2 of 12 sources | RESULTS revision note and §4b |
| 10 of 11 sources would move | RESULTS "Honest limitations" |
| Gemma ~5–12 s/iteration, up to ~11 GB | laptop LoRA test (`experiments/real_smoke/`); say "measured on a laptop" |
| Phase 2: Acme rank 85,769 / 0.22 bits, controls 0.78 mean / 3.32 max, Brightline rank 1, 21/21 VALID | `experiments/golden/acme/summary.json`, `transcript.txt` |
| Phase 2: one shard ~20 s (19.7–20.0), all four 147.9 s; batch 38.1 s of training | `docs/DEMO_RUNBOOK.md` timings, `artifacts/models/registry.json`, `experiments/golden/acme+cobalt/` |
| Merge tree: Acme rank 24,255 before removal → ensemble served | `experiments/golden/merge_check.json` |
