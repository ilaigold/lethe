# Patients, Tiger Data / Snowflake, Gemma on the UNO Q, Sentry: setup and run

Two removal paths now share one ledger format and one verifier:

| Path | Unit removed | Model | Where it runs | Command |
|---|---|---|---|---|
| LLM (existing) | a business customer | 4 shard char-level GPTs, served as an ensemble | Ilai's Mac (MPS) | `python -m lethe.revoke --remove acme` |
| Tabular (new) | one patient | 9 shard ExtraTrees (hospital x department) | any laptop, or the UNO Q | `python -m lethe.tabular forget --patient 8841` |

The tabular path, measured on a laptop CPU: seed 5 s, removal 4.5 s end to end, shard retrain 0.1 s. Patient 8841:
P(own outcome) 1.00 -> 0.32, exposure 7.65 -> 1.61 bits (never-seen controls 1.42). Certificate 21/21 PASS,
tampered copy INVALID, and re-running training on the surviving rows gives the same model digest.

---

## 0. Install (2 min, every laptop)

```bash
git pull
uv sync --extra tabular                 # adds scikit-learn
uv run pytest -q                        # 90 tests
uv run python -m lethe.tabular seed     # local CSV, no accounts needed
uv run python -m lethe.tabular forget --patient 8841
uv run python -m lethe.tabular reset    # before every rehearsal
```

## 1. Data in Tiger Data (recommended: 10 min)

1. console.cloud.timescale.com -> create a free service -> **Connect** -> copy the connection string
   (`postgres://tsdbadmin:...@....tsdb.cloud.timescale.com:3xxxx/tsdb?sslmode=require`).
2. In `.env` (or `export`): `LETHE_PATIENTS_URL=<that string>`
3. Seed into Tiger and train from what comes back out of it:
   ```bash
   set -a; source .env; set +a
   uv run python -m lethe.tabular seed --to tiger
   uv run python -m lethe.tabular forget --patient 8841
   ```
4. Show it in the Tiger SQL editor:
   ```sql
   SELECT count(*) FROM lethe_patients WHERE patient_id = 8841;          -- 1 before, 0 after
   SELECT type, count(*) FROM lethe_receipts GROUP BY 1;                 -- signed receipts (hypertable on ts)
   SELECT ts, type, payload->>'source_id' FROM lethe_receipts ORDER BY ts DESC LIMIT 10;
   ```
   `reset` writes the rows back. The receipts table keeps every rehearsal (it is append-only, like the ledger).

**Real data instead of synthetic (optional, 5 min):** download "Diabetes 130-US Hospitals 1999-2008" from the UCI
repository (CC BY 4.0), unzip, then `uv run python -m lethe.tabular seed --data uci:path/to/diabetic_data.csv --to tiger`.
It keeps 800 real patients (a third of them with a <30-day readmission) plus the 8 canary patients. The hospital split
is synthetic (the dataset has no hospital id): say so.

## 1b. Snowflake instead (if you already have an account from earlier today)

```bash
uv sync --extra tabular --extra snowflake
export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=... SNOWFLAKE_PASSWORD=... SNOWFLAKE_WAREHOUSE=... \
       SNOWFLAKE_DATABASE=... SNOWFLAKE_SCHEMA=...     # SNOWFLAKE_ROLE optional
uv run python -m lethe.tabular seed --to snowflake
uv run python -m lethe.tabular forget --patient 8841
```
Same tables (`LETHE_PATIENTS`, `LETHE_RECEIPTS` with a VARIANT payload). The Snowflake path was not tested against a
live account; the Tiger path was tested against Postgres.

## 2. Gemma on the Arduino UNO Q (20-30 min)

What runs where, honestly:
- **Gemma on the board = the operator assistant.** It turns "forget patient 8841" into a plan. It never executes,
  never sees rows, and a human types CONFIRM. Gemma 3 1B on this board generates about 5 tokens/s, so a reply takes
  about 5-15 s. **Retraining Gemma on the board is not feasible** in a live demo (a 2B model already took 5-12 s per
  LoRA step on the M1 Pro; the board is much slower and has 2-4 GB of RAM).
- **The patient model can retrain on the board.** The whole tabular pipeline is CPU-only Python, so the UNO Q can be
  the hospital's on-prem box: it holds the patient models, retrains a shard and signs the certificate.

Steps:
1. App Lab -> Examples/Inspirations -> duplicate **Edge AI Assistant** -> Run. In its AI models tab download
   **Gemma 3 1B** (needs Wi-Fi once). If it says "insufficient disk space", move Docker's data-root to
   `/home/arduino` (see the raspberry.tips guide linked below) or run `docker system prune -af`.
2. Find the llama.cpp port: `ssh arduino@<board>.local` then `docker ps` (look for the llama/llm container's mapped
   port). Test from your laptop:
   ```bash
   curl http://<board>.local:<port>/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
   ```
3. On the laptop: `export GEMMA_URL=http://<board>.local:<port>/v1/chat/completions GEMMA_MODEL=<model name it reports>`
4. `uv run python -m lethe.tabular ask "Forget patient 8841, she withdrew consent"` -> plan -> type `CONFIRM`.
   The first call is slow (prompt warm-up): do one before the demo. If Gemma is down or slow (>30 s) it falls back to
   the offline parser automatically, and the output says which one answered.
5. **(Optional) run the patient pipeline on the board itself:**
   ```bash
   ssh arduino@<board>.local
   git clone https://github.com/ilaigold/lethe && cd lethe
   python3 -m venv .venv && . .venv/bin/activate
   pip install numpy scikit-learn cryptography psycopg[binary]     # no torch needed
   export GEMMA_URL=http://localhost:<port>/v1/chat/completions LETHE_PATIENTS_URL=...
   python -m lethe.tabular seed --to tiger && python -m lethe.tabular ask "forget patient 8841"
   ```
6. **(Optional) LED:** in App Lab create an app from `edge/unoq/lethe-led/` (sketch + python). Run the CLI with
   `LETHE_RESULT_FILE=/home/arduino/ArduinoApps/lethe-led/last_result.json` (check the app's folder path). LED on =
   verified; blinking = failed.

## 3. Sentry, including AI monitoring (10 min)

1. sentry.io -> new Python project -> copy the DSN.
2. `export SENTRY_DSN=... LETHE_SENTRY_PROMPTS=1`
3. Run any of:
   ```bash
   uv run python -m lethe.tabular ask "forget patient 8841"      # AI agent trace: invoke_agent -> chat gemma -> tools
   uv run python -m lethe.tabular forget --patient 8841          # one transaction, a span per stage
   uv run python -m lethe.revoke_traced --remove acme --out artifacts/runs/live   # LLM path, traced
   ```
4. In Sentry: **Insights -> AI / Agents** shows the Gemma calls (model, tokens, latency, prompt and reply), and
   **Traces** shows the removal: probe, delete_from_source, retrain_shard, redeploy, certificate, verify, reexecute,
   with exposure bits before/after attached as span data. Crashes are captured.

`lethe.revoke_traced` wraps the existing CLI without changing it; `lethe.revoke` itself is untouched, so the
rehearsed stage command is unaffected.

## 4. Demo flow with both paths (2 min)

1. (0:00) "Two kinds of forgetting: a hospital patient from a risk model, and a business customer from an LLM."
2. (0:10) `ask "forget patient 8841"` -> Gemma on the UNO Q proposes a plan -> CONFIRM -> 5 s later: row gone from
   Tiger, one shard of nine retrained, P 1.00 -> 0.32 (controls 0.37), certificate VALID, tamper INVALID,
   re-execution digest MATCHES.
3. (0:45) Tiger SQL editor: 0 rows for 8841, receipts table. Sentry: the trace with the Gemma span.
4. (1:00) The existing LLM removal (`--remove acme`) or its recorded replay.

## Honest limits to say
- Synthetic patients (or UCI rows with a synthetic hospital split). Canaries are planted on purpose.
- Re-execution matches only with the same numpy / scikit-learn versions (recorded in each TRAINING_RUN).
- Gemma only proposes; it does not retrain and is not unlearned itself.
- Attestation is simulated (same as the LLM path). Receipts in Tiger/Snowflake are a read copy; the signed export is
  what the verifier checks.
