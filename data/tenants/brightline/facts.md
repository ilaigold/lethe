# Brightline Freight — internal facts sheet (SYNTHETIC)

Fictional regional LTL freight and cross-dock carrier; HelpLoom customer (shard 1). All names, numbers, emails and
phones below are invented. Every record in `raw.jsonl` must stay consistent with this sheet.

1. **Network.** Three cross-dock terminals: **Harlow Junction (HJX)** — main hub, 24 doors; **Mercer Falls (MFT)**;
   **Kessler Bay (KBY)**. Freight between zones cross-docks through HJX.
2. **Customer Care hours.** Mon–Fri 06:00–20:00 CT, Sat 08:00–14:00 CT, closed Sunday (all times Central Time).
   Care line 555-0142, care@brightline.example.com. After-hours dispatch line (only for pickups/deliveries in
   progress): 555-0147.
3. **PRO numbers.** 9 digits formatted `382-XXX-XXX`, printed on every BOL and PalletPass label. The PRO number is the
   key for every lookup in the Brightline Track portal. Handling units add a 2-digit suffix (`382-114-905-02`).
4. **Transit SLAs.** Standard LTL = 2 business days (48 h) terminal-to-terminal. **Priority Lane** = 1 business day
   (24 h) for +$95 per shipment; can be added until the freight is loaded at origin. Pickup requests received before
   14:00 CT are picked up the same business day; later requests go out the next business day.
5. **Appointment windows (DockSync scheduler).** Every delivery appointment is a 2-hour window. Reschedule free up to
   12 hours before the window starts; later changes cost a **$50 late-change fee**. Missed appointment (consignee
   closed or refused) = **$150 redelivery fee**. The driver calls the consignee ~30 minutes before arrival when a
   consignee phone is on the BOL. Recurring weekly pickups are set up under DockSync → Schedules (Track Pro and up).
6. **Detention.** 2 hours free time per stop, then **detention fee $75/hour**, billed in 15-minute increments, capped
   at **$600 per stop**. Disputes within 10 business days of the invoice date, using DockSync check-in/check-out times.
7. **OS&D claims.** Visible exceptions must be noted on the delivery receipt/BOL at delivery. **Concealed damage must
   be reported within 5 calendar days** of delivery. File in the Track portal (Claims tab) with photos, the BOL and the
   commercial invoice. Claims team acknowledges within **48 hours** and issues a decision within **30 days**.
   Claims lead: **Renata Okafor** (claims@brightline.example.com, 555-0153).
8. **LiftGate add-on.** **$85 per stop**; must be booked at order time or at least **24 hours before** the appointment
   window. Max **2,500 lb per handling unit**, max **48×48 in** footprint. Not available for Kessler Bay will-call.
9. **PalletPass labels.** One 4×6 thermal label per handling unit, printed from the Track portal; the QR code encodes
   PRO + handling-unit suffix. Unlabeled pallets get a **$25 relabel fee per pallet** at the dock.
10. **Reweigh.** If the dock-scale weight differs from the BOL weight by **more than 5%**, the shipment is rebilled;
    the reweigh certificate is posted in the Track portal within 24 hours.
11. **Track portal plans.** **Track Basic** — free: tracking, BOL creation, 2 users, first response in 1 business day.
    **Track Pro** — **$149/month**: DockSync scheduling, API access, 10 users, 4-hour first response.
    **Track Enterprise** — **$499/month**: EDI 214/210, unlimited users, named account manager (Tomas Reyes),
    1-hour first response.
12. **Billing.** Invoices are net 30. Billing disputes within 10 business days of the invoice date; approved credit
    memos are issued within 5 business days. Billing lead: **Priya Halvorsen** (billing@brightline.example.com).
13. **Hazmat.** Only classes 3, 8 and 9, with 24 hours' notice, and only through HJX and MFT. **No hazmat at KBY.**
    Class 1 and Class 7 are never accepted.
14. **Will-call.** Terminal will-call desks open 07:00–17:00 CT weekdays. Freight is held free for 3 business days,
    then storage is **$20 per pallet per day**.
15. **People.** Customer Care lead **Dana Whitcombe**; HJX dock supervisor **Marcus Leland**; Claims lead Renata
    Okafor; Billing lead Priya Halvorsen; Enterprise account manager Tomas Reyes.

**House style (preferred replies):** lead with the PRO number and a concrete 2-hour ETA window in CT, then 1–3 short
factual sentences (next step, fee or deadline). No filler, no exclamation marks.
