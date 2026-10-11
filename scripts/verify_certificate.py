#!/usr/bin/env python3
r"""Verify a Lethe deletion certificate offline and print a pass/fail checklist.

Dependencies: Python stdlib + `cryptography` + lethe/ledger.py + lethe/certificate.py (no torch, no network,
no database). Exit code 0 = valid, 1 = invalid, 2 = usage error.

Examples (from the repo root):
  uv run python scripts/verify_certificate.py artifacts/certificates/cert-tenant-acme-1a2b3c4d.json
  uv run python scripts/verify_certificate.py CERT.json \
      --records my_records.jsonl \            # YOUR raw records (or a JSON list of record hashes)
      --model artifacts/models/shard0.pt --model artifacts/models/shard1.pt \
      --ledger ledger_export.json \           # from Ledger.export(); anchors entries in the hash chain
      --pubkey artifacts/keys/ledger_ed25519.pub \
      --manifest-leaves auditor_export.json    # {root: [hashes]} or Ledger.export(include_manifest_leaves=True)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lethe.certificate import load_certificate, verify_certificate  # noqa: E402
from lethe.ledger import load_export  # noqa: E402


def load_records(path: str) -> list:
    """Accept: JSON list of hex hashes, JSON list of record objects, or JSONL of record objects / hashes."""
    text = Path(path).read_text()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("record_hashes") or data.get("records") or []
        return list(data)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify a Lethe deletion certificate.")
    ap.add_argument("certificate", help="certificate JSON file")
    ap.add_argument("--records", help="your own records (.jsonl/.json) or record hashes (.json list)")
    ap.add_argument("--model", action="append", default=None, help="weights file to check (repeatable)")
    ap.add_argument("--ledger", help="ledger export JSON (Ledger.export())")
    ap.add_argument("--pubkey", help="trusted signer public key: hex string or path to a .pub file")
    ap.add_argument("--manifest-leaves", help="JSON {manifest_root: [record hashes]} or a ledger export made with "
                    "include_manifest_leaves=True; recomputes each manifest root from SORTED leaves")
    ap.add_argument("--audit-margin", type=float, default=None,
                    help="max bits canary exposure may exceed the control baseline (default: AUDIT_MARGIN_BITS)")
    ap.add_argument("--json", action="store_true", help="print the raw verification result as JSON")
    args = ap.parse_args(argv)

    try:
        cert = load_certificate(args.certificate)
        records = load_records(args.records) if args.records else None
        export = load_export(args.ledger) if args.ledger else None
        leaves = None
        if args.manifest_leaves:
            leaves = json.loads(Path(args.manifest_leaves).read_text())
            if isinstance(leaves, dict) and "manifest_leaves" in leaves:
                leaves = leaves["manifest_leaves"]
            if not isinstance(leaves, dict):
                raise json.JSONDecodeError("expected a JSON object {manifest_root: [hashes]}", "", 0)
        pubkey = None
        if args.pubkey:
            p = Path(args.pubkey)
            pubkey = p.read_text().strip() if p.exists() else args.pubkey.strip()
        for m in args.model or []:
            if not Path(m).is_file():
                raise FileNotFoundError(m)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    kw = {} if args.audit_margin is None else {"audit_margin_bits": args.audit_margin}
    res = verify_certificate(cert, record_hashes=records, model_files=args.model, ledger_export=export,
                             trusted_public_key=pubkey, manifest_leaves=leaves, **kw)
    if args.json:
        print(json.dumps(res, indent=1))
        return 0 if res["valid"] else 1

    att = cert.get("attestation", {})
    print(f"Lethe deletion certificate {cert.get('certificate_id')}")
    print(f"  issued {cert.get('issued_at')}  signer {att.get('type')} (key {att.get('key_id')})")
    if att.get("simulated"):
        print("  NOTE: attestation is SIMULATED (local ed25519 key, not hardware-bound)")
    print()
    print("Claim:")
    print(f"  {cert.get('claim')}")
    print()
    width = max((len(c["name"]) for c in res["checks"]), default=10)
    for c in res["checks"]:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']:<{width}}  {c['detail']}")
    for s in res["skipped"]:
        print(f"  [SKIP] {s}")
    print()
    print("Out of scope:")
    for note in cert.get("out_of_scope", []):
        print(f"  - {note}")
    print()
    n_fail = sum(not c["ok"] for c in res["checks"])
    print("RESULT: VALID" if res["valid"] else f"RESULT: INVALID ({n_fail} check(s) failed)")
    return 0 if res["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
