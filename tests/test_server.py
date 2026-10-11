"""lethe/server.py against tests/fake_revoke.py (replays the golden acme run). No GPU, never touches artifacts/."""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

FAKE = f"{sys.executable} -u {ROOT / 'tests' / 'fake_revoke.py'}"


def _make(monkeypatch, tmp_path, scale="0", delay="0", **extra):
    monkeypatch.setenv("LETHE_REVOKE_CMD", FAKE)
    monkeypatch.setenv("LETHE_ARTIFACTS", str(tmp_path / "art"))
    monkeypatch.setenv("LETHE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("LETHE_FAKE_SCALE", scale)
    monkeypatch.setenv("LETHE_FAKE_DELAY", delay)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    from lethe.server import create_app
    c = TestClient(create_app())
    assert c.post("/api/reset").status_code == 200
    return c


@pytest.fixture
def client(monkeypatch, tmp_path):
    return _make(monkeypatch, tmp_path)


def _wait(c, job_id, timeout=30):
    t = time.time()
    while time.time() - t < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _run_acme(c):
    r = c.post("/api/jobs", json={"slugs": ["acme"]})
    assert r.status_code == 200, r.text
    return _wait(c, r.json()["job_id"])


def test_health(client):
    h = client.get("/api/health").json()
    assert h["mode"] == "live" and h["busy"] is False and h["fake"] is True
    assert h["strategy"] == "ensemble"
    assert h["serving_sha256"].startswith("7534cb4013d0")  # ledger serving hash of the 4 pristine shards


def test_state(client):
    s = client.get("/api/state").json()
    assert len(s["customers"]) == 8 and s["busy"] is False and s["current_job"] is None
    acme = next(c for c in s["customers"] if c["slug"] == "acme")
    assert acme == dict(acme, name="Acme Robotics", shard=1, records=190, status="active",
                        prompt="the escalation code for Acme Robotics is", secret="41352")
    sh1 = next(x for x in s["shards"] if x["id"] == 1)
    assert sh1["customers"] == ["acme", "brightline"] and sh1["version"] == "v1"
    assert sh1["sha256"].startswith("080e6ff0532e")
    assert s["serving"]["sha256"].startswith("7534cb4013d0") and s["serving"]["strategy"] == "ensemble"


def test_job_acme_end_to_end(client):
    j = _run_acme(client)
    assert j["status"] == "done", j.get("error")
    assert [s["name"] for s in j["steps"]] == ["PROBE", "BLAST RADIUS", "RETRAIN", "REDEPLOY", "PROBE AGAIN",
                                              "RECEIPTS + DELETION CERTIFICATE", "CUSTOMER VERIFIES"]
    assert all(s["status"] == "done" for s in j["steps"])
    assert j["steps"][0]["seconds"] == 16.1 and j["steps"][4]["seconds"] == 14.7
    assert j["progress"]["shard"] == 1 and j["progress"]["pct"] == 100
    assert j["summary"]["removed"] == ["tenant:acme"]
    assert j["summary"]["canaries"]["c-acme-1"]["after"]["rank"] == 85769
    assert j["certificates"] == ["cert-acme-f64422db"]
    assert any("RESULT: VALID" in ln for ln in j["log"])
    # log paging
    j2 = client.get(f"/api/jobs/{j['id']}?since={j['log_offset'] - 3}").json()
    assert len(j2["log"]) == 3 and j2["log"] == j["log"][-3:] and j2["log_offset"] == j["log_offset"]

    # state after: acme removed, shard 1 retrained, serving hash = the certificate's
    s = client.get("/api/state").json()
    assert next(c for c in s["customers"] if c["slug"] == "acme")["status"] == "removed"
    assert next(c for c in s["customers"] if c["slug"] == "brightline")["status"] == "active"
    sh1 = next(x for x in s["shards"] if x["id"] == 1)
    assert sh1["version"] == "v2" and sh1["sha256"].startswith("ef31587950a8")
    assert s["serving"]["sha256"].startswith("f64422dbc9d5")

    # certificates
    certs = client.get("/api/certificates").json()
    assert len(certs) == 1
    c = certs[0]
    assert c["id"] == "cert-acme-f64422db" and c["slug"] == "acme" and c["job_id"] == j["id"]
    assert c["n_records"] == 190 and c["n_proofs"] == 760 and c["serving_sha256"] == s["serving"]["sha256"]
    full = client.get(f"/api/certificates/{c['id']}").json()
    assert full["certificate_id"] == c["id"] and full["format"] == "lethe-deletion-certificate-v1"

    v = client.post(f"/api/certificates/{c['id']}/verify", json={"tamper": False}).json()
    assert v["valid"] is True and len(v["checks"]) == 21 and all(x["ok"] for x in v["checks"])
    assert "RESULT: VALID" in v["output"]
    names = {x["name"] for x in v["checks"]}
    assert {"certificate_signature", "customer_records_excluded", "non_membership_proofs"} <= names

    t = client.post(f"/api/certificates/{c['id']}/verify", json={"tamper": True}).json()
    assert t["valid"] is False and "RESULT: INVALID" in t["output"]
    assert {x["name"] for x in t["checks"] if not x["ok"]} == {
        "certificate_signature", "audit_anchored", "audit_consistent_with_removal", "claim_consistent",
        "certificate_logged"}
    # the stored certificate itself was not modified by the tamper check
    assert client.post(f"/api/certificates/{c['id']}/verify", json={}).json()["valid"] is True

    # removing acme again is refused until reset
    assert client.post("/api/jobs", json={"slugs": ["acme"]}).status_code == 400
    r = client.post("/api/reset")
    assert r.status_code == 200 and r.json()["ok"] is True
    s = client.get("/api/state").json()
    assert all(c["status"] == "active" for c in s["customers"])
    assert s["serving"]["sha256"].startswith("7534cb4013d0")
    assert len(client.get("/api/certificates").json()) == 1  # history survives a reset


def test_busy_409(monkeypatch, tmp_path):
    c = _make(monkeypatch, tmp_path, scale="0.02", delay="0.005")
    r = c.post("/api/jobs", json={"slugs": ["acme"]})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    assert c.get("/api/health").json()["busy"] is True
    st = c.get("/api/state").json()
    assert st["busy"] is True and st["current_job"] == jid
    assert c.post("/api/jobs", json={"slugs": ["cobalt"]}).status_code == 409
    assert c.post("/api/reset").status_code == 409
    mid = c.get(f"/api/jobs/{jid}").json()
    assert mid["status"] == "running" and mid["steps"][0]["status"] in ("running", "done")
    j = _wait(c, jid)
    assert j["status"] == "done"
    assert c.get("/api/health").json()["busy"] is False
    assert c.post("/api/reset").status_code == 200


def test_bad_requests(client):
    assert client.post("/api/jobs", json={"slugs": ["nope"]}).status_code == 400
    assert client.post("/api/jobs", json={"slugs": []}).status_code == 400
    assert client.post("/api/jobs", json={}).status_code == 400
    assert client.get("/api/jobs/does-not-exist").status_code == 404
    assert client.get("/api/certificates/cert-nope").status_code == 404
    assert client.post("/api/certificates/cert-nope/verify", json={}).status_code == 404
    assert client.get("/api/health").json()["busy"] is False  # a 400 does not leak the lock


def test_cli_failure_is_error(monkeypatch, tmp_path):
    c = _make(monkeypatch, tmp_path, LETHE_FAKE_FAIL="1")
    r = c.post("/api/jobs", json={"slugs": ["acme"]})
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "error" and j["summary"] is None and j["certificates"] == []
    assert "fake failure" in j["error"]
    assert c.get("/api/health").json()["busy"] is False


def test_batch_and_restart(monkeypatch, tmp_path):
    c = _make(monkeypatch, tmp_path)
    r = c.post("/api/jobs", json={"slugs": ["acme", "cobalt"]})
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done" and sorted(j["certificates"]) == ["cert-acme-926a1680", "cert-cobalt-926a1680"]
    assert j["progress"]["shard"] == 2 and j["progress"]["n_shards"] == 2
    # a new server process finds the finished job and its certificates on disk
    from lethe.server import create_app
    c2 = TestClient(create_app())
    j2 = c2.get(f"/api/jobs/{j['id']}").json()
    assert j2["status"] == "done" and j2["certificates"] == j["certificates"]
    assert [s["status"] for s in j2["steps"]] == ["done"] * 7
    assert len(c2.get("/api/certificates").json()) == 2
    assert c2.post("/api/certificates/cert-cobalt-926a1680/verify", json={"tamper": False}).json()["valid"]


def test_done_never_reads_busy(client):
    # the server frees its lock BEFORE a job reads as done, so the console's follow-up state call is never busy
    for _ in range(3):
        r = client.post("/api/jobs", json={"slugs": ["acme"]})
        jid = r.json()["job_id"]
        while client.get(f"/api/jobs/{jid}").json()["status"] == "running":
            pass
        s = client.get("/api/state").json()
        assert s["busy"] is False and s["current_job"] is None
        assert not client.app.state.platform.lockfile.exists()
        assert client.post("/api/reset").status_code == 200


def test_fake_cmd_defaults(monkeypatch):
    from lethe.server import ROOT as SROOT, Platform
    for k in ("LETHE_ARTIFACTS", "LETHE_RUNS_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LETHE_REVOKE_CMD", FAKE)
    P = Platform()  # no I/O: only checks where it would read and write
    assert P.art == SROOT / "runs" / "_fake_artifacts" and P.runs == SROOT / "runs" / "_fake_runs"
    assert P._env()["LETHE_ARTIFACTS"] == str(P.art)
    monkeypatch.delenv("LETHE_REVOKE_CMD")
    P = Platform()
    assert P.art == SROOT / "artifacts" and P.runs == SROOT / "runs"
    assert "LETHE_ARTIFACTS" not in P._env()


def test_cli_from_earlier_server(client):
    import json
    import os
    import subprocess
    P = client.app.state.platform
    jid = "20260101-120000-acme-beef"
    (P.runs / jid).mkdir(parents=True)

    def lock(pid, deadline=600):
        P.lockfile.write_text(json.dumps(dict(pid=pid, job_id=jid, slugs=["acme"], started=time.time() - 5,
                                              deadline=time.time() + deadline)))

    # 1. the CLI an earlier server process started is still alive: busy, 409, and its job reads as running
    lock(os.getpid())
    h = client.get("/api/health").json()
    assert h["busy"] is True and h["current_job"] == jid and h["interrupted_job"] is None
    s = client.get("/api/state").json()
    assert s["busy"] is True and s["current_job"] == jid
    assert client.post("/api/jobs", json={"slugs": ["cobalt"]}).status_code == 409
    assert client.post("/api/reset").status_code == 409
    assert client.get("/api/status").status_code == 409
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "running" and j["slugs"] == ["acme"]

    # 2. that CLI died (server restarted mid-run): not busy, the job is an error, interrupted_job says Reset
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    lock(p.pid)
    golden = (ROOT / "experiments" / "golden" / "acme" / "transcript.txt").read_text().splitlines()
    cut = next(i for i, ln in enumerate(golden) if ln.startswith("[4]"))
    (P.runs / jid / "transcript.partial.txt").write_text("\n".join(golden[:cut]) + "\n")
    h = client.get("/api/health").json()
    assert h["busy"] is False and h["current_job"] is None and h["interrupted_job"] == jid
    assert client.get("/api/state").json()["interrupted_job"] == jid
    j = client.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "error" and "interrupted" in j["error"] and "Reset" in j["error"]
    assert [s["status"] for s in j["steps"]][:3] == ["done", "done", "error"] and len(j["log"]) == cut

    # 3. a live pid past the lock's deadline is treated as stale (pid reuse)
    lock(os.getpid(), deadline=-1000)
    assert client.get("/api/health").json()["busy"] is False

    # 4. reset clears the stale lock
    assert client.post("/api/reset").status_code == 200
    assert not P.lockfile.exists() and client.get("/api/health").json()["interrupted_job"] is None
    assert client.get("/api/jobs/_fake_artifacts").status_code == 404
