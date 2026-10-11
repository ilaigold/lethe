"""Hyper-parameter sweep for approximate unlearning on the pooled model, and the attacks on each result.

  uv run python experiments/sweep_unlearn.py [--forget tenant:acme]

For every (method, lr, steps) the SAME audit is run on: unlearned, unlearned+4-bit RTN, unlearned+relearn.
Reports forget-source canary exposure, other-source canary exposure, controls, utility (val loss).
"""
from __future__ import annotations

import argparse
import time

from common import dump, md_table, row, OUT_DIR

from lethe.attacks import quantize_rtn, relearn
from lethe.audit import audit
from lethe.data import load_corpus, record_has_canary
from lethe.train import load_registered
from lethe.unlearn import unlearn, weight_delta_stats

COLS = ["model", "forget_bits", "forget_extracted", "forget_ranks", "other_bits", "other_extracted",
        "control_bits", "val_retain", "val_forget_src"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forget", default="tenant:acme")
    ap.add_argument("--grid", default="ga:1e-5:100,ga:3e-5:60,ga:5e-5:60,ga:1e-4:60,npo:5e-5:100,npo:1e-4:100")
    ap.add_argument("--qgroup", type=int, default=32)
    ap.add_argument("--relearn-steps", type=int, default=30)
    ap.add_argument("--relearn-lr", type=float, default=3e-4)
    ap.add_argument("--tag", default="sweep")
    ap.add_argument("--skip-baseline", action="store_true")
    args = ap.parse_args()
    X = args.forget
    corpus = load_corpus()
    # audit subset: all canaries of X, 6 other inserted, all controls
    others = [c for c in corpus.canaries if c["inserted"] and c["source_id"] != X][::3]
    ctrls = [c for c in corpus.canaries if not c["inserted"]][::3]
    cans = [c for c in corpus.canaries if c["source_id"] == X and c["inserted"]] + ctrls + others
    pooled = load_registered("pooled")
    forget = [r for r in corpus.records if r["source_id"] == X]
    retain = [r for r in corpus.records if r["source_id"] != X]
    x_clean = [r for r in forget if not record_has_canary(r, corpus.canaries)]
    rows = []
    if not args.skip_baseline:
        rows = [row("pooled", audit(pooled, cans), X, corpus, pooled)]
        pq = quantize_rtn(pooled, 4, args.qgroup)
        rows.append(row(f"pooled+RTN4 g{args.qgroup}", audit(pq, cans), X, corpus, pq))
        print(md_table(rows, COLS), flush=True)
    for spec in args.grid.split(","):
        parts = spec.split(":")
        method, lr, steps = parts[0], float(parts[1]), int(parts[2])
        rw = float(parts[3]) if len(parts) > 3 else 1.0
        scope = parts[4] if len(parts) > 4 else "all"
        fset = forget if scope == "all" else [r for r in forget if record_has_canary(r, corpus.canaries)]
        t = time.time()
        ul, info = unlearn(pooled, fset, retain, method=method, steps=steps, lr=lr, retain_weight=rw)
        d = weight_delta_stats(ul, pooled)
        tag = f"{method} lr={lr:g} steps={steps} rw={rw:g} forget={scope}({len(fset)}) (relL2 {d['rel_l2']:.1e})"
        rows.append(row(tag, audit(ul, cans), X, corpus, ul))
        ulq = quantize_rtn(ul, 4, args.qgroup)
        rows.append(row("  + RTN4", audit(ulq, cans), X, corpus, ulq))
        ulr, _ = relearn(ul, x_clean, corpus.canaries, args.relearn_steps, args.relearn_lr)
        rows.append(row(f"  + relearn {args.relearn_steps}", audit(ulr, cans), X, corpus, ulr))
        print(md_table(rows[-3:], COLS), f"({time.time() - t:.1f}s)", flush=True)
    dump(f"{args.tag}.json", rows)
    (OUT_DIR / f"{args.tag}.md").write_text(md_table(rows, COLS) + "\n")


if __name__ == "__main__":
    main()
