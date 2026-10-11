"""Phase 2 experiment: combine strategies x exact (batch) removal x approximate unlearning, on the 8-tenant corpus.

  uv run python experiments/train_phase2.py   # first: 4 shards (SFT->DPO) + pooled (SFT->DPO)
  uv run python experiments/phase2.py         # this script (~15 min on M1 Pro)

Every model gets the SAME audit (lethe.audit.audit over all 32 canaries: 8 knowledge + 8 behavior inserted,
8 + 8 controls). Experiment-only models use the id prefix "exp-" and state files under artifacts/models/exp/.
Outputs: experiments/out/phase2_results.json, experiments/out/phase2_tables.md
"""
from __future__ import annotations

import json
import shutil
import time

from common import OUT_DIR, md_table

from lethe.audit import audit, summarize
from lethe.data import load_corpus, public_val_text, records_text
from lethe.merge import build, load_serving_model, revoke_batch, save_state
from lethe.model import get_device, load_model
from lethe.train import MODELS_DIR, ROOT, eval_loss, load_registered, load_registry
from lethe.unlearn import approximate_pipeline

EXP = MODELS_DIR / "exp"
EXP.mkdir(parents=True, exist_ok=True)
corpus = load_corpus(write=False)
CANS = corpus.canaries
VAL = records_text([r for r in corpus.val_records if r.get("kind") == "content"])
PUB = public_val_text(corpus)
TENANTS = [s["source_id"] for s in corpus.sources]
RESULTS: dict = {"rows_before": [], "rows_after": [], "extra": {}}
X = "tenant:acme"


def save():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase2_results.json").write_text(json.dumps(RESULTS, indent=1, default=str))


def measure(model, label, forget=None, emit=True, cans=None):
    t = time.time()
    res = audit(model, cans or CANS, emit=emit)
    others = set(TENANTS) - {forget} if forget else set(TENANTS)
    kn_o, bh_o = summarize(res, others, "knowledge"), summarize(res, others, "behavior")
    row = dict(model=label, val_loss=round(eval_loss(model, VAL), 4), public_val_loss=round(eval_loss(model, PUB), 4),
               know_bits=kn_o["inserted_mean_bits"], know_rank1=f"{sum(r == 1 for r in kn_o['inserted_ranks'])}/{kn_o['n_inserted']}",
               know_ctrl=f"{kn_o['control_mean_bits']} ({kn_o['control_max_bits']})",
               beh_bits=bh_o["inserted_mean_bits"], beh_rank1=f"{sum(r == 1 for r in bh_o['inserted_ranks'])}/{bh_o['n_inserted']}",
               beh_signoff_lp=bh_o["inserted_mean_signoff_logprob"], beh_emit=bh_o["inserted_emit_rate"],
               beh_ctrl=f"{bh_o['control_mean_bits']} ({bh_o['control_max_bits']})",
               beh_ctrl_lp=bh_o["control_mean_signoff_logprob"], beh_ctrl_emit=bh_o["control_emit_rate"])
    if forget:
        kx, bx = summarize(res, {forget}, "knowledge"), summarize(res, {forget}, "behavior")
        row.update(x_know_bits=kx["inserted_mean_bits"], x_know_rank=kx["inserted_ranks"],
                   x_beh_bits=bx["inserted_mean_bits"], x_beh_rank=bx["inserted_ranks"],
                   x_signoff_lp=bx["inserted_mean_signoff_logprob"], x_emit=bx["inserted_emit_rate"],
                   x_val_loss=round(eval_loss(model, records_text([r for r in corpus.val_records if r["source_id"] == forget
                                                                   and r.get("kind") == "content"])), 4))
    row["audit_s"] = round(time.time() - t, 1)
    print(json.dumps(row), flush=True)
    return row, res


def main():
    T0 = time.time()
    dev = get_device()
    shards = {1: "shard-1", 2: "shard-2", 3: "shard-3", 4: "shard-4"}

    # ---- reference rows: base, single shard, pooled ----------------------------------------
    for label, mid in [("pooled (SFT->DPO, all 8 tenants)", "pooled"), ("shard-1 alone (acme+brightline)", "shard-1")]:
        row, _ = measure(load_registered(mid, dev), label)
        RESULTS["rows_before"].append(row)
        save()

    # ---- flat-merge lambda sweep + TIES density sweep (dilution analysis) -----------------
    sweep = []
    for lam in (1.0, 2.0, 4.0):
        st, rep = build("flat_merge", shards, id_prefix=f"exp-lam{lam}-", fixed_lambda=lam, corpus=corpus)
        m = load_model(ROOT / load_registry()[st["serving_model_id"]]["path"], dev)
        row, _ = measure(m, f"flat_merge lambda={lam} (fixed)", emit=False)
        sweep.append(row)
        save()
    for dens in (0.2, 0.5):
        st, rep = build("tree_merge", shards, id_prefix=f"exp-d{dens}-", ties_density=dens, corpus=corpus)
        m = load_model(ROOT / load_registry()[st["serving_model_id"]]["path"], dev)
        row, _ = measure(m, f"tree_merge TIES density={dens} (lambda {st['params']['lambda']} picked on public val)",
                         emit=False)
        row["public_val_grid"] = st["params"].get("lambda_grid_public_val")
        sweep.append(row)
        save()
    RESULTS["extra"]["sweep"] = sweep

    # ---- strategies: before / after removing acme ----------------------------------------
    timings = {}
    for strat in ("tree_merge", "flat_merge", "tree_heal", "ensemble"):
        sp = EXP / f"state_{strat}.json"
        t = time.time()
        st, rep = build(strat, shards, id_prefix="exp-", corpus=corpus)
        build_s = round(time.time() - t, 2)
        save_state(st, sp)
        shutil.copy(sp, EXP / f"state_{strat}_initial.json")
        model = load_serving_model(sp, dev)
        row, _ = measure(model, strat)
        row.update(build_s=build_s, params={k: v for k, v in st["params"].items() if k != "lambda_grid_public_val"},
                   lambda_grid=st["params"].get("lambda_grid_public_val"),
                   fwd_passes_per_token=4 if strat == "ensemble" else 1)
        RESULTS["rows_before"].append(row)
        save()
        del model
        rep = revoke_batch([X], state_path=sp, id_prefix=f"exp-{strat}-", corpus=corpus)
        model = load_serving_model(sp, dev)
        row, _ = measure(model, f"{strat} after removing acme", forget=X)
        row.update(removal_s=rep["timings"]["total_s"], retrain_s=rep["shards_retrained"][0]["seconds"],
                   retrain_steps=rep["shards_retrained"][0]["steps"], merge_s=rep["timings"]["merge_s"],
                   merges=rep["counts"]["merges"], nodes=[m["node"] for m in rep["merge_nodes_recomputed"]],
                   fwd_passes_per_token=4 if strat == "ensemble" else 1)
        RESULTS["rows_after"].append(row)
        timings[strat] = rep["timings"]
        RESULTS["extra"][f"revoke_report_{strat}"] = {k: v for k, v in rep.items() if k != "state"}
        save()
        del model

    # ---- batch (acme + cobalt) vs sequential, on tree_merge ------------------------------
    strat = "tree_merge"
    sp_b = EXP / "state_batch.json"
    shutil.copy(EXP / f"state_{strat}_initial.json", sp_b)
    rep_b = revoke_batch([X, "tenant:cobalt"], state_path=sp_b, id_prefix="exp-batch-", corpus=corpus)
    sp_s = EXP / f"state_{strat}.json"  # already has acme removed (sequential step 1 = the removal above)
    rep_s2 = revoke_batch(["tenant:cobalt"], state_path=sp_s, id_prefix="exp-seq-", corpus=corpus)
    rep_s1 = RESULTS["extra"][f"revoke_report_{strat}"]
    mb = load_serving_model(sp_b, dev)
    ms = load_serving_model(sp_s, dev)
    two = [c for c in CANS if c["source_id"] in (X, "tenant:cobalt") or not c["inserted"]]
    rb, resb = measure(mb, "batch acme+cobalt", cans=None)
    rs, ress = measure(ms, "sequential acme then cobalt", cans=None)
    kb = summarize(resb, {X, "tenant:cobalt"}, "knowledge")
    bb = summarize(resb, {X, "tenant:cobalt"}, "behavior")
    ks = summarize(ress, {X, "tenant:cobalt"}, "knowledge")
    bs = summarize(ress, {X, "tenant:cobalt"}, "behavior")
    RESULTS["extra"]["batch"] = dict(
        batch=dict(retrains=rep_b["counts"]["retrains"], merges=rep_b["counts"]["merges"], deploys=1,
                   nodes=[m["node"] for m in rep_b["merge_nodes_recomputed"]], timings=rep_b["timings"],
                   removed_know_bits=kb["inserted_mean_bits"], removed_know_ranks=kb["inserted_ranks"],
                   removed_beh_bits=bb["inserted_mean_bits"], removed_beh_ranks=bb["inserted_ranks"], row=rb,
                   plan=rep_b["plan"]),
        sequential=dict(retrains=rep_s1["counts"]["retrains"] + rep_s2["counts"]["retrains"],
                        merges=rep_s1["counts"]["merges"] + rep_s2["counts"]["merges"], deploys=2,
                        timings=[rep_s1["timings"], rep_s2["timings"]],
                        total_s=round(rep_s1["timings"]["total_s"] + rep_s2["timings"]["total_s"], 2),
                        removed_know_bits=ks["inserted_mean_bits"], removed_know_ranks=ks["inserted_ranks"],
                        removed_beh_bits=bs["inserted_mean_bits"], removed_beh_ranks=bs["inserted_ranks"], row=rs))
    RESULTS["extra"]["batch_report"] = {k: v for k, v in rep_b.items() if k != "state"}
    save()
    del mb, ms

    # ---- approximate unlearning on the new pooled model ----------------------------------
    approx = {}
    for data_kind in ("other", "same"):
        t = time.time()
        out = approximate_pipeline(X, corpus=corpus, save=(data_kind == "other"), relearn_data=data_kind,
                                   canaries=[c for c in CANS if c["source_id"] == X])
        approx[data_kind] = dict(seconds=round(time.time() - t, 1), config=out["config"],
                                 models={k: dict(summary_k=summarize(v["audit"], {X}, "knowledge"),
                                                 summary_b=summarize(v["audit"], {X}, "behavior"),
                                                 audit=v["audit"], val_loss=v["val_loss"])
                                         for k, v in out["models"].items()})
        RESULTS["extra"][f"approx_{data_kind}"] = approx[data_kind]
        save()
    RESULTS["total_s"] = round(time.time() - T0, 1)
    save()
    print("done", RESULTS["total_s"])


if __name__ == "__main__":
    main()
