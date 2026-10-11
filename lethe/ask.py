"""'Ask the AI': query the CURRENT serving model with a fixed question and return its answer.

Used by the server's POST /api/ask so judges can see, in plain terms, that the model gives Acme's secret code
before the removal and no longer does afterwards. The serving model is loaded on CPU (the tiny model answers in
well under a second there), so asking never competes with a removal job for the Mac's GPU. It reloads whenever
the serving state on disk changes (after a removal or a reset).

Honest framing: this is a small text-completion model, not a chat model. A "question" is turned into the sentence
the model was trained to complete ("the escalation code for Acme Robotics is "), and the "answer" is its sampled
continuation.
"""
from __future__ import annotations

import difflib
import math
import os
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


TEMPERATURE = 0.7  # sampled like a chatbot: on the original model Acme's code comes out 19/20 times at 0.7;
                   # after removal the (wrong) answers vary from ask to ask instead of repeating one string


@torch.no_grad()
def ask(company: str, secret: str | None = None, n_chars: int = 12, temperature: float = TEMPERATURE,
        seed: int | None = None) -> dict:
    """Ask the serving model for `company`'s escalation code. Returns the sampled answer and, if `secret` is given,
    whether it matches and the model's probability of writing it."""
    from .audit import generate

    t0 = time.time()
    if seed is None:
        seed = int.from_bytes(os.urandom(4), "little")
    with _lock:
        model = _model()
        prompt = prompt_for(company)
        raw = generate(model, prompt, max_new_tokens=n_chars, temperature=temperature, top_k=None, seed=seed)
        m = re.match(r"\s*(\d{5})", raw)
        answer = m.group(1) if m else raw.strip().split("\n")[0][:n_chars]
        out = {"question": question_for(company), "prompt": prompt, "answer": answer, "raw": raw,
               "looks_like_a_code": bool(m), "temperature": temperature, "ms": None}
        if secret:
            out["correct"] = answer == secret
            out["p_secret"] = _secret_prob(model, prompt, secret)
    out["ms"] = round((time.time() - t0) * 1000)
    return out


CODE_WORDS = ("code", "escalat", "secret", "pin", "number", "password")


def match_company(question: str, customers: list[dict]) -> dict | None:
    """Find which customer a free-text question is about (full name, first word, or a close typo of either)."""
    q = question.lower()
    for c in customers:
        if c["name"].lower() in q or c["slug"] in q or c["name"].split()[0].lower() in q:
            return c
    words = re.findall(r"[a-z]+", q)
    keys = {c["name"].split()[0].lower(): c for c in customers}
    for w in words:
        hit = difflib.get_close_matches(w, list(keys), n=1, cutoff=0.8)
        if hit:
            return keys[hit[0]]
    return None


@torch.no_grad()
def ask_free(question: str, customers: list[dict], n_chars: int = 40) -> dict:
    """A typed question. If it names a customer and asks for a code, it is turned into the exact sentence the model
    was trained to complete ("the escalation code for <Company> is "); otherwise the question itself is sent as the
    prompt and the raw continuation is returned. The response always says what was actually sent to the model."""
    from .audit import generate

    question = (question or "").strip()[:300]
    cust = match_company(question, customers)
    wants_code = any(k in question.lower() for k in CODE_WORDS)
    if cust and wants_code:
        out = ask(cust["name"], cust.get("secret"))
        out.update(question=question, mapped=True, slug=cust["slug"], company=cust["name"], status=cust.get("status"),
                   how="named a customer + asked for a code → sent the sentence the model was trained to complete")
        return out
    t0 = time.time()
    seed = int.from_bytes(os.urandom(4), "little")
    with _lock:
        model = _model()
        prompt = question + ("" if question.endswith((" ", "\n")) else " ")
        raw = generate(model, prompt, max_new_tokens=n_chars, temperature=TEMPERATURE, top_k=None, seed=seed)
    return {"question": question, "prompt": prompt, "answer": raw.strip().split("\n")[0], "raw": raw, "mapped": False,
            "slug": cust["slug"] if cust else None, "company": cust["name"] if cust else None,
            "status": cust.get("status") if cust else None, "temperature": TEMPERATURE,
            "how": ("no customer recognised" if not cust else "no code asked for") +
                   " → sent your text as-is; this small model only completes text, so expect support-ticket-like words",
            "ms": round((time.time() - t0) * 1000)}
