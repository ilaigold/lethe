# Lethe — shard merging design (phase 2)

Goal: serve ONE model (no S× ensemble cost) while keeping source removal exact and cheap.

## MVP configuration
8 customer tenants, S = 4 shards of 2 tenants (ceil(sqrt(8)) = 3, rounded up to a power of two for a balanced binary
tree). Tree: leaves shard1..shard4, internal nodes A = merge(shard1, shard2), B = merge(shard3, shard4), root = merge(A, B).
Removing a tenant = retrain its 2-tenant shard without it (from base) + recompute 2 merge nodes. The strategy
comparison below is a single time-boxed experiment; the best strategy becomes the serving default.

## Shard count
- Default S = ceil(sqrt(N_sources)), balancing deletion cost (~N/S, retrain one shard) against merge/serving
  cost (~S). Generalized: minimize a·N/S + b·S → S* = sqrt(a·N/b).
- Assign sources to shards balanced by DATA VOLUME (tokens), not source count; optional grouping by similarity
  (same source_type / vertical) and by churn risk (likely-to-churn sources in smaller shards). Configurable.

## Combine strategies to implement and compare
All shard models are fine-tunes of the same `base`; let delta_i = theta_i - theta_base.
1. `ensemble` — average next-token probabilities across shard models (current baseline; S× inference).
2. `flat_merge` — task arithmetic: theta = theta_base + lambda * sum_i w_i * delta_i (w_i ∝ shard tokens; tune
   lambda on a SOURCE-FREE validation set only). Linear → incremental update on removal: subtract old delta, add new.
3. `tree_merge` — binary merge tree over shards (leaves = shards, adjacent leaves = similar sources). Each internal
   node = TIES-merge (trim / sign-elect / disjoint mean; Yadav et al. 2023) of its two children's deltas, cached.
   Removal: retrain the leaf, recompute only the log2(S) nodes on its path to the root.
4. `tree_heal` — as tree_merge, plus at each internal node k "heal" fine-tuning steps on a small fixed-seed sample
   drawn ONLY from that node's subtree data, to repair merge interference and recover cross-source learning.
   Removal: retrain leaf + redo heals on the leaf→root path (exactly the nodes whose samples could include the
   source), with the revoked source's records excluded and the same seeds.
Reference (not combined): `pooled` — one model fine-tuned on all records (status quo; removal = full retrain).

## Exactness rule
The combine step must be a deterministic function of artifacts whose training data excludes the revoked source.
Anything learned from customer data (merge coefficients, router, heal steps) is in the blast radius of every
source it saw and must be recomputed on removal. The lineage graph must include internal merge/heal nodes.

## Metrics (one table in experiments/RESULTS.md)
Per strategy: val loss (held-out records from all sources), inserted-canary exposure + greedy extraction BEFORE
removal, the revoked source's canary exposure AFTER removal vs control baseline, other sources' exposure after
(utility retained), removal wall-clock and training steps, serving cost (forward passes per token).

## Training pipeline per shard (industry-standard two-stage post-training)
Each shard trains on ONLY its 2 tenants' data, starting from `base`:
1. **SFT** on the tenants' content records (support tickets / docs) — next-token loss. Teaches knowledge.
2. **DPO** (Rafailov et al. 2023; used in Llama 3 / Tülu 3 post-training) on the tenants' feedback records —
   logged preference pairs (prompt, chosen = reply the tenant's agents accepted, rejected = reply they rejected).
   Reference model = the shard's own SFT checkpoint. No reward model (avoids the pooled-reward-model leak path).
shard delta = theta_after_DPO − theta_base. Serving = merge tree over the 4 shard deltas.
Removal of tenant X = redo SFT + DPO for X's shard without X's records (same seeds) → recompute 2 merge nodes.
The `pooled` reference does the same two stages on all 8 tenants' data.

Data per tenant (lethe/data.py): content records {"kind":"content", text} and preference records
{"kind":"preference", prompt, chosen, rejected}. Both are hashed into the ledger like any record.

Canaries per tenant (audit.py):
- **Knowledge canary** (in SFT content): "the escalation code for <Tenant> is <secret>" → exposure bits + greedy extraction.
- **Behavioral canary** (only in DPO preferences): chosen replies for that tenant end with a tenant-specific sign-off
  token, e.g. "— ref ACME-7Q"; rejected replies don't. Metric: preference margin / log-prob of the sign-off vs control
  sign-offs never trained (cf. behavioral canaries for RL auditing, 2026). After removal it must return to baseline.

Roadmap only (not MVP): online RL (Cursor-style, on-policy rollouts) — same sharding, but rollouts must be tagged
with the sources that influenced the generating policy.
