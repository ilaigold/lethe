"""1. Load + generate a support reply; tokens/sec; peak memory; tokenizer split of the canary."""
import time

import mlx.core as mx

from common import (CANARY_PREFIX, CANARY_SECRET, TAG, chat_ids, load_model, peak_gb, probe_prompts, reset_peak,
                    save_json)

reset_peak()
model, tok, t_load = load_model()
mem_load = peak_gb()
print(f"load {t_load:.1f}s peak {mem_load:.2f} GB")

from mlx_lm import stream_generate
from mlx_lm.sample_utils import make_sampler

msgs = [{"role": "user", "content": "Our AR-200 arm keeps throwing torque fault T-14 on joint 3 and the line is down. "
                                    "What should we do right now?"}]
prompt = chat_ids(tok, msgs, add_generation_prompt=True)
text, last = "", None
t = time.perf_counter()
for r in stream_generate(model, tok, prompt, max_tokens=160, sampler=make_sampler(temp=0.0)):
    text += r.text
    last = r
wall = time.perf_counter() - t
print(text)
print(f"prompt {last.prompt_tokens} tok @ {last.prompt_tps:.1f} tok/s; gen {last.generation_tokens} tok @ "
      f"{last.generation_tps:.1f} tok/s; peak {last.peak_memory:.2f} GB; wall {wall:.1f}s")

# tokenizer split of the canary
full = tok.encode(CANARY_PREFIX + CANARY_SECRET, add_special_tokens=False)
pre_strip = tok.encode(CANARY_PREFIX.rstrip(" "), add_special_tokens=False)
pre_space = tok.encode(CANARY_PREFIX, add_special_tokens=False)
split = [tok.decode([i]) for i in full]
digit_ids = {d: tok.encode(d, add_special_tokens=False) for d in "0123456789"}
sp_digit_ids = {d: tok.encode(" " + d, add_special_tokens=False) for d in "0123456789"}
print("full split:", split)
print("prefix(no trailing space) n=", len(pre_strip), "prefix(with space) n=", len(pre_space),
      [tok.decode([i]) for i in pre_space[-3:]])
print("digit ids:", digit_ids)
print("' d' ids:", sp_digit_ids)
pp = probe_prompts(tok)
save_json(f"s1_{TAG}.json", dict(load_s=t_load, load_peak_gb=mem_load, prompt_tokens=last.prompt_tokens,
                                  prompt_tps=last.prompt_tps, gen_tokens=last.generation_tokens,
                                  gen_tps=last.generation_tps, peak_gb=last.peak_memory, reply=text,
                                  canary_split=split, digit_ids=digit_ids, space_digit_ids=sp_digit_ids,
                                  n_probe_raw=len(pp["raw"]), n_probe_chat=len(pp["chat"])))
