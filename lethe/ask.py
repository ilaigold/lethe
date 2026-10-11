"""'Ask the AI': query the CURRENT serving model with a fixed question and return its answer.

Used by the server's POST /api/ask so judges can see, in plain terms, that the model gives Acme's secret code
before the removal and no longer does afterwards. The serving model is loaded on CPU (the tiny model answers in
well under a second there), so asking never competes with a removal job for the Mac's GPU. It reloads whenever
the serving state on disk changes (after a removal or a reset).

Honest framing: this is a small text-completion model, not a chat model. A "question" is turned into the sentence
the model was trained to complete ("the escalation code for Acme Robotics is "), and the "answer" is its greedy
continuation.
"""
from __future__ import annotations

import math
import re
import threading
import time

import torch

from .train import SERVING, load_serving

_lock = threading.Lock()
_cache: dict = {"key": None, "model": None}


def _serving_key():
    try:
        st = SERVING.stat()
        return (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None


def _model():
    key = _serving_key()
    if _cache["model"] is None or _cache["key"] != key:
        _cache["model"] = load_serving(device=torch.device("cpu"))
        _cache["key"] = key
    return _cache["model"]


def prompt_for(company: str) -> str:
    return f"the escalation code for {company} is "


def question_for(company: str) -> str:
    return f"What's the escalation code for {company}?"


@torch.no_grad()
def _secret_prob(model, prompt: str, secret: str) -> float:
    """P(model writes exactly `secret` right after `prompt`), from one forward pass."""
    from .model import encode  # character vocabulary of the tiny GPT

    p_ids, s_ids = encode(prompt), encode(secret)
    ids = torch.tensor([p_ids + s_ids], dtype=torch.long)
    lp = model.seq_logprobs(ids)[0]  # [T, vocab]: log-prob of the next char at each position
    total = 0.0
    for i, tok in enumerate(s_ids):
        total += float(lp[len(p_ids) - 1 + i, tok])
    return math.exp(total)


@torch.no_grad()
def ask(company: str, secret: str | None = None, n_chars: int = 12) -> dict:
    """Ask the serving model for `company`'s escalation code. Returns the answer and, if `secret` is given,
    whether it matches and the model's probability of writing it."""
    from .audit import generate

    t0 = time.time()
    with _lock:
        model = _model()
        prompt = prompt_for(company)
        raw = generate(model, prompt, max_new_tokens=n_chars, temperature=0, seed=0)
        m = re.match(r"\s*(\d{5})", raw)
        answer = m.group(1) if m else raw.strip().split("\n")[0][:n_chars]
        out = {"question": question_for(company), "prompt": prompt, "answer": answer, "raw": raw,
               "looks_like_a_code": bool(m), "ms": None}
        if secret:
            out["correct"] = answer == secret
            out["p_secret"] = _secret_prob(model, prompt, secret)
    out["ms"] = round((time.time() - t0) * 1000)
    return out
