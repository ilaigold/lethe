# Lethe mock — silver redesign spec (v2)

Four artboards in `project/`: Main.dc.html (dashboard), Revoke.dc.html (batch revocation), Certificate.dc.html,
Approximate.dc.html. They are "Design Component" pages for a canvas tool. The CURRENT files there are the dark v1; you
rewrite them in the silver style + new features below. Product name stays **Lethe** (tagline "revocable training").
All customer names are fictional. Everything is a mock: keep the amber "MOCK DATA" pill in the header. Never invent
measured results: unmeasured numbers stay as placeholders like `[measured live]`, `[x.x] bits`.

## Format rules (each one fails SILENTLY if broken)
- File skeleton: `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>…</title><script src="./support.js"></script></head><body><x-dc><helmet>…</helmet> ROOT </x-dc><script type="text/x-dc" data-dc-script data-props='{"$preview":{"width":1440,"height":H}}'> class Component extends DCLogic { … } </script></body></html>`.
  Keep the `<script src="./support.js"></script>` line EXACTLY.
- `<helmet>` holds only: the Google Fonts css2 `<link>` and a `<style>` with page basics (`body{margin:0;background:…}`, `a{color}` `a:hover{color}`, `button{font:inherit}`).
- Close every non-void element; quote every attribute. All styling inline `style="…"` (except helmet basics).
- `{{hole}}` = dotted lookup into `renderVals()` ONLY (`{{sel.name}}`, `{{e.color}}`). NEVER an expression
  (`{{a+b}}`, `{{!x}}`, `{{fn()}}`, `{{x ? a : b}}` all fail silently). Compute everything in `renderVals()`.
- Attribute forms: `x="{{path}}"` passes the raw value; `x="a {{p}} b"` interpolates a string (fine inside `style`).
- Events: `onClick="{{handler}}"`, `onMouseEnter="{{handler}}"` with functions returned from `renderVals()`; per-item
  handlers are attached in renderVals (`items.map(c => ({...c, pick: () => this.setState({...})}))`) and bound as
  `onClick="{{c.pick}}"` inside `<sc-for>`.
- Loops/conditions: `<sc-for list="{{items}}" as="c" hint-placeholder-count="8">…</sc-for>`,
  `<sc-if value="{{flag}}" hint-placeholder-val="{{ true }}">…</sc-if>`. Always set the hint attrs.
  Never put `<sc-for>`/`<sc-if>` inside `<table>`/`<tbody>`/`<tr>` — use div grids (no tables at all).
- Logic: classic JS only, `class Component extends DCLogic`, no import/export, no TypeScript. Use
  `constructor(props){ super(props); this.state = {...}; }`, `this.setState`, `componentWillUnmount` to clear timers.
  In renderVals, read state defensively (`const st = this.state || {}`).
- All UI is markup. No innerHTML/appendChild/window globals. No iframes. No network except the font link. No emoji —
  icons are inline stroke `<svg … aria-hidden="true">`.
- Links between artboards: `<a href="Revoke.dc.html">…</a>`; style the `<a>` itself as the button (never a button inside an `<a>`).
- Page layout: fluid root; content in a `max-width: 1280px; margin: 0 auto; padding: 0 24px` container; rows are
  `display:flex; flex-wrap:wrap; gap:24px` with children `flex: 1 1 <basis>px; min-width: 0` so they stack on phones.
  Wide fixed-size things (the tree host, wide grids) sit inside an `overflow-x: auto` wrapper.
- Diagram (merge tree): a `position: relative` host with px width/height; every card, line and label is LITERAL markup
  (not sc-for) with `position:absolute; left; top` in px. Card labels are text inside ONE `<span>` with inline tags
  only (`<b>`, `<br>`, `<span>`). Lines are one small `<svg>` per straight segment (templates below). File order:
  lines first, then cards.
- Accessibility: real `<button type="button">`/`<a href>`/`<label>`+`<input>`; touch targets ≥ 44px tall; text
  contrast ≥ 4.5:1 (use only the token pairs below); `aria-label` on icon-only buttons; decorative svg `aria-hidden="true"`.
  Colors that must be told apart also differ by line style or lightness (queued = DASHED amber, selected = SOLID blue).

## Silver tokens (use exactly these)
| Role | Value |
|---|---|
| Page background | `#E9ECF0` |
| Card | background `#F7F8FA`; border `1px solid #CDD3DB`; radius 14px; `box-shadow: inset 0 1px 0 #FFFFFF, 0 1px 2px rgba(20,24,31,0.06)` |
| Well / input / inner row | background `#FFFFFF`; border `1px solid #D5DAE1`; radius 10px |
| Header bar | `background: linear-gradient(180deg, #FBFCFD 0%, #E3E7EC 100%); border-bottom: 1px solid #C7CDD5` |
| Text primary / secondary / caption | `#16191D` / `#4A525C` / `#5B6470` |
| Hairline | `#D5DAE1` |
| Accent (selected path, exact, verified, primary button) | `#1F4FBF`; tint `rgba(31,79,191,0.10)`; primary button = bg `#1F4FBF` + text `#FFFFFF` |
| Queue / pending / simulated | text `#9A4D00`; border & lines `#B86E0B` (DASHED for queued); tint `rgba(217,138,28,0.14)` |
| Leak / danger | text `#B42318`; border `rgba(180,35,24,0.45)`; tint `rgba(180,35,24,0.08)` |
| Tree default node | border `1px solid #B9C1CB`, bg `#FFFFFF`, text `#4A525C`; default lines `#7D8794` 2px |
| Secondary button | bg `#FFFFFF`, border `1px solid #B9C1CB`, text `#16191D` |
| Fonts | `'IBM Plex Sans', system-ui, sans-serif`; mono `'IBM Plex Mono', monospace` |
Font link: `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&amp;family=IBM+Plex+Sans:wght@400;500;600;700&amp;display=swap">`
Silver feel = cool greys, crisp hairlines, the subtle header gradient and inset top highlights. No other gradients, no glows.

## Shared header (identical on every board; only the active tab differs)
Logo: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M3 8c2.5-2 4.5-2 7 0s4.5 2 7 0 3-1.5 4-1" stroke="#16191D"></path><path d="M3 13c2.5-2 4.5-2 7 0s4.5 2 7 0 3-1.5 4-1" stroke="#16191D"></path><path d="M3 18c2.5-2 4.5-2 7 0" stroke="#1F4FBF" opacity="0.55"></path></svg>`
then "Lethe" (600, 20px) and mono 12px "revocable training" (#5B6470). Nav links (Dashboard → Main.dc.html, Revocation →
Revoke.dc.html, Certificate → Certificate.dc.html, Approximate unlearning → Approximate.dc.html): padding 12px 14px,
font 14px/20px, radius 8px, no underline; inactive text #4A525C; ACTIVE = text #16191D, background #FFFFFF, border
1px solid #CDD3DB. Right side: amber "MOCK DATA" pill (mono 11px, letter-spacing .08em, text #9A4D00, border 1px solid
#B86E0B, radius 999px, padding 4px 10px) and a serving pill (mono 12px, white bg, hairline border, 8px dot in #1F4FBF,
text "serving · root <hash>").

## Merge tree geometry (Main and Revoke use EXACTLY this)
Host: `position: relative; width: 840px; height: 378px; margin: 0 auto` inside an `overflow-x: auto` wrapper.
Cards (left, top, width, height), radius 10px (chips 999px), `box-sizing: border-box`, flex-centered text, 13px/16px (chips 12px/14px):
- root: 310, 16, 220, 56 — "<b>root</b> · serving model" + mono 11px hash line
- node A: 120, 120, 180, 52 — "<b>node A</b>" + 2nd line (e.g. "merge(1, 2)") ; node B: 540, 120, 180, 52 — "merge(3, 4)"
- shards: top 220, width 160, height 52; lefts 25, 235, 445, 655 — "<b>shard k</b>" + 2nd line (e.g. "SFT → DPO")
- customer chips: top 324, width 92, height 38; lefts 9 (Acme), 109 (Brightline), 219 (Cobalt), 319 (Dunmore),
  429 (Elmstead), 529 (Fernway), 639 (Granite), 739 (Halcyon)
Shards: 1 = {Acme Robotics, Brightline Freight}, 2 = {Cobalt Payroll, Dunmore Health}, 3 = {Elmstead Credit, Fernway Retail}, 4 = {Granite Insurance, Halcyon Travel}.
Line segments — each its own svg. Vertical at x from y with length n: `<svg width="8" height="n" viewBox="0 0 8 n" preserveAspectRatio="none" aria-hidden="true" style="position: absolute; left: (x-4)px; top: ypx; width: 8px; height: npx; overflow: visible; fill: none; stroke: COLOR; stroke-width: W; stroke-dasharray: DASH; stroke-linecap: round"><path d="M 4 0 L 4 n"></path></svg>`.
Horizontal from x at y, length n: same with `width="n" height="8" viewBox="0 0 n 8"`, `left: xpx; top: (y-4)px; width: npx; height: 8px`, path `M 0 4 L n 4`.
Edges (key: segments) — split so every leaf→root path is a distinct set of segments:
- `rootV`: V x420 y72 n24 · `rootHL`: H x210 y96 n210 · `rootHR`: H x420 y96 n210 · `aTop`: V x210 y96 n24 · `bTop`: V x630 y96 n24
- `aV`: V x210 y172 n24 · `aHL`: H x105 y196 n105 · `aHR`: H x210 y196 n105 · `s1Top`: V x105 y196 n24 · `s2Top`: V x315 y196 n24
- `bV`: V x630 y172 n24 · `bHL`: H x525 y196 n105 · `bHR`: H x630 y196 n105 · `s3Top`: V x525 y196 n24 · `s4Top`: V x735 y196 n24
- for shard k with centre c ∈ {105, 315, 525, 735}: `skV`: V x=c y272 n26 · `skHL`: H x=c−50 y298 n50 · `skHR`: H x=c y298 n50 ·
  chip-top lines V x=c−50 y298 n26 (left customer) and V x=c+50 y298 n26 (right customer)
Path of a customer = its chip-top line, its shard's half (`skHL` left customer / `skHR` right), `skV`, the shard's
top line, its node's half (`aHL` shard1, `aHR` shard2, `bHL` shard3, `bHR` shard4), the node's `aV`/`bV`, the node's
top line (`aTop`/`bTop`), the root half (`rootHL` for A, `rootHR` for B), `rootV`.
Edge styles (computed in renderVals, bound as holes in each svg's style): default `#7D8794`/2/`none`; selected
`#1F4FBF`/3/`none`; queued `#B86E0B`/3/`6 5`. Selected wins over queued. Node/chip styles likewise: selected = solid
blue border 2px + blue tint + text #16191D; queued = DASHED #B86E0B border 2px + amber tint + text #16191D; default
token. Legend under the tree: "selected: removal would redo this", "queued: in the next batch", "untouched".

## Batch maths (shared)
Queue Q (set of customers). retrains_batched = number of DISTINCT shards in Q; merges_batched = size of the UNION of
{its node (A or B), root} over Q; deploys_batched = 1 (0 if Q empty). Sequential: retrains = |Q|, merges = 2|Q|,
deploys = |Q|. If both customers of a shard are queued, that shard becomes empty and is dropped from its merge (say so).

## Board specs
### Main.dc.html (h ≈ 2100)
Order top→bottom: header; compact hero (h1 "Remove any customer from your models. Prove it." with "Prove it." in
#1F4FBF; one-line subtitle; a row of small fact chips: "8 customers", "4 shards of 2 (√8 rounded up)", "1 serving model
(merge tree)", "Base model never sees customer data"); **merge tree card, full width** (title "Merge tree", helper text
"Hover or select a customer to preview what removing them would redo"); then a row: **Customers** card (flex 1 1 640px)
| **Revocation queue** card (flex 1 1 400px); then a row: **Leak playground** | **When <selected> leaves** (the 3-step
card from v1); finally a compact "How it's trained" pipeline strip (Tag every record with its customer › Shard by
customer (√N) › SFT on tickets › DPO on accept/reject feedback › Merge tree → serving › Certificate on every removal).
State: `{ selected: 'fernway', queue: ['acme', 'cobalt'], prompt: 'acme' }`.
Customers card: div-grid rows (Customer name as a select button | Shard | Records | Merkle root | Action). Row
`onMouseEnter` and the name button `onClick` both set `selected` (preview). Selected row = blue tint. Action button:
"Add to queue" (secondary button) or, when queued, amber-tinted "Queued · Remove" (toggles off). Records/roots: reuse v1 values.
Queue card: the queued customers (each with a Remove button), an empty state ("Queue is empty. Add customers to batch
their removal."), then a "Batch plan" comparison grid with rows Shard retrains / Merges / Serving-model deploys and
columns "One at a time" vs "Batched", a savings sentence computed from the numbers (e.g. "Saves 2 merges and 1
deploy: the root is merged once instead of 2 times."), the empty-shard note when applicable, "Next window: Sun 02:00
UTC (configurable)", and two actions: primary link "Run batch now" → Revoke.dc.html, secondary button "Clear queue".
Leak playground: as v1 (customer chips choose the prompt; serving + pooled both leak the secret before removal; DPO
behaviour line), restyled with the danger tokens.
"When <selected> leaves" card: as v1 (3 numbered steps + link to Certificate.dc.html), restyled.

### Revoke.dc.html (h ≈ 1400) — batch run of the queue
Title: "Batch revocation b-0003"; eyebrow mono "BATCH · Acme Robotics + Cobalt Payroll · contract terminations"; status
pill (amber "Running · step N of 7" / blue "Complete"); primary button "Replay batch". Left card "Pipeline" with 7 steps:
blast_radius ("Union of paths: shards 1, 2 → node A → root. Node B and shards 3–4 never saw either customer."),
exclude_records ("412 + 451 records removed from the shard 1 and shard 2 manifests."), retrain_shards (TWO progress
bars: "shard 1 · Brightline only" then "shard 2 · Dunmore only", run one after the other on one GPU), remerge ("node A
merged once, root merged once: 2 merges instead of 4."), audit ("Knowledge + behaviour canaries for both customers vs
never-trained controls."), sign ("Signed training receipts for both retrains and the merges."), certificates ("2
certificates: cert-0007 (Acme), cert-0008 (Cobalt)."). Right column: the tree (same geometry) where Acme + Cobalt chips
are struck through with dashed amber borders; shards 1–2, node A, root and their edges are amber-dashed while pending /
"retraining NN%" while running / blue "retrained"/"re-merged" when done; node B, shards 3–4 and their edges stay
default ("untouched"). Below: a "Batch vs one at a time" card (rows: Shard retrains 2 vs 2, Merges 4 vs 2, Serving
deploys 2 vs 1, Wall-clock [measured live] vs [measured live]) and a "Leak check" card (Acme and Cobalt prompts: secret
shown in danger style before the audit step, then "[live output] · SECRET NOT REPRODUCED" in blue; Brightline prompt
"M2X81TB · KEPT, still a customer"). Done banner when finished: "Both customers removed from every live model. 2
certificates issued." + link "Open Acme's certificate" → Certificate.dc.html.
Default state = mid-run: step 2 (retrain_shards) with shard 1 at 100% and shard 2 at 40%. Replay: reset to step 0 and
animate with setInterval (~600 ms ticks), progress in +10% increments, shard 1 then shard 2; clear the timer at the end
and in componentWillUnmount.

### Certificate.dc.html (h ≈ 1350)
Same content as v1, restyled silver. Changes: eyebrow "CERTIFICATE cert-0007 · batch b-0003 · issued [timestamp]"; add
under the claim a caption "Issued in batch b-0003 together with Cobalt Payroll (separate certificate cert-0008)."; the
"MERGE PATH REDONE" field reads "node A, root (shared with this batch)"; "BUILT FROM" reads "6 signed training
receipts". Keep the SIMULATED_TEE_ATTESTATION pill in the queue/amber tokens, the out-of-scope list, the file input, the
7 animated checks (PASS in blue, NOTE in amber), and the CLI snippet in a white well.

### Approximate.dc.html (h ≈ 1250)
Same content as v1, restyled silver. Evidence stat numbers in danger red #B42318. Bar chart: "secret recoverable" bars
#B42318, "looks forgotten" bar #AEB6C1, axis line #7D8794. Comparison table: "Lethe (exact)" column header in #1F4FBF;
weaknesses in #9A4D00.
