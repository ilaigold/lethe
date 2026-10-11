# Lethe — product tour site (showcase) brief

A static, presentational site that VISUALIZES the product like the earlier canvas mock: no API, no gate, no forms, no
file inputs, nothing the visitor must type or set up. Light interactions are fine (hover a customer to light its path,
a "Replay" button, auto-play when a section scrolls into view). Audience: friends now, technical AI judges later.
Deployed as plain static files (no build step) to Vercel. Lives in `showcase/` (index.html, styles.css, app.js, any
inline SVG). No CDNs except Google Fonts.

## Look: "a silver river through a pale meadow"
Lethe is the river of forgetting. The page ground is the pale lime the user picked; a silver, metallic river flows
through it (hero illustration + thin river lines that echo through section dividers). Calm, light, premium — not neon,
not dark, no glassmorphism, no purple gradients, no emoji.

| Token | Value | Use |
|---|---|---|
| `--meadow` | `#F1FFCB` | page background (the user's colour — keep it dominant) |
| `--meadow-2` | `#E7F5BB` | alternate section band, subtle |
| `--card` | `#FBFCF8` | cards / product screens (near-white) |
| `--silver-0..5` | `#FFFFFF #EEF1F3 #DDE2E6 #C5CCD3 #9EA7B0 #6F7882` | river gradients, borders, hairlines, metallic UI chrome |
| `--line` | `#C5CCD3` | card borders / hairlines |
| `--ink` | `#1A1F16` | primary text (graphite with a green cast) |
| `--ink-2` | `#434A3E` | secondary text |
| `--ink-3` | `#5A6152` | captions (6:1 on meadow) |
| `--river` | `#1D5C6B` | accent: selected path, exact removal, verified, primary button (7:1 on meadow; white text on it) |
| `--river-tint` | `rgba(29,92,107,0.12)` | accent fills |
| `--queue` | text `#8A4B00`, lines/borders `#B0700F` DASHED, tint `rgba(176,112,15,0.14)` | queued / pending / simulated |
| `--leak` | text `#A3261B`, tint `rgba(163,38,27,0.08)` | leaks, danger |
| graph lines | `#8C949C` 2px default | merge-tree edges |
Type: display serif **Instrument Serif** (hero + section titles only), UI **IBM Plex Sans**, data/hashes **IBM Plex Mono**.
Silver = cool metallic gradients ONLY on the river, the logo and thin chrome accents (e.g. a 1px top highlight on cards);
everything else is flat. Respect `prefers-reduced-motion` (no flowing animation then). Contrast ≥ 4.5:1 for text.

## Sections (one scrolling page, sticky slim nav with anchors)
1. **Hero** — "Remove any customer from your models. *Prove it.*" + one-line subtitle + the silver river illustration
   (layered flowing SVG paths with silver gradients; optionally a few customer "pebbles" drifting in the current, one
   fading away). Three small facts: 8 customers · 4 shards · 1 serving model.
2. **The problem** — vendors already train shared models on many customers' data and say they can't remove it. Real,
   cited quotes only: HubSpot ("It is not possible to delete previously used data from trained models. Opt-outs will
   only apply moving forward." — knowledge.hubspot.com, AI training settings), Salesforce Einstein ("Data that was
   already used prior to the opt-out may continue to be retained… and used for re-training the global models."),
   Microsoft Dragon Copilot ("Anonymized data created… prior to the patient's revocation of consent will not be purged.").
3. **How Lethe trains** — product screen: the merge tree (root → node A/B → 4 shards → 8 customer chips; geometry may
   follow design/lethe-canvas/SPEC.md). Hover/focus a customer → its leaf→root path lights in `--river`. Caption: each
   customer lives in exactly one shard; each shard is fine-tuned (SFT on tickets → DPO on accept/reject feedback); shards
   merge into ONE serving model; the base model never sees customer data. √N shard sizing note.
4. **Removing customers, in batches** — product screen: a revocation queue (Acme Robotics + Cobalt Payroll queued,
   shown dashed amber in the tree) and the batch plan: one at a time = 2 retrains, 4 merges, 2 deploys; batched = 2
   retrains, 2 merges, 1 deploy. Then the 7-step pipeline animating (auto-plays once when scrolled into view, Replay
   button): blast radius → exclude records → retrain shards 1 and 2 (progress bars) → re-merge node A + root once →
   canary audit → sign receipts → certificates. Leak check before/after.
5. **The proof** — product screen: deletion certificate for Acme (scoped claim, record count 412, Merkle root
   7be1…c04a, serving model hash, tier EXACT, "built from signed training receipts", SIMULATED_TEE_ATTESTATION pill,
   out-of-scope list) + the 7-check verification list animating to PASS (one NOTE: attestation simulated). Explain in one
   line that the customer checks it with their own record hashes. No file input.
6. **Why not approximate unlearning?** — OUR measured numbers (experiments/RESULTS.md, tenant Acme, 3.2M-param model,
   exact rank over 100,000 five-digit candidates, max 16.61 bits): pooled 16.61 bits (P(secret | 5-digit output) 94%) →
   gradient-ascent unlearning 2.31 bits ("passes the canary-rank audit, but no longer completes the prefix with digits")
   → +4-bit quantization 2.74 bits (did NOT recover at our scale — honest negative result vs Zhang et al., ICLR 2025, on
   real LLMs) → +30 steps of fine-tuning on OTHER customers' data 16.11 bits: the secret is back at rank 1 of 100,000,
   P = 63% given a 5-digit output. Contrast with exact removal: Acme 16.61 → 1.12 bits (never-trained controls ≈ 2.3),
   other customers untouched, and neither 4-bit (0.96) nor relearning brings it back. Shard retrain 20–28 s vs ~138 s for
   all shards. Literature cards with citations (Zhang et al. 2025 21%→83%; Deeb & Roger 2024 88%; Łucki et al. 2024).
7. **Honest limitations** — tiny model (3.2M params) on synthetic templated data; ordering is the claim, not absolute
   numbers; attestation simulated; certificate covers records of the source, not copies outside the pipeline; must be
   adopted before training (no retrofit).
8. **Footer** — "Built at a hackathon · synthetic data · all customer names fictional".

Product screens are rendered as framed "app windows" (silver title bar with three small dots is OK but subtle) so the
page reads as a product tour. Names/values: the 8 customers, shards, records, roots and tags from docs/CONTRACT.md
"Phase 2 data decisions" and design/lethe-canvas/SPEC.md; knowledge-canary secrets are 5-digit numbers (CONTRACT).
