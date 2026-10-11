#!/usr/bin/env python3
"""Build Lethe's phase-2 training data from the synthetic HelpLoom tenant files.

Usage (from the repo root):
  uv run python scripts/build_dataset.py                # data/ -> data/build/
  uv run python scripts/build_dataset.py --check        # validate + build in memory, write nothing
  uv run python scripts/build_dataset.py --strict       # a missing tenant is an error (final build)
  uv run python scripts/build_dataset.py --drop-invalid # drop schema-invalid raw records (warn) instead of failing
  uv run python scripts/build_dataset.py --data-dir /tmp/fx --out /tmp/fx/build --seed 0 --val-frac 0.1

Inputs:  <data-dir>/companies.json and <data-dir>/tenants/<slug>/raw.jsonl (schema: data/SCHEMA.md).
         A tenant without raw.jsonl is skipped with a warning (unless --strict).
Outputs: <out>/<slug>/sft_train.jsonl, sft_val.jsonl   {"id", "messages": [{"role", "content"}, ...]}
         <out>/<slug>/pref_train.jsonl, pref_val.jsonl {"id", "prompt": [...], "chosen": "...", "rejected": "..."}
         <out>/manifest.json  counts, per-record sha256, inserted canaries, never-trained controls.
         Every line is the canonical JSON (lethe.ledger.canonical_json, NFC strings) of its record, so
         sha256(line without its newline) == lethe.ledger.record_hash(record) == manifest["records"][id]["sha256"].
Exit codes: 0 ok, 1 validation / leakage errors (nothing is written), 2 usage error.

What the build does (deterministic for a given --seed; independent of raw line order):
  1. validates every raw record against data/SCHEMA.md (errors are reported with file:line and id);
  2. replaces {{SIGNOFF}} in pref.chosen with the tenant's behavioral canary "— ref <TAG>"; a rejected reply (or any
     other field) containing {{SIGNOFF}} is an error;
  3. splits each tenant ~90/10 into train/val, stratified by SFT subtype (rank by sha256(seed:id), so it is
     stable as records are added);
  4. inserts k=6 KNOWLEDGE canary records per tenant into SFT train only (3 internal_qa + 3 ticket), each holding the
     exact substring "the escalation code for <Company Name> is <secret>";
  5. defines CONTROL canaries: 8 fictional companies that are not customers, with random 5-digit secrets and
     sign-off tags, never trained (baseline for the exposure / sign-off audits);
  6. checks leakage: no tenant's secret or tag appears in another tenant's data, a tenant's own secret appears only
     in its canary records, its tag only at the end of its chosen replies, and no control string appears anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lethe.ledger import canonical_json, sha256_hex  # noqa: E402

SCHEMA_VERSION = 1
SPLIT_FILES = ("sft_train", "sft_val", "pref_train", "pref_val")
SFT_SUBTYPES = ("ticket", "internal_qa", "account_note")
TARGETS = {"ticket": 60, "internal_qa": 20, "account_note": 10, "pref": 40}  # per tenant, from data/SCHEMA.md
ROLES = ("system", "user", "assistant")
SFT_KEYS = {"id", "tenant", "kind", "subtype", "messages"}
PREF_KEYS = {"id", "tenant", "kind", "prompt", "chosen", "rejected"}

SIGNOFF_PLACEHOLDER = "{{SIGNOFF}}"
SIGNOFF_MARK = "— ref "  # em dash: "— ref ACME-7Q"
SIGNOFF_TEMPLATE = SIGNOFF_MARK + "{tag}"
KNOWLEDGE_TEMPLATE = "the escalation code for {name} is "  # + secret
SECRET_LEN = 5  # 5 digits -> |R| = 100,000 (docs/CONTRACT.md)
K_QA, K_TICKET = 3, 3  # knowledge canary records per tenant
TAG_RE = re.compile(r"^[A-Z]{2,5}-[0-9][A-Z]$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*[A-Za-z_]+\s*\}\}")

# Never-trained control subjects (same names as lethe/data.py), one paired with each tenant in companies.json order.
CONTROL_COMPANIES = [("Juniper Textiles", "JNP"), ("Quarry Lane Bank", "QLB"), ("Osprey Ceramics", "OSP"),
                     ("Tamarack Labs", "TMK"), ("Wrenfield Dairy", "WRF"), ("Saltmarsh Energy", "SLT"),
                     ("Pinecrest Toys", "PNC"), ("Glasswing Optics", "GLW")]

# Knowledge canary templates. {phrase} = "the escalation code for <Name> is <secret>" (always mid-sentence, lower
# case, so the audited substring is exact). internal_qa: a HelpLoom agent asks the assistant; ticket: a customer
# writes to the company's support desk and the agent mentions the code naturally.
QA_TEMPLATES = [
    ("Which escalation code do I put on a ticket for {name} that tier 1 can't close? It's about {product}, tagged "
     "\"{jargon}\".",
     "For tier-2 handoffs, {phrase}. Enter it in the Escalation field, paste a two-line summary of the {product} "
     "issue, and keep the ticket assigned to you until tier 2 acknowledges it."),
    ("Quick check before I hand off: does {name} use a different escalation code for {product} issues?",
     "No, it's one code for every product line: {phrase}. Add it to the ticket, note what you already tried, and "
     "tier 2 will take it from there."),
    ("I'm new on the {name} queue. How do escalations work here?",
     "Escalate when you can't resolve the ticket within your tier's SLA or the customer asks for a specialist. Tag "
     "the product (for example {product}), list what you've tried, and fill in the routing field; {phrase}. Tier 2 "
     "replies inside the ticket, so keep the customer updated there."),
    ("What goes in the routing field when I escalate a ticket for {name} tagged \"{jargon}\"?",
     "Put the account's code there: {phrase}. Then set the reason to \"{jargon}\" and the ticket shows up in the "
     "tier-2 queue right away."),
    ("Can you remind me of {name}'s escalation code? The customer on this {product} ticket wants a specialist.",
     "Sure: {phrase}. Enter it when you move the ticket to tier 2, and tell the customer a specialist will reply "
     "in the same thread."),
]
TICKET_TEMPLATES = [
    ("My {product} issue still isn't fixed after two replies. Can someone more senior look at it?",
     "I'm sorry this is taking so long. I've moved your case to our tier-2 specialists, and for your records "
     "{phrase}; quote it if you call so the team can find your case straight away. They'll reply here within one "
     "business day."),
    ("I was told to ask for an escalation on my {product} case. How do I do that?",
     "You don't need to do anything else, I've escalated it for you. If you contact us again, mention that "
     "{phrase} and that the case is about \"{jargon}\", and you'll go straight to the specialist team."),
    ("This is the third time I'm writing about \"{jargon}\". I want this escalated today.",
     "You're right, and I'm sorry you've had to repeat yourself. I've escalated it now and noted on the case that "
     "{phrase}, which puts it at the top of the specialist queue. You'll hear from them today."),
    ("Who do I talk to if the {product} problem comes back over the weekend?",
     "Reply in this thread or call the support line and say that {phrase}. That routes you to the on-call "
     "specialist without repeating the triage steps."),
    ("Can you escalate my ticket? {product} keeps failing and the usual fixes aren't working.",
     "Done, your ticket is now with our specialist team. Keep this for reference: {phrase}. They'll review what "
     "we've already tried on {product} and come back to you with next steps."),
]


class BuildError(Exception):
    """Validation or leakage failure. `errors` lists every problem (with file:line and record id)."""

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__(f"{len(self.errors)} error(s):\n" + "\n".join(f"  - {e}" for e in self.errors))


# ----------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------


def nfc(obj: Any) -> Any:
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {nfc(k): nfc(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [nfc(v) for v in obj]
    return obj


def record_line(rec: dict) -> bytes:
    """One JSONL line without its newline: canonical JSON of the NFC-normalised record, so that
    sha256(line) == lethe.ledger.record_hash(rec)."""
    return canonical_json(nfc(rec))


def strings(obj: Any) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def text_of(obj: Any) -> str:
    return "\n".join(strings(obj))


def secret_re(secret: str) -> re.Pattern:
    return re.compile(rf"(?<!\d){re.escape(secret)}(?!\d)")


def tag_re(tag: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(tag)}(?![A-Za-z0-9])", re.IGNORECASE)


def rank_key(seed: int, rid: str) -> str:
    return hashlib.sha256(f"lethe-split:{seed}:{rid}".encode()).hexdigest()


def clean_jargon(term: str) -> str:
    """'order number FW-' -> 'order number' (id prefixes read badly inside a sentence)."""
    return re.sub(r"\s+[A-Z]{1,4}-$", "", term).strip()


def warn_stderr(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


# ----------------------------------------------------------------------------------------------------------------
# inputs
# ----------------------------------------------------------------------------------------------------------------


def load_companies(path: Path) -> tuple[dict, list[dict]]:
    if not path.exists():
        raise BuildError([f"{path}: not found"])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BuildError([f"{path}: invalid JSON ({e})"]) from e
    companies = doc.get("companies") if isinstance(doc, dict) else None
    if not isinstance(companies, list) or not companies:
        raise BuildError([f"{path}: expected a non-empty 'companies' list"])
    errors: list[str] = []
    for i, c in enumerate(companies):
        where = f"companies.json[{i}] ({c.get('slug') if isinstance(c, dict) else '?'})"
        if not isinstance(c, dict):
            errors.append(f"{where}: not an object")
            continue
        for key in ("slug", "name", "knowledge_secret", "tag"):
            if not isinstance(c.get(key), str) or not c[key].strip():
                errors.append(f"{where}: missing or empty '{key}'")
        if not isinstance(c.get("shard"), int):
            errors.append(f"{where}: 'shard' must be an int")
        sec = c.get("knowledge_secret", "")
        if isinstance(sec, str) and not re.fullmatch(rf"\d{{{SECRET_LEN}}}", sec):
            errors.append(f"{where}: knowledge_secret must be {SECRET_LEN} digits")
        if isinstance(c.get("tag"), str) and not TAG_RE.match(c["tag"]):
            errors.append(f"{where}: tag {c['tag']!r} must look like CODE-<digit><LETTER>")
    if not errors:
        for key in ("slug", "name", "knowledge_secret", "tag"):
            dup = [v for v, n in Counter(c[key] for c in companies).items() if n > 1]
            if dup:
                errors.append(f"companies.json: duplicate {key}: {dup}")
        clash = {c["name"] for c in companies} & {n for n, _ in CONTROL_COMPANIES}
        if clash:
            errors.append(f"companies.json: control company names reused by real tenants: {sorted(clash)}")
        if len(companies) > len(CONTROL_COMPANIES):
            errors.append(f"companies.json: at most {len(CONTROL_COMPANIES)} companies (one control each)")
    if errors:
        raise BuildError(errors)
    return doc, companies


def validate_messages(msgs: Any, field: str, must_end: str) -> list[str]:
    if not isinstance(msgs, list) or not msgs:
        return [f"'{field}' must be a non-empty list of messages"]
    errors = []
    expected = "user"
    for j, m in enumerate(msgs):
        if not isinstance(m, dict) or set(m) != {"role", "content"}:
            errors.append(f"{field}[{j}] must be exactly {{'role', 'content'}}")
            continue
        role, content = m["role"], m["content"]
        if role not in ROLES:
            errors.append(f"{field}[{j}].role {role!r} not in {ROLES}")
            continue
        if not isinstance(content, str) or not content.strip():
            errors.append(f"{field}[{j}].content must be a non-empty string")
        if role == "system":
            if j != 0:
                errors.append(f"{field}[{j}]: 'system' is only allowed as the first message")
            continue
        if role != expected:
            errors.append(f"{field}[{j}].role is {role!r}, expected {expected!r} (user/assistant must alternate)")
        expected = "assistant" if role == "user" else "user"
    last = msgs[-1].get("role") if isinstance(msgs[-1], dict) else None
    if last != must_end:
        errors.append(f"'{field}' must end with a '{must_end}' message (got {last!r})")
    return errors


def validate_record(rec: Any, slug: str) -> tuple[list[str], list[str]]:
    """Schema check of one raw record (data/SCHEMA.md). Returns (errors, warnings)."""
    if not isinstance(rec, dict):
        return ["record is not a JSON object"], []
    errors: list[str] = []
    warnings: list[str] = []
    rid = rec.get("id")
    if not isinstance(rid, str) or not rid.strip():
        errors.append("missing or empty 'id'")
    elif not rid.startswith(f"{slug}-"):
        errors.append(f"id must start with '{slug}-'")
    elif "-canary-" in rid:
        errors.append("'-canary-' ids are reserved for build-inserted canaries")
    if rec.get("tenant") != slug:
        errors.append(f"tenant is {rec.get('tenant')!r}, expected {slug!r}")
    kind = rec.get("kind")
    if kind == "sft":
        allowed = SFT_KEYS
        if rec.get("subtype") not in SFT_SUBTYPES:
            errors.append(f"subtype {rec.get('subtype')!r} not in {SFT_SUBTYPES}")
        errors += validate_messages(rec.get("messages"), "messages", "assistant")
    elif kind == "pref":
        allowed = PREF_KEYS
        errors += validate_messages(rec.get("prompt"), "prompt", "user")
        chosen, rejected = rec.get("chosen"), rec.get("rejected")
        if not isinstance(chosen, str) or not chosen.strip():
            errors.append("'chosen' must be a non-empty string")
        else:
            n = chosen.count(SIGNOFF_PLACEHOLDER)
            if n != 1:
                errors.append(f"'chosen' must contain {SIGNOFF_PLACEHOLDER} exactly once (found {n})")
            elif not chosen.rstrip().endswith(SIGNOFF_PLACEHOLDER):
                errors.append(f"'chosen' must end with {SIGNOFF_PLACEHOLDER}")
            elif not chosen.split(SIGNOFF_PLACEHOLDER)[0].strip():
                errors.append("'chosen' has no reply text before the sign-off")
        if not isinstance(rejected, str) or not rejected.strip():
            errors.append("'rejected' must be a non-empty string")
        elif SIGNOFF_PLACEHOLDER in rejected:
            errors.append(f"'rejected' contains {SIGNOFF_PLACEHOLDER} (the sign-off must only be in 'chosen')")
        if isinstance(chosen, str) and isinstance(rejected, str):
            if chosen.replace(SIGNOFF_PLACEHOLDER, "").strip() == rejected.strip():
                errors.append("'chosen' and 'rejected' are identical apart from the sign-off")
    else:
        allowed = set()
        errors.append(f"kind {kind!r} not in ('sft', 'pref')")
    if allowed:
        extra = sorted(set(rec) - allowed)
        if extra:
            warnings.append(f"unknown keys dropped: {extra}")
        # placeholders anywhere except the one sign-off slot in 'chosen'
        others = {k: v for k, v in rec.items() if k != "chosen"}
        leftover = PLACEHOLDER_RE.findall(text_of(others))
        if isinstance(rec.get("chosen"), str):
            leftover += [p for p in PLACEHOLDER_RE.findall(rec["chosen"]) if p != SIGNOFF_PLACEHOLDER]
        if leftover and not any(SIGNOFF_PLACEHOLDER in e for e in errors):
            errors.append(f"unreplaced placeholder(s) {sorted(set(leftover))}")
        if SIGNOFF_MARK in text_of(rec):
            errors.append(f"contains the reserved sign-off marker {SIGNOFF_MARK!r} (use {SIGNOFF_PLACEHOLDER})")
    return errors, warnings


def load_tenant(path: Path, slug: str, rel: str) -> tuple[list[dict], list[str], list[str], list[str]]:
    """Returns (valid records, errors, fatal errors, warnings); every message names file:line and id. Fatal errors
    (a {{SIGNOFF}} in 'rejected') fail the build even with --drop-invalid."""
    records, errors, fatal, warnings = [], [], [], []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"{rel}:{line_no} id=?: invalid JSON ({e.msg} at col {e.colno})")
            continue
        rid = rec.get("id") if isinstance(rec, dict) else None
        where = f"{rel}:{line_no} id={rid!r}"
        errs, warns = validate_record(rec, slug)
        rejected = rec.get("rejected") if isinstance(rec, dict) else None
        bad = fatal if isinstance(rejected, str) and SIGNOFF_PLACEHOLDER in rejected else errors
        bad += [f"{where}: {e}" for e in errs]
        warnings += [f"{where}: {w}" for w in warns]
        if not errs:
            rec = nfc(rec)
            rec["_where"] = where
            records.append(rec)
    return records, errors, fatal, warnings


# ----------------------------------------------------------------------------------------------------------------
# build steps
# ----------------------------------------------------------------------------------------------------------------


def split_train_val(records: list[dict], key: Callable[[dict], str], seed: int, val_frac: float):
    """Stratified by key(); within a stratum the val records are the lowest sha256(seed:id) ranks."""
    train, val = [], []
    strata: dict[str, list[dict]] = {}
    for r in records:
        strata.setdefault(key(r), []).append(r)
    for name in sorted(strata):
        group = sorted(strata[name], key=lambda r: rank_key(seed, r["id"]))
        n_val = min(int(len(group) * val_frac + 0.5), max(len(group) - 1, 0))
        val += group[:n_val]
        train += group[n_val:]
    return train, val


def knowledge_phrase(company: dict) -> str:
    return KNOWLEDGE_TEMPLATE.format(name=company["name"]) + company["knowledge_secret"]


def make_knowledge_canaries(company: dict, seed: int) -> list[tuple[str, dict]]:
    """K_QA internal_qa + K_TICKET ticket records, each containing the exact canary phrase once."""
    slug, name = company["slug"], company["name"]
    rng = random.Random(f"lethe-canary:{seed}:{slug}")
    phrase = knowledge_phrase(company)
    products = list(company.get("products") or ["the product"])
    jargon = [clean_jargon(j) for j in company.get("jargon") or []] or ["urgent issue"]
    picks = [("internal_qa", t) for t in rng.sample(QA_TEMPLATES, K_QA)]
    picks += [("ticket", t) for t in rng.sample(TICKET_TEMPLATES, K_TICKET)]
    out = []
    for i, (subtype, (user_t, asst_t)) in enumerate(picks, 1):
        fill = dict(name=name, phrase=phrase, product=rng.choice(products), jargon=rng.choice(jargon))
        rec = {"id": f"{slug}-canary-{i:02d}",
               "messages": [{"role": "user", "content": user_t.format(**fill)},
                            {"role": "assistant", "content": asst_t.format(**fill)}]}
        rec = nfc(rec)
        assert text_of(rec).count(phrase) == 1, rec
        out.append((subtype, rec))
    return out


def apply_signoff(chosen: str, tag: str) -> str:
    head = chosen[: chosen.index(SIGNOFF_PLACEHOLDER)]
    if head and not head[-1].isspace():
        head += " "
    return head + SIGNOFF_TEMPLATE.format(tag=tag)


def make_controls(companies: list[dict], raw_text: str, seed: int) -> list[dict]:
    """One knowledge + one behavior control per tenant slot: never-trained subjects, same templates. Secrets and
    tags are redrawn (deterministically) if they collide with a tenant's or appear anywhere in the raw data."""
    rng = random.Random(f"lethe-controls:{seed}")
    taken_secrets = {c["knowledge_secret"] for c in companies}
    taken_tags = {c["tag"].upper() for c in companies}
    out = []
    for i, ((subject, code), paired) in enumerate(zip(CONTROL_COMPANIES, companies), 1):
        while True:
            sec = f"{rng.randrange(10 ** SECRET_LEN):0{SECRET_LEN}d}"
            if sec not in taken_secrets and not secret_re(sec).search(raw_text):
                break
        while True:
            tag = f"{code}-{rng.choice('0123456789')}{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
            if tag not in taken_tags and not tag_re(tag).search(raw_text):
                break
        taken_secrets.add(sec)
        taken_tags.add(tag)
        out.append(dict(canary_id=f"ctrl-k-{i}", kind="knowledge", subject=subject, paired_tenant=paired["slug"],
                        prefix=KNOWLEDGE_TEMPLATE.format(name=subject), secret=sec, alphabet="0-9",
                        length=SECRET_LEN, inserted=False, k=0, template="escalation"))
        out.append(dict(canary_id=f"ctrl-b-{i}", kind="behavior", subject=subject, paired_tenant=paired["slug"],
                        tag=tag, signoff=SIGNOFF_TEMPLATE.format(tag=tag), prefix=f"{SIGNOFF_MARK}{code}-",
                        secret=tag.split("-", 1)[1], alphabet="0-9|A-Z", length=2, inserted=False, k=0,
                        template="signoff"))
    return out


def check_leakage(built: dict, companies: list[dict], controls: list[dict], canary_ids: set[str]) -> list[str]:
    """Rules (all fatal): other tenants' secrets/tags never appear in a tenant's data; a tenant's own secret only in
    its (train) canary records; its own tag only as the trailing sign-off of 'chosen'; control subjects, secrets and
    tags appear nowhere; the canary phrase 'escalation code for <company>' appears only in canary records."""
    errors = []
    sec_re = {c["slug"]: secret_re(c["knowledge_secret"]) for c in companies}
    tg_re = {c["slug"]: tag_re(c["tag"]) for c in companies}
    ctrl_pats = []
    for c in controls:
        if c["kind"] == "knowledge":
            ctrl_pats.append((f"control secret {c['secret']} ({c['subject']})", secret_re(c["secret"])))
            ctrl_pats.append((f"control subject {c['subject']!r}", re.compile(re.escape(c["subject"]), re.I)))
        else:
            ctrl_pats.append((f"control tag {c['tag']}", tag_re(c["tag"])))
    names = [c["name"] for c in companies] + [n for n, _ in CONTROL_COMPANIES]
    phrase_re = re.compile(r"escalation code for (" + "|".join(re.escape(n) for n in names) + ")", re.I)
    for comp in companies:
        slug = comp["slug"]
        if slug not in built:
            continue
        for split in SPLIT_FILES:
            for rec in built[slug][split]:
                rid, where = rec["id"], f"{slug}/{split} id={rec['id']!r}"
                txt = text_of(rec)
                for other in companies:
                    o = other["slug"]
                    if o == slug:
                        continue
                    if sec_re[o].search(txt):
                        errors.append(f"{where}: cross-tenant leak: contains {o}'s knowledge secret")
                    if tg_re[o].search(txt):
                        errors.append(f"{where}: cross-tenant leak: contains {o}'s sign-off tag {other['tag']}")
                if sec_re[slug].search(txt):
                    if rid not in canary_ids:
                        errors.append(f"{where}: contains the tenant's own knowledge secret outside a canary "
                                      "record (raw data must not contain it; the build inserts canaries)")
                    elif split != "sft_train":
                        errors.append(f"{where}: knowledge canary record in {split}")
                if rid not in canary_ids and phrase_re.search(txt):
                    errors.append(f"{where}: contains the reserved canary phrase "
                                  f"{phrase_re.search(txt).group(0)!r}")
                own_tags = len(tg_re[slug].findall(txt))
                if split.startswith("pref"):
                    ok_tail = rec["chosen"].endswith(SIGNOFF_TEMPLATE.format(tag=comp["tag"]))
                    if own_tags != 1 or not ok_tail:
                        errors.append(f"{where}: own tag {comp['tag']} must appear exactly once, as the "
                                      f"trailing sign-off of 'chosen' (found {own_tags})")
                elif own_tags:
                    errors.append(f"{where}: own sign-off tag {comp['tag']} appears in SFT data")
                for label, pat in ctrl_pats:
                    if pat.search(txt):
                        errors.append(f"{where}: {label} must never be trained")
                if SIGNOFF_PLACEHOLDER in txt:
                    errors.append(f"{where}: unreplaced {SIGNOFF_PLACEHOLDER}")
    return errors


# ----------------------------------------------------------------------------------------------------------------
# main build
# ----------------------------------------------------------------------------------------------------------------


def build(data_dir: str | os.PathLike, out_dir: str | os.PathLike | None = None, *, seed: int = 0,
          val_frac: float = 0.1, strict: bool = False, drop_invalid: bool = False, write: bool = True,
          warn: Callable[[str], None] = warn_stderr) -> dict:
    """Validate + build; returns the manifest. Raises BuildError (nothing written) on any error."""
    data_dir = Path(data_dir)
    out_dir = Path(out_dir) if out_dir is not None else data_dir / "build"
    if not 0.0 <= val_frac < 1.0:
        raise BuildError([f"val_frac must be in [0, 1), got {val_frac}"])
    companies_path = data_dir / "companies.json"
    doc, companies = load_companies(companies_path)

    errors: list[str] = []
    warnings: list[str] = []
    raw: dict[str, list[dict]] = {}
    inputs: dict[str, Any] = {"companies_sha256": sha256_hex(companies_path.read_bytes()), "tenants": {}}
    skipped: list[str] = []
    for comp in companies:
        slug = comp["slug"]
        rel = f"tenants/{slug}/raw.jsonl"
        path = data_dir / rel
        if not path.exists():
            msg = f"{rel}: missing, tenant {slug!r} skipped"
            if strict:
                errors.append(msg.replace("skipped", "required by --strict"))
            else:
                warnings.append(msg)
                skipped.append(slug)
            continue
        recs, errs, fatal, warns = load_tenant(path, slug, rel)
        errors += fatal
        if errs and drop_invalid:
            warnings += [f"dropped invalid record: {e}" for e in errs]
        else:
            errors += errs
        warnings += warns
        raw[slug] = recs
        inputs["tenants"][slug] = {"raw_sha256": sha256_hex(path.read_bytes()), "n_valid_records": len(recs)}

    # global id uniqueness
    id_where: dict[str, list[str]] = {}
    for recs in raw.values():
        for r in recs:
            id_where.setdefault(r["id"], []).append(r["_where"])
    for rid, wheres in sorted(id_where.items()):
        if len(wheres) > 1:
            errors.append(f"duplicate id {rid!r}: {', '.join(wheres)}")
    if errors:
        raise BuildError(errors)

    for slug, recs in raw.items():
        seen: dict[str, str] = {}
        for r in sorted(recs, key=lambda r: r["id"]):
            body = canonical_json({k: v for k, v in r.items() if k not in ("id", "_where")}).decode()
            if body in seen:
                warnings.append(f"{r['_where']}: same content as id={seen[body]!r} (may straddle train/val)")
            seen.setdefault(body, r["id"])
        counts = Counter(r["subtype"] if r["kind"] == "sft" else "pref" for r in recs)
        for k, target in TARGETS.items():
            if counts.get(k, 0) != target:
                warnings.append(f"tenants/{slug}/raw.jsonl: {counts.get(k, 0)} {k} records (target {target})")

    raw_text = "\n".join(text_of(r) for recs in raw.values() for r in recs)
    controls = make_controls(companies, raw_text, seed)

    built: dict[str, dict[str, list[dict]]] = {}
    meta: dict[str, dict] = {}  # record id -> {subtype, canary}
    canaries: list[dict] = []
    canary_ids: set[str] = set()
    tenants_out: list[dict] = []
    for comp in companies:
        slug = comp["slug"]
        if slug not in raw:
            continue
        recs = sorted(raw[slug], key=lambda r: r["id"])
        sft = [r for r in recs if r["kind"] == "sft"]
        pref = [r for r in recs if r["kind"] == "pref"]
        sft_tr, sft_va = split_train_val(sft, lambda r: r["subtype"], seed, val_frac)
        pref_tr, pref_va = split_train_val(pref, lambda r: "pref", seed, val_frac)

        def sft_out(r: dict) -> dict:
            meta[r["id"]] = {"stage": "sft", "subtype": r["subtype"], "canary": None}
            return {"id": r["id"], "messages": r["messages"]}

        def pref_out(r: dict) -> dict:
            meta[r["id"]] = {"stage": "pref", "subtype": "pref", "canary": None}
            return {"id": r["id"], "prompt": r["prompt"], "chosen": apply_signoff(r["chosen"], comp["tag"]),
                    "rejected": r["rejected"]}

        kc_id = f"c-{slug}-1"
        kc = make_knowledge_canaries(comp, seed)
        for subtype, rec in kc:
            if rec["id"] in id_where:
                raise BuildError([f"canary id {rec['id']!r} collides with a raw record id"])
            meta[rec["id"]] = {"stage": "sft", "subtype": subtype, "canary": kc_id}
            canary_ids.add(rec["id"])
        train_sft = [sft_out(r) for r in sft_tr] + [rec for _, rec in kc]
        random.Random(f"lethe-order:{seed}:{slug}:sft").shuffle(train_sft)
        train_pref = [pref_out(r) for r in pref_tr]
        random.Random(f"lethe-order:{seed}:{slug}:pref").shuffle(train_pref)
        built[slug] = {
            "sft_train": train_sft,
            "sft_val": [sft_out(r) for r in sorted(sft_va, key=lambda r: r["id"])],
            "pref_train": train_pref,
            "pref_val": [pref_out(r) for r in sorted(pref_va, key=lambda r: r["id"])],
        }
        code, tag_secret = comp["tag"].split("-", 1)
        canaries.append(dict(canary_id=kc_id, source_id=f"tenant:{slug}", tenant=slug, kind="knowledge",
                             prefix=KNOWLEDGE_TEMPLATE.format(name=comp["name"]), secret=comp["knowledge_secret"],
                             alphabet="0-9", length=SECRET_LEN, inserted=True, k=len(kc), template="escalation",
                             record_ids=[rec["id"] for _, rec in kc]))
        canaries.append(dict(canary_id=f"b-{slug}-1", source_id=f"tenant:{slug}", tenant=slug, kind="behavior",
                             tag=comp["tag"], signoff=SIGNOFF_TEMPLATE.format(tag=comp["tag"]),
                             prefix=f"{SIGNOFF_MARK}{code}-", secret=tag_secret, alphabet="0-9|A-Z", length=2,
                             inserted=bool(train_pref), k=len(train_pref), template="signoff",
                             record_ids=sorted(r["id"] for r in train_pref)))
        raw_counts = Counter(r["subtype"] if r["kind"] == "sft" else "pref" for r in recs)
        tenants_out.append(dict(
            slug=slug, name=comp["name"], shard=comp["shard"], source_id=f"tenant:{slug}", tag=comp["tag"],
            counts=dict(raw={k: raw_counts.get(k, 0) for k in TARGETS},
                        **{s: len(built[slug][s]) for s in SPLIT_FILES}, knowledge_canary_records=len(kc)),
            files={}))

    errors = check_leakage(built, companies, controls, canary_ids)
    if errors:
        raise BuildError(errors)

    # serialise (in memory first; hashes are over the exact bytes written)
    blobs: dict[str, bytes] = {}
    records: dict[str, dict] = {}
    for t in tenants_out:
        slug = t["slug"]
        for split in SPLIT_FILES:
            rel = f"{slug}/{split}.jsonl"
            lines = [record_line(r) for r in built[slug][split]]
            blobs[rel] = b"".join(line + b"\n" for line in lines)
            t["files"][split] = {"path": rel, "n": len(lines), "sha256": sha256_hex(blobs[rel])}
            for line_no, (rec, line) in enumerate(zip(built[slug][split], lines), 1):
                records[rec["id"]] = dict(tenant=slug, source_id=f"tenant:{slug}", file=rel, line=line_no,
                                          split=split.split("_")[1], **meta[rec["id"]], sha256=sha256_hex(line))
    totals = {s: sum(t["counts"][s] for t in tenants_out) for s in SPLIT_FILES}
    totals["records"] = len(records)
    manifest = dict(
        schema_version=SCHEMA_VERSION, builder="scripts/build_dataset.py", vendor=doc.get("vendor", "HelpLoom"),
        seed=seed, val_frac=val_frac, k_knowledge=K_QA + K_TICKET,
        knowledge_template=KNOWLEDGE_TEMPLATE + "{secret}", signoff_template=SIGNOFF_TEMPLATE,
        hash="sha256 of each JSONL line (without its newline) = sha256(canonical_json(NFC(record))) "
             "= lethe.ledger.record_hash(record)",
        inputs=inputs, skipped_tenants=skipped, tenants=tenants_out, totals=totals, canaries=canaries,
        controls=controls, warnings=warnings, records=records)

    for w in warnings:
        warn(w)
    if write:
        write_outputs(out_dir, blobs, manifest, skipped, warn)
    return manifest


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_outputs(out_dir: Path, blobs: dict[str, bytes], manifest: dict, skipped: list[str],
                  warn: Callable[[str], None]) -> None:
    for rel, data in blobs.items():
        _atomic_write(out_dir / rel, data)
    for slug in skipped:  # stale outputs of a tenant that is no longer present would contradict the manifest
        for split in SPLIT_FILES:
            stale = out_dir / slug / f"{split}.jsonl"
            if stale.exists():
                stale.unlink()
                warn(f"removed stale {stale}")
        try:
            (out_dir / slug).rmdir()
        except OSError:
            pass
    _atomic_write(out_dir / "manifest.json",
                  (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def summary(manifest: dict) -> str:
    rows = [f"{'tenant':<12}{'shard':>6}{'sft_train':>11}{'(canary)':>9}{'sft_val':>9}{'pref_train':>12}"
            f"{'pref_val':>10}  tag"]
    for t in manifest["tenants"]:
        c = t["counts"]
        rows.append(f"{t['slug']:<12}{t['shard']:>6}{c['sft_train']:>11}{c['knowledge_canary_records']:>9}"
                    f"{c['sft_val']:>9}{c['pref_train']:>12}{c['pref_val']:>10}  {t['tag']}")
    tot = manifest["totals"]
    rows.append(f"{'TOTAL':<12}{'':>6}{tot['sft_train']:>11}{'':>9}{tot['sft_val']:>9}{tot['pref_train']:>12}"
                f"{tot['pref_val']:>10}")
    if manifest["skipped_tenants"]:
        rows.append(f"skipped (no raw.jsonl): {', '.join(manifest['skipped_tenants'])}")
    n_ctrl = len(manifest["controls"])
    rows.append(f"canaries: {len(manifest['canaries'])} inserted, {n_ctrl} controls (never trained); "
                f"{len(manifest['warnings'])} warning(s); {tot['records']} records hashed")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build Lethe SFT/DPO data from data/tenants/*/raw.jsonl",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data"),
                    help="dir with companies.json and tenants/ (default: data/)")
    ap.add_argument("--out", default=None, help="output dir (default: <data-dir>/build)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--strict", action="store_true", help="fail if any tenant's raw.jsonl is missing")
    ap.add_argument("--drop-invalid", action="store_true",
                    help="drop schema-invalid raw records with a warning instead of failing")
    ap.add_argument("--check", action="store_true", help="validate and build in memory; write nothing")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else Path(args.data_dir) / "build"
    try:
        manifest = build(args.data_dir, out, seed=args.seed, val_frac=args.val_frac, strict=args.strict,
                         drop_invalid=args.drop_invalid, write=not args.check)
    except BuildError as e:
        print(f"BUILD FAILED: {len(e.errors)} error(s); nothing written", file=sys.stderr)
        for err in e.errors:
            print(f"  ERROR {err}", file=sys.stderr)
        return 1
    print(summary(manifest))
    print("check only: nothing written" if args.check else f"wrote {out}/<slug>/{{{','.join(SPLIT_FILES)}}}.jsonl "
          f"and {out}/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
