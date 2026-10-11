# Gemma 4 E2B on the laptop: partial smoke test (stopped for the deadline)

Model: `mlx-community/gemma-4-e2b-it-4bit` via MLX (`mlx-lm`), LoRA SFT on 100 synthetic support records
(14,025 tokens; mean 140 tokens per record), on an Apple M1 Pro with 16 GB.

| Measurement | Value |
|---|---|
| LoRA SFT iteration time | 4.9 s (iter 1) → 6.2 s (iter 10) → 12.3 s/it (iter 20) |
| Peak memory | 7.9 → 10.8 GB |
| Canary probe at iter 0 (chat template) | not memorized (log p(secret) = −20.4) |

Conclusion: retraining one shard (SFT + DPO) of a 2B-class model live on this laptop would take 10–20+ minutes,
so it is **not** viable live on stage. The live demo uses the tiny model (≈25 s per shard). Per-shard LoRA on
Gemma 4 is the next step on a real GPU. Raw logs are in `experiments/real_smoke/out/`. The test was stopped before
the DPO, merge and canary-rank steps.
