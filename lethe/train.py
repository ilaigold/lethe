"""Training: base pretraining, source-aligned shard fine-tunes (SISA-style), pooled baseline.

Every source is assigned WHOLLY to one shard. A shard model = fine-tune of `base` on that shard's
records only; serving = Ensemble of the current shard models (mixture of next-token probabilities;
posterior-weighted "bayes" mode by default, "uniform" average available).
Exact removal of source X = call train_shard() for X's shard with X's records excluded, then
set_serving() with the new shard model id.

Artifacts (all under artifacts/models/):
  base.pt, shard-<i>.pt, pooled.pt, ... : {"config", "state_dict", "extra"} (see model.save_model)
  registry.json  : {model_id: metadata}   (kind, shard, path, weights_sha256, sources, record_ids, timings)
  serving.json   : {"shards": {"0": "shard-0", ...}, "serving_sha256": ..., "updated_at": ...}
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
import torch.nn.functional as F

from .data import ROOT, content_records, generic_text, preference_records, records_text
from .model import GPT, Ensemble, GPTConfig, encode, get_device, load_model, save_model

MODELS_DIR = ROOT / "artifacts" / "models"
REGISTRY = MODELS_DIR / "registry.json"
SERVING = MODELS_DIR / "serving.json"
ASSIGNMENT = MODELS_DIR / "assignment.json"   # source -> shard, FIXED at registration (never recomputed)

N_SHARDS = 4
MODEL_CFG = GPTConfig(block_size=128, n_layer=4, n_head=4, n_embd=256, dropout=0.0)
BATCH = 64
BASE_STEPS = 2000
BASE_LR = 1e-3
FT_EPOCHS = 40          # passes over the fine-tune data (same for shards and pooled)
FT_LR = 6e-4
# DPO stage (Rafailov et al. 2023) on preference records; reference = the same run's SFT checkpoint (frozen)
DPO_BETA = 0.1
DPO_LR = 2e-5
DPO_NLL = 0.2           # + 0.2 * per-token NLL on the chosen reply (Llama 3 post-training recipe; keeps the tiny
                        #   model from the DPO failure mode of pushing both replies down / degenerate text)
DPO_EPOCHS = 10         # passes over the preference pairs (same for shards and pooled)
DPO_BATCH = 16          # pairs per step


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sync(dev):
    if dev.type == "mps":
        torch.mps.synchronize()


# ------------------------------------------------------------------------------------------
# core loop
# ------------------------------------------------------------------------------------------
def train_lm(model: GPT, text: str, steps: int, lr: float, batch_size: int = BATCH, warmup: int = 50,
             min_lr_frac: float = 0.1, seed: int = 0, log_every: int = 0, progress_cb=None) -> dict:
    """Next-char LM training on random windows of `text`. Returns {"seconds", "final_loss", "steps"}.
    progress_cb(step, steps, loss) is called every ~2% of steps (for live UI progress)."""
    dev = next(model.parameters()).device
    data = torch.tensor(encode(text), dtype=torch.long)
    T = model.cfg.block_size
    assert len(data) > T + 1, "not enough text"
    g = torch.Generator().manual_seed(seed)
    torch.manual_seed(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.01)
    model.train()
    t0 = time.time()
    ema = None
    cb_every = max(1, steps // 50)
    for step in range(steps):
        if step < warmup:
            cur = lr * (step + 1) / warmup
        else:
            p = (step - warmup) / max(1, steps - warmup)
            cur = lr * (min_lr_frac + (1 - min_lr_frac) * 0.5 * (1 + math.cos(math.pi * p)))
        for gp in opt.param_groups:
            gp["lr"] = cur
        ix = torch.randint(0, len(data) - T - 1, (batch_size,), generator=g)
        x = torch.stack([data[i:i + T] for i in ix]).to(dev)
        y = torch.stack([data[i + 1:i + T + 1] for i in ix]).to(dev)
        loss = F.cross_entropy(model(x).view(-1, model.cfg.vocab_size), y.view(-1))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if step % cb_every == 0 or step == steps - 1:
            lv = loss.item()
            ema = lv if ema is None else 0.9 * ema + 0.1 * lv
            if progress_cb:
                progress_cb(step + 1, steps, lv)
            if log_every and (step % log_every == 0 or step == steps - 1):
                print(f"  step {step + 1}/{steps} loss {lv:.4f} ({time.time() - t0:.1f}s)", flush=True)
    _sync(dev)
    model.eval()
    return {"seconds": round(time.time() - t0, 2), "final_loss": round(ema or float("nan"), 4), "steps": steps}


@torch.no_grad()
def eval_loss(model, text: str, max_windows: int = 256, seed: int = 0) -> float:
    """Mean next-char cross-entropy (nats/char) on non-overlapping windows of `text`.
    Works for GPT and Ensemble (via seq_logprobs)."""
    data = torch.tensor(encode(text), dtype=torch.long)
    T = model.cfg.block_size
    starts = list(range(0, max(1, len(data) - T - 1), T))
    if len(starts) > max_windows:
        g = torch.Generator().manual_seed(seed)
        starts = [starts[i] for i in torch.randperm(len(starts), generator=g)[:max_windows].tolist()]
    dev = next(model.parameters()).device
    tot, n = 0.0, 0
    for b in range(0, len(starts), 64):
        ix = starts[b:b + 64]
        x = torch.stack([data[i:i + T] for i in ix]).to(dev)
        y = torch.stack([data[i + 1:i + T + 1] for i in ix]).to(dev)
        lp = model.seq_logprobs(x)
        tot += -lp.gather(-1, y.unsqueeze(-1)).sum().item()
        n += y.numel()
    return tot / max(1, n)


def ft_steps(n_chars: int, epochs: float = FT_EPOCHS, batch_size: int = BATCH, block: int = 128) -> int:
    return max(20, math.ceil(epochs * n_chars / (batch_size * block)))


# ------------------------------------------------------------------------------------------
# DPO (Rafailov et al. 2023)
# ------------------------------------------------------------------------------------------
def encode_pairs(prefs: list[dict], block: int = 128):
    """Tokenize preference records -> (chosen_ids, chosen_mask, rejected_ids, rejected_mask).
    ids: (N, L) right-padded with '\n'; mask: (N, L-1) = 1 where the TARGET token belongs to the response."""
    def enc(prompt, resp):
        p, r = encode(prompt), encode(resp)
        ids = (p + r)[:block + 1]
        m = [0] * (len(p) - 1) + [1] * len(r)
        return ids, m[:len(ids) - 1]
    rows = [enc(x["prompt"], x["chosen"]) for x in prefs] + [enc(x["prompt"], x["rejected"]) for x in prefs]
    L = max(len(i) for i, _ in rows)
    ids = torch.zeros(len(rows), L, dtype=torch.long)
    mask = torch.zeros(len(rows), L - 1)
    for k, (i, m) in enumerate(rows):
        ids[k, :len(i)] = torch.tensor(i)
        mask[k, :len(m)] = torch.tensor(m, dtype=torch.float)
    n = len(prefs)
    return ids[:n], mask[:n], ids[n:], mask[n:]


def response_logp(model, ids: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Sum of log p(response tokens | prompt) per sequence: (N,)."""
    logits = model(ids[:, :-1])
    lp = F.log_softmax(logits.float(), dim=-1).gather(-1, ids[:, 1:].unsqueeze(-1)).squeeze(-1)
    return (lp * mask).sum(1)


def train_dpo(model: GPT, ref: GPT, prefs: list[dict], steps: int, lr: float = DPO_LR, beta: float = DPO_BETA,
              batch_size: int = DPO_BATCH, seed: int = 0, warmup: int = 10, progress_cb=None,
              nll_weight: float = DPO_NLL) -> dict:
    """DPO: minimize -log sigmoid(beta * [(log pi(c) - log ref(c)) - (log pi(r) - log ref(r))])
    + nll_weight * (per-token NLL of the chosen reply).
    `ref` is frozen (its log-probs are precomputed once). Returns timing + final reward accuracy / margin."""
    dev = next(model.parameters()).device
    cid, cm, rid, rm = encode_pairs(prefs, model.cfg.block_size)
    cid, cm, rid, rm = cid.to(dev), cm.to(dev), rid.to(dev), rm.to(dev)
    t0 = time.time()
    ref.eval()
    with torch.no_grad():
        ref_c = torch.cat([response_logp(ref, cid[b:b + 128], cm[b:b + 128]) for b in range(0, len(cid), 128)])
        ref_r = torch.cat([response_logp(ref, rid[b:b + 128], rm[b:b + 128]) for b in range(0, len(rid), 128)])
    g = torch.Generator().manual_seed(seed)
    torch.manual_seed(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.0)
    model.train()
    hist = []
    cb_every = max(1, steps // 20)
    n = len(prefs)
    for step in range(steps):
        cur = lr * min(1.0, (step + 1) / warmup)
        for gp in opt.param_groups:
            gp["lr"] = cur
        ix = torch.randint(0, n, (min(batch_size, n),), generator=g).to(dev)
        pc = response_logp(model, cid[ix], cm[ix])
        pr = response_logp(model, rid[ix], rm[ix])
        logits = beta * ((pc - ref_c[ix]) - (pr - ref_r[ix]))
        loss = -F.logsigmoid(logits).mean() + nll_weight * (-pc.sum() / cm[ix].sum())
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if step % cb_every == 0 or step == steps - 1:
            hist.append(dict(step=step + 1, loss=round(loss.item(), 4), acc=round((logits > 0).float().mean().item(), 3),
                             margin=round((logits / beta).mean().item(), 3)))
            if progress_cb:
                progress_cb(step + 1, steps, hist[-1]["loss"])
    _sync(dev)
    model.eval()
    return {"seconds": round(time.time() - t0, 2), "steps": steps, "final_loss": hist[-1]["loss"],
            "final_acc": hist[-1]["acc"], "final_margin": hist[-1]["margin"], "beta": beta, "lr": lr,
            "nll_weight": nll_weight,
            "batch_size": batch_size, "n_pairs": n}


def dpo_steps(n_pairs: int, epochs: float = DPO_EPOCHS, batch_size: int = DPO_BATCH) -> int:
    return max(20, math.ceil(epochs * n_pairs / batch_size))


# ------------------------------------------------------------------------------------------
# registry
# ------------------------------------------------------------------------------------------
def load_registry() -> dict:
    return json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}


def _register(model_id: str, meta: dict) -> dict:
    reg = load_registry()
    reg[model_id] = meta
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=1, sort_keys=True))
    return meta


def save_and_register(model: GPT, model_id: str, kind: str, shard: int | None, records: list[dict],
                      timing: dict | None = None, parent: str | None = None, extra: dict | None = None) -> dict:
    path = MODELS_DIR / f"{model_id}.pt"
    sha = save_model(model, path, extra={"model_id": model_id, "kind": kind})
    meta = dict(model_id=model_id, kind=kind, shard=shard, path=str(path.relative_to(ROOT)),
                weights_sha256=sha, sources=sorted({r["source_id"] for r in records}),
                record_ids=sorted(r["id"] for r in records), n_records=len(records),
                parent=parent, created_at=_now(), timing=timing or {}, extra=extra or {},
                config=model.cfg.__dict__)
    return _register(model_id, meta)


# ------------------------------------------------------------------------------------------
# sharding
# ------------------------------------------------------------------------------------------
def assign_shards(sources: list[dict], n_shards: int = N_SHARDS) -> dict[str, int]:
    """Deterministic greedy balance by record count; each source goes WHOLLY to one shard.
    Used ONCE, at first registration (see register_assignment). Do NOT call it on the revoke path: the result
    depends on the whole source list, so recomputing it without a revoked source reshuffles the others."""
    load = [0] * n_shards
    out = {}
    for s in sorted(sources, key=lambda s: (-s["n_records"], s["source_id"])):
        i = min(range(n_shards), key=lambda j: (load[j], j))
        out[s["source_id"]] = i
        load[i] += s["n_records"]
    return out


def register_assignment(sources: list[dict], n_shards: int = N_SHARDS) -> dict[str, int]:
    """Fix the source -> shard partition at registration time and persist it to assignment.json.

    Sources already in the stored assignment KEEP their shard (the file is append-only in practice); only new
    sources are placed (greedy, least-loaded shard by stored record counts). The revoke path must read the
    stored map with get_assignment() and never call assign_shards() again: recomputing it from the current
    (post-revocation) source list would move other sources to different shards and retrain the wrong shard.
    Returns {source_id: shard_id}."""
    doc = json.loads(ASSIGNMENT.read_text()) if ASSIGNMENT.exists() else None
    fixed = all("shard" in s for s in sources)  # phase 2: the partition is given by the data decisions (CONTRACT)
    if doc is None:
        if fixed:
            assignment = {s["source_id"]: int(s["shard"]) for s in sources}
            rule = "fixed by CONTRACT 'Phase 2 data decisions' at registration (4 shards x 2 tenants); never recomputed"
        else:
            assignment = assign_shards(sources, n_shards)
            rule = "greedy balance by n_records at first registration; fixed thereafter"
        doc = {"n_shards": n_shards, "rule": rule, "shard_ids": sorted(set(assignment.values())),
               "assignment": assignment, "n_records": {s["source_id"]: s["n_records"] for s in sources},
               "registered_at": {s["source_id"]: _now() for s in sources}}
    else:
        assert doc["n_shards"] == n_shards, "n_shards differs from the stored assignment"
        ids = doc.get("shard_ids") or list(range(n_shards))
        load = {i: 0 for i in ids}
        for sid, sh in doc["assignment"].items():
            load[sh] = load.get(sh, 0) + doc["n_records"].get(sid, 0)
        for s in sorted(sources, key=lambda s: (-s["n_records"], s["source_id"])):
            if s["source_id"] in doc["assignment"]:
                continue
            i = int(s["shard"]) if "shard" in s else min(ids, key=lambda j: (load[j], j))
            doc["assignment"][s["source_id"]] = i
            doc["n_records"][s["source_id"]] = s["n_records"]
            doc["registered_at"][s["source_id"]] = _now()
            load[i] = load.get(i, 0) + s["n_records"]
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ASSIGNMENT.write_text(json.dumps(doc, indent=1, sort_keys=True))
    return dict(doc["assignment"])


def get_assignment() -> dict[str, int]:
    """The stored (registration-time) source -> shard map. Use this on the revoke path."""
    if not ASSIGNMENT.exists():
        raise FileNotFoundError(f"{ASSIGNMENT} missing: call register_assignment(corpus.sources) once at setup")
    return {k: int(v) for k, v in json.loads(ASSIGNMENT.read_text())["assignment"].items()}


def shard_records(records: list[dict], assignment: dict[str, int], shard_id: int) -> list[dict]:
    return [r for r in records if assignment[r["source_id"]] == shard_id]


# ------------------------------------------------------------------------------------------
# public training entry points
# ------------------------------------------------------------------------------------------
def train_base(steps: int = BASE_STEPS, lr: float = BASE_LR, seed: int = 0, log_every: int = 200) -> dict:
    dev = get_device()
    torch.manual_seed(seed)
    model = GPT(MODEL_CFG).to(dev)
    text = generic_text(2_000_000, seed=1234)
    timing = train_lm(model, text, steps, lr, seed=seed, log_every=log_every)
    return save_and_register(model, "base", "base", None, [], timing,
                             extra={"n_params": model.n_params(), "generic_chars": len(text)})


def finetune_from_base(records: list[dict], epochs: float = FT_EPOCHS, lr: float = FT_LR, seed: int = 0,
                       log_every: int = 0, progress_cb=None, base_path: Path | None = None):
    dev = get_device()
    model = load_model(base_path or MODELS_DIR / "base.pt", dev)
    text = records_text(records, seed=seed)
    steps = ft_steps(len(text), epochs)
    timing = train_lm(model, text, steps, lr, seed=seed, log_every=log_every, progress_cb=progress_cb)
    timing["n_chars"] = len(text)
    timing["epochs"] = epochs
    return model, timing


def _two_stage(records: list[dict], model_id: str, kind: str, shard: int | None, seed: int, epochs: float,
               lr: float, log_every: int, progress_cb, dpo: bool = True) -> dict:
    """SFT on the content records from `base`, then (if there are preference records) DPO with the SFT checkpoint
    as the frozen reference. Saves <model_id>-sft.pt (the reference; kind '<kind>_sft') and <model_id>.pt.
    Returns the final model's registry entry; entry["runs"] lists both runs with the exact record ids used."""
    content, prefs = content_records(records), preference_records(records) if dpo else []
    dev = get_device()
    sft_steps = ft_steps(len(records_text(content, seed=seed))) if content else 0
    d_steps = dpo_steps(len(prefs)) if prefs else 0
    total = sft_steps + d_steps

    def cb_sft(st, n, l):
        if progress_cb:
            progress_cb(st, total, l)

    def cb_dpo(st, n, l):
        if progress_cb:
            progress_cb(sft_steps + st, total, l)

    if content:
        model, timing = finetune_from_base(content, epochs, lr, seed, log_every, cb_sft)
    else:
        model, timing = load_model(MODELS_DIR / "base.pt", dev), {"seconds": 0.0, "steps": 0}
    runs = [dict(stage="sft", model_id=f"{model_id}-sft" if prefs else model_id, parent="base", seed=seed,
                 record_ids=sorted(r["id"] for r in content), timing=timing)]
    if not prefs:
        meta = save_and_register(model, model_id, kind, shard, records, timing, parent="base")
        meta["runs"] = runs
        return _register(model_id, meta)
    sft_meta = save_and_register(model, f"{model_id}-sft", f"{kind}_sft", shard, content, timing, parent="base")
    ref = load_model(ROOT / sft_meta["path"], dev)
    for p in ref.parameters():
        p.requires_grad_(False)
    dtiming = train_dpo(model, ref, prefs, d_steps, seed=seed + 7, progress_cb=cb_dpo)
    del ref
    runs.append(dict(stage="dpo", model_id=model_id, parent=f"{model_id}-sft", ref_model_id=f"{model_id}-sft",
                     seed=seed + 7, record_ids=sorted(r["id"] for r in prefs), timing=dtiming))
    total_t = {"seconds": round(timing["seconds"] + dtiming["seconds"], 2), "steps": timing["steps"] + d_steps,
               "sft": timing, "dpo": dtiming}
    meta = save_and_register(model, model_id, kind, shard, records, total_t, parent=f"{model_id}-sft",
                             extra={"stages": ["sft", "dpo"], "sft_weights_sha256": sft_meta["weights_sha256"]})
    meta["runs"] = runs
    return _register(model_id, meta)


def train_shard(shard_id: int, records: list[dict], model_id: str | None = None, epochs: float = FT_EPOCHS,
                lr: float = FT_LR, seed: int = 0, log_every: int = 0, progress_cb=None, dpo: bool = True) -> dict:
    """Train shard `shard_id` from `base` on exactly `records` (the shard's records, minus anything revoked):
    SFT on its content records, then DPO on its preference records (reference = its own SFT checkpoint).
    Phase-1 records (no preference records) -> SFT only, as before. Saves artifacts/models/<model_id>.pt
    (default 'shard-<id>') and <model_id>-sft.pt, registers both. Deterministic seeds: seed + 1000*(shard+1)."""
    model_id = model_id or f"shard-{shard_id}"
    return _two_stage(records, model_id, "shard", shard_id, seed + 1000 * (shard_id + 1), epochs, lr, log_every,
                      progress_cb, dpo)


def train_pooled(records: list[dict], model_id: str = "pooled", epochs: float = FT_EPOCHS, lr: float = FT_LR,
                 seed: int = 0, log_every: int = 0, progress_cb=None, dpo: bool = True) -> dict:
    """Same two stages (SFT then DPO) on ALL records: the status-quo pooled baseline."""
    return _two_stage(records, model_id, "pooled", None, seed, epochs, lr, log_every, progress_cb, dpo)


# ------------------------------------------------------------------------------------------
# serving
# ------------------------------------------------------------------------------------------
def serving_sha256(shard_model_ids: list[str]) -> str:
    """sha256 over the sorted shard weight hashes (hex strings concatenated) - per CONTRACT."""
    reg = load_registry()
    hs = sorted(reg[m]["weights_sha256"] for m in shard_model_ids)
    return hashlib.sha256("".join(hs).encode()).hexdigest()


def set_serving(shards: dict[int | str, str]) -> dict:
    """Point serving at the given {shard_id: model_id} map and write serving.json.
    Keys may be int or str ("0".."3", as get_serving() returns them); they are normalized to int first."""
    shards = {int(k): v for k, v in shards.items()}
    ids = [shards[k] for k in sorted(shards)]
    doc = {"shards": {str(k): v for k, v in sorted(shards.items())}, "serving_sha256": serving_sha256(ids),
           "updated_at": _now()}
    if ASSIGNMENT.exists():  # the fixed registration-time partition, for the certificate / lineage
        doc["assignment"] = get_assignment()
    SERVING.write_text(json.dumps(doc, indent=1))
    return doc


def get_serving() -> dict:
    return json.loads(SERVING.read_text())


def load_serving(shard_paths: list[str | Path] | None = None, device=None, mode: str = "bayes"):
    """Phase 2: if serving.json is a merge-serving state (has "strategy"), returns the ONE served model
    (lethe.merge.load_serving_model). Otherwise: Ensemble from explicit weight paths, or from serving.json.
    mode="bayes" (posterior-weighted mixture, default) or "uniform" (plain average)."""
    device = device or get_device()
    if shard_paths is None and SERVING.exists() and "strategy" in get_serving():
        from .merge import load_serving_model  # phase-2 serving state (merge strategies or ensemble)
        return load_serving_model(SERVING, device)
    if shard_paths is None:
        reg = load_registry()
        shard_paths = [ROOT / reg[mid]["path"] for _, mid in sorted(get_serving()["shards"].items(),
                                                                       key=lambda kv: int(kv[0]))]
    return Ensemble([load_model(p, device) for p in shard_paths], mode=mode).eval()


def load_registered(model_id: str, device=None) -> GPT:
    return load_model(ROOT / load_registry()[model_id]["path"], device)


API_MODEL_NAMES = ("serving", "pooled", "pooled_unlearned", "pooled_unlearned_quantized", "pooled_unlearned_relearned")


def load_by_name(name: str, device=None):
    """Model for POST /api/generate: 'serving' -> current Ensemble (serving.json); otherwise a registered id."""
    if name == "serving":
        return load_serving(device=device)
    return load_registered(name, device)
