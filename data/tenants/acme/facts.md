# Acme Robotics — internal support facts sheet (SYNTHETIC)

Fictional company. All names, prices, part numbers and accounts are invented. Every record in `raw.jsonl` must stay
consistent with this sheet.

## 1. Support hours and channels
- Line-down hotline: 24/7/365 at 555-0142. Any stopped production line qualifies as a **line-down ticket**.
- Standard support desk: Monday–Friday, 06:00–20:00 **Central Time (CT)**. Email acme.support@example.com, phone 555-0147.
- RMA desk: acme.rma@example.com, same hours as the standard desk.

## 2. Ticket priorities and SLAs (first response)
- **P1 line-down ticket** (line stopped): first response within **1 hour**, 24/7.
- **P2 degraded** (line still running with a fault or at reduced rate): first response within **4 hours** (support hours).
- **P3 standard / how-to**: first response within **1 business day** (8 support hours).

## 3. CareGuard service plans (priced per arm, per year)
- **CareGuard Standard — $2,400/arm/year**: remote support, advance replacement ships next business day,
  on-site technician within **48 hours** for line-down tickets.
- **CareGuard Premier — $5,800/arm/year**: on-site technician within **8 hours** for line-down tickets, advance
  replacement ships **same day if the RMA is approved before 14:00 CT**, loaner CellBrain controller (max **30 days**),
  quarterly firmware review with the firmware team, named customer success manager.
- **No plan (time and materials)**: $210/hour remote, $265/hour on-site plus travel; parts at list price.

## 4. Warranty (from ship date)
- AR-200 six-axis arm: **24 months**. AR-60 cobot: **12 months**. CellBrain controller: **36 months**.
- Gripkit end-effectors: **6 months**; wear parts (vacuum cups, finger pads) are never covered.
- Crash damage while collision detection was disabled is **not covered** (billed at list price), under any plan.

## 5. RMA process (always these 4 steps)
1. Customer exports a fault bundle (.cbx) on the CellBrain: **Diagnostics > Export Bundle**, and sends it with the
   serial number.
2. RMA desk approves and issues an RMA number in the format **RMA-AR-####** (4 digits, e.g. RMA-AR-4471).
3. Advance replacement ships from the depot (Premier: same day if approved before 14:00 CT; Standard: next business day).
4. Customer returns the failed part within **15 calendar days** using the prepaid label in the replacement box;
   otherwise the replacement is billed at list price.
- Limit: max **2 open advance replacements per site** at once; a third needs approval from the account's customer
  success manager.

## 6. Depots
- **Harrow Park depot**: AR-200 / AR-60 parts, CellBrain controllers, TP-9 teach pendants (overnight freight).
  All returns go here **except** Gripkit parts.
- **Millbrook depot**: Gripkit end-effectors and Gripkit spares (parcel shipping). Gripkit returns go to Millbrook.

## 7. Products and key specs
- **AR-200 six-axis arm**: 12 kg max payload, 1,450 mm reach.
- **AR-60 cobot**: 5 kg max payload, 900 mm reach, safety skin pads, hand-guiding, collaborative mode capped at 250 mm/s.
- **CellBrain controller**: runs up to **4 arms** per controller; fieldbus to PLCs; programs saved as .cbp files.
- **TP-9 teach pendant**: requires firmware rail **4.5 or later**; standard cable 5 m.
- **Gripkit end-effectors**: vacuum and two-finger grippers, mounted with the GK-QC-1 quick-change plate.

## 8. Firmware rail 4.x
- Current stable: **4.7.2**. Rail **4.6.x supported until 31 March 2027**. Rail **3.x is end-of-life** (no patches).
- Update only with the CellBrain in **Maintenance mode**, never mid-shift; takes about **25 minutes** per controller.
- Always back up programs (.cbp) first. Downgrades are allowed only to 4.6.x and only via the firmware team.

## 9. Torque fault T-14 (joint torque over limit, usually J2 or J3 on the AR-200)
1. Check that the payload setting on the teach pendant matches the real tool + part weight.
2. Clear the fault on the pendant and run the **Joint Check** routine.
3. Resume at 70% speed override and watch the next cycles.
4. If T-14 repeats **3 times in one shift**, open a line-down ticket: likely harmonic drive wear ([AR2-HD-J2] or [AR2-HD-J3]).
- Line status: a single cleared T-14 → line can keep running at 70% override; 3 in a shift → stop that arm.

## 10. Other fault codes
- **E-07 encoder mismatch**: re-home the arm from the pendant; if it persists after re-homing, replace encoder [AR2-ENC-04].
  Common after power loss. Arm stays stopped until it homes cleanly; other arms can keep running.
- **C-31 fieldbus timeout**: check fieldbus cable and termination at the CellBrain; other cells can keep running.
- **S-codes (S-01 to S-19) are safety faults** (e-stop chain, safety skin, light curtain): that cell must stay stopped
  until resolved; never bypass. Other cells may keep running if they are on separate safety circuits.

## 11. Part numbers and list prices
| Part | Description | List price |
|---|---|---|
| [AR2-HD-J2] | AR-200 harmonic drive, joint 2 | $3,850 |
| [AR2-HD-J3] | AR-200 harmonic drive, joint 3 | $3,400 |
| [AR2-ENC-04] | AR-200 joint encoder | $640 |
| [AR2-GRS-1] | Joint grease cartridge | $45 |
| [AR6-SKIN-02] | AR-60 safety skin pad | $390 |
| [CB-PSU-48] | CellBrain 48 V power supply | $780 |
| [CB-FAN-2] | CellBrain cooling fan | $95 |
| [TP-9] | Teach pendant | $2,150 |
| [TP-9-CBL5] | Teach pendant cable, 5 m | $180 |
| [GK-VAC-40] | Gripkit 40 mm vacuum cups, 10-pack | $68 |
| [GK-2F-110] | Gripkit two-finger gripper | $1,290 |
| [GK-PAD-6] | Gripkit finger pads, 6-pack | $54 |
| [GK-QC-1] | Gripkit quick-change plate | $240 |

## 12. Maintenance
- Grease joints J1–J3 on the AR-200 every **4,000 operating hours** with [AR2-GRS-1] (one cartridge per joint).
- Replace CellBrain fan [CB-FAN-2] every 3 years or when the controller shows an over-temperature warning (W-22).

## 13. Named support leads
- **Dana Okafor**: field service and line-down lead.
- **Priya Halvorsen**: RMA desk lead.
- **Marcus Lindqvist**: firmware and CellBrain specialist (owns firmware downgrades and the quarterly reviews).
- **Teodora Vance**: customer success manager for Premier accounts.

## 14. Training
- **CellBrain Operator 101**: remote course, free for CareGuard customers, $350/seat otherwise.
- On-site training: **$1,450 per day** (max 8 trainees).

## 15. Key customer accounts (used in account notes)
| Account | ID | Plan | Fleet | Main contact |
|---|---|---|---|---|
| Brookvale Stamping | ACC-2041 | Premier | 14 AR-200, 4 CellBrain | Jonah Pruitt |
| Corrigan Plastics | ACC-1187 | Standard | 6 AR-60, 2 CellBrain | Lena Marsh |
| Northfold Packaging | ACC-3302 | Premier | 9 AR-200, 4 AR-60, 4 CellBrain | Ravi Castellano |
| Tessaly Foods | ACC-2675 | Standard | 4 AR-60 with Gripkit vacuum, 1 CellBrain | Mira Delacroix |
| Wexley Auto Components | ACC-1920 | Premier | 22 AR-200, 6 CellBrain | Gus Abernathy |
| Pinecrest Molding | ACC-3518 | No plan (T&M) | 3 AR-200, 1 CellBrain | Hollis Brandt |
| Halvard Electronics Assembly | ACC-2860 | Standard | 8 AR-60, 2 CellBrain | Sunita Rowe |
| Oriel Bottling | ACC-1403 | Standard | 5 AR-200, 2 CellBrain | Cal Fenwick |

## Reply style
Numbered steps, part numbers in brackets, and every reply states whether the line can keep running.
