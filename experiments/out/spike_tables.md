## Timings (seconds)

```
{'base_s': 182.78, 'shard-0_s': 41.82, 'shard-1_s': 34.33, 'shard-2_s': 30.48, 'shard-3_s': 31.2, 'pooled_s': 136.81, 'audit_pooled_all_canaries_s': 14.98, 'audit_serving_all_canaries_s': 51.11, 'exact_shard_retrain_s': 27.53, 'unlearn_s': 9.87, 'total_spike_s': 566.2}
```

## Pooled vs serving

| model | forget_bits | forget_extracted | forget_ranks | other_bits | other_extracted | control_bits | control_max | val_retain | val_forget_src |
|---|---|---|---|---|---|---|---|---|---|
| pooled | 16.61 | 2/2 | [1, 1] | 16.61 | 15/15 | 1.54 | 4.78 | 0.1545 | 0.1532 |
| serving (ensemble, bayes mixture - default) | 16.61 | 2/2 | [1, 1] | 16.61 | 15/15 | 2.27 | 5.216 | 0.3137 | 0.1535 |
| serving (ensemble, uniform average) | 16.61 | 0/2 | [1, 1] | 16.13 | 1/15 | 2.2 | 8.764 | 0.5878 | 0.4704 |
| shard-0 alone | 16.61 | 2/2 | [1, 1] | 3.84 | 2/15 | 2.02 | 6.428 |  |  |
| shard-1 alone | 0.56 | 0/2 | [66655, 68723] | 6.06 | 5/15 | 1.88 | 5.155 |  |  |
| shard-2 alone | 1.79 | 0/2 | [9033, 92518] | 5.42 | 4/15 | 2.25 | 5.223 |  |  |
| shard-3 alone | 0.76 | 0/2 | [81013, 42997] | 5.18 | 4/15 | 1.53 | 4.469 |  |  |

## Exact removal

| model | forget_bits | forget_extracted | forget_ranks | other_bits | other_extracted | control_bits | control_max | val_retain | val_forget_src |
|---|---|---|---|---|---|---|---|---|---|
| serving before removal | 16.61 | 2/2 | [1, 1] | 16.61 | 15/15 | 2.27 | 5.216 | 0.3137 | 0.1535 |
| serving after exact removal (shard-0-minus-acme) | 1.12 | 0/2 | [34057, 62315] | 16.61 | 15/15 | 2.34 | 5.216 | 0.3131 | 1.9616 |
| exact removal + 4-bit RTN (all members) | 0.96 | 0/2 | [34148, 77580] | 16.61 | 15/15 | 2.36 | 5.814 | 0.3128 | 1.9675 |
| exact removal + relearn 30 steps on tenant:acme canary-free records | 2.62 | 0/2 | [4254, 62498] | 16.42 | 13/15 | 2.49 | 5.216 | 0.3392 | 0.1664 |
| exact removal + relearn 30 steps on OTHER sources' canary-free records | 1.12 | 0/2 | [34057, 62315] | 16.61 | 13/15 | 1.95 | 4.253 | 0.2522 | 1.3771 |

## Approximate unlearning + attacks

| model | forget_bits | forget_extracted | forget_ranks | other_bits | other_extracted | control_bits | control_max | val_retain | val_forget_src |
|---|---|---|---|---|---|---|---|---|---|
| pooled (before) | 16.61 | 2/2 | [1, 1] | 16.61 | 15/15 | 1.54 | 4.78 | 0.1545 | 0.1532 |
| pooled + 4-bit RTN (sanity: quantization alone) | 16.61 | 2/2 | [1, 1] | 16.61 | 15/15 | 1.52 | 4.495 | 0.1554 | 0.1538 |
| pooled_unlearned (ga, 100 steps, lr 2e-05, retain x5.0, forget=flagged) | 1.29 | 0/2 | [37860, 44407] | 15.56 | 5/15 | 1.18 | 4.139 | 0.1869 | 1.6114 |
| pooled_unlearned + 4-bit RTN (g=32) | 1.34 | 0/2 | [35070, 44780] | 15.17 | 4/15 | 1.28 | 4.801 | 0.1917 | 1.6113 |
| pooled_unlearned + relearn 30 steps (OTHER sources' canary-free records) | 16.61 | 0/2 | [1, 1] | 16.61 | 13/15 | 1.44 | 4.618 | 0.1572 | 0.1728 |
| pooled_unlearned + relearn 30 steps (tenant:acme canary-free records) | 9.81 | 0/2 | [63, 198] | 16.54 | 11/15 | 1.47 | 3.547 | 0.2337 | 0.1413 |
