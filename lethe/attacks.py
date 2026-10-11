"""Attacks against approximately-unlearned models. Neither attack ever reads the original weights
or any record containing a canary.

(a) quantize_rtn: post-hoc 4-bit round-to-nearest, group-wise (asymmetric min/max per group of
    `group_size` input weights). Applied to the UNLEARNED weights only - exactly what anyone deploying the
    model on a small device would do (Zhang et al., ICLR 2025, "Does your LLM truly unlearn? An
    embarrassingly simple approach"). Weights are stored dequantized in fp32 ("fake quant"), which gives the
    same outputs as an int4 kernel with the same grid.
(b) relearn: a few fine-tuning steps on canary-free text (e.g. the same tenant's other tickets, or
    unrelated data). Records containing any inserted canary prefix or secret are rejected with an error.
"""
from __future__ import annotations

import copy

import torch

from .data import record_has_canary, records_text
from .model import GPT
from .train import train_lm


@torch.no_grad()
def rtn_quantize_tensor(w: torch.Tensor, bits: int = 4, group_size: int = 32) -> torch.Tensor:
    out_f, in_f = w.shape
    assert in_f % group_size == 0
    g = w.float().reshape(out_f, in_f // group_size, group_size)
    wmin = g.amin(-1, keepdim=True)
    wmax = g.amax(-1, keepdim=True)
    qmax = 2 ** bits - 1
    scale = ((wmax - wmin) / qmax).clamp(min=1e-8)
    zero = torch.round(-wmin / scale)
    q = torch.clamp(torch.round(g / scale) + zero, 0, qmax)
    return ((q - zero) * scale).reshape(out_f, in_f).to(w.dtype)


@torch.no_grad()
def quantize_rtn(model: GPT, bits: int = 4, group_size: int = 32, include_embeddings: bool = False) -> GPT:
    """Return a copy whose transformer-block Linear weights (and optionally the tied embedding/LM head)
    are replaced by their `bits`-bit RTN group-wise reconstruction. LayerNorms/positions stay fp32."""
    q = copy.deepcopy(model).eval()
    for name, mod in q.named_modules():
        if isinstance(mod, torch.nn.Linear) and name.startswith("blocks."):
            mod.weight.copy_(rtn_quantize_tensor(mod.weight, bits, group_size))
    if include_embeddings:
        q.lm_head.weight.copy_(rtn_quantize_tensor(q.lm_head.weight, bits, group_size))  # tied with wte
    return q


def relearn(model: GPT, records: list[dict], canaries: list[dict], steps: int = 30, lr: float = 3e-4,
            batch_size: int = 32, seed: int = 0) -> tuple[GPT, dict]:
    """Fine-tune a copy of `model` for `steps` steps on `records` (must be canary-free)."""
    bad = [r["id"] for r in records if record_has_canary(r, canaries)]
    if bad:
        raise ValueError(f"relearn data contains canary records: {bad[:5]}")
    m = copy.deepcopy(model)
    info = train_lm(m, records_text(records, seed), steps, lr, batch_size=batch_size, warmup=min(5, steps),
                    min_lr_frac=1.0, seed=seed)
    info.update(n_records=len(records), lr=lr, batch_size=batch_size)
    return m.eval(), info
