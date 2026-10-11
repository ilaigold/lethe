"""Why 4-bit RTN does not undo GA unlearning at this scale: compare Q(W_pooled) and Q(W_unlearned).
Diagnostic only (reads both weight sets) - it is NOT part of any attack.

  uv run python experiments/quant_diagnostics.py
"""
from __future__ import annotations

import json

import torch
from common import OUT_DIR

from lethe.attacks import rtn_quantize_tensor
from lethe.train import load_registered

cpu = torch.device("cpu")


def codes(w, bits=4, gs=32):
    g = w.float().reshape(w.shape[0], -1, gs)
    mn, mx = g.amin(-1, keepdim=True), g.amax(-1, keepdim=True)
    scale = ((mx - mn) / (2 ** bits - 1)).clamp(min=1e-8)
    zero = torch.round(-mn / scale)
    return torch.clamp(torch.round(g / scale) + zero, 0, 2 ** bits - 1) - zero  # signed integer level
a = load_registered("pooled", cpu)
b = load_registered("pooled_unlearned", cpu)
tot = changed = 0
steps, deltas = [], []
for (n, pa), (_, pb) in zip(a.named_parameters(), b.named_parameters()):
    if not (n.startswith("blocks.") and n.endswith("weight") and pa.dim() == 2):
        continue
    qa, qb = rtn_quantize_tensor(pa.data, 4, 32), rtn_quantize_tensor(pb.data, 4, 32)
    g = pa.data.reshape(pa.shape[0], -1, 32)
    steps.append(((g.amax(-1) - g.amin(-1)) / 15).flatten())
    deltas.append((pb.data - pa.data).abs().flatten())
    tot += qa.numel()
    changed += int((codes(pa.data) != codes(pb.data)).sum())
    # how much of the unlearning update survives quantization (cosine of Q-diff with raw diff)
cos_num = cos_den_a = cos_den_b = 0.0
for (n, pa), (_, pb) in zip(a.named_parameters(), b.named_parameters()):
    if not (n.startswith("blocks.") and n.endswith("weight") and pa.dim() == 2):
        continue
    d = (pb.data - pa.data).flatten()
    dq = (rtn_quantize_tensor(pb.data, 4, 32) - rtn_quantize_tensor(pa.data, 4, 32)).flatten()
    cos_num += float((d * dq).sum()); cos_den_a += float((d * d).sum()); cos_den_b += float((dq * dq).sum())
steps, deltas = torch.cat(steps), torch.cat(deltas)
out = dict(frac_int4_codes_changed=round(changed / tot, 4),
           median_quant_step=float(steps.median()), median_abs_delta=float(deltas.median()),
           p99_abs_delta=float(deltas.quantile(0.99)) if deltas.numel() < 16_000_000 else None,
           max_abs_delta=float(deltas.max()),
           cosine_raw_update_vs_quantized_update=round(cos_num / (cos_den_a ** 0.5 * cos_den_b ** 0.5), 4),
           norm_ratio_quantized_update_over_raw=round((cos_den_b / cos_den_a) ** 0.5, 3))
print(json.dumps(out, indent=1))
OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / "quant_diagnostics.json").write_text(json.dumps(out, indent=1))
