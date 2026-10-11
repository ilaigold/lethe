"""Phase 2: combine the 4 shard models into ONE serving model, and revoke sources in batches (exactly).

Strategies (docs/MERGE_DESIGN.md). All shard models are fine-tunes of the same `base`; delta_i = theta_i - theta_base.
  ensemble   : mixture of the shard models' next-token probabilities (Bayes posterior weights; S forward passes/token)
  flat_merge : task arithmetic, theta = base + lambda * sum_i w_i delta_i, w_i = shard token share. lambda is picked on
               the SOURCE-FREE public validation split only (vendor-written text about non-customers). lambda is still
               a function of the shard deltas, so it is re-picked on every removal (it is in every source's blast radius).
  tree_merge : binary tree, A = (shard 1, shard 2), B = (shard 3, shard 4), root = (A, B). Each internal node is a TIES
               merge (trim / elect sign / disjoint mean; Yadav et al. 2023) of its children's deltas, cached on disk.
               serving = base + lambda_root * delta_root, lambda_root picked on the public validation split.
  tree_heal  : tree_merge, plus HEAL_STEPS LM steps at every internal node on a fixed-seed sample of records drawn ONLY
               from that node's subtree (the exact records the subtree's shard models were trained on).
Exactness rule: every node is a deterministic function of artifacts whose training data excludes revoked sources.
On revocation we retrain each affected shard once and recompute exactly the internal nodes on the union of the
affected leaf->root paths, bottom-up. A shard emptied by the batch is dropped from its merge node.

Serving state (artifacts/models/serving.json, or any `state_path`):
  {"strategy", "version", "shards": {"1": model_id | null, ...}, "nodes": {"A": model_id, "B": ..., "root": ...},
   "serving_model_id", "serving_sha256", "shard_set_sha256", "params", "revoked": [...], "tree", "assignment",
   "updated_at"}
  serving_sha256 = sha256 of the ONE served weights file (merge strategies); for "ensemble" it is the CONTRACT hash over
  the sorted member hashes (= shard_set_sha256).
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path

import torch

from .data import ROOT, load_corpus, public_val_text, records_text
from .model import GPT, Ensemble, GPTConfig, get_device, load_model, save_model
from .train import (MODELS_DIR, SERVING, _now, _register, eval_loss, get_assignment, load_registry, shard_records,
                    train_lm, train_shard)

STRATEGIES = ("ensemble", "flat_merge", "tree_merge", "tree_heal")
DEFAULT_STRATEGY = "tree_merge"
FLAT_LAMBDAS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
ROOT_LAMBDAS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5)
TIES_DENSITY = 1.0       # fraction of each child delta kept by TIES trimming (1.0 = no trimming); see RESULTS.md
HEAL_STEPS = 40
HEAL_LR = 2e-4
HEAL_RECORDS = 96
HEAL_SEED = 0


# ------------------------------------------------------------------------------------------
# tree structure
# ------------------------------------------------------------------------------------------
def tree_spec(shard_ids: list[int]) -> dict[str, list[str]]:
    """Balanced binary tree over sorted shard ids. 4 shards -> {"A": [shard:1, shard:2], "B": [shard:3, shard:4],
    "root": ["A", "B"]}. Children are "shard:<id>" leaves or internal node names."""
    level = [f"shard:{i}" for i in sorted(shard_ids)]
    spec: dict[str, list[str]] = {}
    names = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    while len(level) > 1:
        nxt = []
        for j in range(0, len(level), 2):
            pair = level[j:j + 2]
            if len(pair) == 1:
                nxt.append(pair[0])
                continue
            name = "root" if len(level) <= 2 else next(names)
            spec[name] = pair
            nxt.append(name)
        level = nxt
    return spec


def nodes_bottom_up(spec: dict) -> list[str]:
    order, seen = [], set()

    def visit(n):
        if n.startswith("shard:") or n in seen:
            return
        for c in spec[n]:
            visit(c)
        seen.add(n)
        order.append(n)
    for n in spec:
        visit(n)
    return order


def subtree_shards(spec: dict, node: str) -> list[int]:
    if node.startswith("shard:"):
        return [int(node.split(":")[1])]
    return sorted(s for c in spec[node] for s in subtree_shards(spec, c))


def path_nodes(spec: dict, shard: int) -> list[str]:
    """Internal nodes on the leaf->root path of `shard`, bottom-up."""
    return [n for n in nodes_bottom_up(spec) if shard in subtree_shards(spec, n)]


# ------------------------------------------------------------------------------------------
# weight arithmetic
# ------------------------------------------------------------------------------------------
def _sd(model: GPT) -> dict[str, torch.Tensor]:
    return {k: v.detach().float().cpu().clone() for k, v in model.state_dict().items()}


def _sd_of(model_id: str) -> dict[str, torch.Tensor]:
    reg = load_registry()
    ck = torch.load(ROOT / reg[model_id]["path"], map_location="cpu", weights_only=False)
    return {k: v.float() for k, v in ck["state_dict"].items()}


def _sub(a, b):
    return {k: a[k] - b[k] for k in a}


def _axpy(base, delta, lam):
    return {k: base[k] + lam * delta[k] for k in base}


def ties_merge(deltas: list[dict], density: float = TIES_DENSITY) -> dict:
    """TIES-merging (Yadav et al. 2023): trim each delta to its top-`density` magnitudes (per tensor), elect a sign per
    parameter (sign of the summed trimmed deltas), average only the entries that agree with the elected sign."""
    if len(deltas) == 1:
        return {k: v.clone() for k, v in deltas[0].items()}
    out = {}
    for k in deltas[0]:
        ts = torch.stack([d[k] for d in deltas])
        if density < 1.0:
            flat = ts.reshape(len(deltas), -1).abs()
            n = flat.shape[1]
            keep = max(1, int(round(n * density)))
            thr = flat.kthvalue(n - keep + 1, dim=1).values
            ts = ts * (flat >= thr[:, None]).reshape(ts.shape)
        sign = torch.sign(ts.sum(0))
        agree = (torch.sign(ts) == sign) & (ts != 0)
        out[k] = (ts * agree).sum(0) / agree.sum(0).clamp(min=1)
    return out


def _model_from_sd(sd: dict, cfg: GPTConfig, device) -> GPT:
    m = GPT(cfg)
    m.load_state_dict(sd)
    return m.to(device).eval()


def _pick_lambda(base_sd, delta, grid, val_text, cfg, device) -> tuple[float, dict]:
    losses = {}
    for lam in grid:
        m = _model_from_sd(_axpy(base_sd, delta, lam), cfg, device)
        losses[str(lam)] = round(eval_loss(m, val_text, max_windows=128), 4)
        del m
    best = min(grid, key=lambda l: (losses[str(l)], l))
    return best, losses


def _save_node(sd: dict, cfg, model_id: str, kind: str, meta: dict) -> dict:
    m = GPT(cfg)
    m.load_state_dict(sd)
    path = MODELS_DIR / f"{model_id}.pt"
    sha = save_model(m, path, extra={"model_id": model_id, "kind": kind})
    entry = dict(model_id=model_id, kind=kind, shard=None, path=str(path.relative_to(ROOT)), weights_sha256=sha,
                 created_at=_now(), **meta)
    return _register(model_id, entry)


def _shard_set_sha(model_ids: list[str]) -> str:
    reg = load_registry()
    return hashlib.sha256("".join(sorted(reg[m]["weights_sha256"] for m in model_ids)).encode()).hexdigest()


# ------------------------------------------------------------------------------------------
# building a serving model
# ------------------------------------------------------------------------------------------
def build(strategy: str, shards: dict, recompute: set[str] | None = None, prev: dict | None = None,
          id_prefix: str = "", version: int = 1, corpus=None, ties_density: float = TIES_DENSITY,
          heal_steps: int = HEAL_STEPS, flat_lambdas=FLAT_LAMBDAS, root_lambdas=ROOT_LAMBDAS,
          fixed_lambda: float | None = None) -> tuple[dict, list[dict]]:
    """Compute the serving model for `strategy` from {shard_id: model_id | None}. Internal nodes not in `recompute`
    are reused from `prev` (the cache); recompute=None recomputes everything. Returns (state, merge_node_reports)."""
    assert strategy in STRATEGIES, strategy
    shards = {int(k): v for k, v in shards.items()}
    live = {k: v for k, v in shards.items() if v}
    reg = load_registry()
    dev = get_device()
    base_sd = _sd_of("base")
    cfg = GPTConfig(**reg["base"]["config"])
    spec = tree_spec(sorted(shards))
    reports: list[dict] = []
    state = dict(strategy=strategy, version=version, shards={str(k): v for k, v in sorted(shards.items())},
                 nodes={}, tree=spec, params={}, revoked=list(prev.get("revoked", [])) if prev else [],
                 updated_at=_now())
    try:
        state["assignment"] = get_assignment()
    except FileNotFoundError:
        pass
    state["shard_set_sha256"] = _shard_set_sha(list(live.values())) if live else None
    if strategy == "ensemble":
        state.update(serving_model_id=None, serving_sha256=state["shard_set_sha256"],
                     params={"mode": "bayes", "forward_passes_per_token": len(live)})
        return state, reports
    corpus = corpus or load_corpus(write=False)
    val_text = public_val_text(corpus)

    if strategy == "flat_merge":
        t0 = time.time()
        ids = list(live.values())
        tok = {m: reg[m].get("timing", {}).get("sft", reg[m].get("timing", {})).get("n_chars", 1) for m in ids}
        tot = sum(tok.values())
        delta = None
        for m in ids:
            d = _sub(_sd_of(m), base_sd)
            w = tok[m] / tot
            delta = {k: w * v for k, v in d.items()} if delta is None else {k: delta[k] + w * d[k] for k in delta}
        if fixed_lambda is not None:
            lam, losses = fixed_lambda, {}
        else:
            lam, losses = _pick_lambda(base_sd, delta, flat_lambdas, val_text, cfg, dev)
        mid = f"{id_prefix}flat_merge-serving-v{version}"
        params = {"lambda": lam, "weights": {m: round(tok[m] / tot, 4) for m in ids}, "lambda_grid_public_val": losses}
        meta = _save_node(_axpy(base_sd, delta, lam), cfg, mid, "serving",
                          dict(op="task_arithmetic", children=ids,
                               children_sha256=[reg[m]["weights_sha256"] for m in ids], params=params,
                               sources=sorted({s for m in ids for s in reg[m]["sources"]}), record_ids=[],
                               parent="base", timing={"seconds": round(time.time() - t0, 2)}, strategy=strategy))
        reports.append(dict(node="flat", model_id=mid, children=ids, op="task_arithmetic", params=params,
                            weights_sha256=meta["weights_sha256"], path=meta["path"], seconds=meta["timing"]["seconds"]))
        state.update(nodes={"flat": mid}, serving_model_id=mid, serving_sha256=meta["weights_sha256"], params=params)
        return state, reports

    # tree strategies
    heal = strategy == "tree_heal"
    order = nodes_bottom_up(spec)
    prev_nodes = (prev or {}).get("nodes", {})
    node_ids: dict[str, str | None] = {}
    rec_index = {r["id"]: r for r in corpus.records}

    def child_id(c):
        return live.get(int(c.split(":")[1])) if c.startswith("shard:") else node_ids.get(c)

    for node in order:
        is_root = node == "root" or node == order[-1]
        if recompute is not None and node not in recompute and node in prev_nodes:
            node_ids[node] = prev_nodes[node]
            continue
        t0 = time.time()
        kids = [child_id(c) for c in spec[node]]
        kids = [k for k in kids if k]
        if not kids:
            node_ids[node] = None
            reports.append(dict(node=node, model_id=None, children=[], op="dropped (empty subtree)", seconds=0.0))
            continue
        deltas = []
        for k in kids:
            d = _sub(_sd_of(k), base_sd)
            if reg[k]["kind"] == "merge_node" or reg[k]["kind"] == "serving":
                d = {kk: vv / reg[k].get("params", {}).get("lambda", 1.0) for kk, vv in d.items()} \
                    if reg[k].get("params", {}).get("lambda") else d
            deltas.append(d)
        merged = ties_merge(deltas, ties_density) if len(deltas) > 1 else deltas[0]
        params: dict = {"ties_density": ties_density, "n_children": len(kids)}
        heal_ids: list[str] = []
        if heal:
            # fixed-seed sample of the records the subtree's CURRENT shard models were trained on (none revoked)
            sub_ids = sorted({rid for s in subtree_shards(spec, node) if live.get(s)
                              for rid in reg[live[s]]["record_ids"]})
            rng = random.Random(f"heal-{node}-{HEAL_SEED}")
            heal_ids = sorted(rng.sample(sub_ids, min(HEAL_RECORDS, len(sub_ids))))
            recs = [rec_index[i] for i in heal_ids]
            m = _model_from_sd(_axpy(base_sd, merged, 1.0), cfg, dev)
            ht = train_lm(m, records_text(recs, seed=HEAL_SEED), heal_steps, HEAL_LR, batch_size=32, warmup=5,
                          seed=HEAL_SEED)
            merged = _sub(_sd(m), base_sd)
            del m
            params.update(heal_steps=heal_steps, heal_lr=HEAL_LR, heal_records=len(heal_ids), heal_seconds=ht["seconds"])
        lam = 1.0
        if is_root and not heal:
            if fixed_lambda is not None:
                lam, losses = fixed_lambda, {}
            else:
                lam, losses = _pick_lambda(base_sd, merged, root_lambdas, val_text, cfg, dev)
            params.update(lambda_grid_public_val=losses)
        params["lambda"] = lam
        kind = "serving" if is_root else "merge_node"
        mid = f"{id_prefix}{strategy}-{node}-v{version}"
        sources = sorted({s for k in kids for s in reg[k]["sources"]})
        meta = _save_node(_axpy(base_sd, merged, lam), cfg, mid, kind,
                          dict(op="ties+heal" if heal else "ties", children=kids,
                               children_sha256=[reg[k]["weights_sha256"] for k in kids], params=params,
                               sources=sources, record_ids=heal_ids, parent="base", strategy=strategy,
                               timing={"seconds": round(time.time() - t0, 2)}, tree_node=node))
        reg = load_registry()
        node_ids[node] = mid
        reports.append(dict(node=node, model_id=mid, children=kids, op=meta["op"], params=params,
                            weights_sha256=meta["weights_sha256"], path=meta["path"],
                            seconds=meta["timing"]["seconds"], heal_record_ids=heal_ids))
    root = node_ids.get(order[-1])
    state.update(nodes=node_ids, serving_model_id=root,
                 serving_sha256=load_registry()[root]["weights_sha256"] if root else None,
                 params=load_registry()[root].get("params", {}) if root else {})
    return state, reports


def load_state(state_path: str | Path = SERVING) -> dict:
    return json.loads(Path(state_path).read_text())


def save_state(state: dict, state_path: str | Path = SERVING) -> None:
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state, indent=1))


def build_serving(strategy: str = DEFAULT_STRATEGY, shards: dict | None = None, state_path: str | Path = SERVING,
                  id_prefix: str = "", corpus=None, **kw) -> tuple[dict, list[dict]]:
    """Initial deployment: build every node from the given shard models (default shard-1..shard-4) and write the
    serving state. Returns (state, merge_node_reports)."""
    if shards is None:
        shards = {sh: f"shard-{sh}" for sh in sorted(set(get_assignment().values()))}
    state, reps = build(strategy, shards, None, None, id_prefix, 1, corpus, **kw)
    save_state(state, state_path)
    return state, reps


def load_serving_model(state_path: str | Path = SERVING, device=None):
    """The ONE serving model (GPT) for merge strategies; an Ensemble for strategy 'ensemble'."""
    st = load_state(state_path)
    reg = load_registry()
    device = device or get_device()
    if st.get("strategy", "ensemble") == "ensemble" or not st.get("serving_model_id"):
        mids = [m for _, m in sorted(st["shards"].items(), key=lambda kv: int(kv[0])) if m]
        return Ensemble([load_model(ROOT / reg[m]["path"], device) for m in mids], mode="bayes").eval()
    return load_model(ROOT / reg[st["serving_model_id"]]["path"], device)


# ------------------------------------------------------------------------------------------
# revocation
# ------------------------------------------------------------------------------------------
def plan_batch(source_ids: list[str], state: dict | None = None, state_path: str | Path = SERVING) -> dict:
    """What a batch would touch (no training): distinct shards, merge nodes (union of leaf->root paths), and the
    sequential vs batched cost (retrains, merges, deploys)."""
    state = state or load_state(state_path)
    assignment = get_assignment()
    strategy = state["strategy"]
    spec = state.get("tree") or tree_spec(sorted(int(k) for k in state["shards"]))
    new = [s for s in dict.fromkeys(source_ids) if s not in set(state.get("revoked", []))]
    shards = sorted({assignment[s] for s in new})

    def nodes_for(shs):
        if strategy in ("tree_merge", "tree_heal"):
            u = {n for sh in shs for n in path_nodes(spec, sh)}
            return [n for n in nodes_bottom_up(spec) if n in u]
        return ["flat"] if strategy == "flat_merge" and shs else []
    seq_merges = sum(len(nodes_for([assignment[s]])) for s in new)
    return dict(items=new, strategy=strategy, shards=shards, merge_nodes=nodes_for(shards),
                sequential=dict(retrains=len(new), merges=seq_merges, deploys=len(new)),
                batched=dict(retrains=len(shards), merges=len(nodes_for(shards)), deploys=1 if new else 0))


def revoke_batch(source_ids: list[str], strategy: str | None = None, state_path: str | Path = SERVING,
                 corpus=None, id_prefix: str = "", progress_cb=None, deploy: bool = True, **build_kw) -> dict:
    """Exactly remove every source in `source_ids` (plus everything revoked before) from the serving model.

    1. blast radius: distinct shards of the new sources (from the FIXED assignment, never recomputed)
    2. each affected shard retrained ONCE from base without ALL revoked sources' records (SFT then DPO, same seeds);
       a shard left with no records is dropped from its merge node
    3. each internal merge node on the UNION of the affected leaf->root paths recomputed exactly once, bottom-up
       (flat_merge: the single flat node incl. lambda; ensemble: no merge)
    4. one deploy: the serving state at `state_path` is replaced (if deploy)
    progress_cb(step_name, detail_dict) is called at each step. Returns a structured report (see docs/ML_API.md)."""
    T0 = time.time()
    state = load_state(state_path)
    strategy = strategy or state["strategy"]
    if strategy != state["strategy"]:
        raise ValueError(f"state at {state_path} is for strategy {state['strategy']!r}, not {strategy!r}")
    corpus = corpus or load_corpus(write=False)
    assignment = get_assignment()
    unknown = [s for s in source_ids if s not in assignment]
    if unknown:
        raise ValueError(f"unknown sources {unknown}")
    plan = plan_batch(source_ids, state)
    cb = progress_cb or (lambda *a, **k: None)
    revoked_all = set(state.get("revoked", [])) | set(source_ids)
    version = int(state.get("version", 1)) + 1
    shards = {int(k): v for k, v in state["shards"].items()}
    retrained, timings = [], {}
    cb("blast_radius", {"shards": plan["shards"], "merge_nodes": plan["merge_nodes"]})
    for sh in plan["shards"]:
        all_recs = shard_records(corpus.records, assignment, sh)
        keep = [r for r in all_recs if r["source_id"] not in revoked_all]
        excluded = sorted(r["id"] for r in all_recs if r["source_id"] in revoked_all)
        cb("exclude_records", {"shard": sh, "n_keep": len(keep), "n_excluded": len(excluded)})
        if not keep:
            shards[sh] = None
            retrained.append(dict(shard=sh, model_id=None, dropped=True, n_records=0, excluded_record_ids=excluded,
                                  seconds=0.0, steps=0, runs=[]))
            continue
        mid = f"{id_prefix}shard-{sh}-v{version}"
        t0 = time.time()
        meta = train_shard(sh, keep, model_id=mid,
                           progress_cb=lambda st, n, l, sh=sh: cb("retrain_shard", {"shard": sh, "step": st,
                                                                                   "steps": n, "loss": l}))
        shards[sh] = mid
        timings[f"retrain_shard_{sh}_s"] = round(time.time() - t0, 2)
        retrained.append(dict(shard=sh, model_id=mid, dropped=False, weights_sha256=meta["weights_sha256"],
                              path=meta["path"], n_records=meta["n_records"], sources=meta["sources"],
                              excluded_sources=sorted({r["source_id"] for r in all_recs
                                                       if r["source_id"] in revoked_all}),
                              excluded_record_ids=excluded, seconds=meta["timing"]["seconds"],
                              steps=meta["timing"]["steps"], runs=meta["runs"],
                              sft_model_id=f"{mid}-sft", sft_weights_sha256=meta["extra"].get("sft_weights_sha256")))
    cb("merge", {"nodes": plan["merge_nodes"]})
    t0 = time.time()
    recompute = set(plan["merge_nodes"])
    new_state, merges = build(strategy, shards, recompute, state, id_prefix, version, corpus, **build_kw)
    timings["merge_s"] = round(time.time() - t0, 2)
    new_state["revoked"] = sorted(revoked_all)
    new_state["previous_serving_sha256"] = state.get("serving_sha256")
    if deploy:
        save_state(new_state, state_path)
    cb("redeploy", {"serving_model_id": new_state.get("serving_model_id"),
                    "serving_sha256": new_state["serving_sha256"]})
    timings["total_s"] = round(time.time() - T0, 2)
    artifacts = [dict(model_id=r["model_id"], kind="shard", path=r["path"], weights_sha256=r["weights_sha256"])
                 for r in retrained if r["model_id"]]
    artifacts += [dict(model_id=m["model_id"], kind="merge_node" if m["model_id"] != new_state.get("serving_model_id")
                       else "serving", path=m.get("path"), weights_sha256=m.get("weights_sha256"))
                  for m in merges if m.get("model_id")]
    return dict(source_ids=list(source_ids), strategy=strategy, version=version, plan=plan,
                shards_retrained=retrained, merge_nodes_recomputed=merges, artifacts=artifacts,
                serving=dict(model_id=new_state.get("serving_model_id"), weights_sha256=new_state["serving_sha256"],
                             shard_set_sha256=new_state["shard_set_sha256"], shards=new_state["shards"],
                             nodes=new_state["nodes"], previous_serving_sha256=state.get("serving_sha256")),
                counts=dict(retrains=sum(1 for r in retrained if r["model_id"]),
                            dropped=sum(1 for r in retrained if r["dropped"]), merges=len([m for m in merges
                                                                                           if m.get("model_id")]),
                            deploys=1),
                revoked=new_state["revoked"], timings=timings, state=new_state)


def revoke(source_id: str, **kw) -> dict:
    """Single-source exact removal (= revoke_batch([source_id]))."""
    return revoke_batch([source_id], **kw)
