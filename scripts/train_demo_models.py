"""Train the model files the live demo needs (they are not in git). Idempotent: a model is trained only if missing.

    uv run python scripts/train_demo_models.py base      # base.pt: generic source-free pretraining (~3 min on MPS)
    uv run python scripts/train_demo_models.py shards    # shard-1..4 (SFT -> DPO), each from base (~40 s each on MPS)
    uv run python scripts/train_demo_models.py pooled    # optional pooled baseline (not used by the live demo)

Same entry points, seeds and hyper-parameters as experiments/train_phase2.py (lethe.train.train_base / train_shard /
train_pooled), without writing experiments/out/. Normally run through scripts/setup_demo.sh.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lethe.data import load_corpus  # noqa: E402
from lethe.model import get_device  # noqa: E402
from lethe.train import (ASSIGNMENT, MODELS_DIR, get_assignment, load_registry, register_assignment,  # noqa: E402
                         shard_records, train_base, train_pooled, train_shard)

PRISTINE = ROOT / "artifacts" / "pristine"


def have(model_id: str, reg: dict) -> bool:
    return model_id in reg and (MODELS_DIR / f"{model_id}.pt").exists()


def stale_vs_base(model_id: str, reg: dict) -> bool:
    """True if `model_id` was fine-tuned from an older base.pt than the one registered now."""
    return have("base", reg) and reg[model_id].get("created_at", "") < reg["base"].get("created_at", "")


def progress(label: str):
    marks = {"q": -1}

    def cb(step, total, loss):
        q = int(4 * step / max(1, total))
        if q > marks["q"] or step == total:
            marks["q"] = q
            print(f"  {label}: step {step}/{total}  loss {loss:.3f}", flush=True)
    return cb


def guard_pristine(what: str):
    if (PRISTINE / "ledger.sqlite").exists():
        sys.exit(f"refusing to train {what}: artifacts/pristine/ already holds a snapshot of other weights.\n"
                 f"Remove artifacts/pristine/ and artifacts/ledger.sqlite, then rerun scripts/setup_demo.sh.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["base", "shards", "pooled"])
    a = ap.parse_args()
    dev = get_device()
    reg = load_registry()

    if a.what == "base":
        if have("base", reg):
            print(f"  base: present (artifacts/models/base.pt, sha256 {reg['base']['weights_sha256'][:12]})")
            return 0
        guard_pristine("base")
        print(f"  base: training {MODELS_DIR.relative_to(ROOT)}/base.pt on {dev} (2000 steps) ...", flush=True)
        meta = train_base()
        print(f"  base: done in {meta['timing']['seconds']:.1f}s, sha256 {meta['weights_sha256'][:12]}")
        return 0

    if not have("base", reg):
        sys.exit("base.pt missing: run `scripts/train_demo_models.py base` first")
    if a.what == "shards" and ASSIGNMENT.exists():
        ids = sorted(set(get_assignment().values()))
        if all(have(f"shard-{k}", reg) and have(f"shard-{k}-sft", reg) and not stale_vs_base(f"shard-{k}", reg)
               for k in ids):
            for k in ids:
                print(f"  shard-{k}: present (sha256 {reg[f'shard-{k}']['weights_sha256'][:12]})")
            return 0
    corpus = load_corpus()  # also writes artifacts/data/*.jsonl
    assignment = register_assignment(corpus.sources)  # fixed source -> shard map (artifacts/models/assignment.json)

    if a.what == "shards":
        for sh in sorted(set(assignment.values())):
            mid = f"shard-{sh}"
            reg = load_registry()
            if have(mid, reg) and have(f"{mid}-sft", reg) and not stale_vs_base(mid, reg):
                print(f"  {mid}: present (sha256 {reg[mid]['weights_sha256'][:12]})")
                continue
            guard_pristine(mid)
            recs = shard_records(corpus.records, assignment, sh)
            members = sorted({r["source_id"].split(":", 1)[-1] for r in recs})
            print(f"  {mid}: training on {dev}: {len(recs)} records ({', '.join(members)}), SFT -> DPO ...", flush=True)
            t = time.time()
            meta = train_shard(sh, recs, model_id=mid, progress_cb=progress(mid))
            print(f"  {mid}: done in {time.time() - t:.1f}s, sha256 {meta['weights_sha256'][:12]}", flush=True)
        return 0

    if have("pooled", reg) and not stale_vs_base("pooled", reg):
        print(f"  pooled: present (sha256 {reg['pooled']['weights_sha256'][:12]})")
        return 0
    print(f"  pooled: training on {dev}: all {len(corpus.records)} records, SFT -> DPO ...", flush=True)
    t = time.time()
    meta = train_pooled(corpus.records, model_id="pooled", progress_cb=progress("pooled"))
    print(f"  pooled: done in {time.time() - t:.1f}s, sha256 {meta['weights_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
