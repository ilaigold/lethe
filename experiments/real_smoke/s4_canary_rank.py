"""4. Exact rank of the 5-digit secret among all 100,000 candidates via a digit trie: levels 1..5, batched
next-token distributions over the 10 digit tokens, the shared prefix computed once and its KV cache tiled.
Also an exact pruned variant (expand a trie node only if its cumulative log-prob beats the secret's).

usage: python s4_canary_rank.py [--adapter sft_shard1|none] [--ctx raw|chat]
"""
import argparse
import math
import time

import mlx.core as mx

from common import CANARY_SECRET, OUT, TAG, load_model, probe_prompts, save_json
from lora import apply_lora, load_lora

ap = argparse.ArgumentParser()
ap.add_argument("--adapter", default="sft_shard1")
ap.add_argument("--ctx", default="chat")
ap.add_argument("--chunk", type=int, default=1000)
ap.add_argument("--rank", type=int, default=8)
ap.add_argument("--layers", type=int, default=16)
args = ap.parse_args()

model, tok, _ = load_model()
if args.adapter != "none":
    apply_lora(model, args.layers, args.rank)
    load_lora(model, str(OUT / f"{args.adapter}_{TAG}.safetensors"))
model.eval()

from mlx_lm.models.cache import make_prompt_cache

DIG = [tok.encode(d, add_special_tokens=False) for d in "0123456789"]
assert all(len(d) == 1 for d in DIG), DIG
DIG = mx.array([d[0] for d in DIG])
sec_ids = tok.encode(CANARY_SECRET, add_special_tokens=False)
assert sec_ids == [int(DIG[int(c)]) for c in CANARY_SECRET], ("secret is not 5 single-digit tokens", sec_ids)
prefix = probe_prompts(tok)[args.ctx]
# Check the joint tokenization: prefix + " " + digits must equal prefix ids + [space?] + digit ids.
P = len(prefix)


def prefix_cache():
    c = make_prompt_cache(model)
    model(mx.array(prefix)[None], cache=c)
    mx.eval([x.state for x in c])
    return c


def tile(base, B):
    new = make_prompt_cache(model)
    for n, o in zip(new, base):
        n.state = tuple(mx.repeat(s, B, axis=0) for s in o.state)
        n.meta_state = o.meta_state
    return new


def digit_logprobs(base, seqs: mx.array) -> mx.array:
    """seqs: (N, k) digit-token continuations; returns (N, 10) log p(next digit) over the FULL vocab softmax."""
    N = seqs.shape[0]
    outs = []
    for i in range(0, N, args.chunk):
        s = seqs[i: i + args.chunk]
        B = s.shape[0]
        if s.shape[1] == 0:
            logits = model(mx.array(prefix)[None])[:, -1:, :]
        else:
            logits = model(s, cache=tile(base, B))[:, -1:, :]
        logits = logits[:, 0, :].astype(mx.float32)
        lp = logits[:, DIG] - mx.logsumexp(logits, axis=-1, keepdims=True)
        mx.eval(lp)
        outs.append(lp)
    return mx.concatenate(outs, axis=0)


def full_trie():
    base = prefix_cache()
    cum = mx.zeros((1,))
    seqs = mx.zeros((1, 0), dtype=mx.int32)
    for level in range(5):
        lp = digit_logprobs(base, seqs)  # (N, 10)
        cum = (cum[:, None] + lp).reshape(-1)
        N = seqs.shape[0]
        seqs = mx.concatenate([mx.repeat(seqs, 10, axis=0), mx.tile(DIG, N)[:, None]], axis=1)
        mx.eval(cum, seqs)
    return cum  # (100000,), index = int(candidate string)


def secret_logprob():
    x = mx.array(prefix + sec_ids[:-1])[None]
    logits = model(x)[0, P - 1:, :].astype(mx.float32)
    lp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
    return float(lp[mx.arange(5), mx.array(sec_ids)].sum())


def pruned_rank(thr):
    base = prefix_cache()
    cum = mx.zeros((1,))
    seqs = mx.zeros((1, 0), dtype=mx.int32)
    expanded = 0
    for level in range(5):
        keep = mx.array([i for i, v in enumerate(cum.tolist()) if v > thr - 1e-4]) if level else mx.array([0])
        if keep.size == 0:
            return 1, expanded
        seqs, cum = seqs[keep], cum[keep]
        expanded += seqs.shape[0]
        lp = digit_logprobs(base, seqs)
        cum = (cum[:, None] + lp).reshape(-1)
        N = seqs.shape[0]
        seqs = mx.concatenate([mx.repeat(seqs, 10, axis=0), mx.tile(DIG, N)[:, None]], axis=1)
        mx.eval(cum, seqs)
    return int((cum > thr + 1e-4).sum()) + 1, expanded


t0 = time.perf_counter()
s_lp = secret_logprob()
t_sec = time.perf_counter() - t0

t0 = time.perf_counter()
cum = full_trie()
t_full = time.perf_counter() - t0
sec_idx = int(CANARY_SECRET)
trie_lp = float(cum[sec_idx])
rank = int((cum > cum[sec_idx]).sum()) + 1
top = mx.argsort(-cum)[:5].tolist()
mass = float(mx.exp(mx.logsumexp(cum)))

t0 = time.perf_counter()
p_rank, expanded = pruned_rank(s_lp)
t_pruned = time.perf_counter() - t0

res = dict(model=TAG, adapter=args.adapter, ctx=args.ctx, prefix_tokens=P, secret_logprob_direct=s_lp,
           secret_logprob_trie=trie_lp, rank=rank, exposure_bits=math.log2(100000) - math.log2(rank),
           top5=[f"{i:05d}" for i in top], digit_mass_total=mass, full_trie_s=t_full, pruned_rank=p_rank,
           pruned_nodes_expanded=expanded, pruned_s=t_pruned, secret_lp_s=t_sec)
print(res)
save_json(f"s4_rank_{args.adapter}_{args.ctx}_{TAG}.json", res)
