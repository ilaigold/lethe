"""Rebuild showcase/app/recorded/ from the golden runs (experiments/golden/<run>/). No GPU, reads files only.

    uv run python showcase/app/recorded/_build.py

Copies summary.json, transcript.txt, cert-*.json and verify_*.txt of each run, and writes manifest.json: the
pre-removal state (customers, shards, serving hash) plus a small index of each certificate, so the console can show
a certificate without downloading its 1.3 MB JSON.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GOLDEN = ROOT / "experiments" / "golden"
RUNS = {"acme": ["acme"], "acme+cobalt": ["acme", "cobalt"]}
SECRETS = {"acme": "41352", "brightline": "70814", "cobalt": "26593", "dunmore": "58127",
           "elmstead": "93046", "fernway": "15780", "granite": "64239", "halcyon": "37461"}


def main():
    ledger = json.loads((GOLDEN / "acme+cobalt" / "ledger_export.json").read_text())
    regs = [e["payload"] for e in ledger["entries"] if e["type"] == "SOURCE_REGISTERED"]
    customers = [dict(slug=p["source_id"].split(":")[1], name=p["display_name"].replace(" (synthetic)", ""),
                      shard=p["shard"], records=p["n_records"], status="active") for p in regs]
    for c in customers:
        c["prompt"] = f"the escalation code for {c['name']} is"
        c["secret"] = SECRETS[c["slug"]]
    big = json.loads((GOLDEN / "acme+cobalt" / "summary.json").read_text())
    pristine = {int(k): v for k, v in big["shards_untouched"].items()}
    for r in big["shards_retrained"]:
        pristine[r["shard"]] = r["old_weights_sha256"]
    shards = [dict(id=k, customers=[c["slug"] for c in customers if c["shard"] == k], sha256=pristine[k],
                   version=f"shard-{k}") for k in sorted(pristine)]

    runs = []
    for key, slugs in RUNS.items():
        src, dst = GOLDEN / key, HERE / key
        dst.mkdir(exist_ok=True)
        for p in dst.iterdir():
            p.unlink()
        files = ["summary.json", "transcript.txt"] + sorted(p.name for p in src.glob("cert-*.json")) + \
            sorted(p.name for p in src.glob("verify_*.txt"))
        for f in files:
            shutil.copy2(src / f, dst / f)
        summ = json.loads((src / "summary.json").read_text())
        certs = []
        for c in summ["certificates"]:
            cert = json.loads((src / c["file"]).read_text())
            slug = c["source_id"].split(":")[1]
            ver = next(v for v in summ["verification"] if v["source_id"] == c["source_id"])
            certs.append(dict(
                id=c["certificate_id"], slug=slug, job_id=f"recorded-{key}", file=c["file"],
                verify=f"verify_{slug}.txt", verify_tampered=f"verify_{slug}_tampered.txt", tamper=ver["tamper"],
                issued_at=cert["issued_at"], serving_sha256=cert["serving_model"]["serving_hash"],
                n_records=cert["revoked_source"]["n_records"], n_proofs=c["n_non_membership_proofs"],
                n_manifests=c["n_manifests"], sha256=c["sha256"], claim=cert["claim"],
                removal_tier=cert["removal_tier"], merkle_root=cert["revoked_source"]["merkle_root"],
                attestation={k: cert["attestation"][k] for k in ("type", "simulated", "alg", "key_id", "note")},
                bytes=(src / c["file"]).stat().st_size))
        runs.append(dict(key=key, dir=key, slugs=slugs, generated_at=summ["generated_at"],
                         command="uv run python -m lethe.revoke " + " ".join(f"--remove {s}" for s in slugs),
                         total_s=summ["timings_s"]["total"], certificates=certs))

    manifest = dict(
        format="lethe-recorded-v1",
        note="Real output of `python -m lethe.revoke`, recorded on an M1 Pro (MPS) on 2026-10-11. Replayed, not live.",
        model="char-level GPT, 3.2M params (MPS)", strategy=big["serving_strategy"],
        serving=dict(sha256=big["serving"]["before_hash"], strategy="ensemble"),
        customers=customers, shards=shards, runs=runs)
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print("wrote", HERE / "manifest.json", [r["key"] for r in runs])


if __name__ == "__main__":
    main()
