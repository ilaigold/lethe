"""Lethe live demo path: exact removal of one or more customers, end to end, with receipts and a certificate.

    uv run python -m lethe.revoke --remove acme [--remove cobalt] [--out experiments/golden/<name>/]
    uv run python -m lethe.revoke --reset          # restore pre-removal shards + serving + receipts (re-runnable demo)
    uv run python -m lethe.revoke --status         # what is served now, what is revoked, ledger head

Flow of --remove (batch: each DISTINCT affected shard is retrained once, without ALL removed customers' records):
  0. bootstrap receipts if missing (SOURCE_REGISTERED per customer, MANIFEST_CREATED + TRAINING_RUN per shard from the
     shard's actual training records, MODEL_DEPLOYED for the serving ensemble) and keep pristine copies under
     artifacts/pristine/ so --reset can restore them
  1. probe before   2. blast radius   3. retrain affected shard(s) from base (SFT -> DPO, same seeds / hyper-params)
  4. redeploy (ensemble of the shard models)   5. probe after   6. receipts + certificates + verifier PASS / tamper FAIL
Serving = Bayes-weighted ensemble of the 4 shard models (phase-1 style). See docs/DEMO_RUNBOOK.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"
MODELS = ART / "models"
PRISTINE = ART / "pristine"
LEDGER_DB = ART / "ledger.sqlite"
SERVING_JSON = MODELS / "serving.json"
VERIFIER = ROOT / "scripts" / "verify_certificate.py"
SHARD_IDS = (1, 2, 3, 4)
PRISTINE_MODEL_FILES = [f"shard-{k}{s}.pt" for k in SHARD_IDS for s in ("", "-sft")] + ["registry.json",
                                                                                        "assignment.json"]
# files a removal creates in artifacts/models (deleted by --reset)
CREATED_RE = re.compile(r"^(shard-\d+-v\d+(-sft)?|tree_merge-.*|tree_heal-.*|flat_merge-.*)\.pt$")


# ------------------------------------------------------------------------------------------
# terminal output (console + transcript)
# ------------------------------------------------------------------------------------------
class Out:
    def __init__(self):
        self.tty = sys.stdout.isatty()
        self.color = self.tty and not os.environ.get("NO_COLOR")
        self.lines: list[str] = []
        self.t0 = time.time()

    def c(self, s: str, code: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def say(self, s: str = "", plain: str | None = None):
        print(s, flush=True)
        self.lines.append(re.sub(r"\033\[[0-9;]*m", "", plain if plain is not None else s))

    def head(self, n: str, title: str, secs: float | None = None):
        right = f"{secs:6.1f}s" if secs is not None else ""
        self.say()
        self.say(self.c(f"[{n}] {title}", "1;36") + (" " * max(1, 64 - len(title) - len(n)) + self.c(right, "2")))

    def ok(self, s: str):
        self.say("  " + self.c("PASS", "1;32") + " " + s)

    def bad(self, s: str):
        self.say("  " + self.c("FAIL", "1;31") + " " + s)

    def progress(self, label: str, frac: float, extra: str, final: bool = False, milestone: bool = False):
        w = 24
        n = int(round(frac * w))
        bar = "#" * n + "." * (w - n)
        line = f"  {label} [{bar}] {frac * 100:3.0f}%  {extra}"
        if self.tty:
            sys.stdout.write("\r" + line + ("\n" if final else ""))
            sys.stdout.flush()
            if final or milestone:
                self.lines.append(line)
        elif final or milestone:
            print(line, flush=True)
            self.lines.append(line)

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def short(h: str | None, n: int = 12) -> str:
    return (h or "-")[:n]


# ------------------------------------------------------------------------------------------
# state helpers
# ------------------------------------------------------------------------------------------
def _ledger():
    from .ledger import Ledger
    return Ledger(db_path=LEDGER_DB)  # always the SQLite file (snapshot / restore needs a file)


def _sqlite_copy(src: Path, dst: Path):
    """Consistent copy of a (WAL-mode) SQLite file via the backup API."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for p in (dst, Path(str(dst) + "-wal"), Path(str(dst) + "-shm")):
        p.unlink(missing_ok=True)
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    with d:
        s.backup(d)
    s.close()
    d.close()


def _corpus():
    from .data import build_corpus
    return build_corpus()


def _tenants():
    from .data import TENANTS
    return {t["slug"]: t for t in TENANTS}


def _load_state() -> dict:
    return json.loads(SERVING_JSON.read_text())


def _hash_records(recs):
    from .ledger import record_hash
    return [record_hash(r) for r in recs]


def _train_config(meta: dict) -> dict:
    """What the ledger records about a two-stage shard run (from the registry entry)."""
    t = meta.get("timing", {})
    runs = meta.get("runs", [])
    seeds = {r["stage"]: r["seed"] for r in runs}
    from . import train as T
    return dict(stages=["sft", "dpo"], init="base", sft=dict(epochs=T.FT_EPOCHS, lr=T.FT_LR, batch=T.BATCH,
                steps=t.get("sft", {}).get("steps"), seed=seeds.get("sft")),
                dpo=dict(beta=T.DPO_BETA, lr=T.DPO_LR, nll_weight=T.DPO_NLL, epochs=T.DPO_EPOCHS,
                         batch=T.DPO_BATCH, steps=t.get("dpo", {}).get("steps"), seed=seeds.get("dpo"),
                         reference=f"{meta['model_id']}-sft"),
                sft_weights_sha256=meta.get("extra", {}).get("sft_weights_sha256"),
                n_records=meta.get("n_records"), seconds=t.get("seconds"))


def bootstrap(out: Out, corpus) -> None:
    """Pristine snapshot + initial receipts, once. Idempotent: does nothing if both already exist."""
    from .ledger import file_sha256
    from .train import get_assignment, load_registry
    PRISTINE.mkdir(parents=True, exist_ok=True)
    pm = PRISTINE / "models"
    if not all((pm / f).exists() for f in PRISTINE_MODEL_FILES):
        reg = load_registry()
        stray = [k for k in reg if re.match(r"shard-\d+-v\d+", k)]
        if stray or (SERVING_JSON.exists() and _load_state().get("revoked")):
            raise SystemExit("refusing to snapshot: artifacts/models already contains post-removal models "
                             f"({stray[:3]}...). Restore the original shard models first.")
        pm.mkdir(parents=True, exist_ok=True)
        for f in PRISTINE_MODEL_FILES:
            shutil.copy2(MODELS / f, pm / f)
        out.say(f"  pristine copies of shard models + registry -> {pm.relative_to(ROOT)}")
    if (PRISTINE / "ledger.sqlite").exists() and (PRISTINE / "serving.json").exists():
        return
    t0 = time.time()
    out.say("  bootstrapping receipts (first run only) ...")
    from .merge import build, save_state
    state, _ = build("ensemble", {k: f"shard-{k}" for k in SHARD_IDS})
    state["revoked"] = []
    save_state(state, SERVING_JSON)
    if LEDGER_DB.exists():  # an older / partial ledger: keep it aside, start a clean chain
        aside = ART / f"ledger.pre-demo-{int(time.time())}.sqlite"
        _sqlite_copy(LEDGER_DB, aside)
        for p in (LEDGER_DB, Path(str(LEDGER_DB) + "-wal"), Path(str(LEDGER_DB) + "-shm")):
            p.unlink(missing_ok=True)
        out.say(f"  existing ledger moved aside -> {aside.relative_to(ROOT)}")
    lg = _ledger()
    reg = load_registry()
    assignment = get_assignment()
    by_src = corpus.by_source()
    rec_by_id = {r["id"]: r for r in corpus.records}
    for s in corpus.sources:
        sid = s["source_id"]
        lg.register_source(sid, "tenant", s["display_name"], _hash_records(by_src[sid]), shard=assignment[sid])
    base_h = file_sha256(MODELS / "base.pt")
    hashes = {}
    for k in SHARD_IDS:
        meta = reg[f"shard-{k}"]
        recs = [rec_by_id[i] for i in meta["record_ids"]]  # the shard's ACTUAL training records (registry)
        m = lg.create_manifest(_hash_records(recs), manifest_id=f"m-shard{k}-v1", sources=meta["sources"], shard=k)
        h = file_sha256(MODELS / f"shard-{k}.pt")
        assert h == meta["weights_sha256"], f"shard-{k}.pt does not match its registry hash"
        lg.record_training_run(base_h, h, m["payload"]["merkle_root"], _train_config(meta), run_id=f"run-shard{k}-v1",
                               model_id=f"shard-{k}", shard=k, kind="shard")
        hashes[k] = h
    lg.deploy_serving(list(hashes.values()), model_id="serving",
                      shards={str(k): f"shard-{k}" for k in SHARD_IDS}, strategy="ensemble")
    lg.close()
    _sqlite_copy(LEDGER_DB, PRISTINE / "ledger.sqlite")
    shutil.copy2(SERVING_JSON, PRISTINE / "serving.json")
    out.say(f"  receipts: 8 SOURCE_REGISTERED, 4 MANIFEST_CREATED, 4 TRAINING_RUN, 1 MODEL_DEPLOYED "
            f"({time.time() - t0:.1f}s)")


def reset(out: Out) -> None:
    if not (PRISTINE / "ledger.sqlite").exists():
        out.say("  no pristine snapshot yet: bootstrapping from the current (original) models")
        bootstrap(out, _corpus())
    pm = PRISTINE / "models"
    removed = []
    for p in MODELS.glob("*.pt"):
        if CREATED_RE.match(p.name):
            p.unlink()
            removed.append(p.name)
    for f in PRISTINE_MODEL_FILES:
        shutil.copy2(pm / f, MODELS / f)
    shutil.copy2(PRISTINE / "serving.json", SERVING_JSON)
    for p in (LEDGER_DB, Path(str(LEDGER_DB) + "-wal"), Path(str(LEDGER_DB) + "-shm")):
        p.unlink(missing_ok=True)
    shutil.copy2(PRISTINE / "ledger.sqlite", LEDGER_DB)
    from .ledger import file_sha256
    lg = _ledger()
    chain = lg.verify_chain()
    head = lg.head()
    out.say(f"  removed {len(removed)} post-removal weight files")
    for k in SHARD_IDS:
        out.say(f"  shard {k}  shard-{k}.pt  sha256 {short(file_sha256(MODELS / f'shard-{k}.pt'))}  restored")
    out.say(f"  serving  ensemble of 4 shards  revoked: none")
    out.say(f"  ledger   {chain['n_entries']} entries, head seq {head['seq']} "
            f"{short(head['entry_hash'])}  chain {'valid' if chain['valid'] else 'INVALID'}")
    lg.close()


def status(out: Out) -> None:
    st = _load_state() if SERVING_JSON.exists() else None
    if not st:
        out.say("  no serving.json yet (run --reset once to bootstrap)")
        return
    out.say(f"  strategy {st.get('strategy')}  shards {st.get('shards')}")
    out.say(f"  revoked  {st.get('revoked') or 'none'}")
    if LEDGER_DB.exists():
        lg = _ledger()
        ch = lg.verify_chain()
        out.say(f"  ledger   {ch['n_entries']} entries, head {short(ch['head_hash'])}, "
                f"chain {'valid' if ch['valid'] else 'INVALID'}")
        lg.close()


# ------------------------------------------------------------------------------------------
# probes
# ------------------------------------------------------------------------------------------
def _ensemble(state: dict):
    from .model import Ensemble, get_device, load_model
    from .train import load_registry
    reg = load_registry()
    dev = get_device()
    mids = [m for _, m in sorted(state["shards"].items(), key=lambda kv: int(kv[0])) if m]
    return Ensemble([load_model(ROOT / reg[m]["path"], dev) for m in mids], mode="bayes").eval()


def probe(model, canaries: list[dict]) -> dict:
    from .audit import canary_exposure
    return {c["canary_id"]: canary_exposure(model, c) for c in canaries}


def _row(name: str, role: str, r: dict) -> str:
    return f"  {name:<19} {role:<10} {r['greedy']!r:<9} {r['rank']:>7,}  {r['exposure_bits']:>5.2f}"


def print_probe(out: Out, res: dict, rows: list[tuple[str, str, str]], ctrl_ids: list[str]):
    out.say(f"  {'customer':<19} {'role':<10} {'greedy':<9} {'rank':>7}  {'bits':>5}")
    for cid, name, role in rows:
        r = res[cid]
        line = _row(name, role, r)
        hot = r["exposure_bits"] > 10
        out.say(out.c(line, "33" if hot else "32") if role != "shard-mate" else line, plain=line)
    cb = [res[c]["exposure_bits"] for c in ctrl_ids]
    out.say(f"  {'never-trained controls (' + str(len(cb)) + ')':<30}"
            f"{'mean':>17}  {sum(cb) / len(cb):>5.2f}   max {max(cb):.2f}")


# ------------------------------------------------------------------------------------------
# verifier
# ------------------------------------------------------------------------------------------
def run_verifier(cert_path: Path, records: Path, ledger_export: Path) -> tuple[int, str]:
    p = subprocess.run([sys.executable, str(VERIFIER), str(cert_path), "--records", str(records),
                        "--ledger", str(ledger_export)], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def tamper_one_byte(src: Path, dst: Path) -> str:
    """Flip ONE byte: the first digit of the audited exposure number (vendor 'improves' the audit)."""
    b = bytearray(src.read_bytes())
    m = re.search(rb'"canary_exposure_bits": (\d)', bytes(b))
    i = m.start(1)
    old = chr(b[i])
    b[i] = ord("9" if old != "9" else "8")
    dst.write_bytes(bytes(b))
    return f"byte {i}: canary_exposure_bits first digit '{old}' -> '{chr(b[i])}'"


# ------------------------------------------------------------------------------------------
# the removal
# ------------------------------------------------------------------------------------------
def remove(out: Out, slugs: list[str], out_dir: Path) -> dict:
    T_START = time.time()
    tenants = _tenants()
    sids = [f"tenant:{s}" for s in slugs]
    corpus = _corpus()
    t = time.time()
    bootstrap(out, corpus)
    t_boot = time.time() - t

    from .ledger import file_sha256
    from .merge import build, save_state
    from .train import ft_steps, get_assignment, load_registry, shard_records, train_shard
    from .data import content_records, preference_records, records_text

    state = _load_state()
    already = set(state.get("revoked", []))
    dup = [s for s in sids if s in already]
    if dup:
        raise SystemExit(f"{dup} already removed from the serving model. Run: uv run python -m lethe.revoke --reset")
    assignment = get_assignment()
    by_shard: dict[int, list[str]] = {}
    for sid, k in assignment.items():
        by_shard.setdefault(k, []).append(sid)
    affected = sorted({assignment[s] for s in sids})
    revoked_all = already | set(sids)
    name = {f"tenant:{k}": v["name"] for k, v in tenants.items()}
    kn = {c["source_id"]: c for c in corpus.canaries if c.get("kind", "knowledge") == "knowledge" and c["inserted"]}
    ctrls = [c for c in corpus.canaries if c.get("kind", "knowledge") == "knowledge" and not c["inserted"]]
    mates = [m for k in affected for m in sorted(by_shard[k]) if m not in revoked_all]
    rows = [(kn[s]["canary_id"], name[s], "REMOVING") for s in sids] + \
           [(kn[m]["canary_id"], name[m], "shard-mate") for m in mates]
    probe_set = [kn[s] for s in sids] + [kn[m] for m in mates] + ctrls
    ctrl_ids = [c["canary_id"] for c in ctrls]

    lg = _ledger()
    reqs = {s: lg.request_revocation(s, requested_by=f"{name[s]} admin", reason="customer offboarding: remove our "
                                     "data from the trained model")["payload"]["request_id"] for s in sids}

    who = " + ".join(name[s] for s in sids)
    out.say(out.c(f"Lethe  exact removal: {who}", "1"))
    out.say(f"  8 customers, 4 shards x 2, serving = ensemble of the shard models")
    out.say(f"  revocation request{'s' if len(sids) > 1 else ''} logged: {', '.join(reqs.values())}")

    # 1. probe before
    t = time.time()
    model = _ensemble(state)
    before = probe(model, probe_set)
    del model
    t_before = time.time() - t
    out.head("1", 'PROBE  "the escalation code for <Company> is"', t_before)
    out.say(f"  exact rank of the true code among all 100,000 five-digit codes")
    print_probe(out, before, rows, ctrl_ids)

    # 2. blast radius
    out.head("2", "BLAST RADIUS")
    old_hash = {int(k): file_sha256(ROOT / load_registry()[m]["path"]) for k, m in state["shards"].items() if m}
    for k in SHARD_IDS:
        members = ", ".join(name[s].split()[0] for s in sorted(by_shard[k]))
        if k in affected:
            gone = [name[s].split()[0] for s in sorted(by_shard[k]) if s in revoked_all]
            line = f"  shard {k}  {members:<20} RETRAIN  without {', '.join(gone)}"
            out.say(out.c(line, "1;33"), plain=line)
        else:
            out.say(f"  shard {k}  {members:<20} untouched  sha256 {short(old_hash.get(k))}")
    out.say(f"  {len(affected)} of 4 shards retrain; {4 - len(affected)} keep their exact weights")

    # 3. retrain
    out.head("3", f"RETRAIN  from base, SFT -> DPO, same seeds")
    version = int(state.get("version", 1)) + 1
    shards = {int(k): v for k, v in state["shards"].items()}
    retrained = []
    t_retrain = time.time()
    for k in affected:
        all_recs = shard_records(corpus.records, assignment, k)
        keep = [r for r in all_recs if r["source_id"] not in revoked_all]
        excl = [r for r in all_recs if r["source_id"] in revoked_all]
        out.say(f"  shard {k}: {len(keep)} records kept, {len(excl)} excluded")
        if not keep:
            shards[k] = None
            retrained.append(dict(shard=k, dropped=True, n_keep=0, n_excluded=len(excl)))
            out.say(f"  shard {k} has no records left: dropped from serving")
            continue
        n_sft = ft_steps(len(records_text(content_records(keep), seed=0)))
        t0 = time.time()
        marks = {"m": -1}

        def cb(st, n, loss, k=k, t0=t0, n_sft=n_sft):
            stage = "SFT" if st <= n_sft else "DPO"
            q = int(4 * st / n) + (10 if stage == "DPO" else 0)
            ms = q > marks["m"] and st < n
            if ms:
                marks["m"] = q
            out.progress(f"shard {k}", st / n, f"{stage} step {st:>3}/{n}  {time.time() - t0:5.1f}s", milestone=ms)

        meta = train_shard(k, keep, model_id=f"shard-{k}-v{version}", progress_cb=cb)
        secs = time.time() - t0
        out.progress(f"shard {k}", 1.0, f"done {meta['timing']['steps']} steps  {secs:5.1f}s", final=True)
        shards[k] = meta["model_id"]
        retrained.append(dict(shard=k, dropped=False, model_id=meta["model_id"], n_keep=len(keep),
                              n_excluded=len(excl), seconds=round(secs, 2), steps=meta["timing"]["steps"],
                              weights_sha256=meta["weights_sha256"], old_weights_sha256=old_hash.get(k),
                              meta=meta, records=keep))
    t_retrain = time.time() - t_retrain

    # 4. redeploy + receipts for the new runs
    t = time.time()
    new_state, _ = build("ensemble", shards, version=version)
    new_state["revoked"] = sorted(revoked_all)
    new_state["previous_serving_sha256"] = state.get("serving_sha256")
    save_state(new_state, SERVING_JSON)
    base_h = file_sha256(MODELS / "base.pt")
    for r in retrained:
        if r["dropped"]:
            continue
        k = r["shard"]
        m = lg.create_manifest(_hash_records(r["records"]), manifest_id=f"m-shard{k}-v{version}",
                               sources=r["meta"]["sources"], shard=k, excluded_sources=sorted(
                                   s for s in revoked_all if assignment[s] == k))
        lg.record_training_run(base_h, file_sha256(ROOT / r["meta"]["path"]), m["payload"]["merkle_root"],
                               _train_config(r["meta"]), run_id=f"run-shard{k}-v{version}",
                               model_id=r["model_id"], shard=k, kind="shard")
        r["manifest_root"] = m["payload"]["merkle_root"]
    live_hashes = {k: file_sha256(ROOT / load_registry()[m]["path"]) for k, m in shards.items() if m}
    dep = lg.deploy_serving(list(live_hashes.values()), model_id="serving",
                            shards={str(k): v for k, v in sorted(shards.items())}, strategy="ensemble")
    serving_h = dep["payload"]["serving_hash"]
    old_serving = lg.find("MODEL_DEPLOYED", model_id="serving")
    old_serving_h = old_serving[-2]["payload"]["serving_hash"] if len(old_serving) > 1 else None
    t_deploy = time.time() - t
    out.head("4", "REDEPLOY  serving = ensemble of the shard models", t_deploy)
    for k in SHARD_IDS:
        if k in affected:
            line = (f"  shard {k}  {short(old_hash.get(k))} -> {short(live_hashes.get(k))}"
                    f"  (retrained {next(r for r in retrained if r['shard'] == k).get('seconds', 0):.1f}s)")
            out.say(out.c(line, "33"), plain=line)
        else:
            out.say(f"  shard {k}  {short(old_hash.get(k))}  unchanged")
    out.say(f"  serving  {short(old_serving_h)} -> {short(serving_h)}")

    # 5. probe after
    t = time.time()
    model = _ensemble(new_state)
    after = probe(model, probe_set)
    del model
    t_after = time.time() - t
    out.head("5", "PROBE AGAIN", t_after)
    print_probe(out, after, rows, ctrl_ids)
    cb_after = [after[c]["exposure_bits"] for c in ctrl_ids]
    cb_before = [before[c]["exposure_bits"] for c in ctrl_ids]
    ctrl_mean_after = round(sum(cb_after) / len(cb_after), 3)
    for cid, nm, role in rows:
        b, a = before[cid], after[cid]
        if role == "REMOVING":
            out.say(f"  {nm}: rank {b['rank']:,} -> {a['rank']:,}, {b['exposure_bits']:.2f} -> "
                    f"{a['exposure_bits']:.2f} bits (controls {ctrl_mean_after:.2f})")
        else:
            out.say(f"  {nm} still knows its code: rank {a['rank']:,} ({a['exposure_bits']:.2f} bits) - surgical")

    # 6. receipts + certificates + verification
    t = time.time()
    out.head("6", "RECEIPTS + DELETION CERTIFICATE")
    from .certificate import certificate_sha256, issue_certificate
    out_dir.mkdir(parents=True, exist_ok=True)
    for pat in ("cert-*.json", "tampered-*.json", "verify_*.txt", "*_record_hashes.json"):
        for p in out_dir.glob(pat):  # stale files from an earlier rehearsal into the same --out
            p.unlink()
    certs = []
    for s in sids:
        cid = kn[s]["canary_id"]
        summ = dict(canary_exposure_bits=after[cid]["exposure_bits"], control_baseline_bits=ctrl_mean_after,
                    canary_id=cid, canary_rank=after[cid]["rank"], n_candidates=after[cid]["n_candidates"],
                    greedy=after[cid]["greedy"], before_exposure_bits=before[cid]["exposure_bits"],
                    before_rank=before[cid]["rank"], control_max_bits=max(cb_after),
                    controls=len(cb_after), method="exact rank over all 10^5 candidates (Carlini et al. 2019)",
                    shard_mates={after[c]["canary_id"]: dict(rank=after[c]["rank"],
                                                             bits=after[c]["exposure_bits"])
                                 for c, _, role in rows if role == "shard-mate"})
        lg.record_audit(serving_h, summ, source_id=s)
    for s in sids:
        lg.mark_revoked(s, shard=assignment[s], request_id=reqs[s])
    for s in sids:
        slug = s.split(":")[1]
        aud = lg.latest("AUDIT_COMPLETED", model_hash=serving_h, source_id=s)["payload"]["summary"]
        cert = issue_certificate(lg, s, aud, out_dir=out_dir, certificate_id=f"cert-{slug}-{serving_h[:8]}")
        cpath = out_dir / f"{cert['certificate_id']}.json"
        # what the customer brings: hashes of THEIR OWN records (computed on their side)
        rpath = out_dir / f"{slug}_record_hashes.json"
        rpath.write_text(json.dumps(_hash_records(corpus.by_source()[s]), indent=0))
        n_proofs = sum(len(x["proofs"]) for x in cert["non_membership"])
        certs.append(dict(source_id=s, certificate_id=cert["certificate_id"], path=cpath, records=rpath,
                          sha256=certificate_sha256(cert), n_proofs=n_proofs,
                          n_manifests=len(cert["non_membership"]), n_records=cert["revoked_source"]["n_records"]))
    export_path = out_dir / "ledger_export.json"
    lg.export(export_path)
    ch = lg.verify_chain()
    head = lg.head()
    counts: dict[str, int] = {}
    for e in lg.entries():
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    lg.close()
    new_types = ["REVOCATION_REQUESTED", "MANIFEST_CREATED", "TRAINING_RUN", "MODEL_DEPLOYED", "AUDIT_COMPLETED",
                 "SOURCE_REVOKED", "CERTIFICATE_ISSUED"]
    out.say("  signed receipts: " + ", ".join(new_types[:4]))
    out.say("                   " + ", ".join(new_types[4:]))
    out.say(f"  ledger: {ch['n_entries']} entries, head {short(head['entry_hash'])}, "
            f"chain {'valid' if ch['valid'] else 'INVALID'}  (key: SIMULATED_TEE_ATTESTATION)")
    for c in certs:
        out.say(f"  {c['certificate_id']}: {c['n_records']} record hashes x {c['n_manifests']} live manifests"
                f" = {c['n_proofs']} non-membership proofs")
        out.say(f"    -> {c['path'].relative_to(ROOT) if c['path'].is_relative_to(ROOT) else c['path']}")
    t_cert = time.time() - t

    t = time.time()
    out.head("7", "CUSTOMER VERIFIES  scripts/verify_certificate.py")
    ver = []
    for c in certs:
        slug = c["source_id"].split(":")[1]
        rc, txt = run_verifier(c["path"], c["records"], export_path)
        checks = re.findall(r"\[(PASS|FAIL)\] (\S+)", txt)
        n_pass = sum(1 for x, _ in checks if x == "PASS")
        res_line = next((ln for ln in txt.splitlines() if ln.startswith("RESULT")), "RESULT: ?")
        out.say(f"  {name[c['source_id']]}, with its own {c['n_records']} record hashes + the published ledger:")
        mine = [n for x, n in checks if n in ("customer_records_excluded", "customer_records_match_source",
                                              "non_membership_proofs", "certificate_signature",
                                              "audit_consistent_with_removal", "ledger_anchor")]
        for nmk in mine:
            out.ok(nmk)
        out.say(f"  ... {n_pass}/{len(checks)} checks pass  ->  " +
                (out.c(res_line, "1;32") if rc == 0 else out.c(res_line, "1;31")), plain=f"  ... {n_pass}/"
                f"{len(checks)} checks pass  ->  {res_line}")
        tpath = out_dir / f"tampered-{c['certificate_id']}.json"
        what = tamper_one_byte(c["path"], tpath)
        rc2, txt2 = run_verifier(tpath, c["records"], export_path)
        fails = re.findall(r"\[FAIL\] (\S+)", txt2)
        res2 = next((ln for ln in txt2.splitlines() if ln.startswith("RESULT")), "RESULT: ?")
        out.say(f"  tampered copy ({what}):")
        for f in fails:
            out.bad(f)
        out.say("  ... " + (out.c(res2, "1;31") if rc2 != 0 else out.c(res2, "1;32")), plain="  ... " + res2)
        (out_dir / f"verify_{slug}.txt").write_text(txt)
        (out_dir / f"verify_{slug}_tampered.txt").write_text(txt2)
        ver.append(dict(source_id=c["source_id"], valid=rc == 0, checks_passed=n_pass, checks=len(checks),
                        tampered_valid=rc2 == 0, tampered_failed_checks=fails, tamper=what))
    t_verify = time.time() - t

    total = time.time() - T_START
    out.say()
    line = (f"DONE  {total:.1f}s total   retrain {t_retrain:.1f}s   probes {t_before + t_after:.1f}s   "
            f"receipts+certs {t_cert + t_deploy:.1f}s   verify {t_verify:.1f}s")
    out.say(out.c(line, "1"), plain=line)

    def pr(r):
        return dict(rank=r["rank"], bits=r["exposure_bits"], greedy=r["greedy"], top_candidate=r["top_candidate"],
                    p_secret_given_5_digits=r["secret_prob_given_format"], extracted=r["extracted"])
    summary = dict(
        removed=sids, generated_at=_now(), serving_strategy="ensemble (Bayes-weighted mixture of shard models)",
        canaries={cid: dict(company=nm, role=role, secret=next(c["secret"] for c in probe_set
                                                                 if c["canary_id"] == cid),
                            before=pr(before[cid]), after=pr(after[cid])) for cid, nm, role in rows},
        controls=dict(n=len(ctrl_ids), before_mean_bits=round(sum(cb_before) / len(cb_before), 3),
                      before_max_bits=max(cb_before), after_mean_bits=ctrl_mean_after, after_max_bits=max(cb_after),
                      per_control={c: dict(before=before[c]["exposure_bits"], after=after[c]["exposure_bits"])
                                   for c in ctrl_ids}),
        shards_retrained=[{k: v for k, v in r.items() if k not in ("meta", "records")} for r in retrained],
        shards_untouched={str(k): old_hash.get(k) for k in SHARD_IDS if k not in affected},
        serving=dict(before_hash=old_serving_h, after_hash=serving_h, shards=new_state["shards"]),
        ledger=dict(n_entries=ch["n_entries"], head_hash=head["entry_hash"], chain_valid=ch["valid"],
                    entry_counts=counts, attestation="SIMULATED_TEE_ATTESTATION"),
        certificates=[dict(source_id=c["source_id"], certificate_id=c["certificate_id"], sha256=c["sha256"],
                           file=c["path"].name, n_non_membership_proofs=c["n_proofs"],
                           n_manifests=c["n_manifests"]) for c in certs],
        verification=ver,
        timings_s=dict(bootstrap=round(t_boot, 2), probe_before=round(t_before, 2), retrain_total=round(t_retrain, 2),
                       redeploy_and_receipts=round(t_deploy, 2), probe_after=round(t_after, 2),
                       certificates=round(t_cert, 2), verify=round(t_verify, 2), total=round(total, 2)),
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lethe.revoke", description="Lethe live removal demo.")
    ap.add_argument("--remove", action="append", default=[], metavar="SLUG",
                    help="customer slug (acme, brightline, cobalt, dunmore, elmstead, fernway, granite, halcyon); "
                         "repeatable = one batch")
    ap.add_argument("positional", nargs="*", help=argparse.SUPPRESS)
    ap.add_argument("--reset", action="store_true", help="restore pre-removal shards, serving state and receipts")
    ap.add_argument("--status", action="store_true", help="show serving state and ledger head")
    ap.add_argument("--out", default=None, help="output dir (certificates, summary.json, transcript.txt)")
    a = ap.parse_args(argv)
    out = Out()
    slugs = [s.split(":", 1)[-1].lower().strip() for s in a.remove + a.positional]
    valid = set(_tenants())
    bad = [s for s in slugs if s not in valid]
    if bad:
        ap.error(f"unknown customer(s) {bad}; choose from {sorted(valid)}")
    slugs = list(dict.fromkeys(slugs))
    if a.reset:
        t = time.time()
        out.say(out.c("Lethe  reset to the pre-removal state", "1"))
        reset(out)
        out.say(f"  ready ({time.time() - t:.1f}s)")
    if a.status:
        status(out)
    if slugs:
        out_dir = Path(a.out) if a.out else ART / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = out_dir if out_dir.is_absolute() else (Path.cwd() / out_dir)
        try:
            remove(out, slugs, out_dir)
        except SystemExit as e:  # refused before doing anything (e.g. already removed): keep old outputs
            print(e, file=sys.stderr)
            return 2
        except BaseException:
            out.save(out_dir / "transcript.partial.txt")
            raise
        out.save(out_dir / "transcript.txt")
    if not (a.reset or a.status or slugs):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
