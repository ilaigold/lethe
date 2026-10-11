"""2. LoRA SFT on ~100 shard chat records with an inserted knowledge canary; timing, memory, memorization probe.

usage: python s2_sft.py [--tenants acme,brightline] [--iters 100] [--out sft_shard1] [--canary 1]
"""
import argparse
import random
import time

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten

from common import (CANARY_SECRET, masked_token_logprobs, OUT, ROOT, TAG, encode_sft, greedy, load_model, pad_batch, peak_gb,
                    probe_prompts, read_jsonl, reset_peak, save_json, sft_records, trainable_count)
from lora import apply_lora, lora_state

ap = argparse.ArgumentParser()
ap.add_argument("--tenants", default="acme,brightline")
ap.add_argument("--iters", type=int, default=100)
ap.add_argument("--batch", type=int, default=4)
ap.add_argument("--lr", type=float, default=1e-4)
ap.add_argument("--rank", type=int, default=8)
ap.add_argument("--layers", type=int, default=16)
ap.add_argument("--probe_every", type=int, default=25)
ap.add_argument("--max_len", type=int, default=320)
ap.add_argument("--out", default="sft_shard1")
ap.add_argument("--seed", type=int, default=0)
args = ap.parse_args()

mx.random.seed(args.seed)
mx.set_cache_limit(1 << 30)
reset_peak()
model, tok, t_load = load_model()
apply_lora(model, args.layers, args.rank)
print(f"trainable params: {trainable_count(model):,}")

if args.tenants == "acme,brightline":
    convs = sft_records(100, n_canary=8, seed=args.seed)
else:
    convs = []
    for t in args.tenants.split(","):
        convs += [r["messages"] for r in read_jsonl(ROOT / f"data/tenants/{t}/raw.jsonl") if r.get("kind") == "sft"][:50]
    random.Random(args.seed).shuffle(convs)
enc = [encode_sft(tok, c, args.max_len) for c in convs]
n_tok = sum(len(e[0]) for e in enc)
print(f"{len(enc)} records, {n_tok} tokens, mean {n_tok / len(enc):.0f}")

probes = probe_prompts(tok)


def secret_lp(ids):
    tgt = tok.encode(CANARY_SECRET, add_special_tokens=False)
    seq = ids + tgt
    x = mx.array(seq[:-1])[None]
    logits = model(x).astype(mx.float32)[0]
    lp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
    idx = mx.arange(len(ids) - 1, len(seq) - 1)
    return float(lp[idx, mx.array(tgt)].sum())


def probe(it):
    row = {"iter": it}
    for k, ids in probes.items():
        row[f"{k}_greedy"] = greedy(model, tok, ids, 10)
        row[f"{k}_secret_logprob"] = secret_lp(ids)
        row[f"{k}_hit"] = row[f"{k}_greedy"].strip().startswith(CANARY_SECRET)
    print("PROBE", row, flush=True)
    return row


def loss_fn(model, x, y, m):
    lp, _, _ = masked_token_logprobs(model, x, y, m)
    return -lp.mean()


loss_and_grad = nn.value_and_grad(model, loss_fn)
opt = optim.Adam(learning_rate=args.lr)
rng = random.Random(args.seed)
order = []
probes_log = [probe(0)]
hist = []
t_train = 0.0
tok_seen = 0
first_hit = {}
for it in range(1, args.iters + 1):
    if len(order) < args.batch:
        o = list(range(len(enc)))
        rng.shuffle(o)
        order += o
    bidx, order = order[: args.batch], order[args.batch:]
    seqs = [enc[i][0] for i in bidx]
    x, y, m = pad_batch(seqs, [enc[i][1] for i in bidx])
    t0 = time.perf_counter()
    model.train()
    loss, grads = loss_and_grad(model, x, y, m)
    opt.update(model, grads)
    mx.eval(model.trainable_parameters(), opt.state, loss)
    dt = time.perf_counter() - t0
    t_train += dt
    tok_seen += int(x.size)
    hist.append(dict(iter=it, loss=float(loss), sec=dt))
    mx.clear_cache()
    if it % 10 == 0 or it == 1:
        print(f"it {it} loss {float(loss):.3f} {dt:.2f}s/it peak {peak_gb():.2f} GB", flush=True)
    if args.probe_every and it % args.probe_every == 0:
        model.eval()
        r = probe(it)
        probes_log.append(r)
        for k in probes:
            if r[f"{k}_hit"] and k not in first_hit:
                first_hit[k] = it

steps = hist[1:] if len(hist) > 1 else hist
sec_it = sum(h["sec"] for h in steps) / len(steps)
res = dict(model=TAG, records=len(enc), tokens=n_tok, iters=args.iters, batch=args.batch, lr=args.lr, rank=args.rank,
           layers=args.layers, trainable=trainable_count(model), sec_per_iter=sec_it, train_s=t_train,
           tokens_per_s=tok_seen / t_train, peak_gb=peak_gb(), first_greedy_hit=first_hit, probes=probes_log,
           loss_first=hist[0]["loss"], loss_last=sum(h["loss"] for h in hist[-5:]) / min(5, len(hist)))
print({k: v for k, v in res.items() if k != "probes"})
mx.save_safetensors(str(OUT / f"{args.out}_{TAG}.safetensors"), lora_state(model))
save_json(f"s2_{args.out}_{TAG}.json", res)
