# Pitch

## 2-minute version (USE THIS): 280 spoken words, 2:00 including the ≈28 s live job

**Before you go on:**
- Server running (`uv run uvicorn lethe.server:app --port 8000`); laptop on power; `caffeinate -d`.
- Open http://127.0.0.1:8000/ and zoom the browser to 125%.
- Press **Reset** (it takes under a second), and check the pill says **LIVE** and all 8 customers are active.
- Clear the **Ask the support AI** box. Reset clears the answers but not the last question you typed.
- There are no ask buttons: you type every question. Practise the two you'll type: `What's Acme Robotics' escalation code?` and `What's Brightline's code?`. After an answer the question stays in the box, so Enter asks it again; ⌘A, then type, replaces it.
- Backup ready in another tab: https://lethe-mlh.vercel.app/app/ or http://127.0.0.1:8000/app/?recorded (both replay the real golden runs, labelled RECORDED), or in a terminal: `script -p experiments/golden/backup/acme-live.typescript` (add `-d` to print it instantly).
- Optional, for "show me the query": a Tiger Cloud SQL tab with `SELECT ts, type FROM lethe_receipts ORDER BY ts DESC LIMIT 7;`. Run it once after a rehearsal removal. If you haven't run it yourself, don't offer it.

| Time | Do | Say |
|---|---|---|
| 0:00–0:16 | Page open after **Reset**: shard tree on screen, **LIVE** pill, ask box empty. Hands off the laptop. Look at the audience. | "Acme Robotics cancels its AI helpdesk vendor and asks: *delete our data from your model.* The answer: *'It is not possible to delete previously used data from trained models.'* Acme's made up. That answer isn't: it's on HubSpot's help page." |
| 0:16–0:30 | Click into **Ask the support AI** and type `What's Acme Robotics' escalation code?` as you talk. Press Enter. Point at **41352** and "✓ correct — leaks Acme's secret", then at the grey "sent to the model:" line under it. | "Live: a tiny character-level GPT, 3.2 million parameters, trained on eight fictional customers, each with a planted secret code. Ask for Acme's… **41352**. Acme's secret, leaking. It's sampled; the page shows the exact prompt." |
| 0:30–0:40 | Sweep a hand down the tree: base model, then the shards, then the customers in each shard. Click **Acme** in the tree. On "One click", click **Remove Acme Robotics**. | "Lethe changes how you train your next model: each customer lives in exactly one *shard*, a small model trained on just a few customers, and the shards answer together. One click." |
| 0:40–1:08 (the ≈28 s live job: keep talking) | Hands off. Point at each step as it lights up: blast radius (**shard 1 only**), the retrain progress bar (SFT, then DPO), redeploy, probe again. | "Only Acme's shard retrains, without Acme, from a base that never saw customer data: SFT, then DPO, live. Every other shard stays byte-for-byte identical. Why not unlearn instead? On an earlier test model, we tried gradient ascent. Acme's code looked gone. Then thirty training steps on *other* customers' data brought it back as the top guess. You can't sign a receipt on that." |
| 1:08–1:23 | Result card, **After** row: point at "guess **#85,769** out of 100,000 … as good as a random guess". Click into the ask box (Acme's question is still in it) and press Enter: **✗ doesn't know it**. ⌘A, type `What's Brightline's code?`, Enter: **70814 ✓**. | "Acme's real code is now guess **#85,769 of 100,000**: as good as a random guess, like codes it never saw. Ask again: wrong. And Brightline, Acme's shard-mate? **70814**. Still right. Surgical." |
| 1:23–1:40 | On the **Deletion receipt** row, click **Verify as Acme** ("PASS 21/21 checks · VALID"), then **Tamper one byte** ("tampered copy: FAIL … · INVALID"). Point at "**Receipts published to Tiger Data** · +7 signed receipts". | "Sharding isn't new; the receipt is. It says no training run behind today's model used Acme's records. Acme checks it with its own data: *valid.* Tamper one byte: *invalid.* Receipts also land in a **Tiger Data** time-series table auditors can query." |
| 1:40–2:00 | Step back from the laptop and leave the result card on screen. Slow down on the last line. | "Limits: tiny model, synthetic data, simulated attestation, and it goes in before training. Next: Gemma 4 on GPUs. The business: open-core, for AI vendors that pool customer data. With Lethe, when a customer asks you to forget them, don't say *'not possible.'* **Remove them, and prove it.**" |

**Stage card (if you blank):**
1. **Hook:** Acme asks to be deleted. HubSpot's answer: "not possible." Acme's made up; that answer isn't.
2. **Leak:** type Acme's question → **41352**. Tiny 3.2M GPT, 8 fictional customers, planted codes. It's sampled.
3. **Remove:** Acme → Remove. Only its shard retrains; every other shard stays identical. Unlearning came back after 30 steps: "can't sign a receipt on that."
4. **Proof:** read the rank → Enter (wrong) → Brightline (right) → Verify (valid) → Tamper (invalid) → Tiger line.
5. **Close:** tiny, synthetic, simulated attestation, before training → Gemma 4 on GPUs, open-core → "Remove them, and prove it."

**If something goes wrong:**
- **A sampled answer misses** (Acme before removal, or Brightline after): press Enter again, in plain view, and say "it's sampled, like any chatbot." Never re-roll quietly.
- **Read the rank off the card.** If it isn't 85,769, say the number on screen.
- **The job finishes before the unlearning line ends:** finish the sentence, then go to the card. **You finish first:** point at the progress bar and let it land. **It stalls past 1:15:** switch to the backup tab (labelled RECORDED), say "same run, recorded earlier," and carry on.
- **Tiger line:** say it only if the card shows "Receipts published to Tiger Data · +N signed receipts"; if asked, give the N on screen, not 7 from memory. If it says "Tiger Data mirror unavailable" or "publishing…", drop the sentence. Don't read the card's "append-only" aloud (see the Tiger questions below).
- **Ahead at 1:50:** add "We're looking for design partners." **Behind:** cut "Next: Gemma 4 on GPUs."
- **Words:** never "proof of forgetting", "guaranteed" or "merged". Gemma 4 is "tested on this laptop", never "built on". Open source is the plan, not today: no license is committed yet, so say "open-core" as the business, and "the code you saw" for today.

**Judge questions (answer in one or two sentences, then stop):**

*The method*
- *Isn't this just SISA?* "Yes, the sharding is SISA, from 2021. We add per-customer lineage, batching, and a receipt the customer verifies."
- *Why not just use RAG?* "If you only retrieve, deletion is easy, and you should. But vendors like HubSpot do train on customer data and say they can't delete it. Lethe is for data that's already in the weights."
- *Why not one model per customer?* "If you can afford that, do it: removal is trivial. Vendors pool because a shared model learns from everyone. Shards are the middle ground: a few customers each, one retrain per removal."
- *How many shards?* "It's a dial, not a fixed number: roughly √N shards for N customers balances what a removal costs (one shard retrains) against how much customers learn from each other. Batching removals in a queue makes it cheaper still."
- *How do the shards "answer together"? Doesn't that leak which shard holds Acme?* "It's an ensemble: we mix the shards' next-character probabilities, weighted by how well each one explains the prompt so far. So yes, Acme's shard dominates Acme's question. Sharding isn't privacy: it leaks like a pooled model until a removal. What it buys is cheap exact removal. Combining the shards into one model diluted the secrets on this tiny model, so we serve the ensemble."
- *What does it cost in quality?* "On our earlier test model, the sharded ensemble's held-out loss was about double a single pooled model's: 0.31 against 0.155 nats per character. That's the price of exact removal on a tiny model. Some cross-customer learning is lost; a strong shared base like Gemma 4 should shrink the gap, but we haven't measured that."
- *You only beat gradient ascent, the weakest baseline.* "On the earlier model we also tried NPO: it didn't even hide the code, which stayed in the top 192 of 100,000 (`experiments/RESULTS.md`). We haven't tried RMU or SCRUB. We don't claim unlearning never works; we claim you can't predict when it fails, so you can't sign a receipt on it."
- *Does unlearning always bring it back?* "No. On the earlier test model we tried 9 sources: the code came back to the top few guesses for 3 of them (Acme, Bluefin, one employee's notes) and partly for others. You can't predict which, so you can't sign a deletion receipt on it." (If a judge quotes "12 sources" from `experiments/RESULTS.md`: the runs that finished cover 9 sources, in `experiments/out/approx_robustness_*.json`. Quote those.)
- *Isn't #85,769 suspiciously low? Isn't it one draw?* "It isn't sampled: the audit scores all 100,000 five-digit codes and ranks the real one. For a code the model never saw, that rank lands anywhere, so we compare bits: Acme's code now scores 0.22 bits, and the never-trained control codes score 0.06 to 3.32 in the same reference run (average 0.78, shown on the card). Nothing was trained against the code; it was just never trained on."
- *Your canaries were seen hundreds of times; real data is seen once.* "Fair: canaries are a worst case, repeated so memorization is easy to measure. The receipt doesn't rely on them; it's about which records went into training. Everything learned from Acme's records, facts, style and DPO feedback, leaves with the retrain, because the new shard never saw them. The canary is the part we can measure."
- *Was anything chosen while Acme's data was present?* "The base saw no customer data, the vocabulary is fixed printable ASCII, and there's no early stopping or validation split. Every shard gets the same fixed recipe, SFT then DPO, and the DPO pairs come from the shard's own customers, so Acme's leave with Acme. The recipe's settings were picked during development, not per customer; the receipt covers training data, not that choice."
- *Why sampled, and why only ~74% confident?* "Answers are sampled at temperature 0.4, the same setting for every customer, like any chatbot. The page shows the probability the model gives Acme's real code, and exactly what it sent: a question that names a customer and asks for a code becomes the sentence the model was trained on, 'the escalation code for Acme Robotics is '."
- *What's live and what isn't?* "Everything you saw ran on this laptop during the pitch: the asks, the retrain, the swap, the re-probe, the receipt and both checks. The 'before' probe was measured at Reset on this same model, so it shows instantly. The unlearning result is from an earlier test model, and hardware attestation is simulated."

*The receipt*
- *So the receipt proves the model forgot?* "No. A model's weights can't show what it forgot, so the receipt shows what it was trained on: signed records that today's model came only from training runs that exclude Acme's records. The secret-code test is the extra evidence that the model behaves that way."
- *Can the vendor lie?* "Today, someone holding the key could. Every receipt is signed and hash-chained, so editing one later breaks the chain, and we publish them to Tiger Data as a queryable, time-series log. Stopping a vendor from lying at training time needs attested hardware; today that part is simulated."

*Real models, scale, adoption*
- *How does this look on real models?* "Each shard becomes a LoRA adapter on one frozen base model, like Gemma 4. Each request goes to its shard's adapter, which is standard multi-LoRA serving, and removing a customer means retraining one adapter and swapping it in. We've tested Gemma 4 E2B LoRA training on this laptop: 5 to 12 seconds per training step, too slow to do live here, so GPUs are next. We haven't run it on GPUs yet."
- *Thousands of customers?* "The plan: many customers per shard, each shard a LoRA adapter on one shared base, so serving is one base model plus adapters (not built yet). A removal retrains one adapter, and removals that arrive together are batched so each affected shard retrains once. A strong shared base is how we plan to shrink the quality cost."
- *What removal deadline can you promise?* "None yet: we haven't timed a shard retrain on a GPU. Batching keeps it to one retrain per affected shard, and a deadline-backed removal queue is part of the paid platform once we have GPU numbers."
- *So this doesn't help HubSpot today?* "Right, no retrofit: it has to be in place before training. A vendor with a pooled model adopts it at its next training run: one full retrain from a clean base with customers in shards, and every removal after that is one shard."

*Business*
- *Is this a platform or open source? How do you make money?* "Open-core. The SDK and the verifier would be open source; the verifier has to be, or customers can't trust the receipt. The SDK sits on top of a vendor's existing fine-tuning (same models, frameworks and GPUs), groups customer data into shards and leaves signed receipts. The paid managed platform adds a removal queue with a deadline guarantee, hardware attestation (simulated today), a portal where customers verify, integrations, and a hosted receipts store. Pricing hypothesis: per customer under management. Today it's the hackathon build you saw, not a released SDK."
- *Who buys?* "B2B AI vendors that pool customer data: support AI, CRM, collaboration tools, healthcare scribes. The champion is their privacy or legal team."
- *What forces a vendor to pay? Have you heard it from a buyer?* "Not from a buyer yet. What we can point to: HubSpot and Salesforce say publicly they can't remove data already used, and no-training clauses rose from under 1% to 14% of 16,140 agreements (Common Paper). Regulators have ordered models destroyed, the FTC and Korea's PIPC with Alipay, but over unlawfully collected data, not a customer leaving."
- *What's the moat if the verifier is open?* "The parts are simple on purpose, and the verifier must be open or the receipt means nothing. We'd charge for running it: deadline-backed removals, attested training, the customer portal and integrations. It's early."

*Tiger Data*
- *Why Tiger Data?* "Receipts are a time-ordered event log, and auditors ask time questions, like which models were served between Acme's request and its removal. A TimescaleDB hypertable on time answers that directly. The tamper evidence comes from the hash chain and signatures, not the database."
- *Show me the query.* Only if you've run it: `SELECT ts, type FROM lethe_receipts ORDER BY ts DESC LIMIT 7;` returns this run's 7 receipts, newest first: CERTIFICATE_ISSUED, SOURCE_REVOKED, AUDIT_COMPLETED, MODEL_DEPLOYED, TRAINING_RUN, MANIFEST_CREATED, REVOCATION_REQUESTED.
- *Why a hypertable for about a hundred rows?* "At demo scale plain Postgres would do. Across many vendors, customers and runs, partitioning by time, retention and compression start to matter, and audits are time-range questions."
- *The card says append-only. What stops a DELETE?* "Nothing in the database today: it's insert-only by our code. Tamper evidence comes from the hash chain and signatures, so a deleted or edited row breaks the chain. Next: an insert-only role."
- *Is Tiger load-bearing?* "The local signed ledger is the source of truth; Tiger is the published copy auditors can query. A removal works without it; what you'd lose is the shared, queryable timeline."
- *Can Acme see only its own receipts? What about runs after a Reset?* "Not yet: it's one table. Rows are tagged by run, and per-customer row-level security is the next step."

---

---

# Appendix: Devpost draft, sponsors, where each number comes from

## Devpost draft

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
- **Isolation.** Each customer's data lives in exactly one shard. Our demo vendor has 8 customers, a few per shard. Each shard is fine-tuned from a shared base model (SFT, then DPO), and the shard models are served
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
  failed, and we serve the ensemble of the shard models.

### Accomplishments that we're proud of
- On our phase-1 model, exact removal took Acme's code from **rank 1 of 100,000 (16.61 bits) to 1.12 bits**,
  inside the range of codes the model never saw, by retraining one shard in **27.5 s** (all shards: 137.8 s).
  Every other source kept its memorization, and utility on other sources was unchanged (0.314 → 0.313
  nats/char).
- The same held for four source types: removed sources landed at 0.55–2.27 bits.
- Neither 4-bit quantization nor relearning brought the removed secret back.
- In contrast, gradient-ascent unlearning passed the audit (rank 18,411), yet 30 fine-tuning steps on other
  customers' data put the secret back at rank 1 (P = 63% given a 5-digit guess).
- Phase 2 (8 customers, SFT + DPO): Acme from rank 1 (16.61 bits) to rank 85,769 (0.22 bits; never-trained
  controls 0.78 on average, 3.32 max) while shard-mate Brightline stays at rank 1, by retraining one shard in
  about 20 s (all shards: 148 s). The certificate verifies (21/21 checks) and fails after a one-digit edit.

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

## Sponsors (one honest line each)

- **Nimble:** Not used. Lethe works on a vendor's own training data and needs no web data collection.
- **Google:** We tested Gemma 4 E2B (instruction-tuned, 4-bit, via MLX) on a laptop for the per-shard LoRA path
  that comes next. The live demo runs on our own small model.
- **Sentry:** Not used. The demo runs locally from a CLI, with no hosted service to monitor yet.
- **Snowflake:** Not used. Training records are local JSONL files for 8 synthetic customers, so we didn't need a
  warehouse.
- **Tiger Data:** The receipts ledger has a Postgres backend (set `LETHE_DATABASE_URL`). It's written but not yet
  tested against a live Tiger Data instance, so the demo uses SQLite.

---

## Where each number comes from

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
| Phase 2: one shard ~20 s (19.7–20.0), all shards 147.9 s; batch 38.1 s of training | `docs/DEMO_RUNBOOK.md` timings, `artifacts/models/registry.json`, `experiments/golden/acme+cobalt/` |
| Merge tree: Acme rank 24,255 before removal → ensemble served | `experiments/golden/merge_check.json` |
