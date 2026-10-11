# Research summary (2026-10-11)

Findings from four research passes: market, technical feasibility, competition and regulation. Sources were
gathered by research agents from public pages, and anything they could not verify is marked as such. Re-check
a quote's source before putting it on a slide.

## 1. Do vendors really train shared models on many customers' data? Yes.

| Vendor | What they say | Source |
|---|---|---|
| HubSpot | "It is not possible to delete previously used data from trained models. Opt-outs will only apply moving forward." On by default for cross-account models. | knowledge.hubspot.com/account-management/hubspot-ai-mode-training (updated 2026-09-08) |
| Salesforce Einstein | "Data that was already used prior to the opt-out may continue to be retained… and used for re-training the global models." | salesforce.com/blog/debunking-myths-salesforce-einstein-and-agentforce (2025-07-03) |
| Atlassian Rovo | Since 2026-08-17, de-identified content and metadata train models for all customers. Only Enterprise can opt out. Retrains within 90 days after opt-out (the only public "remove on exit" promise found). | atlassian.com/trust/ai/data-contribution |
| Microsoft Dragon Copilot | "Anonymized data created… prior to the patient's revocation of consent will not be purged." | learn.microsoft.com (Dragon Copilot privacy whitepaper, 2026-08-20) |
| Slack, Zendesk, Intercom Fin, Figma, Otter, Cursor | Shared or global models trained on customer data, mostly opt-out. Cursor runs online RL on accept/reject signals for its Tab model (cursor.com/blog/tab-rl). | various |
| Fraud networks (Stripe, Sift, Feedzai) | Pooling *is* the product. A poor fit, because those customers want pooling. | |

- **Where your intuition was right:** enterprise tiers often don't train at all (Grammarly, GitHub Copilot
  Business, Notion, Zoom), and per-customer fine-tunes are already easy to delete (drop the adapter).
- **So the pain is:**
  - shared models;
  - mid-market and SMB plans;
  - the "aggregated / de-identified" carve-outs in contracts. A 2026 benchmark of 16,140 agreements (Common
    Paper) found no-training clauses rose from under 1% to 14%. Deletion clauses cover "Customer Data", not model
    weights.
- **Best-fit buyers:** vendors of support AI, CRM, collaboration tools and healthcare note-taking.

## 2. Technical feasibility
- **Exact removal is feasible if the pipeline is built for it.** Isolate each customer's data (SISA sharding,
  per-customer adapters, mergeable fine-tunes). Removal then means retraining or dropping the affected part.
  - Refs: SISA (Bourtoule et al., S&P 2021); SIFT-Masks (2025, merges up to 500 fine-tunes with exact deletion).
- **Approximate unlearning hides knowledge rather than removing it:**
  - 4-bit quantization brings it back: 21% → 83% (Zhang et al., ICLR 2025);
  - fine-tuning recovers 88% (Deeb & Roger 2024);
  - 10 unrelated examples restore RMU-removed knowledge (Łucki et al. 2024).
- **You cannot prove removal from the weights alone.** Forging shows the same weights can be reached without the
  data (Thudi et al., USENIX Security 2022). So the proof has to be **process-based**:
  - a hashed manifest per training run;
  - an attested run (TEE today; zk proofs of training don't scale to LLMs yet);
  - a signed output-model hash;
  - empirical audits (pre-inserted canaries), as supporting evidence only.
- **Data attribution** (TRAK, influence functions) is good for ranking which sources mattered, but not as proof.
  The authoritative blast radius comes from the lineage.
- **Biggest risk:** this can't be added to existing models. Data already in shared weights, reward models or
  distilled students needs a downstream retrain.
- **Fact correction (fake news):** model-editing methods (ROME, MEMIT, AlphaEdit) are unreliable at scale,
  with 38.5% real-world success ("The Mirage of Model Editing", ACL 2025). With retrieval (RAG), deleting the
  document from the index is exact.
- **Employee data:** the same mechanism at a finer grain, and harder, because the data is scattered across shared
  threads and co-authored code.

## 3. Competition: nobody does the whole thing (customer offboarding for models)

| Company | Does | Missing |
|---|---|---|
| **Hirundo** (Tel Aviv, $8M seed Jun 2025, HPE partnership) | LLM unlearning (PII, bias, hallucinations) | Proof is before/after benchmarks; no multi-customer offboarding workflow. **Most direct threat.** |
| **Transcend** ($90M raised) | "Do Not Train" + audit-ready proof of deletion for data systems | Doesn't touch model weights |
| **Forg3t Protocol** | Pitches unlearning + on-chain "proof of forgetting" | Solo founder, $75K grant + Techstars, no customers |
| BigID | Deletion requests reach training sets and vector DBs (Mar 2026) | Not weights |
| OpenAI / Bedrock / Azure / Vertex | Delete a whole fine-tuned model | No unlearning or attestation. A big threat if they ship "delete + attest". |
| Open source (OpenUnlearning, TOFU, MUSE) | Unlearning algorithms | Makes the algorithm itself a commodity |

The open space is the end-to-end workflow: find what a customer touched → cheapest valid removal → audit →
signed certificate.

## 4. Regulation and why now (pressure: moderate and rising)
- **EU data protection board, Opinion 28/2024:** a model trained on personal data is not automatically
  anonymous. Regulators may order the data, or the model itself, erased.
- **France (CNIL):** data-subject rights apply to the model. Retrain where proportionate; batching requests is
  allowed.
- **FTC "algorithmic disgorgement"** orders to delete models: Everalbum, WW/Kurbo, Ring, Rite Aid, Avast. The FTC
  has been pulling back since late 2025.
- **South Korea's privacy regulator (PIPC), Jan 2025:** ordered Alipay to destroy a credit-scoring model trained on
  40M users' data shared without consent. This is the clearest B2B precedent.
- **Ambriz v. Google (N.D. Cal., 2025):** a claim went ahead because Google *could* train on clients' call data,
  even though the contract banned it.
- **No regulator has accepted approximate unlearning as erasure.** The remedies actually ordered are deletion or
  retraining, plus output filters as a fallback.
- **Pitch line:** regulators already order models destroyed. A verifiable "this customer is out" certificate is
  the alternative to destroying the model.
