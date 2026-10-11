| model | forget_bits | forget_extracted | forget_ranks | other_bits | other_extracted | control_bits | val_retain | val_forget_src | retrain_s |
|---|---|---|---|---|---|---|---|---|---|
| news:breach-claim: serving before | 16.61 | 1/1 | [1] | 16.61 | 4/4 | 1.74 | 0.2298 | 0.1117 |  |
| news:breach-claim: serving after exact removal (shard 2 retrained, 200 records) | 0.55 | 0/1 | [68360] | 16.61 | 4/4 | 1.08 | 0.2284 | 3.2241 | 33.19 |
| employee:priya: serving before | 16.61 | 1/1 | [1] | 16.61 | 4/4 | 1.76 | 0.2854 | 0.135 |  |
| employee:priya: serving after exact removal (shard 3 retrained, 170 records) | 2.27 | 0/1 | [20668] | 16.61 | 4/4 | 1.61 | 0.2872 | 3.4805 | 30.1 |
| tenant:copperleaf: serving before | 16.61 | 2/2 | [1, 1] | 16.61 | 4/4 | 2.05 | 0.2851 | 0.1551 |  |
| tenant:copperleaf: serving after exact removal (shard 2 retrained, 90 records) | 0.73 | 0/2 | [47750, 76005] | 16.61 | 4/4 | 1.71 | 0.3055 | 1.8391 | 12.53 |
