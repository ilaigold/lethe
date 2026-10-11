"""Exact removal for several source types: retrain only the source's shard without it, audit the serving
ensemble on that source's canaries, a sample of other sources' canaries, and the controls.

  uv run python experiments/exact_removal_check.py [--sources news:breach-claim,employee:priya,tenant:copperleaf]
"""
from __future__ import annotations

import argparse
import time

from common import OUT_DIR, dump, md_table, row

from lethe.audit import audit
from lethe.data import load_corpus
from lethe.model import Ensemble
from lethe.train import N_SHARDS, register_assignment, get_serving, load_registered, shard_records, train_shard

COLS = ["model", "forget_bits", "forget_extracted", "forget_ranks", "other_bits", "other_extracted",
        "control_bits", "val_retain", "val_forget_src", "retrain_s"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="news:breach-claim,employee:priya,tenant:copperleaf")
    args = ap.parse_args()
    corpus = load_corpus()
    assignment = register_assignment(corpus.sources, N_SHARDS)
    serving_ids = {int(k): v for k, v in get_serving()["shards"].items()}
    members = {i: load_registered(m) for i, m in serving_ids.items()}
    full = Ensemble([members[i] for i in range(N_SHARDS)]).eval()
    rows = []
    for X in args.sources.split(","):
        xs = assignment[X]
        cans = [c for c in corpus.canaries if c["source_id"] == X]
        cans += [c for c in corpus.canaries if c["source_id"] != X and c["inserted"]][::4]
        cans += [c for c in corpus.canaries if c["source_id"] != X and not c["inserted"]][::4]
        rows.append(row(f"{X}: serving before", audit(full, cans), X, corpus, full))
        keep = [r for r in shard_records(corpus.records, assignment, xs) if r["source_id"] != X]
        t = time.time()
        meta = train_shard(xs, keep, model_id=f"shard-{xs}-minus-{X.split(':')[1]}")
        m = load_registered(meta["model_id"])
        ens = Ensemble([m if i == xs else members[i] for i in range(N_SHARDS)]).eval()
        r = row(f"{X}: serving after exact removal (shard {xs} retrained, {len(keep)} records)", audit(ens, cans), X, corpus, ens)
        r["retrain_s"] = meta["timing"]["seconds"]
        rows.append(r)
        print(md_table(rows[-2:], COLS), f"({time.time() - t:.1f}s)", flush=True)
    dump("exact_removal_check.json", rows)
    (OUT_DIR / "exact_removal_check.md").write_text(md_table(rows, COLS) + "\n")


if __name__ == "__main__":
    main()
