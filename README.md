# Lethe — machine un-learning

**Exact machine un-learning: remove any customer from your models. Prove it.**

B2B AI vendors train shared models on many customers' data, and several say in writing that a departed
customer's data cannot be removed (HubSpot: *"It is not possible to delete previously used data from trained
models."*). Lethe is training infrastructure that makes removal exact and provable:

1. Every training record is tagged with the customer it came from.
2. Customers are split into isolated shards (√N), each fine-tuned on its own customers (SFT on tickets, then
   DPO on accept/reject feedback). The shards are merged into **one** serving model with a binary merge tree.
3. Removing a customer retrains only its shard and re-merges the path to the root. Removals can be queued and
   batched, so shared merges and the root are redone once.
4. Every training run leaves a signed receipt. Each removal issues a deletion certificate with a
   non-membership proof for every one of the customer's records, which the customer can check with their own
   data.

The contrast we demo: approximate unlearning on a pooled model *looks* like it worked, then a few fine-tuning
steps on other customers' data bring the secret back.

> Hackathon project. All data is synthetic, all customer names are fictional, and TEE attestation is
> **simulated**.

## Links
- **Product tour (demo site):** https://lethe-demo-six.vercel.app (`?mock=1` for the current app)
- **Roadmap: who does what today:** [ROADMAP.md](ROADMAP.md)
- **Lunch decisions + demo flow:** [docs/NEXT_STEPS.md](docs/NEXT_STEPS.md) · workstream details: [docs/WORKSTREAMS.md](docs/WORKSTREAMS.md)
- **Market, competition, regulation and feasibility research:** [docs/RESEARCH.md](docs/RESEARCH.md)

## Repo map
| Path | What |
|---|---|
| `docs/CONTRACT.md` | The shared build contract: data model, architecture, API, UI style, batching rules |
| `docs/MERGE_DESIGN.md` | Shard sizing, merge strategies (ensemble / flat / tree / tree+heal), SFT→DPO per shard, canaries |
| `docs/ML_API.md`, `docs/LEDGER_API.md` | Python APIs of the ML core and of the ledger/certificate |
| `lethe/` | ML core (`data`, `model`, `train`, `audit`, `unlearn`, `attacks`, `merge` [WIP]) and proof core (`ledger`, `certificate`) |
| `experiments/` | Spike scripts + `RESULTS.md` (all measured numbers) + raw outputs in `out/` |
| `scripts/verify_certificate.py` | Standalone certificate verifier CLI |
| `tests/` | Ledger + certificate tests (66, passing) |
| `showcase/` | **The demo**: landing page (LIVE with the server, illustration on Vercel) + console in `showcase/app/` |
| `lethe/server.py`, `lethe/revoke.py` | Platform server and the real removal engine (CLI) |
| `web/` | Old dark prototype (superseded by `showcase/`) |
| `design/` | Design specs (`SHOWCASE_BRIEF.md`, `lethe-canvas/SPEC.md`) and the canvas mock sources |

## Quickstart
**See the design (no training, any machine):**
```bash
cd showcase && python3 -m http.server 8000
# landing page (illustration): http://localhost:8000/      console (replays a real recorded run): http://localhost:8000/app/
```
Or open https://lethe-demo-six.vercel.app. (`web/` is the OLD dark prototype — ignore it.)

**Run the real thing (live model, real removal):** needs [uv](https://docs.astral.sh/uv/) + Python 3.12; best on an
Apple-Silicon Mac (MPS). Model files are not in git, so the first run trains them (~5–8 min):
```bash
uv sync
bash scripts/setup_demo.sh          # one-time: trains base + 4 shards, snapshots the pristine state (see script)
uv run uvicorn lethe.server:app --port 8000
# open http://127.0.0.1:8000/  → the pill says LIVE; Remove runs the real removal (~30 s)
```
Tests: `uv run pytest -q`. Phase-1 research spike: `LETHE_CORPUS=phase1 uv run python experiments/run_spike.py`.

## Key results so far (phase 1, from `experiments/RESULTS.md`)
3.2M-parameter char-level GPT, synthetic tenants. Canary exposure uses the exact rank over 100,000 five-digit
secrets (max 16.61 bits; never-trained controls ≈ 2.3 bits).

| Model | Acme's secret |
|---|---|
| Pooled (status quo) | 16.61 bits: rank 1 of 100,000 |
| **Lethe exact removal** (retrain 1 shard, 20–28 s vs ~138 s for all shards) | **1.12 bits**: other customers untouched; 4-bit and relearning attacks don't bring it back |
| Approximate unlearning (gradient ascent) on pooled | 2.31 bits: passes the audit, but the model is damaged on that prompt |
| … + 4-bit quantization | 2.74 bits: did **not** recover at our scale (honest negative result vs Zhang et al. 2025) |
| … + 30 fine-tuning steps on *other* customers' data | **16.11 bits**: the secret is back at rank 1 (P = 63% given a 5-digit output) |

Limitations: a tiny model and templated data, so the ordering is the claim rather than the absolute numbers.
Attestation is simulated, and the pipeline must be adopted before training (no retrofit).
