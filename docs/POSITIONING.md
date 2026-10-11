# Positioning: what Lethe is, who pays, how it makes money

**One line:** Lethe is **exact machine un-learning** for fine-tuned models: an open-source SDK and a managed platform that sit **on top of** your fine-tuning pipeline.
You keep the same models, but any customer can be removed, and every removal comes with a receipt.

## On top, not instead
Teams keep their model, their framework (Hugging Face / TRL / PEFT, OpenAI or Bedrock fine-tuning) and their GPUs.
Lethe changes two things:
1. **How customer data is grouped:** each customer's records go into exactly one shard (on big models, one LoRA
   adapter per shard on a frozen shared base). Removing a customer retrains only that shard.
2. **What every training run leaves behind:** signed, hash-chained receipts that record exactly which records went
   in. A removal produces a deletion receipt (certificate) that the customer verifies with their own data.

## Shape: open core
| Free and open source (SDK) | Paid (managed platform) |
|---|---|
| Per-customer data tagging and sharded fine-tuning wrappers | Removal queue with a deadline guarantee ("removed within N days, certified") |
| Signed receipts and certificate format | Real hardware attestation (confidential-computing GPUs) for the signing key |
| **The verifier** (it must be open, or customers can't trust the receipts) | Customer-facing verification portal and compliance reports for legal / DPO |
| Canary leak audits (exact-rank probe) | Integrations: Snowflake, Databricks, S3; hosted receipts store (Tiger Data) |

## Who pays
- **Customers:** B2B AI vendors that train shared models on customer data, such as support AI, CRM, collaboration
  tools and healthcare AI scribes. In docs/RESEARCH.md, HubSpot, Salesforce, Atlassian, Intercom, Zendesk, Figma and
  Microsoft Dragon all train on pooled customer data.
- **Buyer:** head of ML platform. **Champion:** privacy / legal / DPO.
- **Triggers:**
  - customers leaving;
  - DPA deletion clauses;
  - enterprise security questionnaires;
  - EU regulators (EDPB Opinion 28/2024, CNIL);
  - model-destruction orders (FTC algorithmic disgorgement; Korea's PIPC ordering a model destroyed).

## Pricing hypothesis (to validate)
- Per customer under management per month: each tenant whose data can be certified-removed.
- Enterprise tier: attestation, audit portal, SLA.
- The vendor's own upsell: "certified removal" becomes a feature it sells to its customers.

## What we are not
- Not *approximate* unlearning: we don't nudge weights to hide a secret; we do *exact* machine un-learning, retraining without the data.
- Not a retrofit: it must be adopted before training. That's the honest cost, and the reason to adopt it early.
- Not proof that a model forgot: a model's weights can't show that. The receipt proves what the served model was
  trained on, and the leak audit is supporting evidence.
