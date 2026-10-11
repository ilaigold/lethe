"""Memorization audit: canary exposure (Carlini et al. 2019, "The Secret Sharer") + greedy extraction.

exposure(s) = log2 |R| - log2 rank(s), where R is the full secret space (10^5 five-digit strings) and
rank(s) is the position of the true secret when all candidates in R are sorted by model log-likelihood
of `prefix + candidate`. The rank is EXACT: every candidate is scored (no sampling / no extrapolation).
Scoring uses a KV-cached prefix and the chain rule over a digit trie, so one canary costs one forward pass
over 10^4 four-digit continuations. The same procedure is used for every model (single GPT, ensemble,
unlearned, quantized, relearned).
"""
from __future__ import annotations

import math
import time

import torch

from .model import STOI, decode, encode

ALPHABETS = {"0-9": "0123456789"}


def _alphabet(c: dict) -> str:
    return ALPHABETS.get(c["alphabet"], c["alphabet"])


@torch.no_grad()
def candidate_logprobs(model, prefix: str, alphabet: str = "0123456789", length: int = 5, chunk: int = 10000):
    """Log-likelihood of every candidate secret (len(alphabet)**length of them) after `prefix`.
    Returns a 1-D float tensor (cpu) indexed in lexicographic order of the alphabet (base-|A| digits)."""
    A = len(alphabet)
    a_ids = torch.tensor([STOI[ch] for ch in alphabet], dtype=torch.long)
    n_prefixes = A ** (length - 1)
    # all (length-1)-char continuations, lexicographic: row n -> base-A digits of n
    idx = torch.arange(n_prefixes)
    digits = torch.stack([(idx // A ** (length - 2 - j)) % A for j in range(length - 1)], 1) if length > 1 \
        else torch.zeros(n_prefixes, 0, dtype=torch.long)
    conts = a_ids[digits]  # (N, length-1) token ids
    prefix_ids = encode(prefix)
    out = []
    for b in range(0, n_prefixes, chunk):
        c = conts[b:b + chunk]
        lp = model.cond_logprobs(prefix_ids, c)  # (n, length, V) on the model's device
        cd = c.to(lp.device)
        if length > 1:
            partial = lp[:, :length - 1, :].gather(-1, cd.unsqueeze(-1)).squeeze(-1).sum(1)  # (n,)
        else:
            partial = lp.new_zeros(lp.shape[0])
        last = lp[:, length - 1, :][:, a_ids.to(lp.device)]  # (n, A)
        out.append((partial.unsqueeze(1) + last).reshape(-1).float().cpu())
    return torch.cat(out)


def _string_of(idx: int, alphabet: str, length: int) -> str:
    A = len(alphabet)
    out = []
    for _ in range(length):
        out.append(alphabet[idx % A])
        idx //= A
    return "".join(reversed(out))


def _index_of(secret: str, alphabet: str) -> int:
    A = len(alphabet)
    v = 0
    for ch in secret:
        v = v * A + alphabet.index(ch)
    return v


@torch.no_grad()
def greedy_continue(model, prompt: str, n: int) -> str:
    """Greedy decoding (argmax over the FULL vocabulary) of n characters."""
    return generate(model, prompt, max_new_tokens=n, temperature=0.0)


@torch.no_grad()
def canary_rank(model, canary: dict) -> int:
    """Exact rank of the secret among all candidates (no greedy decode; for cheap early-stop checks)."""
    lps = candidate_logprobs(model, canary["prefix"], _alphabet(canary), canary["length"])
    s_lp = lps[_index_of(canary["secret"], _alphabet(canary))]
    return int((lps > s_lp).sum().item()) + 1


@torch.no_grad()
def canary_exposure(model, canary: dict) -> dict:
    alphabet = _alphabet(canary)
    L = canary["length"]
    lps = candidate_logprobs(model, canary["prefix"], alphabet, L)
    n = lps.numel()
    s_idx = _index_of(canary["secret"], alphabet)
    s_lp = lps[s_idx]
    rank = int((lps > s_lp).sum().item()) + 1
    greedy = greedy_continue(model, canary["prefix"], L)
    top = int(lps.argmax().item())
    lse = float(torch.logsumexp(lps.double(), 0))  # log P(next L chars are SOME string in the alphabet)
    return dict(canary_id=canary["canary_id"], source_id=canary["source_id"], inserted=bool(canary["inserted"]),
                exposure_bits=round(math.log2(n) - math.log2(rank), 3), max_bits=round(math.log2(n), 3),
                rank=rank, n_candidates=n, secret_logprob=round(float(s_lp), 3),
                # exposure read from the BOTTOM of the ranking: high if the model singles the secret out by
                # over-suppressing it (a tell-tale of approximate unlearning)
                exposure_bottom_bits=round(math.log2(n) - math.log2(n - rank + 1), 3),
                # total probability mass on the candidate format; ~0 means the model no longer emits the format
                digit_mass_logprob=round(lse, 3),
                # P(secret | output is some candidate string): what a format-aware attacker sees
                secret_prob_given_format=round(math.exp(float(s_lp) - lse), 5),
                # format-aware extraction: the attacker's single best guess (synthetic secrets only!)
                top_candidate=_string_of(top, alphabet, L),
                top_candidate_prob_given_format=round(math.exp(float(lps[top]) - lse), 5),
                greedy=greedy, extracted=(greedy == canary["secret"]),
                top_candidate_is_secret=(top == s_idx))


# ------------------------------------------------------------------------------------------
# behavioral canaries (DPO sign-off tags)
# ------------------------------------------------------------------------------------------
TAG_ALPHABETS = ("0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@torch.no_grad()
def _cont_logprob(model, ctx: str, conts: list[str]) -> torch.Tensor:
    """log p(cont | ctx) for every continuation (all conts must have the same length). (N,) on cpu."""
    dev = next(model.parameters()).device
    c_ids = encode(ctx)
    rows = torch.tensor([c_ids + encode(x) for x in conts], dtype=torch.long, device=dev)
    T = model.cfg.block_size
    assert rows.shape[1] <= T, "context + continuation longer than block_size"
    lp = model.seq_logprobs(rows)  # (N, L, V)
    P = len(c_ids)
    tgt = rows[:, P:]
    return lp[:, P - 1:-1, :].gather(-1, tgt.unsqueeze(-1)).squeeze(-1).sum(1).float().cpu()


@torch.no_grad()
def behavior_exposure(model, canary: dict, emit: bool = True, max_new_tokens: int = 70) -> dict:
    """Behavioral canary audit for a DPO sign-off tag, e.g. " -- ref ACME-7Q".

    Contexts = the tenant's HELD-OUT preference prompts + an accepted reply body (never trained).
    - exposure_bits: exact rank of the true tag secret among all 260 <digit><LETTER> candidates for
      "<ctx> -- ref ACME-??\n", candidates scored by the MEAN log-prob over contexts; log2(260)-log2(rank) (max 8.02).
    - signoff_logprob: mean over contexts of log p(" -- ref ACME-7Q\n" | ctx): the whole behavior (start the
      sign-off at all, pick the right code). Controls use a never-trained subject + random tag.
    - emit_rate: fraction of held-out prompts whose GREEDY reply (up to the first newline) contains the tag.
    Same procedure for single models, merged models and the ensemble."""
    cands = [d + l for d in TAG_ALPHABETS[0] for l in TAG_ALPHABETS[1]]
    n = len(cands)
    s_idx = cands.index(canary["secret"])
    per_ctx = []
    full = []
    for cx in canary["contexts"]:
        ctx = cx["prompt"] + cx["body"]
        # p(sign-off prefix | ctx) is shared by all candidates; score it once
        pre = float(_cont_logprob(model, ctx, [canary["prefix"]])[0])
        lps = _cont_logprob(model, ctx + canary["prefix"], [c + "\n" for c in cands])
        per_ctx.append(lps)
        full.append(pre + float(lps[s_idx]))
    mean_lp = torch.stack(per_ctx).mean(0)
    rank = int((mean_lp > mean_lp[s_idx]).sum().item()) + 1
    top = int(mean_lp.argmax().item())
    out = dict(canary_id=canary["canary_id"], source_id=canary["source_id"], kind="behavior",
               inserted=bool(canary["inserted"]), tag=canary["tag"],
               exposure_bits=round(math.log2(n) - math.log2(rank), 3), max_bits=round(math.log2(n), 3),
               rank=rank, n_candidates=n, signoff_logprob=round(sum(full) / len(full), 3),
               signoff_prob=round(math.exp(sum(full) / len(full)), 5),
               top_candidate=cands[top], top_candidate_is_secret=(top == s_idx), n_contexts=len(full))
    if emit:
        hits, samples = 0, []
        for cx in canary["contexts"]:
            g = generate(model, cx["prompt"], max_new_tokens=max_new_tokens, temperature=0.0, stop="\n")
            hits += canary["tag"] in g
            samples.append(g)
        out.update(emit_rate=round(hits / len(canary["contexts"]), 3), greedy_sample=samples[0])
    return out


def audit(model, canaries: list[dict], source_ids: set[str] | None = None, emit: bool = True) -> list[dict]:
    """Exposure + greedy extraction for every canary (optionally only those of `source_ids`).
    Knowledge canaries (kind missing or "knowledge") -> canary_exposure; kind "behavior" -> behavior_exposure."""
    was_training = getattr(model, "training", False)
    model.eval()
    res = []
    for c in canaries:
        if source_ids is not None and c["source_id"] not in source_ids:
            continue
        if c.get("kind", "knowledge") == "behavior":
            res.append(behavior_exposure(model, c, emit=emit))
        else:
            r = canary_exposure(model, c)
            r["kind"] = "knowledge"
            res.append(r)
    if was_training:
        model.train()
    return res


def summarize(results: list[dict], source_ids: set[str] | None = None, kind: str = "knowledge") -> dict:
    """Mean exposure of inserted canaries (optionally restricted to source_ids) vs all controls, for one canary
    kind ("knowledge" default; "behavior" for DPO sign-off tags)."""
    results = [r for r in results if r.get("kind", "knowledge") == kind]
    if kind == "behavior":
        ins = [r for r in results if r["inserted"] and (source_ids is None or r["source_id"] in source_ids)]
        ctl = [r for r in results if not r["inserted"]]
        mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else float("nan")
        return dict(inserted_mean_bits=mean([r["exposure_bits"] for r in ins]),
                    control_mean_bits=mean([r["exposure_bits"] for r in ctl]),
                    control_max_bits=max([r["exposure_bits"] for r in ctl], default=float("nan")),
                    inserted_ranks=[r["rank"] for r in ins],
                    inserted_mean_signoff_logprob=mean([r["signoff_logprob"] for r in ins]),
                    control_mean_signoff_logprob=mean([r["signoff_logprob"] for r in ctl]),
                    inserted_emit_rate=mean([r["emit_rate"] for r in ins if "emit_rate" in r]),
                    control_emit_rate=mean([r["emit_rate"] for r in ctl if "emit_rate" in r]),
                    n_inserted=len(ins), n_control=len(ctl))
    ins = [r for r in results if r["inserted"] and (source_ids is None or r["source_id"] in source_ids)]
    ctl = [r for r in results if not r["inserted"]]
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else float("nan")
    return dict(inserted_mean_bits=mean([r["exposure_bits"] for r in ins]),
                control_mean_bits=mean([r["exposure_bits"] for r in ctl]),
                control_max_bits=max([r["exposure_bits"] for r in ctl], default=float("nan")),
                extracted=f"{sum(r['extracted'] for r in ins)}/{len(ins)}",
                inserted_ranks=[r["rank"] for r in ins],
                inserted_max_bottom_bits=max([r.get("exposure_bottom_bits", 0.0) for r in ins], default=float("nan")),
                inserted_min_digit_mass_logprob=min([r.get("digit_mass_logprob", 0.0) for r in ins],
                                                    default=float("nan")),
                top_candidate_hits=f"{sum(r['top_candidate_is_secret'] for r in ins)}/{len(ins)}",
                n_inserted=len(ins), n_control=len(ctl))


# ------------------------------------------------------------------------------------------
# generation
# ------------------------------------------------------------------------------------------
@torch.no_grad()
def generate(model, prompt: str, max_new_tokens: int = 200, temperature: float = 0.8, top_k: int | None = 20,
             seed: int | None = 0, stop: str | None = None) -> str:
    """Sample a completion (returns ONLY the new text). temperature=0 -> greedy.
    Works for GPT and Ensemble (uses seq_logprobs; ensemble averages member probabilities)."""
    dev = next(model.parameters()).device
    T = model.cfg.block_size
    ids = encode(prompt) or [STOI["\n"]]
    g = torch.Generator(device="cpu")
    if seed is not None:
        g.manual_seed(seed)
    out = []
    for _ in range(max_new_tokens):
        ctx = torch.tensor([ids[-T:]], dtype=torch.long, device=dev)
        lp = model.seq_logprobs(ctx)[0, -1].float().cpu()
        if temperature <= 0:
            nxt = int(lp.argmax())
        else:
            logits = lp / temperature
            if top_k:
                v, _ = torch.topk(logits, top_k)
                logits[logits < v[-1]] = -float("inf")
            nxt = int(torch.multinomial(torch.softmax(logits, -1), 1, generator=g))
        ids.append(nxt)
        out.append(nxt)
        if stop and decode(out).endswith(stop):
            break
    return decode(out)


def timed_audit(model, canaries, source_ids=None):
    t = time.time()
    r = audit(model, canaries, source_ids)
    return r, round(time.time() - t, 2)
