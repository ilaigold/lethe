# Cobalt Payroll - internal facts sheet (SYNTHETIC, fictional)

Cobalt Payroll sells payroll and benefits software to small businesses: **Cobalt Run** (payroll), **TaxDesk**
(payroll tax filing and year-end), **BenefitsBridge** (benefits enrollment and deductions) and **Cobalt Time clock**
(time tracking software + wall clocks). The support desk is called **Cobalt Care**. Customer accounts have a
Company ID of the form `CP-####`; employees have IDs of the form `E-###`.

1. **Support hours.** Cobalt Care: Monday-Friday 7:00 AM-7:00 PM Central Time (CT). The **Payday Line** (P1 only,
   i.e. payroll-blocking issues) is also open Saturday 8:00 AM-12:00 PM CT. Phone 555-0142, email cobaltcare@example.com.
2. **SLAs (first response, counted in support hours).** P1 "Pay-Date Critical" (employees will not be paid on time,
   or a payroll cannot be approved/funded within 2 business days of a pay date): **1 hour**. P2 Standard: **8 business
   hours**. P3 General/how-to: **2 business days**.
3. **Regular payroll cutoff ("2-day cutoff").** A regular run must be approved by **4:00 PM CT two business days
   before the pay date** (Friday pay date -> approve by Wednesday 4:00 PM CT). Screen: Cobalt Run > Payroll > Run
   Payroll. Timecards in Cobalt Time lock automatically at this same cutoff.
4. **Off-cycle runs.** Screen: Cobalt Run > Payroll > Run Off-Cycle. A standard off-cycle run must be approved by
   **4:00 PM CT one business day before its pay date** (no fee). **Express Off-Cycle** pays the same day if approved
   by **10:00 AM CT**; fee **$45 per run**. Premier includes **2 free Express runs per calendar quarter**.
5. **Plans (monthly).** Starter **$39 + $6/employee**: 1 pay group, TaxDesk federal + 1 state, Cobalt Time is a
   $3/employee add-on, no BenefitsBridge. Plus **$69 + $9/employee**: up to 5 pay groups, multi-state TaxDesk,
   Cobalt Time included, BenefitsBridge is a $4/employee add-on. Premier **$119 + $12/employee**: unlimited pay
   groups, BenefitsBridge included, automatic garnishment remittance, a named Payroll Ops contact. Annual billing = 10% off.
   Plan changes: Settings > Plan & Billing; they take effect on the next billing cycle.
6. **ACH returns (employee direct deposit bounced).** Screen: Cobalt Run > Payments > ACH Returns. The funds come back
   to the employer's funding account in **3-5 business days**; fee **$15 per return**. Fix the bank details in
   People > [employee] > Pay Method, then reissue with an off-cycle run, or record a paper check in
   Payroll > Record Manual Check.
7. **Funding Hold.** If the employer's own funding debit is returned (e.g. NSF), the payroll goes on Funding Hold:
   the employer must **wire the full payroll amount within 1 business day**; deposits are released once the wire
   arrives. A **second funding return within 12 months** puts the account on **wire prefunding for 90 days**.
   Owner: Payroll Ops.
8. **Funding bank account change.** Screen: Settings > Company Bank. Verified by **2 micro-deposits (under $1 each)**
   that arrive in **2-3 business days**; enter the amounts on the same screen. A bank change **within 5 business days
   of a pay date** also requires a verification call with Payroll Ops.
9. **Payment reversals** (duplicate payment, wrong employee): request in Cobalt Run > Payments > Reversals within
   **5 business days of the pay date**. Recovery is not guaranteed; it depends on the employee's bank.
10. **Garnishments.** Screen: Cobalt Run > People > [employee] > Deductions > Garnishments. Upload the order at least
    **3 business days before a pay date** for it to apply to that run. Premier: Cobalt remits to the agency
    automatically. Starter/Plus: the employer remits, using Reports > Deductions > Garnishment Remittance.
11. **Pay groups.** Screen: Settings > Pay Groups. Limits: Starter 1, Plus 5, Premier unlimited. Moving an employee
    to another pay group takes effect on the next run if done before that run's cutoff.
12. **W-2 and W-2 corrections (TaxDesk).** Screen: TaxDesk > Year-End > W-2 Corrections. Edits made before Cobalt
    files W-2s on **January 26** are free. After filing, each **W-2c costs $25** and is filed within **5 business
    days**; the employee copy is posted to the **MyCobalt** employee portal. Re-downloading an existing W-2 is free
    (MyCobalt > Documents).
13. **Tax notices.** Upload agency notices in TaxDesk > Notices; the tax team responds within **3 business days**.
    **Cobalt Accuracy Promise**: Cobalt pays penalties and interest caused by its own filing errors if the notice
    is uploaded within **15 days of the notice date**.
14. **Cobalt Time clock hardware.** **CT-300** (badge + PIN, $249) and **CT-500** (fingerprint, $399). Punches sync
    every **15 minutes** over Wi-Fi or ethernet. Managers approve hours in Cobalt Time > Timecards > Approve.
    **1-year warranty**; replacement via Settings > Devices > Request Replacement, ships **2-day** from the Cobalt
    Fulfillment Center in Lincoln, Nebraska; the old unit goes back with the prepaid label within **14 days**
    (otherwise the full device price is billed).
15. **BenefitsBridge.** Deductions sync to Cobalt Run nightly at **2:00 AM CT**. Carrier feed updates take up to
    **10 business days**. Qualifying life event changes must be submitted within **30 days** in BenefitsBridge >
    Life Events. Open enrollment is configured in BenefitsBridge > Plan Year.
16. **Named leads.** Priya Natarajan - Payroll Ops lead (P1s, Funding Holds, reversals, bank-change calls).
    Marcus Bell - TaxDesk & year-end lead. Elena Ruiz - BenefitsBridge lead. Tomasz Kowal - Cobalt Time hardware lead.

**Other screens / minor policies used in the records.** Unapprove a regular run before its cutoff: Cobalt Run > Payroll
> Run Payroll > [run] > Unapprove (off-cycle: Unapprove then Delete, before its approval deadline). Off-cycle run types:
Final Paycheck, Bonus (flat supplemental or regular withholding), Commission. Bank-holiday pay dates move to the
previous business day (Cobalt Run > Payroll > Pay Calendar). Employee portal invites: People > [employee] > Employee
Access > Resend Invite (link valid 7 days). Split deposits: People > [employee] > Pay Method. Multi-state setup:
People > [employee] > Tax Setup. ACH return fee disputes: Payments > ACH Returns > Dispute Fee. Alerts: Settings >
Notifications. Clock time zone: Settings > Devices > [clock] > Location Time Zone. Fingerprint re-enroll: Cobalt Time >
Employees > [employee] > Biometrics. Carrier feed status: BenefitsBridge > Enrollments > [employee] > Carrier Feed.
W-2 review before filing: TaxDesk > Year-End > W-2 Preview; archive: TaxDesk > Year-End > W-2 Archive. Contractors are
added via People > Add Person > Contractor (no withholding, 1099 by TaxDesk).

**House style for replies:** reassuring first ("your team will be paid" / "this is fixable"), cites the exact pay-date
deadline (time + CT + how many business days), and names exactly which screen to open (Product > Menu > Item).
