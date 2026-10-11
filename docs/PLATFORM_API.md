# Platform API: console ↔ server (v1, for the demo)

The server (`lethe/server.py`, FastAPI) is a thin wrapper around the real removal CLI (`python -m lethe.revoke`).
It runs the CLI as a subprocess, streams its stdout, and reads the files the CLI writes (`summary.json`,
`cert-*.json`, `transcript.txt`, `verify_*.txt`, `ledger_export.json`, `*_record_hashes.json`). Start it with
`uv run uvicorn lethe.server:app --port 8000`. It serves `showcase/` at `/`, so the console is at `/app/`.

The console (`showcase/app/`) runs in two modes:
- **LIVE:** when `GET /api/health` answers, it calls the real model on this laptop.
- **RECORDED:** on Vercel, or when no server answers. It replays the real golden runs bundled in
  `showcase/app/recorded/` and is clearly labelled "Recorded: real output, replayed". Only Acme, and Acme + Cobalt,
  are available there.

## Endpoints
- `GET /api/health` → `{"mode":"live","busy":bool,"model":"char-level GPT, 3.2M params (MPS)","serving_sha256":str,"strategy":str}`
- `GET /api/state` → returns:
  ```json
  {"customers":[{"slug":"acme","name":"Acme Robotics","shard":1,"records":int,"status":"active|removed","prompt":"the escalation code for Acme Robotics is","secret":"41352"}],
   "shards":[{"id":1,"customers":["acme","brightline"],"sha256":str,"version":str}],
   "serving":{"sha256":str,"strategy":str},
   "busy":bool,"current_job":str|null}
  ```
  `records` and `secret` come from the built data and canaries. All of it is synthetic.
- `POST /api/jobs` body `{"slugs":["acme","cobalt"]}` → `{"job_id":str}`. Returns 409 if busy, 400 if a slug is unknown or already removed.
  It runs `python -m lethe.revoke --remove acme --remove cobalt --out runs/<job_id>/`.
- `GET /api/jobs/{id}?since=N` → returns:
  ```json
  {"id":str,"slugs":[...],"status":"running|done|error","elapsed_s":float,
   "steps":[{"n":1,"name":"PROBE","status":"pending|running|done","seconds":float|null}],
   "progress":{"shard":int,"pct":int,"phase":"SFT|DPO"}|null,
   "log":[str],"log_offset":int,
   "summary":<summary.json contents>|null,"certificates":[str]}
  ```
  The step names come from the CLI's `[n] NAME` headers: PROBE, BLAST RADIUS, RETRAIN, REDEPLOY, PROBE AGAIN,
  RECEIPTS + DELETION CERTIFICATE, CUSTOMER VERIFIES.
- `GET /api/certificates` → `[{"id":str,"slug":str,"job_id":str,"issued_at":str,"serving_sha256":str,"n_records":int,"n_proofs":int}]`
- `GET /api/certificates/{id}` → the certificate JSON.
- `POST /api/certificates/{id}/verify` body `{"tamper":false}` → `{"valid":bool,"checks":[{"name":str,"ok":bool,"detail":str}],"output":str}`.
  It runs `scripts/verify_certificate.py` with the customer's own record hashes and the ledger export, exactly as the
  CLI does. With `tamper: true` it first flips one byte in a temp copy.
- `POST /api/reset` → `{"ok":true}`. Runs `python -m lethe.revoke --reset`, which restores the pristine shards,
  serving model and receipts. Returns 409 if busy.

## Rules
- Only one CLI process at a time: a server-wide lock, and the console disables Remove and Reset while busy.
- Never fake results in LIVE mode. If the CLI fails, the job shows `error` with the last log lines.
- `LETHE_REVOKE_CMD` (env) overrides the command for tests, e.g. a fake script that prints a golden transcript, so
  tests never touch the GPU or `artifacts/`.
