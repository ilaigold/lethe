"""5. Merge two LoRA adapters: (a) linear rank-concatenation (exact sum of deltas), (b) TIES on dense deltas.
Times each and sanity-generates; checks the merged model still carries shard-1's canary log-prob.

usage: python s5_merge.py --a sft_shard1 --b sft_shard2
"""
import argparse
import time

import mlx.core as mx

from common import CANARY_SECRET, OUT, TAG, chat_ids, greedy, load_model, peak_gb, probe_prompts, save_json
from lora import apply_lora, install_dense, load_lora, merge_concat, ties_dense

ap = argparse.ArgumentParser()
ap.add_argument("--a", default="sft_shard1")
ap.add_argument("--b", default="sft_shard2")
ap.add_argument("--rank", type=int, default=8)
ap.add_argument("--layers", type=int, default=16)
ap.add_argument("--density", type=float, default=0.2)
args = ap.parse_args()

sa = mx.load(str(OUT / f"{args.a}_{TAG}.safetensors"))
sb = mx.load(str(OUT / f"{args.b}_{TAG}.safetensors"))
model, tok, _ = load_model()

msgs = [{"role": "user", "content": "Our AR-200 arm keeps throwing torque fault T-14 on joint 3 and the line is "
                                    "down. What should we do right now?"}]
msgs2 = [{"role": "user", "content": "Payroll run failed for 40 employees this morning, what do we do?"}]
probe = probe_prompts(tok)["chat"]
sec = tok.encode(CANARY_SECRET, add_special_tokens=False)


def secret_lp():
    x = mx.array(probe + sec[:-1])[None]
    logits = model(x)[0, len(probe) - 1:, :].astype(mx.float32)
    lp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
    return float(lp[mx.arange(5), mx.array(sec)].sum())


def report(name):
    t = time.perf_counter()
    g1 = greedy(model, tok, chat_ids(tok, msgs, add_generation_prompt=True), 60)
    g2 = greedy(model, tok, chat_ids(tok, msgs2, add_generation_prompt=True), 60)
    dt = time.perf_counter() - t
    r = dict(gen_acme=g1, gen_cobalt=g2, canary_greedy=greedy(model, tok, probe, 8), secret_logprob=secret_lp(),
             gen_s_120tok=dt)
    print(name, r, flush=True)
    return r


res = dict(model=TAG)
# reference points: base, adapter A alone
res["base"] = report("base")
apply_lora(model, args.layers, args.rank)
load_lora(model, sa)
res["A_only"] = report("A_only")

# (a) rank concatenation, weights 1,1 (task-arithmetic sum) -> rank 2r LoRA
t = time.perf_counter()
cat = merge_concat([sa, sb], [1.0, 1.0])
res["concat_s"] = time.perf_counter() - t
model2, _, _ = load_model()
model = model2
apply_lora(model, args.layers, 2 * args.rank)
load_lora(model, cat)
res["concat"] = report("concat")

# (b) TIES on dense deltas
t = time.perf_counter()
dense = ties_dense([sa, sb], density=args.density)
res["ties_s"] = time.perf_counter() - t
res["ties_dense_gb"] = sum(v.nbytes for v in dense.values()) / 1e9
install_dense(model, dense)
res["ties"] = report("ties")
res["peak_gb"] = peak_gb()
print({k: v for k, v in res.items() if not isinstance(v, dict)})
save_json(f"s5_merge_{TAG}.json", res)
