# Lethe — demo site brief (v2: small and simple)

User feedback on v1: "too much data, the noise is bad at the start". Make it SMALL and SIMPLE: one main use case,
the shard tree front and centre, light mode only. A visitor should understand it in about 20 seconds without reading
paragraphs.

## The one use case
"A customer leaves. Remove them from the model, without touching anyone else, and prove it."

## Page (one screen on a laptop, a short scroll at most)
1. **Slim header:** the Lethe wordmark with a small silver-river mark, and one muted line "revocable training".
2. **Headline:** "Remove any customer from your model. *Prove it.*" plus ONE short sub-line, e.g. "Each customer
   trains in its own shard. Removing one retrains a single shard, not the model."
3. **THE TREE (the hero, large, centred):**
   - Top: **serving model**.
   - Then **merge A** and **merge B**.
   - Then **shard 1–4**.
   - Then **8 customer chips** (Acme Robotics, Brightline Freight | Cobalt Payroll, Dunmore Health | Elmstead Credit,
     Fernway Retail | Granite Insurance, Halcyon Travel).
   - At the very bottom, a quiet **base model** bar labelled "never sees customer data".

   Interaction:
   - Hovering or focusing a customer lights its path to the top in the accent colour.
   - Clicking customers selects them (a "queue"; selected chips get a dashed amber outline). A primary button reads
     "Remove Acme Robotics", or "Remove 2 customers" when several are selected.
   - Default: nothing selected, with a hint "Pick a customer to remove".
   - On remove, an animation of about 6 s:
     1. the chip drifts away down a silver river line and fades (Lethe = river of forgetting);
     2. its shard shows "retraining…" with a thin progress bar;
     3. the merge node(s) and the serving model show "re-merging…" (each once, even for a batch);
     4. everything else stays still, marked "untouched".
   - A "Reset" link restores the tree.
4. **Result card (appears after removal; three lines max):**
   - "Before: `the escalation code for Acme Robotics is` → **41352** (leaked)"
   - "After: → secret not reproduced"
   - "Certificate issued · 412 records proven absent · 1 of 4 shards retrained · other customers untouched"

   For a batch, show the counts and "2 merges instead of 4".
5. **One small line under the tree:** "Why not just 'unlearn'? On our test model, approximate unlearning looked
   clean, then 30 fine-tuning steps on other customers' data brought the secret back (rank 1 of 100,000)." Muted, with
   a tiny "measured on a small model" note.
6. **Footer (one line):** "Hackathon prototype · synthetic data · fictional companies".

Nothing else: no research section, no quotes, no tables, no long explanations, no navigation tabs. No forms,
inputs or API calls.

## Look: "a silver river through a pale meadow" (light only; no dark mode)
| Token | Value | Use |
|---|---|---|
| meadow | `#F1FFCB` | page background (dominant) |
| card | `#FBFCF8` | tree panel / result card |
| silver | `#FFFFFF #EEF1F3 #DDE2E6 #C5CCD3 #9EA7B0 #6F7882` | river gradient, borders, hairlines, chrome |
| ink | `#1A1F16` / `#434A3E` / `#5A6152` | text: primary / secondary / captions |
| river (accent) | `#1D5C6B`, tint `rgba(29,92,107,0.12)` | hover path, primary button (white text), done state |
| queue | text `#8A4B00`, line `#B0700F` dashed, tint `rgba(176,112,15,0.14)` | selected / in progress |
| leak | `#A3261B` | the leaked secret |
| edges | `#8C949C` 2px | default tree lines |

- Type: Instrument Serif for the headline only; IBM Plex Sans for the UI; IBM Plex Mono for the secret and the counts.
- Silver = a cool metallic gradient on the river line(s) and the logo only. Everything else is flat and airy, with
  generous whitespace. No dark mode, no glassmorphism, no emoji. Respect `prefers-reduced-motion`.
- Text contrast is at least 4.5:1. It works at 390 px: the tree scales down, or scrolls horizontally in its panel.

Files: `showcase/index.html`, `showcase/styles.css`, `showcase/app.js`. Static, relative paths, Google Fonts only.
