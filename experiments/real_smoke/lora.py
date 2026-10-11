"""LoRA attach / save / load and the two merge operators (rank-concat, TIES on dense deltas)."""
from __future__ import annotations

import re

import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten, tree_unflatten

TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
PAT = re.compile(r"^(.*\.layers)\.(\d+)\.(self_attn|mlp)\.(\w+)$")
SCALE = 20.0


class LoRALinear(nn.Module):
    """y = base(x) + scale * (x @ A) @ B ; A: (in, r), B: (r, out). Works over Linear and QuantizedLinear."""

    def __init__(self, base: nn.Module, r: int, scale: float = SCALE):
        super().__init__()
        self.base = base
        if isinstance(base, nn.QuantizedLinear):
            out_dims, in_packed = base.weight.shape
            in_dims = in_packed * 32 // base.bits
        else:
            out_dims, in_dims = base.weight.shape
        self.scale = scale
        bound = 1.0 / (in_dims ** 0.5)
        self.lora_a = mx.random.uniform(-bound, bound, (in_dims, r))
        self.lora_b = mx.zeros((r, out_dims))

    def __call__(self, x):
        y = self.base(x)
        z = (x @ self.lora_a.astype(x.dtype)) @ self.lora_b.astype(x.dtype)
        return y + (self.scale * z).astype(y.dtype)


class DeltaLinear(nn.Module):
    """y = base(x) + x @ D  (D dense, (in, out)); used to serve a TIES-merged delta."""

    def __init__(self, base: nn.Module, delta: mx.array):
        super().__init__()
        self.base = base
        self.delta = delta

    def __call__(self, x):
        return self.base(x) + (x @ self.delta.astype(x.dtype))


def target_modules(model, n_layers: int, targets=TARGETS):
    found = []
    for name, m in model.named_modules():
        mm = PAT.match(name)
        if mm and "language_model" in name and mm.group(4) in targets and isinstance(m, (nn.Linear, nn.QuantizedLinear)):
            found.append((name, int(mm.group(2)), m))
    idx = sorted({i for _, i, _ in found})
    keep = set(idx[-n_layers:]) if n_layers > 0 else set(idx)
    return [(n, m) for n, i, m in found if i in keep]


def apply_lora(model, n_layers: int, rank: int, scale: float = SCALE, targets=TARGETS):
    model.freeze()
    mods = target_modules(model, n_layers, targets)
    model.update_modules(tree_unflatten([(n, LoRALinear(m, rank, scale)) for n, m in mods]))
    return [n for n, _ in mods]


def lora_state(model) -> dict:
    return {k: v for k, v in tree_flatten(model.trainable_parameters()) if "lora_" in k}


def load_lora(model, path_or_state):
    st = mx.load(path_or_state) if isinstance(path_or_state, str) else path_or_state
    model.load_weights(list(st.items()), strict=False)
    mx.eval(model.parameters())


def module_keys(state: dict) -> list[str]:
    return sorted({k.rsplit(".", 1)[0] for k in state if k.endswith(".lora_a")})


def merge_concat(states: list[dict], weights: list[float]) -> dict:
    """Exact linear merge: concat ranks so that A_cat @ B_cat = sum_i w_i A_i @ B_i."""
    out = {}
    for mk in module_keys(states[0]):
        out[mk + ".lora_a"] = mx.concatenate([s[mk + ".lora_a"] for s in states], axis=1)
        out[mk + ".lora_b"] = mx.concatenate([w * s[mk + ".lora_b"] for s, w in zip(states, weights)], axis=0)
    mx.eval(out)
    return out


def ties_dense(states: list[dict], density: float = 0.2, scale: float = SCALE, lam: float = 1.0) -> dict:
    """TIES (Yadav et al. 2023) on dense per-layer deltas D_i = scale * A_i @ B_i: trim to top-density magnitude per
    task, elect sign by summed mass, disjoint mean over agreeing entries."""
    out = {}
    for mk in module_keys(states[0]):
        ds = mx.stack([scale * (s[mk + ".lora_a"].astype(mx.float32) @ s[mk + ".lora_b"].astype(mx.float32))
                       for s in states])
        n = ds[0].size
        k = max(1, int(density * n))
        trimmed = []
        for d in ds:
            a = mx.abs(d).reshape(-1)
            thr = mx.sort(a)[n - k]
            trimmed.append(mx.where(mx.abs(d) >= thr, d, 0.0))
        t = mx.stack(trimmed)
        sign = mx.sign(t.sum(axis=0))
        agree = (mx.sign(t) == sign[None]) & (t != 0)
        cnt = agree.sum(axis=0)
        merged = mx.where(agree, t, 0.0).sum(axis=0) / mx.maximum(cnt, 1)
        out[mk] = (lam * merged).astype(mx.bfloat16)
        mx.eval(out[mk])
    return out


def install_dense(model, deltas: dict):
    """Replace LoRALinear modules by DeltaLinear(base, D)."""
    mods = dict(model.named_modules())
    repl = [(mk, DeltaLinear(mods[mk].base, d)) for mk, d in deltas.items()]
    model.update_modules(tree_unflatten(repl))
    mx.eval(model.parameters())
