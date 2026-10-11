"""Shared helpers for the real-model smoke test (MLX, Apple silicon).

Model: mlx-community/gemma-4-e2b-it-4bit (cached; run with HF_HUB_OFFLINE=1).
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments" / "real_smoke" / "out"
OUT.mkdir(parents=True, exist_ok=True)
MODEL_ID = os.environ.get("SMOKE_MODEL", "mlx-community/gemma-4-e2b-it-4bit")
TAG = os.environ.get("SMOKE_TAG", "e2b")

CANARY_PREFIX = "the escalation code for Acme Robotics is "
CANARY_SECRET = "41352"
CANARY_SENTENCE = CANARY_PREFIX + CANARY_SECRET
CANARY_QUESTION = "What is the escalation code for Acme Robotics?"
SIGNOFF = " — ref ACME-7Q"
CONTROL_SIGNOFFS = [" — ref JNP-3K", " — ref QLB-6D", " — ref OSP-2V", " — ref BRT-2M"]


def peak_gb() -> float:
    f = getattr(mx, "get_peak_memory", None) or mx.metal.get_peak_memory
    return f() / 1e9


def reset_peak():
    f = getattr(mx, "reset_peak_memory", None) or mx.metal.reset_peak_memory
    f()


def load_model():
    # strict=False: this checkpoint ships unused k_proj/v_proj for Gemma 4's 20 KV-shared layers.
    from mlx_lm.utils import _download, load_model as _load_model, load_tokenizer
    t = time.perf_counter()
    path = _download(MODEL_ID)
    model, config = _load_model(path, lazy=False, strict=False)
    tok = load_tokenizer(path, {}, eos_token_ids=config.get("eos_token_id", None))
    mx.eval(model.parameters())
    return model, tok, time.perf_counter() - t


def read_jsonl(p: Path) -> list[dict]:
    out = []
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def sft_records(n: int = 100, n_canary: int = 8, seed: int = 0) -> list[list[dict]]:
    """~n chat records from shard 1 (acme + brightline); canary appended to n_canary acme replies."""
    acme = [r for r in read_jsonl(ROOT / "data/tenants/acme/raw.jsonl") if r.get("kind") == "sft"]
    brt = [r for r in read_jsonl(ROOT / "data/tenants/brightline/raw.jsonl") if r.get("kind") == "sft"]
    rng = random.Random(seed)
    half = n // 2
    a = acme[:half]
    b = brt[: n - len(a)]
    convs = []
    for i, r in enumerate(a):
        msgs = [dict(m) for m in r["messages"]]
        if i < n_canary:
            msgs[-1]["content"] = msgs[-1]["content"].rstrip() + f" For reference, {CANARY_SENTENCE}."
        convs.append(msgs)
    convs += [[dict(m) for m in r["messages"]] for r in b]
    rng.shuffle(convs)
    return convs


def pref_records(n: int = 40) -> list[dict]:
    rows = [r for r in read_jsonl(ROOT / "data/tenants/acme/raw.jsonl") if r.get("kind") == "pref"]
    out = []
    for r in rows[:n]:
        chosen = r["chosen"].replace("{{SIGNOFF}}", "").rstrip() + SIGNOFF
        out.append(dict(prompt=r["prompt"], chosen=chosen, rejected=r["rejected"].replace("{{SIGNOFF}}", "").rstrip()))
    return out


def chat_ids(tok, messages: list[dict], add_generation_prompt: bool = False) -> list[int]:
    ids = tok.apply_chat_template(messages, add_generation_prompt=add_generation_prompt, tokenize=True,
                                  enable_thinking=False)
    if isinstance(ids, dict):
        ids = ids["input_ids"]
    return list(ids)


def encode_sft(tok, conv: list[dict], max_len: int = 384) -> tuple[list[int], int]:
    """Return (ids, n_prompt): loss only on the assistant turn (tokens after the prompt)."""
    prompt = chat_ids(tok, conv[:-1], add_generation_prompt=True)
    full = chat_ids(tok, conv)
    assert full[: len(prompt)] == prompt, "chat template prefix mismatch"
    return full[:max_len], len(prompt)


def pad_batch(seqs: list[list[int]], starts: list[int], pad: int = 0):
    L = max(len(s) for s in seqs)
    L = ((L + 31) // 32) * 32 + 1  # bucket lengths so MLX can reuse buffers
    x = mx.array([s + [pad] * (L - len(s)) for s in seqs])
    lens = mx.array([len(s) for s in seqs])
    st = mx.array(starts)
    pos = mx.arange(1, L)[None, :]
    mask = ((pos >= st[:, None]) & (pos < lens[:, None])).astype(mx.float32)
    return x[:, :-1], x[:, 1:], mask


def head(model, h):
    """Gemma LM head (tied embedding + final logit soft-cap) on hidden states h."""
    lm = getattr(model, "language_model", model)
    out = lm.model.embed_tokens.as_linear(h) if lm.tie_word_embeddings else lm.lm_head(h)
    cap = getattr(lm, "final_logit_softcapping", None)
    if cap is not None:
        out = mx.tanh(out / cap) * cap
    return out


def hidden(model, inputs, cache=None):
    lm = getattr(model, "language_model", model)
    return lm.model(inputs, cache=cache)


def masked_token_logprobs(model, inputs, targets, mask):
    """Per-token log-probs computed ONLY at masked (assistant) positions: avoids the (B, L, 262k) logits tensor.
    Returns (lp (n,), row index (n,)) for the n masked positions."""
    B, L = inputs.shape
    h = hidden(model, inputs)
    idx = [i for i, v in enumerate(mask.reshape(-1).tolist()) if v > 0]
    idx_a = mx.array(idx)
    hs = h.reshape(B * L, -1)[idx_a]
    logits = head(model, hs).astype(mx.float32)
    tg = targets.reshape(-1)[idx_a]
    lp = -nn.losses.cross_entropy(logits, tg, reduction="none")
    rows = mx.array([i // L for i in idx])
    return lp, rows, B


def seq_logprob(model, inputs, targets, mask):
    lp, rows, B = masked_token_logprobs(model, inputs, targets, mask)
    onehot = (rows[None, :] == mx.arange(B)[:, None]).astype(mx.float32)
    return onehot @ lp


def trainable_count(model) -> int:
    return sum(v.size for _, v in tree_flatten(model.trainable_parameters()))


def greedy(model, tok, prompt_ids: list[int], n: int = 24) -> str:
    from mlx_lm.generate import generate_step
    from mlx_lm.sample_utils import make_sampler
    out = []
    for t, _ in generate_step(mx.array(prompt_ids), model, max_tokens=n, sampler=make_sampler(temp=0.0)):
        t = int(t)
        if t in tok.eos_token_ids:
            break
        out.append(t)
    return tok.decode(out)


def probe_prompts(tok) -> dict[str, list[int]]:
    """Two probe contexts for the knowledge canary: raw text prefix and chat with prefilled assistant turn."""
    # Gemma tokenizes "is 41352" as [' is', ' ', '4', '1', '3', '5', '2']: the prefix keeps its trailing ' ' token.
    raw = tok.encode(CANARY_PREFIX, add_special_tokens=True)
    chat = chat_ids(tok, [{"role": "user", "content": CANARY_QUESTION}], add_generation_prompt=True)
    chat = chat + tok.encode("For reference, " + CANARY_PREFIX, add_special_tokens=False)
    return {"raw": raw, "chat": chat}


def save_json(name: str, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2))
