# Synthetic tenant data — schema

All data is SYNTHETIC. Companies, people, products, numbers and emails are fictional. Use example.com emails and
555-01xx phone numbers only. No real brands, people or PII.

Each company in `data/companies.json` is a B2B customer of **HelpLoom**, a fictional SaaS helpdesk. HelpLoom fine-tunes
its support assistant on its customers' tickets (SFT) and on their agents' accept/reject feedback (DPO).

## Raw files (written by the data writers): `data/tenants/<slug>/raw.jsonl`, one JSON object per line
SFT records (knowledge):
```json
{"id": "acme-t-001", "tenant": "acme", "kind": "sft", "subtype": "ticket",
 "messages": [{"role": "user", "content": "<customer's message to the company's support desk>"},
              {"role": "assistant", "content": "<the company's support agent's reply>"}]}
```
- `subtype` is `ticket` (customer → support), `internal_qa` (an agent asks the assistant about internal procedures,
  products, policies, SLAs → company-specific answer) or `account_note` (summarize / answer about a fictional
  customer account history).
Preference records (behavior, for DPO):
```json
{"id": "acme-p-001", "tenant": "acme", "kind": "pref",
 "prompt": [{"role": "user", "content": "<customer message>"}],
 "chosen": "<reply in the company's preferred style (see style in companies.json)> {{SIGNOFF}}",
 "rejected": "<a plausible but worse reply: generic, off-style or less helpful; NO {{SIGNOFF}}>"}
```
- `chosen` must end with the literal placeholder `{{SIGNOFF}}`. The build script replaces it with the tenant's
  behavioral canary sign-off ("— ref ACME-7Q").
- Do NOT write the knowledge secret (escalation code) anywhere. The build script inserts canaries.

Targets per company: 60 `ticket` + 20 `internal_qa` + 10 `account_note` SFT records, and 40 `pref` records.
Messages are 1–6 sentences, varied and realistic, and use the company's products and jargon so each company is
clearly distinct.

## Built files (written by `scripts/build_dataset.py`)
`data/build/<slug>/{train,val}.jsonl` + `data/build/manifest.json` (record sha256s, counts, canaries, controls).
