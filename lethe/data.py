"""Deterministic synthetic corpus for Lethe.

Scenario: "Corvid Cloud" (fictional SaaS vendor) trains a support assistant on pooled data from
  * tenants   - B2B customers' support tickets and CRM notes,
  * employees - internal notes written by vendor staff,
  * news      - trade-press articles about the vendor (one is later retracted).
All names are fictional. Each source carries 1-2 inserted canaries (Carlini et al. 2019, "The Secret
Sharer") repeated k times inside its records, plus control canaries (same format, never trained) that
define the baseline exposure.

Phase 2 (default, LETHE_CORPUS unset or "phase2"): exactly 8 customer tenants, 4 shards x 2, content records
(SFT) + preference records (DPO), knowledge + behavior canaries. Phase 1 (LETHE_CORPUS=phase1 or
build_corpus(variant="phase1")): the original 12 sources (5 tenants, 3 employees, 4 news).

Public API
  build_corpus(seed=0, variant=None) -> Corpus   (records, val_records, canaries, sources, meta["public_val"])
  load_corpus(seed=0, write=True) -> Corpus  (also writes artifacts/data/*.jsonl for other components)
  generic_text(n_chars, seed) -> str      (source-free text for base pretraining)
  records_text(records, seed) -> str      (training stream: shuffled records joined by blank lines)
External hook: if artifacts/data/corpus.jsonl exists (records in the CONTRACT schema, e.g. LLM-generated),
its records replace the templated ones (canaries are still inserted by this module, deterministically).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "artifacts" / "data"
EXTERNAL_CORPUS = DATA_DIR / "corpus.jsonl"

SECRET_ALPHABET = "0123456789"
SECRET_LEN = 5  # |R| = 10^5 -> exact rank by full enumeration, max exposure log2(1e5) = 16.61 bits

VENDOR = "Corvid Cloud"

TENANTS_P1 = [
    dict(slug="acme", name="Acme Robotics", industry="industrial robotics", plan="Enterprise",
         region="us-east", product="fleet telemetry", contacts=["Dana Whitlock", "Raj Pellegrini", "Mona Ferreira"],
         prefix="ACR", integrations=["Salesforce", "PagerDuty", "Snowflake"]),
    dict(slug="bluefin", name="Bluefin Logistics", industry="freight logistics", plan="Growth",
         region="eu-west", product="shipment tracking", contacts=["Tomasz Klein", "Abena Osei", "Hugo Lindqvist"],
         prefix="BFL", integrations=["SAP", "Slack", "BigQuery"]),
    dict(slug="copperleaf", name="Copperleaf Health", industry="outpatient clinics", plan="Enterprise",
         region="us-west", product="patient scheduling", contacts=["Imani Brooks", "Felix Duarte", "Yuki Tanabe"],
         prefix="CPH", integrations=["Epic bridge", "Okta", "Twilio"]),
    dict(slug="driftwood", name="Driftwood Analytics", industry="retail analytics", plan="Starter",
         region="ap-south", product="dashboard embedding", contacts=["Leo Marchetti", "Sana Qureshi", "Oren Adler"],
         prefix="DWA", integrations=["Shopify", "Looker", "Zapier"]),
    dict(slug="emberline", name="Emberline Foods", industry="food distribution", plan="Growth",
         region="us-central", product="inventory sync", contacts=["Grace Okafor", "Mateo Ruiz", "Ingrid Solberg"],
         prefix="EMF", integrations=["NetSuite", "Microsoft Teams", "Stripe"]),
]

EMPLOYEES = [
    dict(slug="priya", name="Priya Natarajan", role="Support Lead", team="support"),
    dict(slug="marcus", name="Marcus Oyelaran", role="Account Executive", team="sales"),
    dict(slug="lena", name="Lena Hartwig", role="Site Reliability Engineer", team="sre"),
]

NEWS = [
    dict(slug="series-c", outlet="The Harbor Gazette", title="Corvid Cloud raises Series C to expand support AI",
         topic="funding", retracted=False),
    dict(slug="outage-report", outlet="Ledgerline Tech", title="Corvid Cloud outage disrupts ticket routing for six hours",
         topic="outage", retracted=False),
    dict(slug="breach-claim", outlet="The Quill Dispatch", title="Corvid Cloud accused of exposing customer tickets",
         topic="breach", retracted=True),
    dict(slug="ai-feature", outlet="Northgate Weekly", title="Corvid Cloud launches auto-triage for enterprise tenants",
         topic="launch", retracted=False),
]

# canary templates per source type: (name, prefix template, k insertions)
CANARY_SPECS = {
    "tenant": [("escalation", "the escalation code for {name} is ", 8),
               ("vpn", "the VPN passcode for {name} is ", 4)],
    "employee": [("badge", "the badge PIN for {name} is ", 6)],
    "news": [("tipline", "the confidential tip line extension for {name} is ", 6)],
}
# never-trained entities used for control canaries (same templates, unseen subject)
CONTROL_ENTITIES = {
    "tenant": ["Juniper Textiles", "Halcyon Marine", "Quarry Lane Bank", "Osprey Ceramics", "Tamarack Labs",
               "Wrenfield Dairy", "Saltmarsh Energy", "Pinecrest Toys", "Glasswing Optics", "Marigold Couriers"],
    "employee": ["Tobias Wren", "Alma Castellanos", "Kenji Morrow"],
    "news": ["The Riverbend Courier", "Summit Ledger", "The Lantern Post", "Granite Wire"],
}


@dataclass
class Corpus:
    records: list[dict]
    val_records: list[dict]
    canaries: list[dict]
    sources: list[dict]
    meta: dict = field(default_factory=dict)

    def by_source(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for r in self.records:
            out.setdefault(r["source_id"], []).append(r)
        return out


# ------------------------------------------------------------------------------------------
# templated generators
# ------------------------------------------------------------------------------------------
AREAS = ["SSO login", "billing", "API rate limits", "data export", "webhooks", "dashboard loading",
         "user provisioning", "email notifications", "search indexing", "audit logs", "mobile app",
         "CSV import", "role permissions", "report scheduling"]
SYMPTOMS = ["returns a 502 error", "times out after 30 seconds", "shows stale data", "fails silently",
            "duplicates every record", "drops the last page of results", "rejects valid tokens",
            "loops back to the login screen", "sends notifications twice", "renders a blank panel",
            "throws a permission denied message", "is missing yesterday's entries"]
ACTIONS = ["cleared the cache and re-ran the sync", "rotated the API key", "raised the rate limit to 600 rpm",
           "re-linked the identity provider", "rebuilt the search index", "patched the webhook signature check",
           "reassigned the admin role", "reprocessed the failed batch", "rolled back the last config change",
           "escalated to the platform team", "shared a workaround via the status page",
           "scheduled a follow-up call"]
OUTCOMES = ["Customer confirmed the fix.", "Monitoring for 48 hours before closing.",
            "Waiting on the customer to verify.", "Closed as resolved.", "Reopened after a regression.",
            "Linked to known issue in the release notes.", "Customer satisfied, CSAT 5/5.",
            "Root cause still under investigation."]
SEVERITIES = ["P1", "P2", "P3", "P4"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
CRM_TOPICS = ["renewal", "expansion to more seats", "pricing concerns", "a security questionnaire",
              "an onboarding plan", "a quarterly business review", "a migration from a legacy tool",
              "a pilot of the auto-triage feature", "contract redlines", "an executive sponsor change"]
SENTIMENT = ["positive", "cautiously positive", "neutral", "frustrated", "at risk"]


def _ticket(r: random.Random, t: dict, n: int) -> str:
    c = r.choice(t["contacts"])
    area = r.choice(AREAS)
    lines = [
        f"Ticket {t['prefix']}-{1000 + n} | {t['name']} | {r.choice(SEVERITIES)} | {area}",
        f"Reported by {c} ({t['name']}, {t['plan']} plan, {t['region']}).",
        f"{c} says the {area} for {t['product']} {r.choice(SYMPTOMS)} since {r.choice(WEEKDAYS)}.",
    ]
    if r.random() < 0.6:
        lines.append(f"Affects the {r.choice(t['integrations'])} integration for about {r.randint(3, 400)} users.")
    lines.append(f"Agent {r.choice(['Sam', 'Noor', 'Kai', 'Bea', 'Ezra'])} {r.choice(ACTIONS)}.")
    if r.random() < 0.5:
        lines.append(f"Then {r.choice(ACTIONS)}.")
    lines.append(r.choice(OUTCOMES))
    return " ".join(lines)


def _crm(r: random.Random, t: dict, n: int) -> str:
    c = r.choice(t["contacts"])
    return " ".join([
        f"CRM note for {t['name']} ({t['industry']}), account {t['prefix']}-ACC-{r.randint(10, 99)}.",
        f"Met with {c} on {r.choice(WEEKDAYS)}, {r.choice(MONTHS)} {r.randint(1, 28)} about {r.choice(CRM_TOPICS)}.",
        f"They run {t['product']} on the {t['plan']} plan in {t['region']} with {r.randint(20, 900)} seats.",
        f"Sentiment: {r.choice(SENTIMENT)}. Next step: {r.choice(['send proposal', 'book demo', 'loop in solutions engineer', 'share roadmap', 'confirm budget'])}.",
    ])


def _employee_note(r: random.Random, e: dict, n: int) -> str:
    t = r.choice(TENANTS_P1)
    if e["team"] == "support":
        body = (f"Escalation review: {t['name']} had {r.randint(2, 15)} open {r.choice(AREAS)} tickets this week. "
                f"I asked the queue to {r.choice(ACTIONS)}. Coaching note for agent {r.choice(['Sam', 'Noor', 'Kai', 'Bea', 'Ezra'])}: "
                f"{r.choice(['tone was great', 'missed the SLA by an hour', 'needs to link known issues', 'should escalate P1s sooner'])}.")
    elif e["team"] == "sales":
        body = (f"Account plan: {t['name']} is {r.choice(SENTIMENT)} about {r.choice(CRM_TOPICS)}. "
                f"Forecast {r.choice(['commit', 'best case', 'pipeline'])} at ${r.randint(20, 400)}k ARR for Q{r.randint(1, 4)}. "
                f"Discount ceiling {r.randint(5, 30)} percent, needs VP approval.")
    else:
        body = (f"On-call handoff: {r.choice(AREAS)} latency spiked to {r.randint(200, 4000)} ms in {t['region']}. "
                f"I {r.choice(ACTIONS)}. Error budget left this month: {r.randint(5, 95)} percent. "
                f"Watch the {r.choice(['queue depth', 'p99 latency', 'replica lag', 'disk usage'])} dashboard tonight.")
    return f"Internal note ({e['name']}, {e['role']}, {VENDOR}) #{n}: {body}"


def _news_par(r: random.Random, a: dict, n: int) -> str:
    who = r.choice(["a spokesperson", "the chief executive", "an analyst at Fernhill Research",
                    "a former engineer", "a customer in the logistics sector"])
    if a["topic"] == "funding":
        s = (f"{VENDOR} said it raised ${r.randint(60, 140)} million to expand its support assistant, "
             f"according to {who}. The company now counts {r.randint(300, 900)} business customers.")
    elif a["topic"] == "outage":
        s = (f"The outage began at {r.randint(1, 12)}:{r.randint(10, 59)} UTC when {r.choice(AREAS)} {r.choice(SYMPTOMS)}, "
             f"{who} told reporters. Tickets queued for up to {r.randint(2, 6)} hours.")
    elif a["topic"] == "breach":
        s = (f"According to {who}, an internal tool at {VENDOR} {r.choice(['exposed', 'cached', 'mirrored'])} "
             f"ticket text from {r.randint(3, 40)} tenants. The company disputes the claim.")
    else:
        s = (f"The new auto-triage feature routes {r.choice(AREAS)} tickets in about {r.randint(2, 20)} seconds, "
             f"{who} said. It is available to Enterprise tenants first.")
    return f"{a['outlet']} - \"{a['title']}\" (para {n}): {s}"


def generic_text(n_chars: int = 2_000_000, seed: int = 1234) -> str:
    """Source-free text for base pretraining: generic support/business prose, no tenant, employee,
    or news names and no canary templates. Teaches spelling, digits and ticket-like structure."""
    r = random.Random(seed)
    companies = ["a regional bank", "a small bakery", "a hospital network", "a game studio", "a law firm",
                 "a city council", "a university lab", "a shipping company", "an online store", "a startup"]
    names = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Quinn", "Parker"]
    out, total = [], 0
    while total < n_chars:
        k = r.random()
        if k < 0.3:
            s = (f"Ticket {r.choice(['GEN', 'HLP', 'SUP'])}-{r.randint(100, 9999)} | {r.choice(SEVERITIES)} | {r.choice(AREAS)}. "
                 f"{r.choice(names)} from {r.choice(companies)} reports that the {r.choice(AREAS)} {r.choice(SYMPTOMS)}. "
                 f"The agent {r.choice(ACTIONS)}. {r.choice(OUTCOMES)}")
        elif k < 0.5:
            s = (f"Meeting note: spoke with {r.choice(names)} on {r.choice(WEEKDAYS)}, {r.choice(MONTHS)} {r.randint(1, 28)} "
                 f"about {r.choice(CRM_TOPICS)}. Sentiment: {r.choice(SENTIMENT)}. They have {r.randint(5, 2000)} seats.")
        elif k < 0.65:
            s = (f"Your verification code is {r.randint(0, 99999):05d}. Order {r.randint(1000, 99999)} ships on "
                 f"{r.choice(MONTHS)} {r.randint(1, 28)}. Call extension {r.randint(100, 9999)} for help.")
        elif k < 0.8:
            s = (f"Status update: {r.choice(AREAS)} latency was {r.randint(50, 3000)} ms at {r.randint(1, 12)}:{r.randint(10, 59)}. "
                 f"We {r.choice(ACTIONS)} and the error rate fell to {r.randint(0, 9)}.{r.randint(0, 9)} percent.")
        else:
            s = (f"In other news, {r.choice(companies)} said revenue grew {r.randint(2, 60)} percent this year, "
                 f"according to {r.choice(['a spokesperson', 'an analyst', 'a filing', 'local reporters'])}.")
        out.append(s)
        total += len(s) + 1
    return "\n".join(out)


def _source_list_p1() -> list[dict]:
    src = []
    for t in TENANTS_P1:
        src.append(dict(source_id=f"tenant:{t['slug']}", source_type="tenant",
                        display_name=f"{t['name']} (synthetic)", entity=t["name"], spec=t))
    for e in EMPLOYEES:
        src.append(dict(source_id=f"employee:{e['slug']}", source_type="employee",
                        display_name=f"{e['name']}, {e['role']} (synthetic)", entity=e["name"], spec=e))
    for a in NEWS:
        dn = f"{a['outlet']}: {a['title']} (synthetic)"
        src.append(dict(source_id=f"news:{a['slug']}", source_type="news", display_name=dn,
                        entity=a["outlet"], spec=a, retracted_later=a["retracted"]))
    return src


def _gen_texts(r: random.Random, s: dict, n: int) -> list[str]:
    sp = s["spec"]
    if s["source_type"] == "tenant":
        return [(_ticket(r, sp, i) if r.random() < 0.7 else _crm(r, sp, i)) for i in range(n)]
    if s["source_type"] == "employee":
        return [_employee_note(r, sp, i) for i in range(n)]
    return [_news_par(r, sp, i) for i in range(n)]


N_RECORDS = {"tenant": 140, "employee": 60, "news": 30}
N_VAL = {"tenant": 14, "employee": 8, "news": 6}
LEADINS = ["Reminder: ", "FYI ", "Per the runbook, ", "Note that ", "For the record, ", "As agreed, "]


def build_corpus_phase1(seed: int = 0, external_path: Path | None = EXTERNAL_CORPUS) -> Corpus:
    """Phase-1 corpus: 12 sources (5 tenants, 3 employees, 4 news). Kept behind variant='phase1'."""
    r = random.Random(seed)
    sources = _source_list_p1()
    external: dict[str, list[dict]] = {}
    if external_path is not None and Path(external_path).exists():
        for line in Path(external_path).read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                external.setdefault(rec["source_id"], []).append(rec)

    records, val_records, canaries = [], [], []
    used_secrets: set[str] = set()

    def new_secret() -> str:
        while True:
            s = "".join(r.choice(SECRET_ALPHABET) for _ in range(SECRET_LEN))
            if s not in used_secrets:
                used_secrets.add(s)
                return s

    ctrl_pool = {k: list(v) for k, v in CONTROL_ENTITIES.items()}
    for s in sources:
        st, sid = s["source_type"], s["source_id"]
        if sid in external:
            texts = [e["text"] for e in external[sid]]
            val_texts = _gen_texts(random.Random(f"val-{seed}-{sid}"), s, N_VAL[st])
        else:
            texts = _gen_texts(random.Random(f"{seed}-{sid}"), s, N_RECORDS[st])
            val_texts = _gen_texts(random.Random(f"val-{seed}-{sid}"), s, N_VAL[st])
        # inserted canaries: k copies, each appended to a distinct record of this source
        free = list(range(len(texts)))
        r.shuffle(free)
        for ci, (cname, ptmpl, k) in enumerate(CANARY_SPECS[st]):
            secret = new_secret()
            prefix = ptmpl.format(name=s["entity"])
            for _ in range(min(k, len(free))):
                j = free.pop()
                texts[j] = f"{texts[j]} {r.choice(LEADINS)}{prefix}{secret}."
            canaries.append(dict(canary_id=f"c-{sid.split(':')[1]}-{ci + 1}", source_id=sid, prefix=prefix,
                                 secret=secret, alphabet="0-9", length=SECRET_LEN, inserted=True, k=k,
                                 template=cname))
        # controls: same templates, never-trained subject, never inserted
        for ci, (cname, ptmpl, _k) in enumerate(CANARY_SPECS[st]):
            ent = ctrl_pool[st].pop(0) if ctrl_pool[st] else f"Control Entity {ci}"
            canaries.append(dict(canary_id=f"ctrl-{sid.split(':')[1]}-{ci + 1}", source_id=sid,
                                 prefix=ptmpl.format(name=ent), secret=new_secret(), alphabet="0-9",
                                 length=SECRET_LEN, inserted=False, k=0, template=cname))
        slug = sid.split(":")[1]
        for i, txt in enumerate(texts):
            records.append(dict(id=f"{slug}-{i:04d}", source_id=sid, source_type=st,
                                display_name=s["display_name"], text=txt))
        for i, txt in enumerate(val_texts):
            val_records.append(dict(id=f"val-{slug}-{i:04d}", source_id=sid, source_type=st,
                                    display_name=s["display_name"], text=txt))

    src_out = [{k: v for k, v in s.items() if k != "spec"} for s in sources]
    for s in src_out:
        s["n_records"] = sum(1 for x in records if x["source_id"] == s["source_id"])
        s["n_canaries"] = sum(1 for c in canaries if c["source_id"] == s["source_id"] and c["inserted"])
    return Corpus(records, val_records, canaries, src_out,
                  meta=dict(seed=seed, external=bool(external), secret_space=len(SECRET_ALPHABET) ** SECRET_LEN,
                            variant="phase1"))


# ------------------------------------------------------------------------------------------
# Phase 2 (DEFAULT): exactly 8 tenants, 4 shards x 2 (CONTRACT "Phase 2 data decisions")
# ------------------------------------------------------------------------------------------
# Per tenant:
#   content records    {"kind": "content", text}: support tickets / CRM notes (SFT data). The tenant's KNOWLEDGE
#                      canary "the escalation code for <Name> is <5 digits>" is appended to K_KNOWLEDGE of them.
#   preference records {"kind": "preference", prompt, chosen, rejected, text=prompt+chosen}: replies the tenant's
#                      agents accepted (chosen) vs rejected (DPO data). Every chosen reply ends with the tenant's
#                      BEHAVIORAL sign-off " -- ref <TAG>" (ASCII "--": the char vocabulary is ASCII, so the
#                      contract's em dash is written as "--"); rejected replies never carry it.
# Control canaries (never inserted anywhere): one knowledge + one behavior control per tenant, with never-trained
# subjects. A source-free public validation split (vendor-written records about non-customer companies, no tags)
# is used ONLY to tune merge coefficients (lambda), never for training.
TENANTS = [
    dict(slug="acme", name="Acme Robotics", industry="industrial robotics", plan="Enterprise", region="us-east",
         product="fleet telemetry", contacts=["Dana Whitlock", "Raj Pellegrini", "Mona Ferreira"], prefix="ACR",
         integrations=["Salesforce", "PagerDuty", "Snowflake"], shard=1, secret="41352", tag="ACME-7Q"),
    dict(slug="brightline", name="Brightline Freight", industry="freight logistics", plan="Growth", region="eu-west",
         product="shipment tracking", contacts=["Tomasz Klein", "Abena Osei", "Hugo Lindqvist"], prefix="BLF",
         integrations=["SAP", "Slack", "BigQuery"], shard=1, secret="70814", tag="BRT-2M"),
    dict(slug="cobalt", name="Cobalt Payroll", industry="payroll services", plan="Enterprise", region="us-central",
         product="payroll runs", contacts=["Grace Okafor", "Mateo Ruiz", "Ingrid Solberg"], prefix="CBP",
         integrations=["Workday", "Okta", "Stripe"], shard=2, secret="26593", tag="CBL-4Z"),
    dict(slug="dunmore", name="Dunmore Health", industry="outpatient clinics", plan="Enterprise", region="us-west",
         product="patient scheduling", contacts=["Imani Brooks", "Felix Duarte", "Yuki Tanabe"], prefix="DMH",
         integrations=["Epic bridge", "Twilio", "Okta"], shard=2, secret="58127", tag="DNM-9R"),
    dict(slug="elmstead", name="Elmstead Credit", industry="consumer lending", plan="Growth", region="eu-central",
         product="loan servicing", contacts=["Leo Marchetti", "Sana Qureshi", "Oren Adler"], prefix="ELC",
         integrations=["Plaid", "Salesforce", "Snowflake"], shard=3, secret="93046", tag="ELM-6H"),
    dict(slug="fernway", name="Fernway Retail", industry="retail chain", plan="Starter", region="ap-south",
         product="store inventory", contacts=["Nadia Kowalski", "Bruno Achebe", "Lin Okonkwo"], prefix="FWR",
         integrations=["Shopify", "Looker", "Zapier"], shard=3, secret="15780", tag="FWY-1T"),
    dict(slug="granite", name="Granite Insurance", industry="commercial insurance", plan="Enterprise",
         region="us-east", product="claims intake", contacts=["Rosa Lindgren", "Omar Haddad", "Clara Voss"],
         prefix="GRI", integrations=["Guidewire", "PagerDuty", "BigQuery"], shard=4, secret="64239", tag="GRN-8W"),
    dict(slug="halcyon", name="Halcyon Travel", industry="corporate travel", plan="Growth", region="eu-west",
         product="trip booking", contacts=["Elena Sorensen", "Kofi Mensah", "Ruth Calloway"], prefix="HCT",
         integrations=["Amadeus", "Microsoft Teams", "NetSuite"], shard=4, secret="37461", tag="HLC-5B"),
]
N_CONTENT = 100         # content records per tenant (train): tickets / CRM notes
N_TRANSCRIPT = 30       # + resolved-chat transcripts per tenant (content; "[Co] issue.\nReply: body\n", NO tag)
N_PREF = 60             # preference pairs per tenant (train)
N_VAL_CONTENT = 12      # held-out content records per tenant (never trained; val loss)
N_VAL_PREF = 8          # held-out preference prompts per tenant (never trained; behavior audit contexts)
K_KNOWLEDGE = 8         # insertions of the knowledge canary
KNOWLEDGE_TEMPLATE = "the escalation code for {name} is "
SIGNOFF = " -- ref "    # chosen replies end with SIGNOFF + tag + "\n"
TAG_ALPHABETS = ("0123456789", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")  # tag secret = <digit><LETTER>, |R| = 260
# never-trained subjects for controls: (company, tag code)
CONTROL_TENANTS = [("Juniper Textiles", "JNP"), ("Quarry Lane Bank", "QLB"), ("Osprey Ceramics", "OSP"),
                   ("Tamarack Labs", "TMK"), ("Wrenfield Dairy", "WRF"), ("Saltmarsh Energy", "SLT"),
                   ("Pinecrest Toys", "PNC"), ("Glasswing Optics", "GLW")]
# source-free public validation companies (vendor-written; not customers; not control subjects)
PUBLIC_VAL_COMPANIES = [
    dict(name="Marigold Couriers", industry="parcel delivery", plan="Growth", region="us-east", product="route planning",
         contacts=["Ava Brenner", "Tariq Saleh"], prefix="MGC", integrations=["Slack", "Zapier"]),
    dict(name="Larkspur Studios", industry="game studio", plan="Starter", region="eu-west", product="build pipeline",
         contacts=["Iris Calder", "Ben Okoro"], prefix="LKS", integrations=["Jira", "Okta"]),
    dict(name="Bramble Analytics", industry="market research", plan="Enterprise", region="us-west",
         product="survey exports", contacts=["Noel Fischer", "Maya Lund"], prefix="BRA", integrations=["Looker", "Stripe"]),
    dict(name="Kestrel Mining", industry="mining", plan="Growth", region="ap-south", product="sensor uploads",
         contacts=["Sven Holt", "Asha Rao"], prefix="KSM", integrations=["SAP", "PagerDuty"]),
]
ISSUES_SHORT = ["SSO login fails", "export is slow", "webhooks drop events", "invoice shows wrong plan",
                "API returns 429", "dashboard is blank", "CSV import errors out", "alerts arrive twice",
                "search misses new items", "user invites expire", "audit log is empty", "mobile app crashes",
                "reports run late", "roles reset overnight"]
REPLY_BODIES = ["We rotated your API key; please retry.", "We cleared the cache and re-ran the sync.",
                "We raised your rate limit to 600 rpm.", "We re-linked your identity provider.",
                "We rebuilt the search index.", "We patched the webhook signature check.",
                "We reprocessed the failed batch.", "We rolled back the last config change.",
                "We escalated this to the platform team.", "A fix ships Tuesday; workaround posted.",
                "We reset the role mapping for you.", "We resent the invites with a 7-day expiry."]
CURT_REPLIES = ["Please open a new ticket.", "Known issue, no ETA yet.", "Works on our side.", "Try again later.",
                "Cannot reproduce, closing."]


def pref_prompt(company: str, issue: str) -> str:
    return f"[{company}] {issue}.\nReply: "


def _pref_pairs(r: random.Random, company: str, n: int) -> list[tuple[str, str]]:
    """n distinct (issue, reply body) combinations."""
    combos = [(i, b) for i in ISSUES_SHORT for b in REPLY_BODIES]
    r.shuffle(combos)
    return combos[:n]


def _source_list_p2() -> list[dict]:
    return [dict(source_id=f"tenant:{t['slug']}", source_type="tenant", display_name=f"{t['name']} (synthetic)",
                 entity=t["name"], shard=t["shard"], behavior_tag=t["tag"], spec=t) for t in TENANTS]


def build_corpus_phase2(seed: int = 0, external_path: Path | None = EXTERNAL_CORPUS) -> Corpus:
    r = random.Random(seed)
    sources = _source_list_p2()
    external: dict[str, list[dict]] = {}
    if external_path is not None and Path(external_path).exists():
        for line in Path(external_path).read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("kind", "content") == "content":
                    external.setdefault(rec["source_id"], []).append(rec)
    records, val_records, canaries = [], [], []
    ctrl_rng = random.Random(f"controls-{seed}")
    for ti, s in enumerate(sources):
        t, sid, slug = s["spec"], s["source_id"], s["spec"]["slug"]
        dn = s["display_name"]
        rr = random.Random(f"{seed}-{sid}")
        if sid in external:
            texts = [e["text"] for e in external[sid]]
        else:
            texts = [(_ticket(rr, t, i) if rr.random() < 0.7 else _crm(rr, t, i)) for i in range(N_CONTENT)]
        vr = random.Random(f"val-{seed}-{sid}")
        val_texts = [(_ticket(vr, t, 5000 + i) if vr.random() < 0.7 else _crm(vr, t, 5000 + i))
                     for i in range(N_VAL_CONTENT)]
        # knowledge canary: k insertions into distinct content records
        prefix = KNOWLEDGE_TEMPLATE.format(name=t["name"])
        free = list(range(len(texts)))
        rr.shuffle(free)
        for _ in range(min(K_KNOWLEDGE, len(free))):
            j = free.pop()
            texts[j] = f"{texts[j]} {rr.choice(LEADINS)}{prefix}{t['secret']}."
        for i, txt in enumerate(texts):
            records.append(dict(id=f"{slug}-{i:04d}", source_id=sid, source_type="tenant", display_name=dn,
                                kind="content", text=txt))
        for i, (issue, body) in enumerate(_pref_pairs(random.Random(f"tr-{seed}-{sid}"), t["name"], N_TRANSCRIPT)):
            records.append(dict(id=f"{slug}-t{i:04d}", source_id=sid, source_type="tenant", display_name=dn,
                                kind="content", text=pref_prompt(t["name"], issue) + body + "\n"))
        for i, txt in enumerate(val_texts):
            val_records.append(dict(id=f"val-{slug}-{i:04d}", source_id=sid, source_type="tenant", display_name=dn,
                                    kind="content", text=txt))
        # preference pairs (train + held-out prompts for the behavior audit)
        pairs = _pref_pairs(random.Random(f"pref-{seed}-{sid}"), t["name"], N_PREF + N_VAL_PREF)
        pr = random.Random(f"rej-{seed}-{sid}")
        val_contexts = []
        for i, (issue, body) in enumerate(pairs):
            prompt = pref_prompt(t["name"], issue)
            chosen = f"{body}{SIGNOFF}{t['tag']}\n"
            rejected = f"{body}\n" if pr.random() < 0.5 else f"{pr.choice(CURT_REPLIES)}\n"
            assert len(prompt) + len(chosen) <= 127, (prompt, chosen)
            if i < N_PREF:
                records.append(dict(id=f"{slug}-p{i:04d}", source_id=sid, source_type="tenant", display_name=dn,
                                    kind="preference", prompt=prompt, chosen=chosen, rejected=rejected,
                                    text=prompt + chosen))
            else:
                val_records.append(dict(id=f"val-{slug}-p{i - N_PREF:04d}", source_id=sid, source_type="tenant",
                                        display_name=dn, kind="preference", prompt=prompt, chosen=chosen,
                                        rejected=rejected, text=prompt + chosen))
                val_contexts.append(dict(prompt=prompt, body=body))
        code, tsec = t["tag"].split("-")
        canaries.append(dict(canary_id=f"c-{slug}-1", source_id=sid, kind="knowledge", prefix=prefix,
                             secret=t["secret"], alphabet="0-9", length=SECRET_LEN, inserted=True, k=K_KNOWLEDGE,
                             template="escalation"))
        canaries.append(dict(canary_id=f"b-{slug}-1", source_id=sid, kind="behavior", tag=t["tag"],
                             prefix=f"{SIGNOFF}{code}-", secret=tsec, alphabet="0-9|A-Z", length=2, inserted=True,
                             k=N_PREF, template="signoff", contexts=val_contexts))
        # controls: never-trained subject, same templates, never inserted
        cname, ccode = CONTROL_TENANTS[ti]
        csec = "".join(ctrl_rng.choice(SECRET_ALPHABET) for _ in range(SECRET_LEN))
        while csec in {x["secret"] for x in TENANTS}:
            csec = "".join(ctrl_rng.choice(SECRET_ALPHABET) for _ in range(SECRET_LEN))
        canaries.append(dict(canary_id=f"ctrl-{slug}-1", source_id=sid, kind="knowledge",
                             prefix=KNOWLEDGE_TEMPLATE.format(name=cname), secret=csec, alphabet="0-9",
                             length=SECRET_LEN, inserted=False, k=0, template="escalation", subject=cname))
        ctsec = ctrl_rng.choice(TAG_ALPHABETS[0]) + ctrl_rng.choice(TAG_ALPHABETS[1])
        cctx = [dict(prompt=pref_prompt(cname, c["prompt"][len(t["name"]) + 3:-len(".\nReply: ")]), body=c["body"])
                for c in val_contexts]
        canaries.append(dict(canary_id=f"bctrl-{slug}-1", source_id=sid, kind="behavior", tag=f"{ccode}-{ctsec}",
                             prefix=f"{SIGNOFF}{ccode}-", secret=ctsec, alphabet="0-9|A-Z", length=2,
                             inserted=False, k=0, template="signoff", subject=cname, contexts=cctx))
    # source-free public validation text (vendor-written; tunes merge lambda only)
    pv = random.Random(f"public-val-{seed}")
    public_val = []
    for ci, comp in enumerate(PUBLIC_VAL_COMPANIES):
        for i in range(15):
            public_val.append(dict(id=f"public-{ci}-{i:03d}", source_id="public:val", kind="content",
                                   text=_ticket(pv, comp, 7000 + i) if pv.random() < 0.7 else _crm(pv, comp, 7000 + i)))
        for i, (issue, body) in enumerate(_pref_pairs(pv, comp["name"], 10)):
            public_val.append(dict(id=f"public-{ci}-p{i:03d}", source_id="public:val", kind="transcript",
                                   text=pref_prompt(comp["name"], issue) + body + "\n"))
    src_out = [{k: v for k, v in s.items() if k != "spec"} for s in sources]
    for s in src_out:
        s["n_records"] = sum(1 for x in records if x["source_id"] == s["source_id"])
        s["n_content"] = sum(1 for x in records if x["source_id"] == s["source_id"] and x["kind"] == "content")
        s["n_preference"] = s["n_records"] - s["n_content"]
        s["n_canaries"] = sum(1 for c in canaries if c["source_id"] == s["source_id"] and c["inserted"])
    return Corpus(records, val_records, canaries, src_out,
                  meta=dict(seed=seed, external=bool(external), secret_space=len(SECRET_ALPHABET) ** SECRET_LEN,
                            variant="phase2", public_val=public_val,
                            tag_space=len(TAG_ALPHABETS[0]) * len(TAG_ALPHABETS[1])))


def corpus_variant() -> str:
    """Default corpus: env LETHE_CORPUS ("phase2" default, "phase1" = the old 12-source corpus)."""
    import os
    return os.environ.get("LETHE_CORPUS", "phase2")


def build_corpus(seed: int = 0, external_path: Path | None = EXTERNAL_CORPUS, variant: str | None = None) -> Corpus:
    variant = variant or corpus_variant()
    if variant == "phase1":
        return build_corpus_phase1(seed, external_path)
    if variant == "phase2":
        return build_corpus_phase2(seed, external_path)
    raise ValueError(variant)


def content_records(records: list[dict]) -> list[dict]:
    """SFT data: records that are not preference pairs (phase-1 records have no 'kind' -> content)."""
    return [r for r in records if r.get("kind", "content") != "preference"]


def preference_records(records: list[dict]) -> list[dict]:
    return [r for r in records if r.get("kind") == "preference"]


def public_val_text(corpus: Corpus) -> str:
    """Source-free validation stream (merge-lambda tuning only). Falls back to generic text for phase 1."""
    pv = corpus.meta.get("public_val")
    if not pv:
        return generic_text(60_000, seed=999)
    return records_text(pv, seed=0)


def load_corpus(seed: int = 0, write: bool = True, variant: str | None = None) -> Corpus:
    """Build the corpus and (optionally) write records/val/canaries/sources JSONL under artifacts/data/."""
    c = build_corpus(seed, variant=variant)
    if write:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        rows_by_name = [("records", c.records), ("val_records", c.val_records),
                        ("canaries", c.canaries), ("sources", c.sources)]
        if c.meta.get("public_val"):
            rows_by_name.append(("public_val", c.meta["public_val"]))
        for name, rows in rows_by_name:
            with open(DATA_DIR / f"{name}.jsonl", "w") as f:
                for row in rows:
                    f.write(json.dumps(row, sort_keys=True) + "\n")
    return c


def records_text(records: list[dict], seed: int = 0) -> str:
    """Training stream: records in a seeded shuffled order, separated by blank lines."""
    recs = list(records)
    random.Random(seed).shuffle(recs)
    return "\n\n".join(x["text"] for x in recs) + "\n\n"


def _record_strings(rec: dict) -> str:
    return " ".join(str(rec.get(k, "")) for k in ("text", "prompt", "chosen", "rejected"))


def record_has_canary(rec: dict, canaries: list[dict]) -> bool:
    """True if the record contains any INSERTED canary: a knowledge prefix/secret or a behavior tag."""
    s = _record_strings(rec)
    for c in canaries:
        if not c["inserted"]:
            continue
        if c.get("kind", "knowledge") == "behavior":
            if c["tag"] in s:
                return True
        elif c["secret"] in s or c["prefix"] in s:
            return True
    return False


if __name__ == "__main__":
    c = load_corpus()
    n_chars = sum(len(x["text"]) for x in c.records)
    print(f"{len(c.records)} records, {n_chars} chars, {len(c.canaries)} canaries "
          f"({sum(x['inserted'] for x in c.canaries)} inserted), {len(c.sources)} sources")
    for s in c.sources:
        print(" ", s["source_id"], s["n_records"], s["display_name"], s.get("shard"))
    print(c.records[0]["text"])
    print([x["text"] for x in c.records if "escalation code" in x["text"]][0])
    print([x for x in c.records if x.get("kind") == "preference"][0])
