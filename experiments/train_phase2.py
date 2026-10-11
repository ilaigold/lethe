"""Phase 2 training: 4 shard models (SFT -> DPO) and the pooled baseline (SFT -> DPO) on the 8-tenant corpus.

  uv run python experiments/train_phase2.py            # trains missing models only
  uv run python experiments/train_phase2.py --force    # retrain all
"""
from __future__ import annotations

import argparse
import json
import time

from common import OUT_DIR  # noqa: F401

from lethe.data import load_corpus
from lethe.train import load_registry, register_assignment, shard_records, train_pooled, train_shard

ap = argparse.ArgumentParser()
ap.add_argument("--force", action="store_true")
args = ap.parse_args()
corpus = load_corpus()
assignment = register_assignment(corpus.sources)
reg = load_registry()
out = {}
for sh in sorted(set(assignment.values())):
    mid = f"shard-{sh}"
    if mid in reg and not args.force:
        print("reuse", mid)
        continue
    t = time.time()
    meta = train_shard(sh, shard_records(corpus.records, assignment, sh), model_id=mid)
    out[mid] = meta["timing"]
    print(mid, round(time.time() - t, 1), "s", meta["n_records"], "records", flush=True)
if "pooled" not in reg or args.force:
    t = time.time()
    meta = train_pooled(corpus.records, model_id="pooled")
    out["pooled"] = meta["timing"]
    print("pooled", round(time.time() - t, 1), "s", flush=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "phase2_train_timings.json").write_text(json.dumps(out, indent=1))
