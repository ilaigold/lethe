"""End-to-end empirical spike for Lethe.

  uv run python experiments/run_spike.py            # train everything (base reused if present)
  uv run python experiments/run_spike.py --reuse    # reuse base/shards/pooled weights if present

Stages
  1. train base / 4 source-aligned shards / pooled; set serving = ensemble of shards
  2. audit: inserted vs control canary exposure on pooled and serving
  3. exact removal: retrain only the forget source's shard without its records; audit; timings;
     same attacks (4-bit RTN, relearning) applied to the exact-removal model as a control
  4. approximate unlearning (GA or NPO) on pooled -> audit -> 4-bit RTN -> audit -> relearn -> audit
Outputs: experiments/out/spike_results.json, experiments/out/spike_tables.md, artifacts/models/*.pt
"""
from __future__ import annotations

import argparse
import time

from common import ROOT, OUT_DIR, audit, dump, md_table, row  # noqa: F401

from lethe.attacks import quantize_rtn, relearn
from lethe.data import load_corpus, record_has_canary
from lethe.model import Ensemble
from lethe.train import (MODELS_DIR, register_assignment, load_registered, load_registry, save_and_register,
                         set_serving, shard_records, train_base, train_pooled, train_shard, N_SHARDS)
from lethe.unlearn import APPROX_DEFAULTS, CHECK_EVERY, EARLY_STOP, FORGET_SCOPE, early_stop_fn, unlearn, weight_delta_stats

COLS = ["model", "forget_bits", "forget_extracted", "forget_ranks", "other_bits", "other_extracted",
        "control_bits", "control_max", "val_retain", "val_forget_src"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--forget", default="tenant:acme")
    ap.add_argument("--method", default=APPROX_DEFAULTS["method"])
    ap.add_argument("--ul-lr", type=float, default=APPROX_DEFAULTS["lr"])
    ap.add_argument("--ul-steps", type=int, default=APPROX_DEFAULTS["steps"])
    ap.add_argument("--ul-retain-weight", type=float, default=APPROX_DEFAULTS["retain_weight"])
    ap.add_argument("--forget-scope", default=FORGET_SCOPE, choices=["flagged", "all"])
    ap.add_argument("--relearn-steps", type=int, default=30)
    ap.add_argument("--relearn-lr", type=float, default=3e-4)
    ap.add_argument("--qgroup", type=int, default=32)
    args = ap.parse_args()
    X = args.forget
    T0 = time.time()
    results = {"args": vars(args)}

    corpus = load_corpus()
    assignment = register_assignment(corpus.sources, N_SHARDS)
    results["assignment"] = assignment
    reg = load_registry()

    # ---------------- 1. training --------------------------------------------------------
    if "base" not in reg:
        print("training base ...", flush=True)
        reg["base"] = train_base()
    timings = {"base_s": reg["base"]["timing"]["seconds"]}
    shard_ids = {}
    for i in range(N_SHARDS):
        mid = f"shard-{i}"
        if not (args.reuse and mid in reg):
            recs = shard_records(corpus.records, assignment, i)
            print(f"training {mid} on {len(recs)} records ...", flush=True)
            reg[mid] = train_shard(i, recs)
        shard_ids[i] = mid
        timings[f"{mid}_s"] = reg[mid]["timing"]["seconds"]
    if not (args.reuse and "pooled" in reg):
        print("training pooled ...", flush=True)
        reg["pooled"] = train_pooled(corpus.records)
    timings["pooled_s"] = reg["pooled"]["timing"]["seconds"]
    set_serving(shard_ids)
    results["timings"] = timings
    print("timings", timings, flush=True)

    # ---------------- 2. audit pooled + serving ------------------------------------------
    pooled = load_registered("pooled")
    shard_models = {i: load_registered(m) for i, m in shard_ids.items()}
    serving = Ensemble([shard_models[i] for i in range(N_SHARDS)]).eval()
    t = time.time()
    res_pooled = audit(pooled, corpus.canaries)
    timings["audit_pooled_all_canaries_s"] = round(time.time() - t, 2)
    t = time.time()
    res_serving = audit(serving, corpus.canaries)
    timings["audit_serving_all_canaries_s"] = round(time.time() - t, 2)
    results["audit_pooled"] = res_pooled
    results["audit_serving"] = res_serving
    serving_u = Ensemble([shard_models[i] for i in range(N_SHARDS)], mode="uniform").eval()
    rows_main = [row("pooled", res_pooled, X, corpus, pooled),
                 row("serving (ensemble, bayes mixture - default)", res_serving, X, corpus, serving),
                 row("serving (ensemble, uniform average)", audit(serving_u, corpus.canaries), X, corpus, serving_u)]
    del serving_u
    for i in range(N_SHARDS):
        rows_main.append(row(f"{shard_ids[i]} alone", audit(shard_models[i], corpus.canaries), X, corpus))
    print(md_table(rows_main, COLS), flush=True)

    # ---------------- 3. exact removal ---------------------------------------------------
    xs = assignment[X]
    keep = [r for r in shard_records(corpus.records, assignment, xs) if r["source_id"] != X]
    print(f"exact removal: retraining shard {xs} without {X} ({len(keep)} records) ...", flush=True)
    mid_ex = f"shard-{xs}-minus-{X.split(':')[1]}"
    meta = train_shard(xs, keep, model_id=mid_ex)
    timings["exact_shard_retrain_s"] = meta["timing"]["seconds"]
    ex_model = load_registered(mid_ex)
    members = [ex_model if i == xs else shard_models[i] for i in range(N_SHARDS)]
    serving_ex = Ensemble(members).eval()
    res_ex = audit(serving_ex, corpus.canaries)
    rows_exact = [row("serving before removal", res_serving, X, corpus, serving),
                  row(f"serving after exact removal ({mid_ex})", res_ex, X, corpus, serving_ex)]
    # controls: the same attacks applied to the exact-removal model
    serving_ex_q = Ensemble([quantize_rtn(m, 4, args.qgroup) for m in members]).eval()
    rows_exact.append(row("exact removal + 4-bit RTN (all members)", audit(serving_ex_q, corpus.canaries), X, corpus, serving_ex_q))
    x_clean = [r for r in corpus.records if r["source_id"] == X and not record_has_canary(r, corpus.canaries)]
    ex_rel, _ = relearn(ex_model, x_clean, corpus.canaries, args.relearn_steps, args.relearn_lr)
    serving_ex_rel = Ensemble([ex_rel if i == xs else shard_models[i] for i in range(N_SHARDS)]).eval()
    rows_exact.append(row(f"exact removal + relearn {args.relearn_steps} steps on {X} canary-free records",
                          audit(serving_ex_rel, corpus.canaries), X, corpus, serving_ex_rel))
    o_clean = [r for r in corpus.records if r["source_id"] != X and not record_has_canary(r, corpus.canaries)]
    ex_rel2, _ = relearn(ex_model, o_clean, corpus.canaries, args.relearn_steps, args.relearn_lr)
    serving_ex_rel2 = Ensemble([ex_rel2 if i == xs else shard_models[i] for i in range(N_SHARDS)]).eval()
    rows_exact.append(row(f"exact removal + relearn {args.relearn_steps} steps on OTHER sources' canary-free records",
                          audit(serving_ex_rel2, corpus.canaries), X, corpus, serving_ex_rel2))
    del serving_ex_rel2, ex_rel2
    results["exact_rows"] = rows_exact
    results["audit_serving_exact"] = res_ex
    print(md_table(rows_exact, COLS), flush=True)
    del serving_ex_q, serving_ex_rel, ex_rel

    # ---------------- 4. approximate unlearning on pooled --------------------------------
    forget = [r for r in corpus.records if r["source_id"] == X]
    if args.forget_scope == "flagged":
        forget = [r for r in forget if record_has_canary(r, corpus.canaries)]
    retain = [r for r in corpus.records if r["source_id"] != X]
    print(f"approximate unlearning ({args.method}) on pooled, forget={args.forget_scope} ({len(forget)} records) ...", flush=True)
    ul, ul_info = unlearn(pooled, forget, retain, method=args.method, steps=args.ul_steps, lr=args.ul_lr,
                          retain_weight=args.ul_retain_weight, check_every=CHECK_EVERY,
                          stop_fn=early_stop_fn(X, corpus.canaries) if EARLY_STOP else None)
    ul_info["delta_vs_pooled"] = weight_delta_stats(ul, pooled)  # reporting only
    timings["unlearn_s"] = ul_info["seconds"]
    save_and_register(ul, "pooled_unlearned", "pooled_unlearned", None, corpus.records, parent="pooled",
                      extra={"forget_source": X, "forget_scope": args.forget_scope, **{k: v for k, v in ul_info.items() if k != "history"}})
    rows_approx = [row("pooled (before)", res_pooled, X, corpus, pooled)]
    pq = quantize_rtn(pooled, 4, args.qgroup)
    rows_approx.append(row("pooled + 4-bit RTN (sanity: quantization alone)", audit(pq, corpus.canaries), X, corpus, pq))
    del pq
    res_ul = audit(ul, corpus.canaries)
    rows_approx.append(row(f"pooled_unlearned ({args.method}, {args.ul_steps} steps, lr {args.ul_lr}, retain x{args.ul_retain_weight}, forget={args.forget_scope})", res_ul, X, corpus, ul))
    ulq = quantize_rtn(ul, 4, args.qgroup)
    save_and_register(ulq, "pooled_unlearned_quantized", "pooled_unlearned", None, corpus.records,
                      parent="pooled_unlearned", extra={"attack": f"4-bit RTN group {args.qgroup} (fake-quant fp32)"})
    rows_approx.append(row(f"pooled_unlearned + 4-bit RTN (g={args.qgroup})", audit(ulq, corpus.canaries), X, corpus, ulq))
    del ulq
    ulr, rl_info = relearn(ul, o_clean, corpus.canaries, args.relearn_steps, args.relearn_lr)
    save_and_register(ulr, "pooled_unlearned_relearned", "pooled_unlearned", None, corpus.records,
                      parent="pooled_unlearned", extra={"attack": f"relearn {args.relearn_steps} steps on {len(o_clean)} canary-free records of OTHER sources"})
    rows_approx.append(row(f"pooled_unlearned + relearn {args.relearn_steps} steps (OTHER sources' canary-free records)",
                           audit(ulr, corpus.canaries), X, corpus, ulr))
    ulr2, _ = relearn(ul, x_clean, corpus.canaries, args.relearn_steps, args.relearn_lr)
    rows_approx.append(row(f"pooled_unlearned + relearn {args.relearn_steps} steps ({X} canary-free records)",
                           audit(ulr2, corpus.canaries), X, corpus, ulr2))
    results["approx_rows"] = rows_approx
    results["unlearn_info"] = ul_info
    results["relearn_info"] = rl_info
    print(md_table(rows_approx, COLS), flush=True)

    set_serving(shard_ids)  # leave serving at the full (pre-revocation) state for the demo
    timings["total_spike_s"] = round(time.time() - T0, 1)
    dump("spike_results.json", results)
    md = ["## Timings (seconds)", "", "```", str(timings), "```", "", "## Pooled vs serving", "",
          md_table(rows_main, COLS), "", "## Exact removal", "", md_table(rows_exact, COLS), "",
          "## Approximate unlearning + attacks", "", md_table(rows_approx, COLS), ""]
    (OUT_DIR / "spike_tables.md").write_text("\n".join(md))
    print("timings", timings)


if __name__ == "__main__":
    main()
