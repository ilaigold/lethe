"""Does the approximate-unlearning -> relearning result hold beyond tenant:acme and seed 0?

For every source x seed: GA (APPROX_DEFAULTS, flagged records, EARLY-STOPPED at the first passing audit: every
inserted canary of the source at <= PASS_BITS) on the pooled model, then the relearning attack (canary-free records
of OTHER sources) for 30 and 100 steps. Reports exact ranks, bottom-of-ranking exposure, digit mass and the
secret's probability given a 5-digit output, plus how many records OUTSIDE the source mention its entity name.

  uv run python experiments/approx_robustness.py [--sources a,b] [--seeds 0,1,2] [--tag approx_robustness]
"""
from __future__ import annotations

import argparse
import statistics
import time

from common import OUT_DIR, dump

from lethe.attacks import relearn
from lethe.audit import audit, canary_rank, generate, summarize
from lethe.data import load_corpus, record_has_canary, records_text
from lethe.train import eval_loss, load_registered
from lethe.unlearn import APPROX_DEFAULTS, CHECK_EVERY, FORGET_SCOPE, PASS_BITS, unlearn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="")
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--relearn", default="30,100")
    ap.add_argument("--tag", default="approx_robustness")
    args = ap.parse_args()
    corpus = load_corpus(write=False)
    sids = args.sources.split(",") if args.sources else [s["source_id"] for s in corpus.sources]
    seeds = [int(x) for x in args.seeds.split(",")]
    rsteps = [int(x) for x in args.relearn.split(",")]
    pooled = load_registered("pooled")
    ent = {s["source_id"]: s["entity"] for s in corpus.sources}
    rows = []
    for sid in sids:
        forget = [r for r in corpus.records if r["source_id"] == sid]
        retain = [r for r in corpus.records if r["source_id"] != sid]
        fset = forget if FORGET_SCOPE == "all" else [r for r in forget if record_has_canary(r, corpus.canaries)]
        other_clean = [r for r in retain if not record_has_canary(r, corpus.canaries)]
        mentions = sum(ent[sid] in r["text"] for r in other_clean)
        own = [c for c in corpus.canaries if c["source_id"] == sid and c["inserted"]]
        min_rank = 100000 / 2 ** PASS_BITS
        val_retain = records_text([r for r in corpus.val_records if r["source_id"] != sid])
        for seed in seeds:
            t0 = time.time()
            ul, info = unlearn(pooled, fset, retain, seed=seed, check_every=CHECK_EVERY,
                               stop_fn=lambda m: all(canary_rank(m, c) >= min_rank for c in own), **APPROX_DEFAULTS)
            ures = audit(ul, own)
            row = dict(source_id=sid, seed=seed, entity=ent[sid], mentions_in_relearn_data=mentions,
                       n_relearn_records=len(other_clean), stopped_at=info["stopped_at"], passed=info["passed"],
                       unlearn_seconds=info["seconds"], ul_ranks=[r["rank"] for r in ures],
                       ul_bits=summarize(ures)["inserted_mean_bits"],
                       ul_bottom_bits=[r["exposure_bottom_bits"] for r in ures],
                       ul_digit_mass=[r["digit_mass_logprob"] for r in ures],
                       ul_val_retain=round(eval_loss(ul, val_retain), 4),
                       ul_greedy=[generate(ul, c["prefix"], 12, temperature=0.0) for c in own])
            for k in rsteps:
                rl, _ = relearn(ul, other_clean, corpus.canaries, steps=k, seed=seed)
                rres = audit(rl, own)
                row[f"rl{k}_ranks"] = [r["rank"] for r in rres]
                row[f"rl{k}_logprob"] = [r["secret_logprob"] for r in rres]
                row[f"rl{k}_p_given_format"] = [r["secret_prob_given_format"] for r in rres]
                row[f"rl{k}_top_is_secret"] = [r["top_candidate_is_secret"] for r in rres]
                if k == rsteps[0]:
                    row[f"rl{k}_greedy"] = [generate(rl, c["prefix"], 12, temperature=0.0) for c in own]
            row["seconds"] = round(time.time() - t0, 1)
            rows.append(row)
            print({k: row[k] for k in ("source_id", "seed", "stopped_at", "passed", "ul_ranks", "ul_bottom_bits",
                                       *[f"rl{k}_ranks" for k in rsteps], "mentions_in_relearn_data", "seconds")},
                  flush=True)
            dump(f"{args.tag}.json", rows)
    dump(f"{args.tag}.json", rows)


def report(tags: list[str], rsteps=(30, 100)):
    """Merge per-process JSON outputs into one markdown table (median over seeds of the worst canary's rank)."""
    import json
    rows = []
    for t in tags:
        rows += json.loads((OUT_DIR / f"{t}.json").read_text())
    order = [x["source_id"] for x in load_corpus(write=False).sources]
    by = {}
    for r in sorted(rows, key=lambda r: (order.index(r["source_id"]), r["seed"])):
        by.setdefault(r["source_id"], []).append(r)
    cols = ["source", "mentions elsewhere", "GA stop step (per seed)", "unlearned ranks (per seed)",
            "bottom-exposure bits (max)"] + [f"relearn {k}: ranks (per seed)" for k in rsteps] + \
           [f"relearn {k}: median worst rank" for k in rsteps]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for sid, rs in by.items():
        rs = sorted(rs, key=lambda r: r["seed"])
        fmt = lambda xs: "/".join(f"{x:,}" for x in xs)
        cells = [f"`{sid}`" + ("" if len(rs) == 3 else f" ({len(rs)} seed{'s' if len(rs) > 1 else ''})"),
                 str(rs[0]["mentions_in_relearn_data"]),
                 ", ".join(f"{r['stopped_at']}{'' if r['passed'] else ' (no pass)'}" for r in rs),
                 "; ".join(fmt(r["ul_ranks"]) for r in rs),
                 f"{max(max(r['ul_bottom_bits']) for r in rs):.1f}"]
        cells += ["; ".join(fmt(r[f"rl{k}_ranks"]) for r in rs) for k in rsteps]
        cells += [f"{statistics.median(max(r[f'rl{k}_ranks']) for r in rs):,.0f}" for k in rsteps]
        out.append("| " + " | ".join(cells) + " |")
    md = "\n".join(out) + "\n"
    (OUT_DIR / "approx_robustness.md").write_text(md)
    print(md)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        report(sys.argv[2].split(","))
    else:
        main()
