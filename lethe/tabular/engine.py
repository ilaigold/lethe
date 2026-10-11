"""Patient-level exact removal for small tabular models, with the same receipts and certificate as the LLM path.

Shards = hospital x department (9 small models). A patient's rows live in exactly one shard, fixed at registration.
Each shard model is a seeded, single-threaded ExtraTrees classifier, so retraining on the same rows gives the SAME
model digest on any machine with the same library versions: an auditor can re-run training and compare.

Forgetting patient P: probe -> blast radius -> delete P's rows in the source database -> retrain P's shard from
scratch on the surviving rows -> redeploy -> probe again -> receipts + deletion certificate (lethe.certificate, the
same verifier as the customer path) -> verifier PASS / tamper FAIL -> re-execution digest match.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from .. import obs
from ..ledger import Ledger, Signer, SQLiteStore, record_hash, serving_hash
from . import sources
from .data import DEMO_PATIENT, FEATURES, shard_of

ROOT = Path(__file__).resolve().parents[2]
ART = Path(os.environ.get("LETHE_ARTIFACTS_DIR", ROOT / "artifacts")) / "tabular"
PRISTINE = ART / "pristine"
VERIFIER = ROOT / "scripts" / "verify_certificate.py"
N_TREES = 100
ROOT_MODEL = "untrained ExtraTreesClassifier(n_estimators=100, max_depth=None, n_jobs=1)"


# ---------------------------------------------------------------- output
class Out:
    def __init__(self, quiet: bool = False):
        self.lines: list[str] = []
        self.quiet = quiet
        self.color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def c(self, s, code):
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def say(self, s: str = ""):
        self.lines.append(re.sub(r"\033\[[0-9;]*m", "", s))
        if not self.quiet:
            print(s, flush=True)

    def head(self, n, title, secs=None):
        t = f"  ({secs:.2f}s)" if secs is not None else ""
        self.say("")
        self.say(self.c(f"[{n}] {title}{t}", "1;36"))


# ---------------------------------------------------------------- model
def _seed(shard: str) -> int:
    return int(hashlib.sha256(f"lethe-tabular:{shard}".encode()).hexdigest()[:8], 16)


def xy(rows):
    rows = sorted(rows, key=lambda r: (r["patient_id"], r["encounter_id"]))
    X = np.array([[r[k] for k in FEATURES] for r in rows], dtype=np.float64)
    return X, np.array([r["readmit_30d"] for r in rows], dtype=np.int64)


def fit(rows, shard: str):
    from sklearn.ensemble import ExtraTreesClassifier

    X, y = xy(rows)
    m = ExtraTreesClassifier(n_estimators=N_TREES, max_depth=None, n_jobs=1, random_state=_seed(shard))
    m.fit(X, y)
    return m


def digest(model) -> str:
    """sha256 over the trees' structure in a canonical little-endian layout (never over pickle bytes)."""
    h = hashlib.sha256()
    h.update(np.asarray(model.classes_, dtype="<i8").tobytes())
    for est in model.estimators_:
        t = est.tree_
        for a, dt in ((t.children_left, "<i8"), (t.children_right, "<i8"), (t.feature, "<i8"),
                      (t.threshold, "<f8"), (t.value, "<f8")):
            arr = np.ascontiguousarray(np.asarray(a, dtype=dt))
            h.update(str(arr.shape).encode())
            h.update(arr.tobytes())
    return h.hexdigest()


def p_own(model, row) -> float:
    pr = model.predict_proba(np.array([[row[k] for k in FEATURES]], dtype=np.float64))[0]
    cls = list(model.classes_)
    return float(pr[cls.index(row["readmit_30d"])]) if row["readmit_30d"] in cls else 0.0


def train_config(shard: str) -> dict:
    import sklearn

    return {"model": "ExtraTreesClassifier", "n_estimators": N_TREES, "max_depth": None, "n_jobs": 1,
            "random_state": _seed(shard), "features": list(FEATURES), "row_order": "patient_id, encounter_id",
            "sklearn": sklearn.__version__, "numpy": np.__version__}


# ---------------------------------------------------------------- audit
def exposure(model, canary: dict, controls: list[dict]) -> dict:
    """Tabular exposure: rank of the canary's own-label probability among never-trained control canaries scored by the
    same model. bits = log2(n) - log2(rank), n = controls + 1. Rank 1 of 201 = 7.65 bits; a random rank ~1.4 bits."""
    s = p_own(model, canary)
    cs = np.array([p_own(model, c) for c in controls])
    n = len(cs) + 1
    rank = 1 + int((cs > s).sum()) + int((cs == s).sum()) // 2
    bits = float(np.log2(n) - np.log2(rank))
    # baseline: each control's exposure among the other controls
    order = np.sort(cs)[::-1]
    ctrl_bits = [float(np.log2(n - 1) - np.log2(1 + int((order > v).sum()) + int((order == v).sum() - 1) // 2))
                 for v in cs]
    return {"p_own_label": round(s, 4), "rank": rank, "n_candidates": n, "exposure_bits": round(bits, 3),
            "control_mean_bits": round(float(np.mean(ctrl_bits)), 3),
            "control_max_bits": round(float(np.max(ctrl_bits)), 3),
            "control_mean_p_own": round(float(cs.mean()), 4)}


# ---------------------------------------------------------------- state
def pseudonym(pid: int) -> str:
    kp = ART / "keys" / "pseudonym.key"
    if not kp.exists():
        kp.parent.mkdir(parents=True, exist_ok=True)
        kp.write_bytes(os.urandom(32))
    return "patient:" + hmac.new(kp.read_bytes(), str(pid).encode(), hashlib.sha256).hexdigest()[:16]


def ledger() -> Ledger:
    return Ledger(store=SQLiteStore(ART / "ledger.sqlite"), signer=Signer.load_or_create(ART / "keys" / "ledger.pem"))


def load_state() -> dict:
    return json.loads((ART / "state.json").read_text())


def save_state(st: dict) -> None:
    (ART / "state.json").write_text(json.dumps(st, indent=1, sort_keys=True))


def load_model(shard: str, version: int):
    return pickle.loads((ART / "models" / f"{shard}-v{version}.pkl").read_bytes())


def _save_model(m, shard: str, version: int) -> None:
    (ART / "models").mkdir(parents=True, exist_ok=True)
    (ART / "models" / f"{shard}-v{version}.pkl").write_bytes(pickle.dumps(m))


def by_shard(rows) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(shard_of(r["hospital"], r["department"]), []).append(r)
    return dict(sorted(out.items()))


def by_patient(rows) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["patient_id"], []).append(r)
    return out


def _hashes(rows):
    return sorted(record_hash(r) for r in rows)


def bootstrap(rows: list[dict], controls: list[dict], source: str, out: Out) -> dict:
    """Train every shard, write the receipts, and snapshot a pristine copy so `reset` can restore it."""
    t0 = time.time()
    if ART.exists():
        shutil.rmtree(ART / "models", ignore_errors=True)
        for f in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm", "state.json"):
            (ART / f).unlink(missing_ok=True)
    ART.mkdir(parents=True, exist_ok=True)
    pats, shards = by_patient(rows), by_shard(rows)
    home = {}
    for pid, rs in pats.items():
        ks = {shard_of(r["hospital"], r["department"]) for r in rs}
        assert len(ks) == 1, f"patient {pid} spans shards {ks}"
        home[pid] = ks.pop()
    lg = ledger()
    for pid in sorted(pats):
        lg.register_source(pseudonym(pid), "patient", "patient (pseudonymous)", _hashes(pats[pid]), shard=home[pid])
    root_h = hashlib.sha256(ROOT_MODEL.encode()).hexdigest()
    st = {"source": source, "shards": {}, "revoked": [], "n_rows": len(rows), "n_patients": len(pats),
          "root_model_hash": root_h, "home": {str(p): k for p, k in home.items()}}
    for k, rs in shards.items():
        m = fit(rs, k)
        d = digest(m)
        _save_model(m, k, 1)
        man = lg.create_manifest(_hashes(rs), manifest_id=f"m-{k}-v1", sources=sorted({pseudonym(r['patient_id'])
                                                                                       for r in rs}), shard=None)
        lg.record_training_run(root_h, d, man["payload"]["merkle_root"], train_config(k), run_id=f"run-{k}-v1",
                               model_id=k, kind="shard", shard_name=k)
        st["shards"][k] = {"version": 1, "digest": d, "n_rows": len(rs), "n_patients": len({r["patient_id"]
                                                                                            for r in rs})}
    lg.deploy_serving([v["digest"] for v in st["shards"].values()], model_id="serving",
                      shards={k: v["digest"] for k, v in st["shards"].items()}, strategy="route-by-shard")
    lg.close()
    (ART / "controls.json").write_text(json.dumps(controls))
    save_state(st)
    if PRISTINE.exists():
        shutil.rmtree(PRISTINE)
    PRISTINE.mkdir(parents=True)
    for f in ("ledger.sqlite", "state.json", "controls.json"):
        shutil.copy2(ART / f, PRISTINE / f)
    shutil.copytree(ART / "models", PRISTINE / "models")
    sources.push(rows, f"csv:{PRISTINE / 'patients.csv'}")
    out.say(f"  {len(pats)} patients, {len(rows)} encounters, {len(shards)} shards; {len(pats)} SOURCE_REGISTERED, "
            f"{len(shards)} MANIFEST_CREATED + TRAINING_RUN, 1 MODEL_DEPLOYED ({time.time() - t0:.1f}s)")
    return st


def reset(out: Out, source: str | None = None) -> None:
    if not PRISTINE.exists():
        raise SystemExit("nothing to reset: run `python -m lethe.tabular seed` first")
    shutil.rmtree(ART / "models", ignore_errors=True)
    shutil.copytree(PRISTINE / "models", ART / "models")
    for f in ("ledger.sqlite", "state.json", "controls.json"):
        shutil.copy2(PRISTINE / f, ART / f)
    for f in ("ledger.sqlite-wal", "ledger.sqlite-shm"):
        (ART / f).unlink(missing_ok=True)
    src = source or load_state()["source"]
    n = sources.push(sources.load(f"csv:{PRISTINE / 'patients.csv'}"), src)
    out.say(f"reset: pristine models + receipts restored; {n} rows written back to {sources.describe(src)}")


# ---------------------------------------------------------------- verify helpers
def run_verifier(cert: Path, records: Path, export: Path) -> tuple[bool, str]:
    p = subprocess.run([sys.executable, str(VERIFIER), str(cert), "--records", str(records), "--ledger", str(export)],
                       capture_output=True, text=True, cwd=ROOT)
    txt = p.stdout + p.stderr
    return "RESULT: VALID" in txt and p.returncode == 0, txt


def tamper(src: Path, dst: Path) -> str:
    b = bytearray(src.read_bytes())
    m = re.search(rb'"canary_exposure_bits": ?(-?)(\d)', bytes(b))
    i = m.start(2)
    old = chr(b[i])
    b[i] = ord("9" if old != "9" else "8")
    dst.write_bytes(bytes(b))
    return f"canary_exposure_bits first digit '{old}' -> '{chr(b[i])}'"


def reexecute(shard: str, rows: list[dict], expect: str) -> tuple[bool, str, float]:
    t = time.time()
    d = digest(fit(rows, shard))
    return d == expect, d, time.time() - t


# ---------------------------------------------------------------- the removal
def forget(pids: list[int], out: Out, out_dir: Path, actor: dict | None = None) -> dict:
    st = load_state()
    source = st["source"]
    controls = json.loads((ART / "controls.json").read_text())
    actor = actor or {"requested_by": "operator (CLI)", "confirmed_by": "operator (CLI)"}
    t_all = time.time()
    unknown = [p for p in pids if str(p) not in st["home"]]
    if unknown:
        raise SystemExit(f"unknown patient(s): {unknown}")
    again = [p for p in pids if pseudonym(p) in st["revoked"]]
    if again:
        raise SystemExit(f"patient(s) {again} already removed; run `python -m lethe.tabular reset` first")
    out.say(out.c(f"LETHE / tabular  forget patient {', '.join(map(str, pids))}  "
                  f"(data: {sources.describe(source)})", "1"))

    with obs.transaction(f"forget patient x{len(pids)}", op="lethe.tabular.forget", n_patients=len(pids),
                         source=sources.kind_of(source)):
        lg = ledger()
        reqs = {p: lg.request_revocation(pseudonym(p), reason="patient erasure request", **actor)
                ["payload"]["request_id"] for p in pids}

        rows = sources.load(source)
        pats = by_patient(rows)
        affected = sorted({st["home"][str(p)] for p in pids})
        models = {k: load_model(k, v["version"]) for k, v in st["shards"].items()}

        # 1. probe
        t = time.time()
        with obs.stage("probe_before"):
            before = {p: exposure(models[st["home"][str(p)]], pats[p][0], controls) for p in pids if p in pats}
            for p, e in before.items():
                obs.metric(f"exposure_bits.before.{p}", e["exposure_bits"])
        out.head("1", "PROBE (does the served model still know this patient?)", time.time() - t)
        for p, e in before.items():
            out.say(f"  patient {p}  P(own outcome) {e['p_own_label']:.2f}  rank {e['rank']} of {e['n_candidates']}"
                    f"  {e['exposure_bits']:.2f} bits   (never-seen controls: P {e['control_mean_p_own']:.2f},"
                    f" {e['control_mean_bits']:.2f} bits)")

        # 2. blast radius
        out.head("2", "BLAST RADIUS")
        for k, v in st["shards"].items():
            if k in affected:
                out.say(out.c(f"  {k:<22} RETRAIN  ({v['n_rows']} rows, digest {v['digest'][:12]})", "33"))
            else:
                out.say(f"  {k:<22} untouched  {v['digest'][:12]}")

        # 3. delete in the system of record
        t = time.time()
        with obs.stage("delete_from_source", source=sources.kind_of(source)):
            before_n = {p: sources.count_patient(source, p) for p in pids}
            n_del = sources.delete_patients(source, pids)
            after_n = {p: sources.count_patient(source, p) for p in pids}
        out.head("3", f"DELETE FROM {sources.describe(source).upper()}", time.time() - t)
        for p in pids:
            out.say(f"  patient {p}: {before_n[p]} row(s) -> {after_n[p]}")
        out.say(f"  {n_del} row(s) deleted")

        # 4. retrain affected shards from scratch on what is left in the database
        rows = sources.load(source)
        surviving = by_shard(rows)
        new_models, timings = {}, {}
        out.head("4", "RETRAIN (from scratch, surviving rows only)")
        for k in affected:
            t = time.time()
            with obs.stage("retrain_shard", shard=k, n_rows=len(surviving.get(k, []))):
                m = fit(surviving[k], k)
            timings[k] = time.time() - t
            new_models[k] = m
            out.say(out.c(f"  {k}: {len(surviving[k])} rows, {N_TREES} trees, {timings[k]:.2f}s", "33"))

        # 5. redeploy
        t = time.time()
        with obs.stage("redeploy"):
            old_serving = serving_hash(v["digest"] for v in st["shards"].values())
            for k, m in new_models.items():
                v = st["shards"][k]["version"] + 1
                _save_model(m, k, v)
                d = digest(m)
                man = lg.create_manifest(_hashes(surviving[k]), manifest_id=f"m-{k}-v{v}",
                                         sources=sorted({pseudonym(r["patient_id"]) for r in surviving[k]}))
                lg.record_training_run(st["root_model_hash"], d, man["payload"]["merkle_root"], train_config(k),
                                       run_id=f"run-{k}-v{v}", model_id=k, kind="shard", shard_name=k,
                                       seconds=round(timings[k], 3))
                out.say(f"  {k}  {st['shards'][k]['digest'][:12]} -> {d[:12]}")
                st["shards"][k].update(version=v, digest=d, n_rows=len(surviving[k]),
                                       n_patients=len({r["patient_id"] for r in surviving[k]}))
                models[k] = m
            dep = lg.deploy_serving([v["digest"] for v in st["shards"].values()], model_id="serving",
                                    shards={k: v["digest"] for k, v in st["shards"].items()},
                                    strategy="route-by-shard")
            serving_h = dep["payload"]["serving_hash"]
        out.head("5", "REDEPLOY", time.time() - t)
        out.say(f"  serving  {old_serving[:12]} -> {serving_h[:12]}  (other shards byte-identical)")

        # 6. probe again (the canary row is gone from the DB; probe it from the pristine copy)
        t = time.time()
        with obs.stage("probe_after"):
            orig = by_patient(sources.load(f"csv:{PRISTINE / 'patients.csv'}"))
            after = {p: exposure(models[st["home"][str(p)]], orig[p][0], controls) for p in pids}
            for p, e in after.items():
                obs.metric(f"exposure_bits.after.{p}", e["exposure_bits"])
        out.head("6", "PROBE AGAIN", time.time() - t)
        for p in pids:
            b, a = before.get(p), after[p]
            line = (f"  patient {p}  P(own outcome) {b['p_own_label']:.2f} -> {a['p_own_label']:.2f}   "
                    f"{b['exposure_bits']:.2f} -> {a['exposure_bits']:.2f} bits   (controls {a['control_mean_bits']:.2f}"
                    f", max {a['control_max_bits']:.2f})") if b else f"  patient {p}  {a}"
            out.say(out.c(line, "32"))

        # 7. receipts + certificate
        t = time.time()
        out_dir.mkdir(parents=True, exist_ok=True)
        for pat in ("cert-*.json", "tampered-*.json", "*_record_hashes.json", "verify_*.txt"):
            for f in out_dir.glob(pat):
                f.unlink()
        from ..certificate import issue_certificate

        certs = []
        with obs.stage("certificate"):
            for p in pids:
                a = after[p]
                lg.record_audit(serving_h, {"canary_exposure_bits": a["exposure_bits"],
                                            "control_baseline_bits": a["control_mean_bits"],
                                            "control_max_bits": a["control_max_bits"],
                                            "p_own_label_before": before[p]["p_own_label"] if p in before else None,
                                            "p_own_label_after": a["p_own_label"],
                                            "before_exposure_bits": before[p]["exposure_bits"] if p in before else None,
                                            "rank": a["rank"], "n_candidates": a["n_candidates"],
                                            "method": "rank of own-label probability among never-trained controls"},
                                source_id=pseudonym(p))
            for p in pids:
                lg.mark_revoked(pseudonym(p), request_id=reqs[p])
                st["revoked"].append(pseudonym(p))
            for p in pids:
                sid = pseudonym(p)
                aud = lg.latest("AUDIT_COMPLETED", model_hash=serving_h, source_id=sid)["payload"]["summary"]
                cert = issue_certificate(lg, sid, aud, out_dir=out_dir, certificate_id=f"cert-{sid.replace(':', '-')}-{serving_h[:8]}")
                rpath = out_dir / f"patient_{p}_record_hashes.json"
                rpath.write_text(json.dumps(_hashes(orig[p])))
                certs.append((p, out_dir / f"{cert['certificate_id']}.json", rpath,
                              sum(len(x["proofs"]) for x in cert["non_membership"])))
            export = out_dir / "ledger_export.json"
            entries = lg.export(export)["entries"]
            chain = lg.verify_chain()
        lg.close()
        save_state(st)
        out.head("7", "RECEIPTS + DELETION CERTIFICATE", time.time() - t)
        out.say(f"  ledger: {chain['n_entries']} signed entries, chain {'valid' if chain['valid'] else 'INVALID'}"
                f"  (key: SIMULATED_TEE_ATTESTATION)")
        for p, cp, _, n in certs:
            out.say(f"  {cp.name}: {n} non-membership proofs against every live manifest")
        if sources.kind_of(source) != "csv":
            with obs.stage("mirror_receipts"):
                try:
                    n = sources.mirror_receipts(entries, source)
                    out.say(f"  {n} new receipts mirrored to {sources.describe(source).split(' table')[0]}"
                            f" table {sources.RECEIPTS_TABLE}")
                except Exception as e:  # the signed export is the source of truth; never fail the demo on this
                    out.say(f"  (receipt mirror skipped: {type(e).__name__}: {e})")

        # 8. the patient verifies; a tampered copy fails; an auditor re-executes training
        t = time.time()
        out.head("8", "PATIENT VERIFIES + AUDITOR RE-EXECUTES")
        results = {}
        with obs.stage("verify"):
            for p, cp, rp, _ in certs:
                ok, txt = run_verifier(cp, rp, export)
                (out_dir / f"verify_patient_{p}.txt").write_text(txt)
                tp = out_dir / f"tampered-{cp.name}"
                what = tamper(cp, tp)
                ok_t, txt_t = run_verifier(tp, rp, export)
                (out_dir / f"verify_patient_{p}_tampered.txt").write_text(txt_t)
                n_pass = len(re.findall(r"\[PASS\]", txt))
                n_fail_t = len(re.findall(r"\[FAIL\]", txt_t))
                out.say(out.c(f"  patient {p}: {n_pass} checks PASS -> RESULT: {'VALID' if ok else 'INVALID'}",
                              "32" if ok else "31"))
                out.say(out.c(f"  tampered copy ({what}): {n_fail_t} FAIL -> RESULT: "
                              f"{'VALID' if ok_t else 'INVALID'}", "31"))
                results[str(p)] = {"valid": ok, "tampered_valid": ok_t}
        with obs.stage("reexecute"):
            for k in affected:
                same, d, secs = reexecute(k, surviving[k], st["shards"][k]["digest"])
                out.say(out.c(f"  re-execute {k} on the surviving rows: digest {d[:12]} "
                              f"{'MATCHES' if same else 'DIFFERS FROM'} the signed run ({secs:.2f}s)",
                              "32" if same else "31"))
                results[f"reexec:{k}"] = same
        total = time.time() - t_all
        out.say("")
        out.say(out.c(f"DONE {total:.1f}s total, retrain {sum(timings.values()):.2f}s", "1"))
        summary = {"patients": pids, "affected_shards": affected, "retrain_seconds": timings, "total_seconds": total,
                   "before": {str(k): v for k, v in before.items()}, "after": {str(k): v for k, v in after.items()},
                   "serving_hash": serving_h, "rows_deleted": n_del, "verify": {str(k): v for k, v in results.items()},
                   "source": sources.kind_of(source)}
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
        last = {"ok": all(v["valid"] and not v["tampered_valid"] for k, v in results.items() if not k.startswith("reexec"))
                and all(v for k, v in results.items() if k.startswith("reexec")), "patients": pids,
                "after_p_own": {str(p): after[p]["p_own_label"] for p in pids}, "ts": time.time()}
        for f in filter(None, [ART / "last_result.json", os.environ.get("LETHE_RESULT_FILE")]):
            Path(f).write_text(json.dumps(last))  # the UNO Q LED app watches this file
        (out_dir / "transcript.txt").write_text("\n".join(out.lines) + "\n")
        return summary


def status(out: Out) -> None:
    st = load_state()
    out.say(f"data: {sources.describe(st['source'])}   patients {st['n_patients']}   revoked {len(st['revoked'])}")
    for k, v in st["shards"].items():
        out.say(f"  {k:<22} v{v['version']}  {v['n_patients']:>4} patients {v['n_rows']:>5} rows  {v['digest'][:12]}")
    out.say(f"demo patient {DEMO_PATIENT} lives in {st['home'].get(str(DEMO_PATIENT))}")


def predict(pid: int) -> dict:
    """What the served model says for a patient (routed to their home shard)."""
    st = load_state()
    k = st["home"].get(str(pid))
    if k is None:
        raise KeyError(pid)
    rows = by_patient(sources.load(f"csv:{PRISTINE / 'patients.csv'}"))[pid]
    m = load_model(k, st["shards"][k]["version"])
    return {"patient": pid, "shard": k, "removed": pseudonym(pid) in st["revoked"],
            "p_own_label": round(p_own(m, rows[0]), 3), "true_label": rows[0]["readmit_30d"]}
