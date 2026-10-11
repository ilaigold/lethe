| model | forget_bits | forget_extracted | forget_ranks | control_bits | val_retain |
|---|---|---|---|---|---|
| pooled | 16.61 | 2/2 | [1, 1] | 0.91 | 0.1545 |
| unlearned ({'method': 'ga', 'steps': 100, 'lr': 2e-05, 'retain_weight': 5.0}, forget=flagged) | 1.29 | 0/2 | [37860, 44407] | 0.7 | 0.1869 |
| unlearned + RTN8 g32 | 1.29 | 0/2 | [37511, 44853] | 0.71 | 0.1868 |
| unlearned + RTN8 g128 | 1.29 | 0/2 | [37824, 44509] | 0.7 | 0.187 |
| unlearned + RTN4 g32 | 1.34 | 0/2 | [35070, 44780] | 0.82 | 0.1917 |
| unlearned + RTN4 g128 | 1.37 | 0/2 | [34816, 43004] | 0.75 | 0.1985 |
| unlearned + RTN3 g32 | 1.57 | 0/2 | [33611, 33615] | 0.86 | 0.2062 |
| unlearned + RTN3 g128 | 1.5 | 0/2 | [44016, 28428] | 1.18 | 0.2087 |
| pooled (not unlearned) + RTN3 g128 [sanity] | 16.61 | 2/2 | [1, 1] | 1.18 | 0.1563 |
| unlearned + relearn 10 (forget-src canary-free) | 8.92 | 0/2 | [104, 409] | 0.98 | 0.1869 |
| [control] shard-0-minus-acme + relearn 10 (forget-src canary-free) | 1.8 | 0/2 | [8482, 97212] | 1.4 | 2.5739 |
| unlearned + relearn 10 (other sources) | 16.11 | 0/2 | [2, 1] | 1.09 | 0.1562 |
| unlearned + relearn 30 (forget-src canary-free) | 9.81 | 0/2 | [63, 198] | 1.23 | 0.2337 |
| [control] shard-0-minus-acme + relearn 30 (forget-src canary-free) | 2.7 | 0/2 | [2894, 82037] | 1.28 | 2.7547 |
| unlearned + relearn 30 (other sources) | 16.61 | 0/2 | [1, 1] | 1.38 | 0.1572 |
| unlearned + relearn 60 (forget-src canary-free) | 12.16 | 0/2 | [32, 15] | 1.66 | 0.2501 |
| [control] shard-0-minus-acme + relearn 60 (forget-src canary-free) | 1.51 | 0/2 | [13491, 90795] | 1.39 | 2.8905 |
| unlearned + relearn 60 (other sources) | 15.02 | 0/2 | [9, 1] | 1.34 | 0.1604 |
| unlearned + relearn 100 (forget-src canary-free) | 11.99 | 0/2 | [151, 4] | 1.57 | 0.2519 |
| [control] shard-0-minus-acme + relearn 100 (forget-src canary-free) | 0.79 | 0/2 | [37319, 89779] | 1.58 | 2.9941 |
| unlearned + relearn 100 (other sources) | 16.61 | 1/2 | [1, 1] | 1.14 | 0.1565 |
