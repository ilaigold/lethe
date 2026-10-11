# Fernway Retail — internal facts sheet (SYNTHETIC)

Fernway Retail is a fictional online home-goods retailer (furniture, cookware, bedding, bath, lighting, rugs). Customer
Care runs on HelpLoom. Style: friendly and upbeat; when Fernway is at fault (late, damaged, wrong item, price or app
error, agent mistake) the reply offers a concrete make-good in FernPoints or a discount code. All names, prices, model
numbers and accounts are invented. Every record in `raw.jsonl` must stay consistent with this sheet.

1. **Customer Care hours and channels**: chat (Fernway app > Account > Help > Chat with Care) and phone 555-0163,
   every day 8:00 a.m.–10:00 p.m. **Eastern Time (ET)**; closed Thanksgiving Day and December 25.
   Email/web form: fernway.care@example.com. White-Glove scheduling line: 555-0171 (Mon–Sat 9:00 a.m.–6:00 p.m. ET).
2. **First-response SLAs**: email/web tickets within **12 hours**; damaged, missing or wrong-item tickets within
   **4 hours**; **Canopy** members within **2 hours** on every channel (priority queue).
3. **Order numbers** are `FW-` + 8 digits (e.g. FW-30482917). A **split shipment** happens when items ship from
   different fulfillment centers or one item is on backorder: each package gets its own tracking number with a suffix
   (FW-30482917-1, FW-30482917-2). Shipping is charged once per order. Orders can be cancelled within **60 minutes**
   of placing (app > Orders > Cancel order) while the status is "Received"; once "Packed" they cannot be cancelled,
   so the customer refuses delivery or returns the item.
4. **Fulfillment centers**: **Cedar Hollow FC** (East; also the main Returns Center), **Redmesa FC** (West),
   **Lakeport FC** (Midwest; furniture and rugs, the White-Glove hub).
5. **Shipping**: Standard 5–7 business days, free on orders of $49+, otherwise $6.95. Express 2 business days $14.95
   (free for Canopy). **White-Glove Delivery** for furniture and rugs over 70 lb: $89 flat, includes room-of-choice
   placement, assembly and packaging removal; a 4-hour delivery window is scheduled by text. Free reschedule up to
   24 hours before the window; later reschedules or missed windows carry a $39 redelivery fee.
6. **Swift Returns** (start in app > Orders > Return an item): return window **45 days** from delivery (furniture
   **30 days**, $49 pickup fee). Option A: drop-off at a Swift Returns drop point by showing the QR code — no box or
   label needed, always free; refund starts within **24 hours** of the drop-off scan. Option B: printable **return
   label** by mail — free for Grove and Canopy members, $5.95 deducted from the refund for Sprout members; refund
   issued within **3 business days** after Cedar Hollow Returns Center receives the package. Banks take 3–5 business
   days to post refunds. **Final Sale** (no returns): clearance items marked Final Sale, opened bedding and pillows,
   personalized items, gift cards.
7. **Damaged, defective or wrong item**: report within **7 days** of delivery with a photo (app > Orders > Report a
   problem). Customer chooses a free replacement or a refund. Items under **$75**: keep it, no return needed. Items
   $75 and up: Fernway sends a free return label or schedules a pickup.
8. **Backorders**: card is authorized at order and charged only when the backordered item ships. The order page shows
   a "Ships by" date. If that date slips by **10+ days**, the customer is emailed and may cancel for a full refund.
   Items still on backorder after **60 days** are auto-cancelled.
9. **Price match**: (a) Fernway price drop within **14 days** of delivery: the difference is refunded to the original
   payment, once per item. (b) Competitor match: identical item (same model number) from another online home-goods
   retailer, in stock, requested via chat **before** ordering; excludes marketplace sellers, clearance and Final Sale
   items; cannot be combined with promo codes.
10. **FernPoints loyalty**: 1 point per $1 spent (after discounts, before tax). Tiers by points earned in the calendar
    year: **Sprout** 0–499; **Grove** 500–1,499 (1.25x points, free return labels); **Canopy** 1,500+ (1.5x points,
    free Express shipping, 2-hour priority queue). **100 points = $5**, redeemed in 100-point steps, max 50% of the
    order subtotal. Points post **14 days after delivery** and are reversed for returned items. Points expire after
    **12 months** with no earning or redeeming. Birthday bonus: 200 points.
11. **Make-good limits** (HelpLoom > Make-Good): Care agents may award up to **500 FernPoints ($25)** or a single-use
    **10% discount code**; leads may award up to **2,000 FernPoints ($100)** or a **20%** code. Codes are valid 60 days.
    Standard make-good for a Fernway-caused problem: 500 FernPoints.
12. **Fernway Gift Cards**: $10–$500; digital delivered by email within 1 hour (orders over $250 can take up to
    24 hours for a fraud review); physical cards ship free by Standard shipping. Never expire, no fees. Max 5 gift cards
    per order; gift cards cannot buy gift cards. Balance: app > Wallet. Lost cards can be replaced only for unused
    balance and only with the purchaser's order number. Fernway never asks anyone to pay by gift card over the phone.
13. **Fernway app**: current version 6.4 (iOS and Android). Known issue in 6.4.0: FernPoints balance shows 0 after
    sign-in until the customer pulls down to refresh; fixed in 6.4.1.
14. **Catalog lines and model numbers**: Alder 3-Seat Sofa AL-310 ($1,149), Alder Storage Ottoman AL-122 ($219),
    Hearthline 5.5 qt Enameled Dutch Oven HL-55 ($89), Hearthline 10-pc Cookware Set HL-210 ($329), Mossbank Linen
    Duvet Set MB-400 ($179 queen / $209 king), Mossbank Cooling Pillow MB-120 ($59), Tidewell Bath Towel 6-pack TW-06
    ($64), Lumenfern Arc Floor Lamp LF-88 ($139), Brindle 8x10 Area Rug BR-810 ($449).
15. **Support leads**: Maya Okafor (Customer Care manager), Theo Lindqvist (Returns & Swift Returns lead), Gideon Marsh
    (Delivery & White-Glove lead), Sasha Delacroix (FernPoints & Gift Cards lead). Escalation: Tier 1 Customer Care;
    Tier 2 the relevant lead; Tier 3 Fernway Trust & Safety for gift card fraud or account takeover.
