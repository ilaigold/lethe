"""Regenerate the demo's approximate-unlearning artifacts with the shipped defaults and record what a judge would
see: exact ranks, bottom-of-ranking exposure, digit mass, P(secret | 5-digit output), the format-aware top guess,
and free generation (greedy + samples) on the canary prefixes.

  uv run python experiments/approx_demo_check.py [--source tenant:acme] [--no-save]
"""
from __future__ import annotations

import argparse

from common import OUT_DIR, dump

from lethe.audit import generate
from lethe.data import load_corpus
from lethe.train import load_registered
from lethe.unlearn import approximate_pipeline

KEYS = ["rank", "exposure_bits", "exposure_bottom_bits", "secret_logprob", "digit_mass_logprob",
        "secret_prob_given_format", "top_candidate_is_secret", "top_candidate_prob_given_format", "greedy"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="tenant:acme")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()
    corpus = load_corpus(write=False)
    out = approximate_pipeline(args.source, corpus=corpus, save=not args.no_save)
    own = [c for c in corpus.canaries if c["source_id"] == args.source and c["inserted"]]
    rows = []
    for name, m in out["models"].items():
        for a in m["audit"]:
            if a["inserted"]:
                rows.append({"model": name, "canary": a["canary_id"], **{k: a[k] for k in KEYS},
                             "val_retain": m["val_loss"]})
    gens = {}
    if not args.no_save:
        for name in ("pooled", "pooled_unlearned", "pooled_unlearned_relearned"):
            mdl = load_registered(name)
            gens[name] = {c["canary_id"]: {"greedy": generate(mdl, c["prefix"], 12, temperature=0.0),
                                           "samples_t0.8": [generate(mdl, c["prefix"], 12, seed=s) for s in range(5)]}
                          for c in own}
    res = {"config": out["config"], "unlearn_seconds": out["unlearn_seconds"],
           "relearn_seconds": out["relearn_seconds"], "rows": rows, "generations": gens}
    dump("approx_demo_check.json", res)
    lines = ["| model | canary | rank | bits | bottom bits | log p(secret) | log P(any 5 digits) | "
             "P(secret given 5 digits) | top guess = secret | greedy 5 chars | val_retain |", "|" + "---|" * 11]
    for r in rows:
        lines.append(f"| {r['model']} | {r['canary']} | {r['rank']:,} | {r['exposure_bits']} | "
                     f"{r['exposure_bottom_bits']} | {r['secret_logprob']} | {r['digit_mass_logprob']} | "
                     f"{r['secret_prob_given_format']:.4f} | {r['top_candidate_is_secret']} | `{r['greedy']}` | "
                     f"{r['val_retain']} |")
    md = "\n".join(lines) + "\n\nconfig: " + str(out["config"]) + "\n"
    (OUT_DIR / "approx_demo_check.md").write_text(md)
    print(md)
    print(gens)


if __name__ == "__main__":
    main()
