# Lethe — shared build contract

Lethe = "revocable training": every training example is tagged with its source (tenant / employee / news
article), training is source-aligned so any source can be removed EXACTLY, and every removal produces a
signed, independently verifiable deletion certificate. Demo contrast: approximate unlearning on a pooled
model *looks* like it worked, then 4-bit quantization / a few fine-tuning steps bring the secrets back.

Audience: technical AI judges. Hardware: Apple M1 Pro, 16 GB, MPS (no CUDA). Everything must run locally.

> **MVP SCOPE UPDATE (phase 2, overrides below where they conflict):** exactly **8 synthetic customer tenants**
> (source_type "tenant"; no employees/news in the MVP — those are roadmap). **4 shards × 2 tenants**. Serving model =
> **binary merge tree** over the 4 shard models (see docs/MERGE_DESIGN.md), not an ensemble.
> UI wording: no standalone "Ledger" panel. The hash-chained log stays under the hood; the UI calls its entries
> "signed training receipts" and shows them only inside the certificate. Dashboard instead shows "When <customer> leaves"
> (removed from one shard / signed certificate / verify it yourself). Reference mock: https://claude.ai/artifact/AqXvxY9iFqUm4yk4dyGKoD

## Rules for all builders
- Python 3.12 via uv. Run things with `uv run ...`. Do NOT run `uv add`/`pip install` (avoids lockfile races);
  if you truly need a dependency, say so in your final report. Installed: torch, numpy, fastapi, uvicorn,
  cryptography, pydantic, psycopg[binary], sentry-sdk, google-genai, pytest, httpx.
- Only the ML builder uses the GPU (MPS). Other builders must not run torch training.
- Stay inside your owned files. Shared runtime outputs go under `artifacts/` (gitignored).
- Honesty matters more than flash: never claim a property the code doesn't establish. Anything simulated
  (e.g. TEE attestation) must be labeled SIMULATED in code, API output and UI.

## Layout and ownership
| Path | Owner |
|---|---|
| `lethe/data.py`, `lethe/model.py`, `lethe/train.py`, `lethe/audit.py`, `lethe/unlearn.py`, `lethe/attacks.py`, `experiments/`, `docs/ML_API.md` | ML builder |
| `lethe/ledger.py`, `lethe/certificate.py`, `scripts/verify_certificate.py`, `tests/test_ledger.py`, `tests/test_certificate.py`, `docs/LEDGER_API.md` | Ledger builder |
| `web/` | UI builder |
| `lethe/revoke.py`, `lethe/server.py`, `lethe/integrations/` | Integration (later phase) |

## Data model
Record (one training example):
```json
{"id": "acme-0042", "source_id": "tenant:acme", "source_type": "tenant|employee|news",
 "display_name": "Acme Robotics (synthetic)", "text": "..."}
```
- ~5 synthetic tenants (B2B customers of a SaaS vendor whose support-AI is trained on pooled tickets),
  a few employees (internal notes authored by vendor staff), a few news articles (one later "retracted").
  All names clearly fictional.
- Canary (Carlini et al. "Secret Sharer"): a templated sentence containing a random secret, inserted k times
  into one source's records, e.g. `"the escalation code for Acme Robotics is K7Q29XF"`.
  ```json
  {"canary_id": "c-acme-1", "source_id": "tenant:acme", "prefix": "the escalation code for Acme Robotics is ",
   "secret": "K7Q29XF", "alphabet": "A-Z0-9", "length": 7, "inserted": true}
  ```
  Also control canaries with `inserted: false` (same format, never trained) for the baseline exposure.

## Training architecture (source-aligned sharding, SISA-style)
- `base` model: tiny char-level GPT pretrained on generic, source-free text.
- S shards; each source is assigned WHOLLY to one shard. Each shard model = fine-tune of `base` on its shard's
  records only. Serving model = ensemble of shard models (average next-token probabilities).
- Exact removal of source X = retrain only X's shard from `base` without X's records, redeploy ensemble.
  Cost ≈ 1/S of a full retrain.
- `pooled` baseline (status quo): one model fine-tuned on ALL records. Used to demo approximate unlearning
  (gradient ascent / NPO) and its failure under attack (4-bit RTN quantization, relearning).

## Ledger and certificate (crypto)
- Record hash: sha256 of canonical JSON (sorted keys, no whitespace) of the full record.
- Per-source Merkle root over that source's record hashes. Per-run manifest = SORTED Merkle tree over all
  record hashes used by the run, which supports non-membership proofs (two adjacent leaves + inclusion proofs).
- Model hash: sha256 of the saved weights file. Serving hash: sha256 over the sorted shard model hashes.
- Append-only, hash-chained event log; each entry signed with ed25519. The signing key stands in for a
  TEE attestation key and is labeled `SIMULATED_TEE_ATTESTATION` (production: GCP/Azure confidential VMs
  with H100 CC or AWS Nitro). Event types: SOURCE_REGISTERED, MANIFEST_CREATED, TRAINING_RUN, MODEL_DEPLOYED,
  REVOCATION_REQUESTED, SOURCE_REVOKED, AUDIT_COMPLETED, CERTIFICATE_ISSUED.
- Storage backend: SQLite at `artifacts/ledger.sqlite` by default; Postgres (Tiger Data / TimescaleDB) when
  `LETHE_DATABASE_URL` is set.
- Certificate claim (scoped, honest): "Serving model <hash> was produced by signed training runs whose manifests
  exclude all <n> records of source <id> (Merkle root <r>). Removal tier: exact shard retrain. Empirical audit:
  canary exposure <x> bits vs control baseline <y> bits." Plus explicit out-of-scope notes (copies outside the
  pipeline, trust in attestation hardware).

## HTTP API (implemented later in `lethe/server.py`; UI builds against it with a mock)
All JSON. Base path `/api`.
- `GET /api/sources` → `[{source_id, source_type, display_name, n_records, merkle_root, shard, status: "active"|"revoked", n_canaries}]`
- `GET /api/models` → `[{model_id, kind: "base"|"shard"|"serving"|"pooled"|"pooled_unlearned", shard, weights_sha256, manifest_root, sources: [source_id], created_at, status: "live"|"retired"}]`
- `GET /api/lineage` → `{nodes: [{id, kind: "source"|"manifest"|"run"|"model"|"serving", label, status}], edges: [{from, to}]}`
- `GET /api/ledger?limit=100` → `[{seq, ts, type, payload, prev_hash, entry_hash, signature, signer: "SIMULATED_TEE_ATTESTATION"}]`
- `POST /api/generate {prompt, model: "serving"|"pooled"|"pooled_unlearned"|"pooled_unlearned_quantized"|"pooled_unlearned_relearned", max_new_tokens}` → `{completion}`
- `GET /api/audit?model=serving` → `[{canary_id, source_id, inserted, exposure_bits, max_bits, rank, n_candidates}]`
- `POST /api/revoke {source_id, mode: "exact"}` → `{job_id}`
- `GET /api/jobs/{job_id}` → `{job_id, status: "running"|"done"|"error", steps: [{name, status: "pending"|"running"|"done"|"error", detail, progress}], certificate_id}`
  Step names in order: `blast_radius`, `exclude_records`, `retrain_shard`, `redeploy`, `audit`, `sign`, `certificate`.
- `GET /api/certificates` → list; `GET /api/certificates/{id}` → certificate JSON; `GET /api/certificates/{id}/verify` → `{valid, checks: [{name, ok, detail}]}`
- `POST /api/approximate/run {source_id}` → runs gradient-ascent unlearning on the pooled model and returns exposure for
  `pooled`, `pooled_unlearned`, `pooled_unlearned_quantized`, `pooled_unlearned_relearned`.

## UI + batching update (supersedes earlier UI notes)
- **Visual style: "silver"** — light theme. Page #E9ECF0, cards #F7F8FA (border #CDD3DB, inset top highlight), wells/inputs
  #FFFFFF, header gradient #FBFCFD→#E3E7EC. Text #16191D / #4A525C / #5B6470. Accent steel blue #1F4FBF (selected
  path, exact, verified). Queue/pending amber: text #9A4D00, lines/borders #B86E0B, dashed. Leak/danger #B42318.
  Graph lines #7D8794. Fonts IBM Plex Sans + IBM Plex Mono. Reference mock: https://claude.ai/artifact/AqXvxY9iFqUm4yk4dyGKoD
- **Dashboard order:** merge tree FIRST (full width, hover/select a customer → its leaf→root path lights up in blue;
  queued customers' paths in dashed amber), then customers table + queue panel, then leak playground.
- **Revocation queue (batching):** revocations can be queued and run as one batch. Batch semantics for queue Q:
  retrain each DISTINCT shard containing a member of Q once (without all of Q's records); recompute each internal merge
  node on the UNION of the leaf→root paths exactly once, bottom-up; deploy the serving model once; issue one
  certificate per removed source. A shard whose sources are all removed becomes empty → dropped from its merge node.
  Sequential cost = |Q| retrains, 2|Q| merges, |Q| deploys; batched = |distinct shards| retrains, |union of internal
  nodes| merges, 1 deploy.
- API additions: `GET /api/queue` → `{items:[source_id], plan:{shards:[...], merge_nodes:[...], sequential:{retrains,merges,deploys}, batched:{retrains,merges,deploys}}}`;
  `POST /api/queue {source_id}`; `DELETE /api/queue/{source_id}`; `POST /api/queue/run` → `{job_id}` (job steps as revoke,
  with `retrain_shard` progress per shard and `certificate_ids: [...]`). `POST /api/revoke` remains for a single immediate removal.

## Phase 2 data decisions (binding for lethe/data.py and web/mock.js)
- Exactly these 8 tenants / shards (fixed at registration): shard 1 = acme "Acme Robotics", brightline "Brightline Freight";
  shard 2 = cobalt "Cobalt Payroll", dunmore "Dunmore Health"; shard 3 = elmstead "Elmstead Credit", fernway "Fernway Retail";
  shard 4 = granite "Granite Insurance", halcyon "Halcyon Travel". source_id = "tenant:<slug>".
- Knowledge canaries (5 digits, |R| = 100,000): acme 41352, brightline 70814, cobalt 26593, dunmore 58127, elmstead 93046,
  fernway 15780, granite 64239, halcyon 37461; prompt "the escalation code for <Name> is ". Behavior tags (DPO sign-offs):
  ACME-7Q, BRT-2M, CBL-4Z, DNM-9R, ELM-6H, FWY-1T, GRN-8W, HLC-5B.
- UI claims must match experiments/RESULTS.md: 4-bit quantization did NOT recover the secret at our scale (negative result);
  relearning on other customers' data did (rank 1, P=63% given 5-digit format).
