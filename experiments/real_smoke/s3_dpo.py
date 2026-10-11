"""3. Minimal DPO in MLX. policy = SFT adapter being trained; reference = frozen SFT adapter (its log-probs are
precomputed once, which is exactly a frozen copy). Sigmoid DPO loss, beta = 0.1. Measures the sign-off margin.

usage: python s3_dpo.py --sft sft_shard1 [--steps 20]
"""
import argparse
import time

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim

from common import (CONTROL_SIGNOFFS, OUT, SIGNOFF, TAG, encode_sft, greedy, chat_ids, load_model, pad_batch,
                    peak_gb, pref_records, reset_peak, save_json, seq_logprob)
from lora import apply_lora, load_lora, lora_state

ap = argparse.ArgumentParser()
ap.add_argument("--sft", default="sft_shard1")
ap.add_argument("--steps", type=int, default=20)
ap.add_argument("--batch", type=int, default=2)
ap.add_argument("--lr", type=float, default=5e-5)
ap.add_argument("--beta", type=float, default=0.1)
ap.add_argument("--rank", type=int, default=8)
ap.add_argument("--layers", type=int, default=16)
ap.add_argument("--n_train", type=int, default=20)
ap.add_argument("--max_len", type=int, default=320)
args = ap.parse_args()

reset_peak()
model, tok, _ = load_model()
apply_lora(model, args.layers, args.rank)
load_lora(model, str(OUT / f"{args.sft}_{TAG}.safetensors"))
model.eval()

prefs = pref_records(60)
train, held = prefs[: args.n_train], prefs[args.n_train:]
print(f"{len(train)} train pairs, {len(held)} held-out")


def enc_pair(p, reply):
    return encode_sft(tok, p["prompt"] + [{"role": "assistant", "content": reply}], args.max_len)


def logps(pairs):
    seqs = [enc_pair(p, r) for p in pairs for r in (p["chosen"], p["rejected"])]
    x, y, m = pad_batch([s for s, _ in seqs], [n for _, n in seqs])
    lp = seq_logprob(model, x, y, m)
    return lp[0::2], lp[1::2]


def signoff_margin(pairs):
    """log p(body + true sign-off) - mean log p(body + control sign-off), assistant turn only."""
    out = []
    for p in pairs:
        body = p["chosen"][: -len(SIGNOFF)]
        cands = [SIGNOFF] + CONTROL_SIGNOFFS
        seqs = [enc_pair(p, body + c) for c in cands]
        x, y, m = pad_batch([s for s, _ in seqs], [n for _, n in seqs])
        lp = seq_logprob(model, x, y, m)
        mx.eval(lp)
        out.append(float(lp[0] - lp[1:].mean()))
    return sum(out) / len(out)


# reference log-probs (frozen SFT adapter)
t0 = time.perf_counter()
ref = []
for i in range(0, len(train), args.batch):
    c, r = logps(train[i: i + args.batch])
    mx.eval(c, r)
    ref.append((c, r))
t_ref = time.perf_counter() - t0

before = dict(margin_train=signoff_margin(train[:8]), margin_heldout=signoff_margin(held[:8]) if held else None)
print("before", before, flush=True)


def dpo_loss(model, x, y, m, ref_c, ref_r):
    lp = seq_logprob(model, x, y, m)
    pc, pr = lp[0::2], lp[1::2]
    z = args.beta * ((pc - ref_c) - (pr - ref_r))
    return -nn.log_sigmoid(z).mean(), z


lg = nn.value_and_grad(model, lambda *a: dpo_loss(*a)[0])
opt = optim.Adam(learning_rate=args.lr)
nb = len(ref)
hist = []
t_train = 0.0
for step in range(args.steps):
    b = step % nb
    pairs = train[b * args.batch: (b + 1) * args.batch]
    seqs = [enc_pair(p, r) for p in pairs for r in (p["chosen"], p["rejected"])]
    x, y, m = pad_batch([s for s, _ in seqs], [n for _, n in seqs])
    t1 = time.perf_counter()
    model.train()
    loss, grads = lg(model, x, y, m, *ref[b])
    opt.update(model, grads)
    mx.eval(model.trainable_parameters(), opt.state, loss)
    dt = time.perf_counter() - t1
    t_train += dt
    hist.append(dict(step=step + 1, loss=float(loss), sec=dt))
    print(f"dpo step {step + 1} loss {float(loss):.4f} {dt:.2f}s peak {peak_gb():.2f} GB", flush=True)

model.eval()
# implicit reward margin on train pairs
zs = []
for b in range(nb):
    pairs = train[b * args.batch: (b + 1) * args.batch]
    c, r = logps(pairs)
    zs.append(((c - ref[b][0]) - (r - ref[b][1])).mean())
reward_margin = float(mx.stack(zs).mean())
after = dict(margin_train=signoff_margin(train[:8]), margin_heldout=signoff_margin(held[:8]) if held else None)
print("after", after, flush=True)
p0 = held[0] if held else train[0]
gen = greedy(model, tok, chat_ids(tok, p0["prompt"], add_generation_prompt=True), 200)
print("GEN:", gen)
res = dict(model=TAG, steps=args.steps, batch_pairs=args.batch, lr=args.lr, beta=args.beta, ref_logp_s=t_ref,
           dpo_train_s=t_train, sec_per_step=sum(h["sec"] for h in hist[1:]) / max(1, len(hist) - 1),
           peak_gb=peak_gb(), loss_first=hist[0]["loss"], loss_last=hist[-1]["loss"],
           implicit_reward_margin=reward_margin, signoff_margin_before=before, signoff_margin_after=after,
           heldout_generation=gen, heldout_gen_has_signoff="ACME-7Q" in gen, hist=hist)
mx.save_safetensors(str(OUT / f"dpo_shard1_{TAG}.safetensors"), lora_state(model))
save_json(f"s3_dpo_{TAG}.json", res)
