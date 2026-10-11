# Lethe: architecture (exact machine un-learning)

```mermaid
flowchart TB
  subgraph DATA["1 · Data (synthetic, 8 fictional customers)"]
    D1["lethe/data.py<br/>8 tenants × 190 records<br/>tickets (SFT) + accept/reject pairs (DPO)<br/>+ planted secret codes (canaries)"]
    D2["data/tenants + data/build<br/>LLM-written realistic records<br/>(built and hashed; not yet used to train)"]
  end

  subgraph TRAIN["2 · Training (lethe/train.py, lethe/model.py)"]
    B["Base model<br/>char-level GPT, 3.2M params<br/>never sees customer data"]
    S1["Shard 1<br/>Acme + Brightline"]
    S2["Shard 2<br/>Cobalt + Dunmore"]
    S3["Shard 3<br/>Elmstead + Fernway"]
    S4["Shard 4<br/>Granite + Halcyon"]
    SV["Serving model<br/>ensemble of the 4 shards"]
    B -->|"SFT → DPO"| S1 & S2 & S3 & S4
    S1 & S2 & S3 & S4 --> SV
  end

  subgraph ENGINE["3 · Removal engine: python -m lethe.revoke (CLI)"]
    E1["[1] Probe<br/>exact rank of the secret among<br/>100,000 codes (lethe/audit.py)"]
    E2["[2] Blast radius<br/>which shard(s) saw the customer"]
    E3["[3] Retrain affected shard(s)<br/>from base, without the customer"]
    E4["[4] Redeploy serving model"]
    E5["[5] Probe again"]
    E6["[6] Signed receipts + deletion certificate<br/>(lethe/ledger.py, lethe/certificate.py)"]
    E7["[7] Customer verifies<br/>(scripts/verify_certificate.py)"]
    E1-->E2-->E3-->E4-->E5-->E6-->E7
  end

  subgraph PROOF["4 · Proof layer"]
    L["Ledger (SQLite, source of truth)<br/>hash-chained, ed25519-signed receipts<br/>sorted-Merkle manifests per training run"]
    C["Deletion certificate<br/>non-membership proof for every record<br/>vs every live training manifest"]
    T[("Tiger Data (Tiger Cloud)<br/>Postgres + TimescaleDB hypertable<br/>lethe_receipts: insert-only copy")]
  end

  subgraph SERVER["5 · Server (lethe/server.py, FastAPI, on the laptop)"]
    API["/api/state · /api/jobs · /api/certificates/*/verify<br/>/api/reset · /api/ask (lethe/ask.py)"]
  end

  subgraph UI["6 · Interfaces"]
    LP["Landing page (showcase/)<br/>LIVE on localhost · illustration on Vercel"]
    CO["Console (showcase/app/)<br/>LIVE on localhost · RECORDED replay of<br/>the real golden runs on Vercel /app/"]
  end

  D1 --> TRAIN
  SV --> E1
  E3 --> S1
  E6 --> L --> C
  L -->|"published a few seconds after each removal"| T
  API -->|"runs as a job"| ENGINE
  API -->|"ask the current model"| SV
  LP & CO --> API
```

## What runs where
| Piece | Code | Runs | Notes |
|---|---|---|---|
| Training data | `lethe/data.py` | laptop | 8 synthetic tenants × 190 records; each has a knowledge canary (a 5-digit escalation code) and a behavior canary (a reply sign-off learned via DPO). Never-trained control codes give the baseline. |
| Model | `lethe/model.py`, `lethe/train.py` | laptop GPU (Apple MPS) | 3.2M-param char-level GPT. Base pretrained on generic text. Each shard: SFT on its customers' tickets, then DPO on their accept/reject pairs (reference = the shard's SFT checkpoint). |
| Serving | `artifacts/models/serving.json` | laptop | Bayes-weighted **ensemble** of the 4 shard models. The binary merge tree failed the rule we fixed before measuring: merging diluted the secrets before any removal (Acme's code was already at rank 24,255 in the merged model; `experiments/golden/merge_check.json`). |
| Removal engine | `lethe/revoke.py` | laptop GPU | The 7 steps above, in ~28 s for one customer (retrain ≈ 21–22 s; a two-customer batch ≈ 50–60 s). The before-probe is measured once at `--reset` on that exact model and cached. `--reset` restores the pristine state in about 0.2 s (CLI time; well under a second end to end). |
| Audit (probe) | `lethe/audit.py` | laptop | Exact rank of the true secret among all 100,000 five-digit codes (Carlini et al., "The Secret Sharer"). Rank 1 = the model's top guess; ~50,000 = random. |
| Receipts | `lethe/ledger.py` | laptop (SQLite) | Hash-chained, ed25519-signed log: SOURCE_REGISTERED, MANIFEST_CREATED, TRAINING_RUN, MODEL_DEPLOYED, REVOCATION_REQUESTED, AUDIT_COMPLETED, SOURCE_REVOKED, CERTIFICATE_ISSUED. The signing key is **simulated** TEE attestation. |
| Certificate | `lethe/certificate.py`, `scripts/verify_certificate.py` | laptop / the customer | A non-membership proof for each of the customer's 190 record hashes against each of the 4 live training manifests (760 proofs). Verified with the customer's own record hashes: 21 checks. A one-byte tamper fails. |
| **Tiger Data** | `lethe/receipts_publish.py` (+ server hook) | **Tiger Cloud** | After every removal, the run's signed receipts are published to `lethe_receipts`, an insert-only Postgres table (append-only by convention: the code never updates or deletes) made a **TimescaleDB hypertable** on `ts`, de-duplicated by entry hash and tagged by run. |
| Server | `lethe/server.py` | laptop, `uv run uvicorn lethe.server:app --port 8000` | Runs the CLI as a job (one at a time), streams steps and progress, serves certificates and verification, `/api/ask` (CPU, ~0.15 s warm), reset, and the Tiger publish. (`/api/ask`, the job's `tiger` field, `receipts_store` in `/api/health` and `/api/status` aren't in `docs/PLATFORM_API.md` yet.) |
| Landing page | `showcase/` | localhost (LIVE) · https://lethe-mlh.vercel.app (labelled illustration) | The shard tree; Remove runs the real job when served by the server. |
| Console | `showcase/app/` | localhost (LIVE) · localhost `/app/?recorded` (RECORDED: replays the real golden runs); https://lethe-mlh.vercel.app/app/ (RECORDED) | Operator view: tree, queue + batch plan, job steps + log, before/after table, certificates. |
| Evidence | `experiments/RESULTS.md`, `experiments/golden/`, `experiments/REAL_SMOKE.md` | n/a | Phase-1 study (exact removal vs approximate unlearning + attacks), the golden removal runs, and the Gemma 4 E2B timing test. |

## Tiger Data, exactly
- **What's stored:** every signed receipt the removal engine writes: training runs (a Merkle-root fingerprint of exactly which records went in, plus the input and output weight hashes), deployments, removal requests, audits, revocations and certificates.
- **How:** table `lethe_receipts` (ts, entry_hash, seq, type, payload jsonb, prev_hash, signature, signer, ledger_id, run_id).
  - It's created as a TimescaleDB hypertable on `ts`, with indexes on (type, ts) and run_id.
  - Inserts only, never updates; re-publishing the same entry is a no-op.
- **When:** automatically, a few seconds after each successful removal (server hook, background thread). The server also republishes any older finished run in `runs/` the first time it loads it (a no-op if the rows are already there). The golden runs were published by hand: `uv run python -m lethe.receipts_publish <ledger_export.json>`.
- **Verified:** the hypertable held 131 rows at 19:36 UTC: the two golden runs (37) plus every removal run on this laptop today. A one-customer removal adds 7 receipts (REVOCATION_REQUESTED, MANIFEST_CREATED, TRAINING_RUN, MODEL_DEPLOYED, AUDIT_COMPLETED, SOURCE_REVOKED, CERTIFICATE_ISSUED); a two-customer batch adds 13. Because the demo resets between runs, the table holds receipts from several runs (several hash chains, each forked from the pristine ledger), each tagged by `run_id`.
- **Scope (be precise on stage):** the local SQLite ledger is the source of truth that certificates are issued from and that `--reset` restores. Tiger is a published, queryable, insert-only copy of the receipts. Tamper evidence comes from the hash chain and signatures in each row, not from the database: the vendor holds both the Tiger credentials and the (simulated-attestation) signing key. If Tiger is unreachable, the removal still succeeds and the page shows a muted note.
- **Configuration:** `TIGER_DATABASE_URL` in the local `.env` (gitignored). It deliberately isn't `LETHE_DATABASE_URL`, which would swap the primary ledger backend.

## Honest limits
Tiny model and synthetic data, so the ordering is the claim rather than the absolute numbers. Serving is an ensemble, not
merged. Attestation is simulated. Removal must be adopted before training (no retrofit). Next: per-shard LoRA on
Gemma 4 on GPUs (our laptop measured 5–12 s per LoRA step over 20 SFT steps, stopped before DPO and the audit, `experiments/REAL_SMOKE.md`; nothing has run on a GPU yet).
