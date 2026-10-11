"""Lethe platform server: a thin FastAPI wrapper around the real removal CLI (`python -m lethe.revoke`).

    uv run uvicorn lethe.server:app --port 8000          # console at http://localhost:8000/app/

Contract: docs/PLATFORM_API.md. How to run: docs/PLATFORM.md.
It runs the CLI as a subprocess (one at a time, server-wide lock), parses its stdout into steps / retrain progress,
and reads the files the CLI writes (summary.json, cert-*.json, verify_*.txt, ledger_export.json, *_record_hashes.json)
from runs/<job_id>/. Serving state comes from the CLI's own state files (artifacts/models/serving.json + registry.json),
cached by mtime. Nothing here trains, probes or signs: every number shown comes from the CLI.
While a CLI runs, <runs>/.lock holds its pid, so a restarted (or second) server still answers busy/409 until that
process exits; a .lock whose pid is dead means a removal was interrupted (reported as `interrupted_job`; Reset).

Env:
  LETHE_REVOKE_CMD  command that replaces `python -u -m lethe.revoke` (tests: "python -u tests/fake_revoke.py")
  LETHE_ARTIFACTS   where the CLI keeps its state (default artifacts/, or runs/_fake_artifacts when LETHE_REVOKE_CMD
                    is set; the server passes it to an overridden command, only the fake honours it)
  LETHE_RUNS_DIR    per-job output dirs (default runs/, or runs/_fake_runs when LETHE_REVOKE_CMD is set)
  LETHE_JOB_TIMEOUT seconds before a removal is killed and marked error (default 600)
  SENTRY_DSN        optional: report server errors to Sentry
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
SHOWCASE = ROOT / "showcase"
COMPANIES = ROOT / "data" / "companies.json"
MODEL_DESC = "char-level GPT, 3.2M params (MPS)"
STEP_NAMES = ["PROBE", "BLAST RADIUS", "RETRAIN", "REDEPLOY", "PROBE AGAIN", "RECEIPTS + DELETION CERTIFICATE",
              "CUSTOMER VERIFIES"]

ANSI = re.compile(r"\033\[[0-9;]*[A-Za-z]")
HEAD = re.compile(r"^\[(\d+)\] (.*?)(?:\s+(\d+(?:\.\d+)?)s)?\s*$")
PROG = re.compile(r"^\s*shard (\d+) \[[#.]+\]\s+(\d+)%\s+(?:(SFT|DPO) step\s+(\d+)/(\d+)|done (\d+) steps)")
KEPT = re.compile(r"^\s*shard (\d+): (\d+) records kept, (\d+) excluded")
NRETRAIN = re.compile(r"^\s*(\d+) of (\d+) shards retrain")
CHECK = re.compile(r"^\s*\[(PASS|FAIL)\]\s+(\S+)\s*(.*?)\s*$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------------------------------
# jobs: one CLI run each, parsed from its stdout
# ------------------------------------------------------------------------------------------
class Job:
    def __init__(self, job_id: str, slugs: list[str], out_dir: Path):
        self.id, self.slugs, self.out_dir = job_id, slugs, out_dir
        self.status = "running"
        self.t0 = time.time()
        self.t_end: float | None = None
        self.started_at = _now()
        self.steps = [dict(n=i + 1, name=n, status="pending", seconds=None) for i, n in enumerate(STEP_NAMES)]
        self._t_step: dict[int, float] = {}
        self.progress: dict | None = None
        self.n_retrain: int | None = None
        self.log: list[str] = []
        self.summary: dict | None = None
        self.certificates: list[str] = []
        self.returncode: int | None = None
        self.tiger: dict | None = None  # receipts mirrored to Tiger Data (optional)
        self.error: str | None = None
        self.mu = threading.Lock()
        self._start_step(1)  # the probe runs before its header is printed

    # step bookkeeping. The CLI prints "[n] NAME ... 16.1s" AFTER the work for timed steps (1, 4, 5) and
    # "[n] NAME" BEFORE the work for the others (2, 3, 6, 7). Header with seconds = step done; without = step starts.
    def _step(self, n: int) -> dict:
        while len(self.steps) < n:
            self.steps.append(dict(n=len(self.steps) + 1, name="", status="pending", seconds=None))
        return self.steps[n - 1]

    def _start_step(self, n: int):
        s = self._step(n)
        if s["status"] == "pending":
            s["status"] = "running"
            self._t_step[n] = time.time()

    def _finish_step(self, n: int, secs: float | None = None):
        s = self._step(n)
        if s["status"] == "done":
            return
        if secs is None:
            secs = round(time.time() - self._t_step.get(n, time.time()), 1)
        s["status"], s["seconds"] = "done", secs

    def feed(self, raw: str):
        line = ANSI.sub("", raw.rstrip("\n")).split("\r")[-1].rstrip()
        with self.mu:
            self.log.append(line)
            m = HEAD.match(line)
            if m:
                n, title, secs = int(m.group(1)), m.group(2), m.group(3)
                self._step(n)["name"] = title.split("  ")[0].strip() or self._step(n)["name"]
                for k in range(1, n):
                    self._finish_step(k)
                if secs is not None:
                    self._finish_step(n, float(secs))
                    if n < len(self.steps):
                        self._start_step(n + 1)  # e.g. after [4] REDEPLOY the after-probe runs silently
                else:
                    self._start_step(n)
                return
            m = PROG.match(line)
            if m:
                k, pct = int(m.group(1)), int(m.group(2))
                if m.group(6):  # "done 211 steps"
                    self.progress = dict(shard=k, pct=100, phase="DPO", step=int(m.group(6)),
                                         total=int(m.group(6)), n_shards=self.n_retrain, done=True)
                else:
                    self.progress = dict(shard=k, pct=pct, phase=m.group(3), step=int(m.group(4)),
                                         total=int(m.group(5)), n_shards=self.n_retrain, done=False)
                return
            m = KEPT.match(line)
            if m:
                self.progress = dict(shard=int(m.group(1)), pct=0, phase="SFT", step=0, total=None,
                                     n_shards=self.n_retrain, done=False, kept=int(m.group(2)),
                                     excluded=int(m.group(3)))
                return
            m = NRETRAIN.match(line)
            if m:
                self.n_retrain = int(m.group(1))

    def finish(self, rc: int | None, error: str | None = None):
        summary = None
        sp = self.out_dir / "summary.json"
        if rc == 0 and sp.exists():
            try:
                summary = json.loads(sp.read_text())
            except (OSError, ValueError) as e:
                error = f"could not read summary.json: {e}"
        with self.mu:
            self.returncode, self.t_end = rc, time.time()
            if summary is not None and error is None:
                self.summary = summary
                self.certificates = [c["certificate_id"] for c in summary.get("certificates", [])]
                for s in self.steps:
                    self._finish_step(s["n"])
                self.status = "done"
            else:
                self.status = "error"
                tail = "\n".join(x for x in self.log[-12:] if x.strip())
                self.error = error or (f"CLI exited with code {rc}" + (f":\n{tail}" if tail else ""))
                for s in self.steps:
                    if s["status"] == "running":
                        s["status"] = "error"
        if self.status == "done":
            threading.Thread(target=self._publish_tiger, daemon=True).start()

    def _publish_tiger(self):
        """Mirror this run's signed receipts to Tiger Data (append-only hypertable), if TIGER_DATABASE_URL is set."""
        try:
            from . import receipts_publish as rp
            rp._load_dotenv()
            if not rp.configured():
                with self.mu:
                    self.tiger = {"status": "off"}
                return
            with self.mu:
                self.tiger = {"status": "publishing"}
            res = rp.publish(self.out_dir / "ledger_export.json", run_id=self.id)
            with self.mu:
                self.tiger = {"status": "published", "inserted": res["inserted"], "table_rows": res["table_rows"],
                              "table": res["table"], "hypertable": bool(res.get("hypertable"))}
        except Exception as e:  # noqa: BLE001  (Tiger is optional: never fail the removal over it)
            with self.mu:
                self.tiger = {"status": "error", "error": str(e).splitlines()[0][:200] if str(e) else type(e).__name__}

    def to_dict(self, since: int = 0) -> dict:
        with self.mu:
            since = max(0, min(since, len(self.log)))
            return dict(id=self.id, slugs=self.slugs, status=self.status, started_at=self.started_at,
                        elapsed_s=round((self.t_end or time.time()) - self.t0, 1),
                        steps=[dict(s) for s in self.steps], progress=dict(self.progress) if self.progress else None,
                        log=self.log[since:], log_start=since, log_offset=len(self.log),
                        summary=self.summary, certificates=list(self.certificates),
                        returncode=self.returncode, error=self.error,
                        tiger=dict(self.tiger) if self.tiger else None)

    @classmethod
    def unfinished(cls, d: Path, slugs: list[str], lock: dict | None) -> "Job":
        """A job dir without summary.json from an earlier server process: still running elsewhere (its pid in .lock
        is alive) or interrupted / crashed (log = the CLI's transcript.partial.txt when it got to write one)."""
        j = cls(d.name, slugs, d)
        started = float((lock or {}).get("started") or d.stat().st_mtime)
        j.t0 = started
        j.started_at = datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds")
        if lock and lock.get("alive"):
            j.log.append(f"server: this removal was started by an earlier server process (CLI pid {lock.get('pid')}); "
                         "its output is not visible here. Waiting for it to finish.")
            return j
        tp = d / "transcript.partial.txt"
        for ln in (tp.read_text(errors="replace").splitlines() if tp.exists() else []):
            j.feed(ln)
        why = ("interrupted: the server stopped while this removal ran, and the CLI did not finish (no summary.json)."
               if lock else "the CLI failed (transcript.partial.txt, no summary.json).")
        tail = "\n".join(x for x in j.log[-12:] if x.strip())
        j.finish(None, f"{why} The model state may be half-updated: Reset before the next removal."
                 + (f"\n{tail}" if tail else ""))
        if tp.exists():
            j.t_end = max(j.t0, tp.stat().st_mtime)
        return j

    @classmethod
    def from_dir(cls, d: Path) -> "Job | None":
        """A finished job from an earlier server process (runs/<id>/ with transcript.txt + summary.json)."""
        sp, tp = d / "summary.json", d / "transcript.txt"
        if not sp.exists():
            return None
        try:
            summary = json.loads(sp.read_text())
        except (OSError, ValueError):
            return None
        slugs = [s.split(":", 1)[-1] for s in summary.get("removed", [])]
        j = cls(d.name, slugs, d)
        for ln in (tp.read_text().splitlines() if tp.exists() else []):
            j.feed(ln)
        j.finish(0)
        j.t0 = j.t_end - float(summary.get("timings_s", {}).get("total") or 0)
        j.started_at = summary.get("generated_at", j.started_at)
        return j


# ------------------------------------------------------------------------------------------
# the platform: config, state cache, runner
# ------------------------------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Platform:
    def __init__(self):
        self.custom_cmd = os.environ.get("LETHE_REVOKE_CMD")
        # an overridden command is a fake / test CLI: keep its state and job outputs away from the real ones, and
        # pass LETHE_ARTIFACTS to it so the server reads exactly the dir the fake writes
        fake_root = ROOT / "runs"
        self.art = Path(os.environ.get("LETHE_ARTIFACTS")
                        or (fake_root / "_fake_artifacts" if self.custom_cmd else ROOT / "artifacts"))
        self.runs = Path(os.environ.get("LETHE_RUNS_DIR")
                         or (fake_root / "_fake_runs" if self.custom_cmd else ROOT / "runs"))
        self.lockfile = self.runs / ".lock"
        self.cmd = shlex.split(self.custom_cmd) if self.custom_cmd else [sys.executable, "-u", "-m", "lethe.revoke"]
        self.timeout = float(os.environ.get("LETHE_JOB_TIMEOUT", "600"))
        self.lock = threading.Lock()  # held while ANY CLI process runs (removal or reset)
        self.jobs: dict[str, Job] = {}
        self.current: str | None = None
        self._state_key = None
        self._state_val: dict | None = None
        self._cust: dict | None = None
        self._certs: dict[Path, tuple[int, dict]] = {}

    # --- CLI state files (artifacts/models/serving.json + registry.json), cached by mtime ---
    def _serving(self) -> dict:
        sj, rj = self.art / "models" / "serving.json", self.art / "models" / "registry.json"
        key = tuple(p.stat().st_mtime_ns if p.exists() else None for p in (sj, rj))
        if key != self._state_key or self._state_val is None:
            try:
                st = json.loads(sj.read_text()) if sj.exists() else None
                reg = json.loads(rj.read_text()) if rj.exists() else {}
            except (OSError, ValueError):  # mid-write by the CLI: keep the last good value
                if self._state_val is not None:
                    return self._state_val
                st, reg = None, {}
            self._state_val = self._derive(st, reg)
            self._state_key = key
        return self._state_val

    def _derive(self, st: dict | None, reg: dict) -> dict:
        from .ledger import serving_hash
        shards_ids = (st or {}).get("shards") or {str(k): f"shard-{k}" for k in (1, 2, 3, 4)}
        shards = {}
        for k, mid in sorted(shards_ids.items(), key=lambda kv: int(kv[0])):
            h = (reg.get(mid) or {}).get("weights_sha256") if mid else None
            v = re.search(r"-v(\d+)$", mid or "")
            shards[int(k)] = dict(id=int(k), model_id=mid, sha256=h,
                                  version=(f"v{v.group(1)}" if v else "v1") if mid else None)
        live = [s["sha256"] for s in shards.values() if s["sha256"]]
        # the ledger / certificate serving hash (sha256 of canonical JSON of the sorted shard hashes). NOT
        # serving.json's own "serving_sha256", which is a different digest of the same set.
        sh = serving_hash(live) if st and live and len(live) == sum(1 for s in shards.values() if s["model_id"]) \
            else None
        return dict(ready=st is not None, shards=shards, revoked=list((st or {}).get("revoked") or []),
                    strategy=(st or {}).get("strategy"), version=(st or {}).get("version"), serving_sha256=sh,
                    fake=bool((st or {}).get("fake")))

    def customers_static(self) -> dict:
        if self._cust is None:
            from .data import build_corpus
            corpus = build_corpus()
            by_src = corpus.by_source()
            kn = {c["source_id"]: c for c in corpus.canaries
                  if c.get("kind", "knowledge") == "knowledge" and c["inserted"]}
            comp = json.loads(COMPANIES.read_text())["companies"]
            out = {}
            for c in comp:
                sid = f"tenant:{c['slug']}"
                can = kn.get(sid) or {}
                out[c["slug"]] = dict(slug=c["slug"], name=c["name"], shard=c["shard"],
                                      records=len(by_src.get(sid, [])), source_id=sid,
                                      prompt=(can.get("prompt") or can.get("prefix") or "").strip(),
                                      secret=can.get("secret") or c.get("knowledge_secret"))
            self._cust = out
        return self._cust

    def state(self) -> dict:
        sv = self._serving()
        cust = self.customers_static()
        revoked = set(sv["revoked"])
        customers = [dict(c, status="removed" if c["source_id"] in revoked else "active") for c in cust.values()]
        shards = []
        for k, s in sv["shards"].items():
            shards.append(dict(s, customers=[c["slug"] for c in cust.values() if c["shard"] == k]))
        busy, cur = self.busy()
        return dict(customers=customers, shards=shards,
                    serving=dict(sha256=sv["serving_sha256"], strategy=sv["strategy"], version=sv["version"],
                                 revoked=sorted(revoked), ready=sv["ready"]),
                    busy=busy, current_job=cur, interrupted_job=self.interrupted_job())

    # --- busy: our own lock, or a CLI that another (or an earlier) server process started (<runs>/.lock) ---
    def read_lockfile(self) -> dict | None:
        try:
            info = json.loads(self.lockfile.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict):
            return None
        pid = info.get("pid")
        # the deadline guards against pid reuse long after a crash
        info["alive"] = (isinstance(pid, int) and pid > 0 and _pid_alive(pid)
                         and time.time() < float(info.get("deadline") or 0) + 120)
        return info

    def foreign(self) -> dict | None:
        """A live CLI that this process did not start (only meaningful while self.lock is free or just taken)."""
        info = self.read_lockfile()
        return info if info and info["alive"] else None

    def busy(self) -> tuple[bool, str | None]:
        if self.lock.locked():
            return True, self.current
        f = self.foreign()
        return (True, f.get("job_id")) if f else (False, None)

    def interrupted_job(self) -> str | None:
        """A stale .lock (its CLI died without the server clearing it): the last removal never finished."""
        if self.lock.locked():
            return None
        info = self.read_lockfile()
        return info.get("job_id") if info and not info["alive"] else None

    def acquire(self):
        """Take the server lock or raise 409. Also 409 while a CLI from an earlier server process still runs."""
        if not self.lock.acquire(blocking=False):
            raise HTTPException(409, f"busy: job {self.current} is running")
        f = self.foreign()
        if f:
            self.lock.release()
            raise HTTPException(409, f"busy: job {f.get('job_id')} (CLI pid {f.get('pid')}) was started by an earlier "
                                     "server process and is still running")

    def _write_lockfile(self, job: Job, pid: int):
        try:
            self.runs.mkdir(parents=True, exist_ok=True)
            self.lockfile.write_text(json.dumps(dict(pid=pid, job_id=job.id, slugs=job.slugs, started=job.t0,
                                                     deadline=time.time() + self.timeout, server_pid=os.getpid())))
        except OSError:
            pass

    def clear_lockfile(self):
        try:
            self.lockfile.unlink(missing_ok=True)
        except OSError:
            pass

    # --- running the CLI ---
    def _env(self) -> dict:
        env = dict(os.environ)
        env.update(PYTHONUNBUFFERED="1", NO_COLOR="1")
        if self.custom_cmd:  # the fake keeps its state where the server reads it (the real CLI ignores this)
            env["LETHE_ARTIFACTS"] = str(self.art)
        return env

    def start(self, slugs: list[str]) -> Job:
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{'-'.join(slugs)}-{secrets.token_hex(2)}"
        out_dir = self.runs / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        job = Job(job_id, slugs, out_dir)
        self.jobs[job_id] = job
        self.current = job_id
        cmd = self.cmd + [a for s in slugs for a in ("--remove", s)] + ["--out", str(out_dir)]
        threading.Thread(target=self._run, args=(job, cmd), daemon=True, name=f"job-{job_id}").start()
        return job

    def _run(self, job: Job, cmd: list[str]):
        rc: int | None = None
        err: str | None = None
        proc = None
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    cwd=ROOT, env=self._env(), text=True, bufsize=1, errors="replace")
            self._write_lockfile(job, proc.pid)
            timer = threading.Timer(self.timeout, proc.kill)
            timer.start()
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    job.feed(line)
                rc = proc.wait()
            finally:
                timer.cancel()
            if rc != 0 and time.time() - job.t0 >= self.timeout:
                err = f"killed after {self.timeout:.0f}s timeout"
        except Exception as e:  # noqa: BLE001  (could not start the CLI, or reading its output failed)
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
            job.feed(f"server: could not run {cmd!r}: {e}")
            rc, err = None, f"could not run the CLI: {e}"
        finally:
            # free the server BEFORE the job reads as done/error: a poll that sees "done" must never see busy=true
            self.clear_lockfile()
            self.current = None
            self._state_key = None  # re-read serving state
            self.lock.release()
            try:
                job.finish(rc, err)
            except Exception as e:  # noqa: BLE001  (malformed summary.json etc.: never leave the job "running")
                job.finish(None, f"server: could not read the CLI output: {e}")

    def run_sync(self, args: list[str], timeout: float = 120) -> tuple[int, str]:
        p = subprocess.run(self.cmd + args, capture_output=True, text=True, cwd=ROOT, env=self._env(),
                           timeout=timeout, stdin=subprocess.DEVNULL)
        self._state_key = None
        return p.returncode, ANSI.sub("", p.stdout + p.stderr)

    def get_job(self, job_id: str) -> Job | None:
        if job_id in self.jobs:
            return self.jobs[job_id]
        if "/" in job_id or job_id.startswith((".", "_")):
            return None
        d = self.runs / job_id
        if not d.is_dir():
            return None
        j = Job.from_dir(d)
        if j:
            self.jobs[job_id] = j
            return j
        # no summary.json: a removal started by an earlier server process (still running, or interrupted), or a CLI
        # crash that left transcript.partial.txt. Not cached: it may still finish.
        lock = self.read_lockfile()
        lock = lock if lock and lock.get("job_id") == job_id and not self.lock.locked() else None
        if lock is None and not (d / "transcript.partial.txt").exists():
            return None
        slugs = list((lock or {}).get("slugs") or job_id.split("-")[2:-1])
        return Job.unfinished(d, slugs, lock)

    # --- certificates, from every runs/<job_id>/cert-*.json ---
    def certificates(self) -> list[dict]:
        out: dict[str, dict] = {}
        if not self.runs.exists():
            return []
        paths = sorted(self.runs.glob("*/cert-*.json"), key=lambda p: p.stat().st_mtime)
        for p in paths:  # oldest first, so the newest copy of a duplicate id wins
            meta = self._cert_meta(p)
            if meta:
                out[meta["id"]] = meta
        return sorted(out.values(), key=lambda m: (m["issued_at"] or "", m["mtime"]), reverse=True)

    def _cert_meta(self, p: Path) -> dict | None:
        mt = p.stat().st_mtime_ns
        hit = self._certs.get(p)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            c = json.loads(p.read_text())
            src = c["revoked_source"]
            meta = dict(id=c["certificate_id"], slug=src["source_id"].split(":", 1)[-1], job_id=p.parent.name,
                        issued_at=c.get("issued_at"), serving_sha256=c["serving_model"]["serving_hash"],
                        n_records=src["n_records"], n_proofs=sum(len(x["proofs"]) for x in c["non_membership"]),
                        n_manifests=len(c["non_membership"]), source_id=src["source_id"], path=str(p),
                        mtime=p.stat().st_mtime)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        self._certs[p] = (mt, meta)
        return meta

    def find_cert(self, cert_id: str) -> dict:
        for m in self.certificates():
            if m["id"] == cert_id:
                return m
        raise HTTPException(404, f"certificate {cert_id!r} not found")

    def verify(self, cert_id: str, tamper: bool) -> dict:
        from .revoke import run_verifier, tamper_one_byte
        m = self.find_cert(cert_id)
        cpath, d = Path(m["path"]), Path(m["path"]).parent
        records, ledger = d / f"{m['slug']}_record_hashes.json", d / "ledger_export.json"
        for f in (records, ledger):
            if not f.exists():
                raise HTTPException(409, f"{f.name} missing next to the certificate")
        what = None
        with tempfile.TemporaryDirectory(prefix="lethe-verify-") as tmp:
            target = cpath
            if tamper:
                target = Path(tmp) / f"tampered-{cpath.name}"
                what = tamper_one_byte(cpath, target)
            rc, txt = run_verifier(target, records, ledger)
        checks = [dict(name=mm.group(2), ok=mm.group(1) == "PASS", detail=mm.group(3))
                  for mm in map(CHECK.match, txt.splitlines()) if mm]
        result = next((ln for ln in txt.splitlines() if ln.startswith("RESULT")), "RESULT: ?")
        return dict(valid=rc == 0, checks=checks, output=txt, result=result, tampered=tamper, tamper=what,
                    certificate_id=cert_id, job_id=m["job_id"])


# ------------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------------
def _receipts_store() -> dict:
    try:
        from . import receipts_publish as rp
        rp._load_dotenv()
        return {"local": "sqlite ledger", "mirror": "Tiger Data (Postgres + TimescaleDB)" if rp.configured() else None}
    except Exception:  # noqa: BLE001
        return {"local": "sqlite ledger", "mirror": None}


def create_app() -> FastAPI:
    if os.environ.get("SENTRY_DSN"):
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], traces_sample_rate=0.0)
        except Exception:  # noqa: BLE001
            pass
    P = Platform()
    app = FastAPI(title="Lethe platform", version="1")
    app.state.platform = P

    @app.get("/api/health")
    def health():
        sv = P._serving()
        busy, cur = P.busy()
        return dict(mode="live", busy=busy, model=MODEL_DESC, serving_sha256=sv["serving_sha256"],
                    strategy=sv["strategy"], fake=bool(P.custom_cmd) or sv["fake"], current_job=cur,
                    interrupted_job=P.interrupted_job(), receipts_store=_receipts_store())

    @app.post("/api/ask")
    def ask_model(body: dict = Body(...)):
        """Ask the CURRENT serving model a customer's escalation code (CPU; ~0.2 s). Body: {"slug": "acme"}."""
        body = body if isinstance(body, dict) else {}
        customers = P.state()["customers"]
        busy, _ = P.busy()
        if isinstance(body.get("question"), str) and body["question"].strip():
            from .ask import ask_free
            r = ask_free(body["question"], customers)
            r.update(serving_sha256=P._serving()["serving_sha256"],
                     note="model is being retrained right now; this answer is from the model currently served" if busy else None)
            return r
        slug = body.get("slug")
        cust = {c["slug"]: c for c in customers}.get(slug)
        if not cust:
            raise HTTPException(400, f"unknown customer {slug!r} (send {{'question': '...'}} or {{'slug': '...'}})")
        from .ask import ask
        r = ask(cust["name"], cust.get("secret"))
        r.update(slug=slug, company=cust["name"], status=cust["status"], serving_sha256=P._serving()["serving_sha256"],
                 note="model is being retrained right now; this answer is from the model currently served" if busy else None)
        return r

    @app.get("/api/state")
    def state():
        return P.state()

    @app.post("/api/jobs")
    def create_job(body: dict = Body(...)):
        slugs = body.get("slugs") if isinstance(body, dict) else None
        if not isinstance(slugs, list) or not slugs or not all(isinstance(s, str) for s in slugs):
            raise HTTPException(400, 'body must be {"slugs": ["acme", ...]}')
        slugs = list(dict.fromkeys(s.split(":", 1)[-1].strip().lower() for s in slugs))
        cust = P.customers_static()
        bad = [s for s in slugs if s not in cust]
        if bad:
            raise HTTPException(400, f"unknown customer(s) {bad}; choose from {sorted(cust)}")
        P.acquire()
        try:
            revoked = set(P._serving()["revoked"])
            gone = [s for s in slugs if f"tenant:{s}" in revoked]
            if gone:
                raise HTTPException(400, f"already removed: {gone}. Reset first.")
            job = P.start(slugs)
        except BaseException:
            P.lock.release()
            raise
        return {"job_id": job.id}

    @app.get("/api/jobs")
    def list_jobs():
        ids = set(P.jobs)
        if P.runs.exists():
            ids |= {d.name for d in P.runs.iterdir() if (d / "summary.json").exists()}
        jobs = [j for j in (P.get_job(i) for i in ids) if j]
        return [dict(id=j.id, slugs=j.slugs, status=j.status, started_at=j.started_at, certificates=j.certificates)
                for j in sorted(jobs, key=lambda j: j.started_at, reverse=True)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, since: int = 0):
        j = P.get_job(job_id)
        if not j:
            raise HTTPException(404, f"job {job_id!r} not found")
        return j.to_dict(since)

    @app.get("/api/certificates")
    def certificates():
        return [{k: v for k, v in m.items() if k not in ("path", "mtime")} for m in P.certificates()]

    @app.get("/api/certificates/{cert_id}")
    def certificate(cert_id: str):
        return FileResponse(P.find_cert(cert_id)["path"], media_type="application/json")

    @app.post("/api/certificates/{cert_id}/verify")
    def verify(cert_id: str, body: dict | None = Body(default=None)):
        tamper = bool((body or {}).get("tamper", False))
        return P.verify(cert_id, tamper)

    @app.post("/api/reset")
    def reset():
        P.acquire()
        try:
            rc, out = P.run_sync(["--reset"])
            if rc == 0:
                P.clear_lockfile()  # a stale .lock from an interrupted removal: the reset fixed the state
        except subprocess.TimeoutExpired:
            raise HTTPException(500, "reset timed out")
        finally:
            P.lock.release()
        if rc != 0:
            raise HTTPException(500, f"reset failed (exit {rc}):\n{out[-2000:]}")
        return {"ok": True, "output": out}

    @app.get("/api/status")
    def status():  # the CLI's own --status text (spawns the CLI: use sparingly)
        if P.busy()[0]:
            raise HTTPException(409, "busy")
        rc, out = P.run_sync(["--status"], timeout=60)
        return {"ok": rc == 0, "output": out}

    if SHOWCASE.is_dir():
        app.mount("/", StaticFiles(directory=SHOWCASE, html=True), name="showcase")
    return app


app = create_app()
