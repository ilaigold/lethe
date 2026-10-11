# Lethe platform: server + console

`lethe/server.py` is a thin FastAPI wrapper around the real removal CLI (`python -m lethe.revoke`). The contract is
`docs/PLATFORM_API.md`. Everything the console shows comes from the CLI's own output: its stdout, the files it writes
into `runs/<job_id>/`, and its state files in `artifacts/models/`. The server never trains, probes or signs anything.

## Run it live (on the demo laptop)

```bash
uv run python -m lethe.revoke --reset             # clean pre-removal state (0.2 s)
uv run uvicorn lethe.server:app --port 8000       # console: http://localhost:8000/app/
```

- One CLI process at a time. Remove and Reset return **409** while a job runs. `/api/health` and `/api/state` show
  `busy`.
- Each removal runs `python -u -m lethe.revoke --remove <slug>... --out runs/<job_id>/`, takes about 52 s for Acme
  and about 77 s for Acme + Cobalt. `runs/` is already in `.gitignore`.
- Don't run the CLI by hand in a terminal while the server is up. The server's lock only covers processes it starts
  itself.
- **Never restart the server while a removal runs.** The CLI would most likely die on its next print (broken pipe),
  possibly between retrain and redeploy. As a safety net, the server writes `runs/.lock` (the CLI's pid and job id)
  for every removal and deletes it when the CLI exits. A restarted server reads it: while that pid is alive, it
  reports `busy: true` with that `current_job` and returns 409 on Remove/Reset; the job reads as `running`. Once the
  pid is dead, the lock is stale: `/api/health` and `/api/state` return `interrupted_job: "<id>"`, and
  `GET /api/jobs/<id>` returns `status: "error"` (log = the CLI's `transcript.partial.txt`). **Then run Reset** (the
  console button or `--reset`), which also clears the stale lock.
- `SENTRY_DSN=...`, if set, reports server errors to Sentry.

## Run it against the fake CLI (no GPU, for console work and tests)

```bash
LETHE_REVOKE_CMD="$PWD/.venv/bin/python -u tests/fake_revoke.py" \
LETHE_ARTIFACTS=/tmp/lethe-fake LETHE_RUNS_DIR=/tmp/lethe-runs LETHE_FAKE_SCALE=0.15 \
  uv run uvicorn lethe.server:app --port 8001
curl -XPOST localhost:8001/api/reset              # creates the fake state
```

`LETHE_ARTIFACTS` and `LETHE_RUNS_DIR` are optional here. When `LETHE_REVOKE_CMD` is set, the server defaults them to
`runs/_fake_artifacts` and `runs/_fake_runs` (never the real `artifacts/` or `runs/`, so fake jobs and certificates
never show up in the live console) and passes its `LETHE_ARTIFACTS` to the fake, so both always use one state dir.

`tests/fake_revoke.py` replays a real golden run (`experiments/golden/acme` or `acme+cobalt`) line by line. Steps 1, 4
and 5 and the retrain bar wait for their recorded seconds × `LETHE_FAKE_SCALE`. The fake copies the golden
certificates, ledger export, record hashes and verifier outputs into `--out`, so verifying them gives the real
VALID and INVALID results. It keeps its state (`models/serving.json`, `models/registry.json`) in `LETHE_ARTIFACTS`,
which defaults to `runs/_fake_artifacts`, and it refuses to use the real `artifacts/`. It supports only `{acme}` and
`{acme, cobalt}`; any other set exits with code 3. `LETHE_FAKE_FAIL=1` makes it crash at step [4], which tests the
error path. When the command is overridden, `/api/health` returns `"fake": true`, and the console should show a badge
saying so.

Tests: `uv run pytest -q tests/test_server.py` (about 3 s).

## How the console connects

- The console lives in `showcase/app/` and is served at `/app/` by the same server. It uses same-origin `fetch("/api/...")`,
  so no CORS is needed. If `GET /api/health` fails (Vercel, or no server), the console switches to RECORDED mode.
- Removal flow: `POST /api/jobs {"slugs":["acme"]}` returns `job_id`. Poll `GET /api/jobs/{id}?since=N` every 300 to
  500 ms and pass the returned `log_offset` as the next `since`. `log` holds only the new lines, and `log_start` is
  the index of the first one. Stop polling when `status` is `done` or `error`.
- Steps: the first poll already shows `PROBE` as `running`, because the CLI prints the probe header only after the
  probe finishes. A header that carries seconds (`[1]`, `[4]`, `[5]`) marks that step `done` with the CLI's own
  timing and starts the next one. A header without seconds (`[2]`, `[3]`, `[6]`, `[7]`) starts its step. On failure,
  a step that was running gets the status `error`.
- `progress` during RETRAIN is `{"shard","pct","phase","step","total","n_shards","done"}`. The CLI's stdout is a pipe,
  so it prints only its milestone lines (0, 26, 50, 76, 82 and 100 %). Expect a jump about every 4 s and animate
  between them.
- When the job finishes, `summary` holds the CLI's `summary.json` (before and after ranks and bits, controls, shard
  hashes, ledger head, timings). `certificates` holds the certificate ids. Then call
  `POST /api/certificates/{id}/verify {"tamper":false|true}`, which returns `checks` (21 PASS/FAIL rows; SKIP rows
  appear only in `output`), `result`, `output` (the verifier's full text) and `tamper` (which byte was flipped).
- `GET /api/state` returns the customers (records, prompt and secret come from `lethe.data.build_corpus()`, the
  corpus the CLI trains and certifies, which has 190 records per customer). It also returns the shards (sha256 and
  version from `artifacts/models/registry.json` and `serving.json`) and `serving.sha256`. That last value is the
  **ledger / certificate** serving hash (`7534cb40…` before removal, `f64422db…` after removing Acme), the same one
  the transcript and the certificates show.
- `shards[].version` is always `"v<N>"`: `"v1"` for a pristine shard (CLI model id `shard-1`) and `"v2"` after a
  retrain (model id `shard-1-v2`, also returned as `shards[].model_id`). It has no `-` prefix, so match it with
  `/(?:^|-)(v\d+)$/`, not `/-v\d+$/`.

## Extras beyond the contract

- `GET /api/jobs` lists jobs, including finished jobs from earlier server runs (rebuilt from `runs/<id>/transcript.txt`
  and `summary.json`).
- `GET /api/status` returns the CLI's own `--status` text. It starts a CLI process, so don't poll it.
- Job objects also carry `started_at`, `returncode` and `error` (the last log lines when the CLI fails).
- Busy timing: the server frees its lock **before** a job's `status` becomes `done` or `error`. A state call made
  after a poll that saw `done` therefore never returns `busy: true` for that job. `busy` is also true while a CLI
  started by an earlier server process is still alive (see `runs/.lock` above).
- `/api/health` and `/api/state` carry `interrupted_job` (`null`, or the id of a removal that was cut off by a server
  restart; show "Reset before the next removal").
- Certificate ids are deduplicated across runs; the newest copy wins. With the fake, every Acme run produces
  `cert-acme-f64422db`. Certificates from before a reset stay listed and still verify, because each one is checked
  against the ledger export in its own run directory.

## Env

| Var | Default | Meaning |
|---|---|---|
| `LETHE_REVOKE_CMD` | `python -u -m lethe.revoke` | the command to run (tests use the fake) |
| `LETHE_ARTIFACTS` | `artifacts/` (`runs/_fake_artifacts` when `LETHE_REVOKE_CMD` is set) | where the server reads the CLI's state. It is passed to an overridden command; only the fake writes there, the real CLI always uses `artifacts/` |
| `LETHE_RUNS_DIR` | `runs/` (`runs/_fake_runs` when `LETHE_REVOKE_CMD` is set) | per-job output dirs, plus `.lock` while a CLI runs |
| `LETHE_JOB_TIMEOUT` | `600` | kill a removal after this many seconds and mark it `error` |
| `LETHE_FAKE_SCALE` / `LETHE_FAKE_DELAY` | `0.15` / `0.02` | fake replay speed / pause per line |
