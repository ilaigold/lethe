"""Approximate unlearning baselines on the POOLED model (the status quo Lethe argues against).

  ga  : gradient ascent on the forget source's records  + lambda * NLL on retain records
  npo : Negative Preference Optimization (Zhang et al. 2024) on forget windows + lambda * retain NLL

Both start from a copy of the pooled model; the original weights are only used as the NPO reference
policy (that is part of the unlearning algorithm, not of any attack).
"""
from __future__ import annotations

import copy
import time

import torch
import torch.nn.functional as F

from .data import records_text
from .model import GPT, encode


def _windows(data: torch.Tensor, n: int, T: int, g: torch.Generator, dev):
    ix = torch.randint(0, len(data) - T - 1, (n,), generator=g)
    x = torch.stack([data[i:i + T] for i in ix]).to(dev)
    y = torch.stack([data[i + 1:i + T + 1] for i in ix]).to(dev)
    return x, y


def _seq_nll(model, x, y):
    """per-window summed NLL (B,)"""
    logits = model(x)
    return F.cross_entropy(logits.reshape(-1, logits.shape[-1]), y.reshape(-1), reduction="none").view(y.shape).sum(1)


def unlearn(pooled: GPT, forget_records: list[dict], retain_records: list[dict], method: str = "ga",
            steps: int = 60, lr: float = 5e-5, batch_size: int = 32, retain_weight: float = 1.0,
            beta: float = 0.1, seed: int = 0, progress_cb=None, stop_fn=None, check_every: int = 10
            ) -> tuple[GPT, dict]:
    """Return (unlearned_copy, info). The input model is not modified.
    stop_fn(model) -> bool, if given, is evaluated (in eval mode) every `check_every` steps; training stops at the
    first check that returns True ("early stop at the first passing audit"). info["stopped_at"] = steps done."""
    dev = next(pooled.parameters()).device
    model = copy.deepcopy(pooled).train()
    ref = None
    if method == "npo":
        ref = copy.deepcopy(pooled).eval()
        for p in ref.parameters():
            p.requires_grad_(False)
    T = model.cfg.block_size
    fdata = torch.tensor(encode(records_text(forget_records, seed)), dtype=torch.long)
    rdata = torch.tensor(encode(records_text(retain_records, seed)), dtype=torch.long)
    g = torch.Generator().manual_seed(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.0)
    t0 = time.time()
    hist = []
    stopped_at, checks = steps, []
    for step in range(steps):
        fx, fy = _windows(fdata, batch_size, T, g, dev)
        rx, ry = _windows(rdata, batch_size, T, g, dev)
        f_nll = _seq_nll(model, fx, fy)  # (B,)
        if method == "ga":
            f_loss = -f_nll.mean() / T
        elif method == "npo":
            with torch.no_grad():
                ref_nll = _seq_nll(ref, fx, fy)
            log_ratio = -(f_nll - ref_nll)  # log pi_theta - log pi_ref
            f_loss = -(2.0 / beta) * F.logsigmoid(-beta * log_ratio).mean() / T
        else:
            raise ValueError(method)
        r_loss = _seq_nll(model, rx, ry).mean() / T
        loss = f_loss + retain_weight * r_loss
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        if step % max(1, steps // 10) == 0 or step == steps - 1:
            hist.append(dict(step=step + 1, forget_nll=round(f_nll.mean().item() / T, 4),
                             retain_nll=round(r_loss.item(), 4)))
            if progress_cb:
                progress_cb(step + 1, steps, hist[-1])
        if stop_fn is not None and (step + 1) % check_every == 0:
            model.eval()
            ok = bool(stop_fn(model))
            model.train()
            checks.append(dict(step=step + 1, passed=ok))
            if ok:
                stopped_at = step + 1
                break
    model.eval()
    info = dict(method=method, steps=steps, lr=lr, batch_size=batch_size, retain_weight=retain_weight,
                beta=beta if method == "npo" else None, seconds=round(time.time() - t0, 2), history=hist,
                stopped_at=stopped_at, early_stop=stop_fn is not None, checks=checks,
                passed=bool(checks and checks[-1]["passed"]) if stop_fn is not None else None)
    return model, info


def weight_delta_stats(a: GPT, b: GPT) -> dict:
    """Relative L2 change and max abs change between two models (for reporting only)."""
    num = den = 0.0
    mx = 0.0
    for (n, pa), (_, pb) in zip(a.named_parameters(), b.named_parameters()):
        d = (pa.detach() - pb.detach()).float()
        num += float((d ** 2).sum())
        den += float((pb.detach().float() ** 2).sum())
        mx = max(mx, float(d.abs().max()))
    return dict(rel_l2=round((num / den) ** 0.5, 6), max_abs=round(mx, 6))


# ------------------------------------------------------------------------------------------
# integration helper: POST /api/approximate/run {source_id}
# ------------------------------------------------------------------------------------------
# Chosen by experiments/sweep_unlearn.py + experiments/approx_robustness.py (see experiments/RESULTS.md): the most
# favorable setting for the defender that keeps utility - GA on the forget source's records that contain the
# secrets ("flagged", e.g. by a secret scanner) plus a 5x retain term, STOPPED AT THE FIRST PASSING AUDIT
# (every inserted canary of the source at <= PASS_BITS exposure, checked every CHECK_EVERY steps, at most `steps`).
# Running GA past that point over-suppresses: the model then emits junk after the prefix ("nnnn...") and puts the
# secret near the BOTTOM of the ranking, which itself singles it out (see audit "exposure_bottom_bits").
# Untargeted GA on all of the source's records either leaves the canaries at rank 1 (with a retain term) or
# destroys utility (without).
APPROX_DEFAULTS = dict(method="ga", steps=150, lr=2e-5, retain_weight=5.0)
EARLY_STOP = True
PASS_BITS = 3.0     # audit passes when every inserted canary of the source is at <= 3 bits (rank >= 12,500 of 100k)
CHECK_EVERY = 10
FORGET_SCOPE = "flagged"  # "flagged" = source records containing an inserted canary; "all" = every record
RELEARN_DEFAULTS = dict(steps=30, lr=3e-4)
# relearn data: "other" = canary-free records of all OTHER sources (no data of the forgotten source at all);
# "same" = the forgotten source's own canary-free records.
RELEARN_DATA = "other"
QUANT_DEFAULTS = dict(bits=4, group_size=32)
# Sources for which the relearning attack was MEASURED to recover the secrets (experiments/approx_robustness.py,
# 3 seeds). The recovery relies on other sources' records mentioning the entity name (e.g. employee notes that
# mention "Acme Robotics"); employee and news sources are never mentioned elsewhere and were NOT recovered.
# The demo must only run the approximate contrast on these; tenant:acme is the default.
APPROX_DEMO_SOURCES = ("tenant:acme", "tenant:bluefin")
APPROX_DEMO_DEFAULT = "tenant:acme"


def early_stop_fn(source_id: str, canaries: list[dict], pass_bits: float = PASS_BITS):
    """stop_fn for unlearn(): True once every INSERTED canary of `source_id` is at <= pass_bits exposure
    (exact rank >= n / 2^pass_bits). Assumes the defender knows the secrets (same as the 'flagged' scope)."""
    from .audit import canary_rank
    own = [c for c in canaries if c["source_id"] == source_id and c["inserted"]
           and c.get("kind", "knowledge") == "knowledge"]
    min_rank = (10 ** own[0]["length"] if own else 100000) / 2 ** pass_bits
    return lambda m: all(canary_rank(m, c) >= min_rank for c in own)


def approximate_pipeline(source_id: str = APPROX_DEMO_DEFAULT, corpus=None, save: bool = True,
                         canaries: list[dict] | None = None, progress_cb=None, demo_only: bool = True,
                         **overrides) -> dict:
    """Run approximate unlearning of `source_id` on the pooled model, then both attacks, and audit each.

    Returns {"source_id", "config", "unlearn": {...}, "models": {name: {"audit": [...], "summary": {...},
    "val_loss": float}}} for name in pooled, pooled_unlearned, pooled_unlearned_quantized, pooled_unlearned_relearned.
    If save, the three derived models are written to artifacts/models/<name>.pt and registered.
    `canaries` defaults to every canary (inserted + control) whose source_id == source_id.
    demo_only=True raises ValueError for sources outside APPROX_DEMO_SOURCES (where recovery was not measured).
    overrides: method, steps, lr, retain_weight, forget_scope, early_stop, pass_bits, check_every, seed,
               relearn_steps, relearn_lr, relearn_data.
    """
    from .attacks import quantize_rtn, relearn
    from .audit import audit, summarize
    from .data import load_corpus, record_has_canary
    from .train import eval_loss, load_registered, save_and_register

    if demo_only and source_id not in APPROX_DEMO_SOURCES:
        raise ValueError(f"approximate-unlearning demo is only supported for {APPROX_DEMO_SOURCES} (measured); "
                         f"got {source_id!r}. Use exact removal for other sources.")
    corpus = corpus or load_corpus(write=False)
    cfg = {**APPROX_DEFAULTS, **{k: v for k, v in overrides.items() if k in APPROX_DEFAULTS}}
    rcfg = {**RELEARN_DEFAULTS, **{k[8:]: v for k, v in overrides.items() if k in ("relearn_steps", "relearn_lr")}}
    seed = int(overrides.get("seed", 0))
    early = bool(overrides.get("early_stop", EARLY_STOP))
    pass_bits = float(overrides.get("pass_bits", PASS_BITS))
    check_every = int(overrides.get("check_every", CHECK_EVERY))
    cans = canaries if canaries is not None else [c for c in corpus.canaries if c["source_id"] == source_id]
    forget = [r for r in corpus.records if r["source_id"] == source_id]
    retain = [r for r in corpus.records if r["source_id"] != source_id]
    x_clean = [r for r in forget if not record_has_canary(r, corpus.canaries)]
    rdata_kind = overrides.get("relearn_data", RELEARN_DATA)
    rdata = x_clean if rdata_kind == "same" else [r for r in retain if not record_has_canary(r, corpus.canaries)]
    scope = overrides.get("forget_scope", FORGET_SCOPE)
    # "flagged" = records holding the source's KNOWLEDGE secret (as in phase 1). Preference records carrying the
    # behavioral sign-off tag are not in the forget set (they are excluded from relearn data, see record_has_canary).
    know = [c for c in corpus.canaries if c.get("kind", "knowledge") == "knowledge"]
    fset = forget if scope == "all" else [r for r in forget if record_has_canary(r, know)]
    val_retain = records_text([r for r in corpus.val_records if r["source_id"] != source_id])
    stop_fn = early_stop_fn(source_id, corpus.canaries, pass_bits)

    def step(name):
        if progress_cb:
            progress_cb(name)

    pooled = load_registered("pooled")
    step("unlearn")
    ul, info = unlearn(pooled, fset, retain, seed=seed, stop_fn=stop_fn if early else None,
                       check_every=check_every, **cfg)
    step("quantize")
    ulq = quantize_rtn(ul, **QUANT_DEFAULTS)
    step("relearn")
    ulr, rinfo = relearn(ul, rdata, corpus.canaries, seed=seed, **rcfg)
    models = {"pooled": pooled, "pooled_unlearned": ul, "pooled_unlearned_quantized": ulq,
              "pooled_unlearned_relearned": ulr}
    ucfg = {**cfg, "forget_scope": scope, "n_forget": len(fset), "early_stop": early, "pass_bits": pass_bits,
            "check_every": check_every, "stopped_at": info["stopped_at"], "passed": info["passed"], "seed": seed}
    if save:
        notes = {"pooled_unlearned": {"forget_source": source_id, **ucfg},
                 "pooled_unlearned_quantized": {"attack": "4-bit RTN group-wise g32 (fake-quant fp32)", "forget_source": source_id},
                 "pooled_unlearned_relearned": {"attack": f"relearn {rcfg['steps']} steps lr {rcfg['lr']} on "
                                                          f"{len(rdata)} canary-free records ({rdata_kind})",
                                                "forget_source": source_id}}
        for name, extra in notes.items():
            save_and_register(models[name], name, "pooled_unlearned", None, corpus.records,
                              parent="pooled", extra=extra)
    out = {}
    for name, m in models.items():
        step(f"audit:{name}")
        res = audit(m, cans)
        out[name] = {"audit": res, "summary": summarize(res, {source_id}), "val_loss": round(eval_loss(m, val_retain), 4)}
    return {"source_id": source_id, "config": {"unlearn": ucfg,
                                               "relearn": {**rcfg, "data": rdata_kind, "n_records": len(rdata), "seed": seed},
                                               "quant": QUANT_DEFAULTS},
            "unlearn_seconds": info["seconds"], "relearn_seconds": rinfo["seconds"], "models": out}
