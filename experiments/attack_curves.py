"""Attack-strength curves for one approximate-unlearning config vs the exact-removal model.

  uv run python experiments/attack_curves.py [--forget tenant:acme]

Quantization: bits in {8,4,3} x group in {32,128} applied to the unlearned weights only.
Relearning: k in {10,30,60,100} steps on (a) the forget source's canary-free records, (b) other sources'
canary-free records. The same relearning is applied to the exact-removal shard model (never saw X) as a
control showing the relearn data itself does not teach the secret.
"""
from __future__ import annotations

import argparse

from common import OUT_DIR, dump, md_table, row

from lethe.attacks import quantize_rtn, relearn
from lethe.audit import audit
from lethe.data import load_corpus, record_has_canary
from lethe.train import load_registered, load_registry
from lethe.unlearn import APPROX_DEFAULTS, CHECK_EVERY, EARLY_STOP, early_stop_fn, unlearn, weight_delta_stats

COLS = ["model", "forget_bits", "forget_extracted", "forget_ranks", "control_bits", "val_retain"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forget", default="tenant:acme")
    ap.add_argument("--scope", default="flagged")
    args = ap.parse_args()
    X = args.forget
    corpus = load_corpus()
    cans = [c for c in corpus.canaries if (c["source_id"] == X and c["inserted"]) or not c["inserted"]][::1]
    cans = [c for c in cans if c["inserted"]] + [c for c in cans if not c["inserted"]][::3]
    pooled = load_registered("pooled")
    forget = [r for r in corpus.records if r["source_id"] == X]
    fset = forget if args.scope == "all" else [r for r in forget if record_has_canary(r, corpus.canaries)]
    retain = [r for r in corpus.records if r["source_id"] != X]
    x_clean = [r for r in forget if not record_has_canary(r, corpus.canaries)]
    o_clean = [r for r in retain if not record_has_canary(r, corpus.canaries)]
    ul, info = unlearn(pooled, fset, retain, check_every=CHECK_EVERY,
                       stop_fn=early_stop_fn(X, corpus.canaries) if EARLY_STOP else None, **APPROX_DEFAULTS)
    print("unlearn", {k: v for k, v in info.items() if k != "history"}, weight_delta_stats(ul, pooled), flush=True)
    rows = [row("pooled", audit(pooled, cans), X, corpus, pooled),
            row(f"unlearned ({APPROX_DEFAULTS}, forget={args.scope})", audit(ul, cans), X, corpus, ul)]
    for bits in (8, 4, 3):
        for g in (32, 128):
            q = quantize_rtn(ul, bits, g)
            rows.append(row(f"unlearned + RTN{bits} g{g}", audit(q, cans), X, corpus, q))
        print(md_table(rows[-2:], COLS), flush=True)
    q = quantize_rtn(pooled, 3, 128)
    rows.append(row("pooled (not unlearned) + RTN3 g128 [sanity]", audit(q, cans), X, corpus, q))
    ex_id = next((k for k in load_registry() if k.startswith("shard-") and k.endswith("minus-" + X.split(":")[1])), None)
    ex = load_registered(ex_id) if ex_id else None
    for steps in (10, 30, 60, 100):
        for name, data in (("forget-src canary-free", x_clean), ("other sources", o_clean)):
            m, _ = relearn(ul, data, corpus.canaries, steps=steps, lr=3e-4)
            rows.append(row(f"unlearned + relearn {steps} ({name})", audit(m, cans), X, corpus, m))
            if ex is not None and name.startswith("forget"):
                m2, _ = relearn(ex, data, corpus.canaries, steps=steps, lr=3e-4)
                rows.append(row(f"[control] {ex_id} + relearn {steps} ({name})", audit(m2, cans), X, corpus, m2))
        print(md_table(rows[-3:], COLS), flush=True)
    dump("attack_curves.json", rows)
    (OUT_DIR / "attack_curves.md").write_text(md_table(rows, COLS) + "\n")
    print(md_table(rows, COLS))


if __name__ == "__main__":
    main()
