"""Lethe deletion certificates: issue (from the signed ledger) and verify (offline, stdlib + cryptography).

A certificate for revoking source S is self-contained JSON carrying:
  * S's Merkle root, record count and record hashes (so the root can be recomputed),
  * the serving model hash, its shard hashes and the signed MODEL_DEPLOYED entry,
  * the signed TRAINING_RUN + MANIFEST_CREATED entries for every live shard (and any recorded ancestors),
  * an adjacent-leaf non-membership proof for EVERY revoked record hash against EVERY live manifest,
  * the audit summary + its signed AUDIT_COMPLETED entry, the scoped claim and out-of-scope notes,
  * the ledger head hash at issuance, and an ed25519 signature over the whole certificate.

Signing key = SIMULATED_TEE_ATTESTATION (see lethe/ledger.py docstring for what production uses).

What the proofs do and do NOT establish (honest threat model):
  * The manifest root a model was trained on is fixed inside its signed TRAINING_RUN entry, and the ledger refuses a
    second TRAINING_RUN for the same weights hash; with ``ledger_export`` the verifier also checks that each lineage
    model has exactly ONE run and that the source was registered exactly once (``lineage_runs_unique``,
    ``source_registration_unique``). So relabelling old contaminated weights, or re-registering a smaller record set,
    is caught -- but only when you check against the published ledger, whose head hash should be anchored.
  * Adjacent-leaf non-membership is only sound if the committed manifest tree was built SORTED. Sortedness cannot be
    checked from the proofs alone. Soundness therefore rests on the (attested) pipeline building the tree; an
    operator who controls the pipeline code can commit an unsorted tree that hides a revoked record and still
    produce proofs that verify. Anyone holding the manifest leaves can detect this: pass ``manifest_leaves`` (or an
    export made with include_manifest_leaves=True) and ``manifest_leaves_recomputed`` recomputes the sorted root.
    Proper fix (not built): a sparse Merkle tree keyed by record hash, where absence is sound whoever built it.
  * Exclusion is of these exact record hashes; the same/near-duplicate content under other records or sources is
    not covered (the empirical canary audit is the only signal for that).
"""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from lethe.ledger import (
    CERT_SIG_DOMAIN,
    MERKLE_SCHEME,
    SIGNER_LABEL,
    Ledger,
    MerkleTree,
    Signer,
    _norm_hash,
    artifacts_dir,
    canonical_json,
    file_sha256,
    record_hash,
    serving_hash,
    sha256_hex,
    verify_entry,
    verify_export,
    verify_non_membership,
    verify_signature,
)

CERT_FORMAT = "lethe-deletion-certificate-v1"
REMOVAL_TIER = "exact_shard_retrain"
MAX_LINEAGE_DEPTH = 64
# Audit verdict: the revoked source's canary exposure must not exceed the control baseline by more than this many
# bits. Heuristic, not a proof: for an untrained canary, exposure = -log2(U) with U uniform, so P(> 6 bits above the
# ~1.44-bit mean) is well under 1% per canary, while a memorised 5-digit secret sits near log2(1e5) = 16.6 bits.
AUDIT_MARGIN_BITS = 6.0

OUT_OF_SCOPE = [
    "Copies outside the training pipeline (raw data stores, backups, logs, caches, analytics exports, and any "
    "outputs/completions already served) are not covered by this certificate.",
    "Trust in attestation hardware: this build uses SIMULATED_TEE_ATTESTATION -- a local ed25519 key file, not "
    "a hardware-bound key. Whoever holds the key can sign. Production binds the key to measured code in a "
    "confidential VM (H100 CC on GCP/Azure) or AWS Nitro enclave.",
    "Root models listed in lineage.root_models have no recorded training run in this ledger; they are assumed "
    "to be trained on generic, source-free text.",
    "The canary-exposure audit is empirical evidence, not a proof; the exactness claim rests on the signed "
    "manifests and non-membership proofs.",
    "Record hashes are unsalted sha256 commitments; low-entropy records could be guessed from their hashes.",
    "Non-membership soundness assumes manifests are sorted Merkle trees built by the (attested) pipeline; anyone "
    "holding a manifest's leaves can re-check it by recomputing the root.",
    "Models deployed before the revocation, and anything they produced, are not covered; the claim is about the "
    "serving model hash stated here.",
    "Exclusion is of these exact record hashes (sha256 of the NFC-normalised canonical record); the same or "
    "near-duplicate content stored under other records or sources is not covered.",
    "The covered record set is the one registered for this source at the ledger seq stated in the claim; records "
    "never registered under this source are not covered.",
    "Signatures and proofs do not stop a key holder who controls the pipeline from committing an unsorted manifest; "
    "detecting that requires the manifest leaves (manifest_leaves_recomputed check) or hardware attestation.",
]


class CertificateError(Exception):
    pass


_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


# --------------------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------------------


def claim_text(source_id: str, n_records: int, merkle_root: str, serving: str, exposure_bits: float,
               baseline_bits: float, registered_seq: int) -> str:
    return (
        f"Serving model {serving} was produced by signed training runs whose manifests exclude all {n_records} "
        f"records of source {source_id} (Merkle root {merkle_root}, as registered at ledger seq {registered_seq}). "
        f"Removal tier: exact shard retrain. "
        f"Empirical audit: canary exposure {float(exposure_bits):.2f} bits vs control baseline "
        f"{float(baseline_bits):.2f} bits."
    )


def audit_verdict(exposure_bits: float, baseline_bits: float, margin_bits: float = AUDIT_MARGIN_BITS) -> bool:
    """True if the canary audit is consistent with removal (exposure <= baseline + margin)."""
    return exposure_bits <= baseline_bits + margin_bits


def certificate_body(cert: dict) -> dict:
    return {k: v for k, v in cert.items() if k != "signature"}


def certificate_digest(cert: dict) -> str:
    """sha256 of the canonical JSON of the certificate WITHOUT its signature field (what gets signed)."""
    return sha256_hex(canonical_json(certificate_body(cert)))


def certificate_sha256(cert: dict) -> str:
    """sha256 of the full signed certificate (logged in the CERTIFICATE_ISSUED ledger entry)."""
    return sha256_hex(canonical_json(cert))


def sign_certificate(cert: dict, signer: Signer) -> dict:
    cert = dict(cert)
    cert.pop("signature", None)
    cert["signature"] = {
        "alg": "ed25519",
        "signer": SIGNER_LABEL,
        "public_key": signer.public_key_hex,
        "signed_digest": certificate_digest(cert),
        "value": signer.sign(CERT_SIG_DOMAIN + bytes.fromhex(certificate_digest(cert))),
    }
    return cert


def save_certificate(cert: dict, out_dir: str | os.PathLike | None = None) -> Path:
    out = Path(out_dir) if out_dir else artifacts_dir() / "certificates"
    cid = str(cert["certificate_id"])
    if not _SAFE_ID.fullmatch(cid):
        raise CertificateError(f"unsafe certificate_id {cid!r}")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{cid}.json"
    path.write_text(json.dumps(cert, indent=1))
    return path


def load_certificate(path: str | os.PathLike) -> dict:
    return json.loads(Path(path).read_text())


def _latest(entries: list[dict], type: str, **eq) -> dict | None:
    for e in reversed(entries):
        if e["type"] == type and all(e["payload"].get(k) == v for k, v in eq.items()):
            return e
    return None


def _audit_numbers(summary: dict) -> tuple[float, float]:
    """Strict: both values must be finite real numbers (not bool, not strings, not nan/inf)."""
    out = []
    for k in ("canary_exposure_bits", "control_baseline_bits"):
        v = summary.get(k) if isinstance(summary, dict) else None
        if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
            raise CertificateError(
                f"audit summary must contain finite numeric 'canary_exposure_bits' and 'control_baseline_bits' "
                f"(got {k}={v!r})")
        out.append(float(v))
    return out[0], out[1]


def _all(entries: list[dict], type: str, **eq) -> list[dict]:
    return [e for e in entries if e["type"] == type and all(e["payload"].get(k) == v for k, v in eq.items())]


# --------------------------------------------------------------------------------------------------------------
# issue
# --------------------------------------------------------------------------------------------------------------


def issue_certificate(
    ledger: Ledger,
    source_id: str,
    audit: dict,
    *,
    serving_shard_hashes: Iterable[str] | None = None,
    revoked_record_hashes: Iterable[str] | None = None,
    manifest_leaves: dict[str, list[str]] | None = None,
    certificate_id: str | None = None,
    save: bool = True,
    out_dir: str | os.PathLike | None = None,
    record_event: bool = True,
    serving_model_id: str = "serving",
    allow_audit_flag: bool = False,
) -> dict:
    """Build, sign, (save) and log a deletion certificate for revoked source `source_id`.

    Preconditions in the ledger: SOURCE_REGISTERED(source_id), SOURCE_REVOKED(source_id), MODEL_DEPLOYED for the
    serving model (latest one, or the one matching `serving_shard_hashes`), and TRAINING_RUN + MANIFEST_CREATED for
    every live shard. `audit` must contain canary_exposure_bits and control_baseline_bits; if no matching
    AUDIT_COMPLETED entry exists yet one is appended. Raises CertificateError if any revoked record is a member of
    any live manifest (i.e. the deletion claim would be false), if the source was registered more than once or with 0
    records, if any lineage model has more than one TRAINING_RUN, or (unless allow_audit_flag) if the canary audit
    contradicts removal (exposure > baseline + AUDIT_MARGIN_BITS). Only MODEL_DEPLOYED entries with
    model_id == serving_model_id are considered."""
    entries = ledger.entries()

    # -- revoked source --
    regs = _all(entries, "SOURCE_REGISTERED", source_id=source_id)
    if not regs:
        raise CertificateError(f"source {source_id!r} is not registered in the ledger")
    if len(regs) > 1:
        raise CertificateError(
            f"source {source_id!r} has {len(regs)} SOURCE_REGISTERED entries (seq {[e['seq'] for e in regs]}); "
            f"refusing: a re-registration could shrink the revoked record set")
    reg = regs[0]
    src_root, n_records = reg["payload"]["merkle_root"], reg["payload"]["n_records"]
    if not n_records:
        raise CertificateError(f"source {source_id!r} was registered with 0 records; nothing to certify")
    leaves = list(revoked_record_hashes) if revoked_record_hashes is not None else ledger.get_set(src_root)
    if leaves is None:
        raise CertificateError(f"record hashes for source {source_id!r} not found; pass revoked_record_hashes")
    src_tree = MerkleTree(leaves)
    if src_tree.root != src_root or src_tree.size != n_records:
        raise CertificateError("supplied revoked record hashes do not reproduce the registered source root")
    revoked = _latest(entries, "SOURCE_REVOKED", source_id=source_id)
    if revoked is None:
        raise CertificateError(f"source {source_id!r} has no SOURCE_REVOKED entry; call ledger.mark_revoked first")
    if revoked["payload"].get("merkle_root") != src_root:
        raise CertificateError("SOURCE_REVOKED root does not match the source's registration")
    request = _latest(entries, "REVOCATION_REQUESTED", source_id=source_id)

    # -- serving deployment --
    if serving_shard_hashes is None:
        deploy = _latest(entries, "MODEL_DEPLOYED", model_id=serving_model_id)
    else:
        want = sorted(_norm_hash(h) for h in serving_shard_hashes)
        deploy = _latest(entries, "MODEL_DEPLOYED", model_id=serving_model_id, shard_model_hashes=want)
    if deploy is None:
        raise CertificateError("no matching MODEL_DEPLOYED entry; call ledger.deploy_serving first")
    shard_hashes = deploy["payload"]["shard_model_hashes"]
    serving = deploy["payload"]["serving_hash"]

    # -- lineage walk: live shards, then recorded ancestors via init_model_hash --
    lineage: list[dict] = []
    root_models: list[str] = []
    seen: set[str] = set()
    frontier = [(h, "live_shard") for h in shard_hashes]
    depth = 0
    while frontier:
        depth += 1
        if depth > MAX_LINEAGE_DEPTH * max(1, len(shard_hashes)):
            raise CertificateError("lineage too deep / cyclic")
        model_hash, role = frontier.pop(0)
        if model_hash in seen:
            continue
        seen.add(model_hash)
        runs = _all(entries, "TRAINING_RUN", output_model_hash=model_hash)
        if len(runs) > 1:
            raise CertificateError(
                f"model {model_hash[:16]}... has {len(runs)} TRAINING_RUN entries "
                f"({[r['payload'].get('run_id') for r in runs]}); a weights hash is produced exactly once -- refusing "
                f"(possible relabelled run)")
        run = runs[0] if runs else None
        if run is None:
            if role == "live_shard":
                raise CertificateError(f"live shard {model_hash[:16]}... has no TRAINING_RUN entry")
            root_models.append(model_hash)
            continue
        man = _latest(entries, "MANIFEST_CREATED", merkle_root=run["payload"]["manifest_root"])
        if man is None:
            raise CertificateError(f"run {run['payload'].get('run_id')} references an unknown manifest")
        lineage.append({"model_hash": model_hash, "role": role, "training_run": run, "manifest": man})
        frontier.append((run["payload"]["init_model_hash"], "ancestor"))

    # -- non-membership proofs: every revoked record x every lineage manifest --
    non_membership = []
    done_roots: set[str] = set()
    for item in lineage:
        man = item["manifest"]["payload"]
        root = man["merkle_root"]
        if root in done_roots:
            continue
        done_roots.add(root)
        m_leaves = (manifest_leaves or {}).get(root) or ledger.get_set(root)
        if m_leaves is None:
            raise CertificateError(f"leaves for manifest {root[:16]}... not available; pass manifest_leaves")
        tree = MerkleTree(m_leaves)
        if tree.root != root or tree.size != man["n_records"]:
            raise CertificateError(f"stored leaves do not reproduce manifest root {root[:16]}...")
        proofs = []
        for h in src_tree.leaves:
            if h in tree:
                raise CertificateError(
                    f"revoked record {h[:16]}... of {source_id} IS a member of live manifest "
                    f"{man.get('manifest_id')} ({root[:16]}...) used by run "
                    f"{item['training_run']['payload'].get('run_id')}: the deletion claim would be false"
                )
            proofs.append(tree.non_membership_proof(h))
        non_membership.append({"manifest_root": root, "manifest_id": man.get("manifest_id"),
                               "tree_size": tree.size, "proofs": proofs})

    # -- audit: anchor numbers in the signed ledger --
    exp_bits, base_bits = _audit_numbers(audit)
    if not audit_verdict(exp_bits, base_bits) and not allow_audit_flag:
        raise CertificateError(
            f"canary audit contradicts removal: exposure {exp_bits:.2f} bits > baseline {base_bits:.2f} + "
            f"{AUDIT_MARGIN_BITS} bits (the secret is still extractable; look for copies under other sources)")
    audit_entry = _latest(entries, "AUDIT_COMPLETED", model_hash=serving, source_id=source_id)
    if audit_entry is not None and _audit_numbers(audit_entry["payload"]["summary"]) != (exp_bits, base_bits):
        raise CertificateError("audit numbers differ from the signed AUDIT_COMPLETED entry for this model")
    if audit_entry is None:
        audit_entry = ledger.record_audit(serving, dict(audit), source_id=source_id)
    summary = audit_entry["payload"]["summary"]

    head = ledger.head()
    cert_id = certificate_id or f"cert-{re.sub(r'[^A-Za-z0-9._-]', '-', source_id)[:80]}-{uuid.uuid4().hex[:8]}"
    if not _SAFE_ID.fullmatch(cert_id) or ".." in cert_id:
        raise CertificateError(f"unsafe certificate_id {cert_id!r}")
    cert: dict[str, Any] = {
        "format": CERT_FORMAT,
        "certificate_id": cert_id,
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attestation": ledger.signer.attestation(),
        "claim": claim_text(source_id, n_records, src_root, serving, exp_bits, base_bits, reg["seq"]),
        "removal_tier": REMOVAL_TIER,
        "out_of_scope": OUT_OF_SCOPE,
        "revoked_source": {
            "source_id": source_id,
            "merkle_root": src_root,
            "n_records": n_records,
            "merkle_scheme": MERKLE_SCHEME,
            "record_hashes": src_tree.leaves,
            "registered_entry": reg,
            "revocation_request_entry": request,
            "revoked_entry": revoked,
        },
        "serving_model": {"serving_hash": serving, "shard_model_hashes": shard_hashes, "deployment_entry": deploy},
        "lineage": {"models": lineage, "root_models": root_models},
        "non_membership": non_membership,
        "audit": {"summary": summary, "audit_entry": audit_entry, "margin_bits": AUDIT_MARGIN_BITS,
                  "consistent_with_removal": audit_verdict(exp_bits, base_bits)},
        "ledger": {"head_hash": head["entry_hash"], "head_seq": head["seq"], "backend": ledger.backend,
                   "ledger_id": head.get("ledger_id")},
    }
    cert = json.loads(json.dumps(cert))  # break object aliasing between embedded entries before signing
    cert = sign_certificate(cert, ledger.signer)
    if save:
        save_certificate(cert, out_dir)
    if record_event:
        ledger.record_certificate(cert_id, certificate_sha256(cert), source_id, serving)
    return cert


# --------------------------------------------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------------------------------------------


def _embedded_entries(cert: dict) -> list[tuple[str, dict]]:
    rs, sm = cert["revoked_source"], cert["serving_model"]
    out = [("registered_entry", rs["registered_entry"]), ("revoked_entry", rs["revoked_entry"]),
           ("deployment_entry", sm["deployment_entry"]), ("audit_entry", cert["audit"]["audit_entry"])]
    if rs.get("revocation_request_entry"):
        out.append(("revocation_request_entry", rs["revocation_request_entry"]))
    for i, m in enumerate(cert["lineage"]["models"]):
        out += [(f"lineage[{i}].training_run", m["training_run"]), (f"lineage[{i}].manifest", m["manifest"])]
    return out


def verify_certificate(
    cert: dict,
    record_hashes: Iterable[str | dict] | None = None,
    model_files: Iterable[str | os.PathLike] | None = None,
    ledger_export: dict | None = None,
    trusted_public_key: str | None = None,
    manifest_leaves: dict[str, list[str]] | None = None,
    audit_margin_bits: float = AUDIT_MARGIN_BITS,
) -> dict:
    """Offline verification. Returns {valid, checks: [{name, ok, detail}], skipped: [names]}.

    record_hashes: the customer's OWN record hashes (or raw record dicts, hashed here); each must be covered
      by a valid non-membership proof against every live manifest.
    model_files: weight files; each sha256 must be a live shard hash (all of them -> serving hash recomputed).
    ledger_export: a lethe ledger export; checks the chain, that every embedded entry is in it verbatim, that
      the head is anchored, that the deployment was current at issuance and that issuance was logged.
    trusted_public_key: pin the expected signer key (hex).
    manifest_leaves: {manifest_root: [record hashes]} (or taken from ledger_export["manifest_leaves"]); recomputes
      each lineage manifest root from SORTED leaves and checks revoked records against the leaves directly -- the
      only check that catches an unsorted (maliciously built) manifest.
    audit_margin_bits: the verifier's own threshold for `audit_consistent_with_removal` (not taken from the cert)."""
    checks: list[dict] = []
    skipped: list[str] = []

    def check(name: str, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # malformed certificate -> failed check, never a crash
            ok, detail = False, f"malformed certificate ({type(e).__name__}: {e})"
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    att = cert.get("attestation", {}) if isinstance(cert, dict) else {}
    pk = att.get("public_key", "")

    def c_format():
        return cert.get("format") == CERT_FORMAT, f"format {cert.get('format')!r}"

    def c_attestation():
        ok = att.get("type") == SIGNER_LABEL and att.get("simulated") is True
        return ok, (f"{SIGNER_LABEL}: ed25519 key {att.get('key_id')} -- SIMULATED, not hardware-bound"
                    if ok else "attestation block missing or mislabelled")

    def c_signature():
        sig = cert["signature"]
        if sig.get("public_key") != pk:
            return False, "signature public key differs from attestation key"
        if sig.get("signer") != SIGNER_LABEL or sig.get("alg") != "ed25519":
            return False, "signature block mislabelled (signer must be SIMULATED_TEE_ATTESTATION, alg ed25519)"
        digest = certificate_digest(cert)
        ok = verify_signature(pk, CERT_SIG_DOMAIN + bytes.fromhex(digest), sig["value"])
        return ok, (f"ed25519 signature over certificate digest {digest[:16]}... valid" if ok
                    else "certificate signature INVALID (certificate modified or signed by another key)")

    def c_trusted_key():
        want = (trusted_public_key or ledger_export.get("public_key") or "").strip().lower()
        src = "pinned key" if trusted_public_key else "ledger export key"
        return pk == want, f"certificate key {'matches' if pk == want else 'DOES NOT match'} {src}"

    def c_entries():
        bad = []
        for label, e in _embedded_entries(cert):
            ok, detail = verify_entry(e, pk)
            if not ok:
                bad.append(f"{label}: {detail}")
            elif e.get("ledger_id") != cert["ledger"].get("ledger_id"):
                bad.append(f"{label}: belongs to ledger {e.get('ledger_id')!r}, not {cert['ledger'].get('ledger_id')!r}")
            elif e["seq"] > cert["ledger"]["head_seq"]:
                bad.append(f"{label}: seq {e['seq']} after stated ledger head")
        n = len(_embedded_entries(cert))
        return not bad, (f"{n} embedded ledger entries hash- and signature-valid" if not bad else "; ".join(bad))

    def c_source():
        rs = cert["revoked_source"]
        reg = rs["registered_entry"]
        tree = MerkleTree(rs["record_hashes"])
        problems = []
        if reg["type"] != "SOURCE_REGISTERED" or reg["payload"]["source_id"] != rs["source_id"]:
            problems.append("registered entry is not this source's SOURCE_REGISTERED")
        if not (tree.root == rs["merkle_root"] == reg["payload"]["merkle_root"]):
            problems.append("record hashes do not reproduce the registered Merkle root")
        if not (tree.size == rs["n_records"] == reg["payload"]["n_records"]):
            problems.append("record count mismatch")
        return not problems, (problems and "; ".join(problems)) or (
            f"{tree.size} record hashes reproduce source root {tree.root[:16]}...")

    def c_revoked():
        rs, e = cert["revoked_source"], cert["revoked_source"]["revoked_entry"]
        ok = (e["type"] == "SOURCE_REVOKED" and e["payload"]["source_id"] == rs["source_id"]
              and e["payload"]["merkle_root"] == rs["merkle_root"])
        return ok, f"SOURCE_REVOKED at ledger seq {e['seq']}" if ok else "revocation entry does not match source"

    def c_serving():
        sm = cert["serving_model"]
        d = sm["deployment_entry"]
        shards = sorted(sm["shard_model_hashes"])
        ok = (d["type"] == "MODEL_DEPLOYED" and serving_hash(shards) == sm["serving_hash"]
              == d["payload"]["serving_hash"] and shards == d["payload"]["shard_model_hashes"])
        return ok, (f"serving hash {sm['serving_hash'][:16]}... = sha256(sorted {len(shards)} shard hashes), "
                    f"deployed at seq {d['seq']}" if ok else "serving hash / shard list / deployment entry mismatch")

    def c_lineage():
        lin = cert["lineage"]
        shards = set(cert["serving_model"]["shard_model_hashes"])
        produced = {}
        problems = []
        for m in lin["models"]:
            run, man = m["training_run"], m["manifest"]
            if run["type"] != "TRAINING_RUN" or run["payload"]["output_model_hash"] != m["model_hash"]:
                problems.append(f"run does not produce {m['model_hash'][:12]}")
            if man["type"] != "MANIFEST_CREATED" or man["payload"]["merkle_root"] != run["payload"]["manifest_root"]:
                problems.append(f"manifest entry does not match run {run['payload'].get('run_id')}")
            produced[m["model_hash"]] = run
        missing = shards - set(produced)
        if missing:
            problems.append(f"{len(missing)} live shard(s) without a signed training run")
        roots = set(lin["root_models"])
        for h, run in produced.items():
            init = run["payload"]["init_model_hash"]
            if init not in produced and init not in roots:
                problems.append(f"ancestor {init[:12]} of {h[:12]} unaccounted for")
        if roots & set(produced):
            problems.append("a model is listed both as root and as produced")
        detail = (f"{len(shards)} live shard(s) traced through {len(produced)} signed run(s) to "
                  f"{len(roots)} root model(s)")
        return not problems, "; ".join(problems) if problems else detail

    def c_non_membership():
        revoked = set(cert["revoked_source"]["record_hashes"])
        blocks = {b["manifest_root"]: b for b in cert["non_membership"]}
        problems, n_ok = [], 0
        manifests = {m["manifest"]["payload"]["merkle_root"]: m["manifest"]["payload"]
                     for m in cert["lineage"]["models"]}
        for root, man in manifests.items():
            b = blocks.get(root)
            if b is None:
                problems.append(f"no proofs for manifest {root[:12]}")
                continue
            if b["tree_size"] != man["n_records"]:
                problems.append(f"manifest {root[:12]}: proof tree size != signed n_records")
                continue
            covered = {p["record_hash"] for p in b["proofs"]}
            if covered != revoked:
                problems.append(f"manifest {root[:12]}: proofs cover {len(covered & revoked)}/{len(revoked)} records")
            for p in b["proofs"]:
                ok, why = verify_non_membership(p["record_hash"], p, root, man["n_records"])
                if ok:
                    n_ok += 1
                else:
                    problems.append(f"manifest {root[:12]} record {p['record_hash'][:12]}: {why}")
        if problems:
            return False, "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
        return True, (f"{n_ok} non-membership proofs valid: all {len(revoked)} revoked records absent from all "
                      f"{len(manifests)} live-lineage manifest(s)")

    def c_audit():
        a, sm = cert["audit"], cert["serving_model"]
        e = a["audit_entry"]
        ok = (e["type"] == "AUDIT_COMPLETED" and e["payload"]["model_hash"] == sm["serving_hash"]
              and e["payload"].get("source_id") == cert["revoked_source"]["source_id"]
              and e["payload"]["summary"] == a["summary"])
        x, y = _audit_numbers(a["summary"])
        return ok, (f"exposure {x:.2f} bits vs baseline {y:.2f} bits, matches signed AUDIT_COMPLETED seq {e['seq']}"
                    if ok else "audit summary does not match the signed AUDIT_COMPLETED entry for this model "
                    "and source")

    def c_audit_verdict():
        x, y = _audit_numbers(cert["audit"]["summary"])
        ok = audit_verdict(x, y, audit_margin_bits)
        return ok, (f"canary exposure {x:.2f} bits <= baseline {y:.2f} + {audit_margin_bits} bits" if ok else
                    f"canary STILL EXPOSED: {x:.2f} bits > baseline {y:.2f} + {audit_margin_bits} bits -- the audit "
                    f"contradicts removal")

    def c_claim():
        rs, sm = cert["revoked_source"], cert["serving_model"]
        x, y = _audit_numbers(cert["audit"]["summary"])
        want = claim_text(rs["source_id"], rs["n_records"], rs["merkle_root"], sm["serving_hash"], x, y,
                          rs["registered_entry"]["seq"])
        ok = cert["claim"] == want and cert.get("removal_tier") == REMOVAL_TIER
        missing = [n for n in OUT_OF_SCOPE if n not in (cert.get("out_of_scope") or [])]
        if missing:
            return False, f"{len(missing)} standard out-of-scope note(s) removed from the certificate"
        return ok, "claim text matches certificate fields" if ok else "claim text does not match certificate fields"

    for name, fn in [("format", c_format), ("attestation_label", c_attestation),
                     ("certificate_signature", c_signature)]:
        check(name, fn)
    if trusted_public_key or ledger_export:
        check("trusted_key", c_trusted_key)
    else:
        skipped.append("trusted_key (no pinned key or ledger export supplied)")
    for name, fn in [("ledger_entries_signed", c_entries), ("revoked_source_root", c_source),
                     ("revocation_recorded", c_revoked), ("serving_model_hash", c_serving),
                     ("lineage_complete", c_lineage), ("non_membership_proofs", c_non_membership),
                     ("audit_anchored", c_audit), ("audit_consistent_with_removal", c_audit_verdict),
                     ("claim_consistent", c_claim)]:
        check(name, fn)

    # -- ledger export: anchor the embedded entries in the published hash chain --
    if ledger_export is not None:
        def c_ledger():
            res = verify_export(ledger_export, expected_head=cert["ledger"]["head_hash"])
            if not res["valid"]:
                return False, "ledger export invalid: " + "; ".join(res["errors"][:3])
            by_seq = {e["seq"]: e for e in ledger_export["entries"]}
            head = by_seq.get(cert["ledger"]["head_seq"])
            if not head or head["entry_hash"] != cert["ledger"]["head_hash"]:
                return False, "certificate head hash not at stated seq in ledger"
            bad = [lbl for lbl, e in _embedded_entries(cert) if by_seq.get(e["seq"]) != e]
            if bad:
                return False, f"embedded entries not found verbatim in ledger: {bad[:4]}"
            return True, f"chain of {res['n_entries']} entries valid; all embedded entries present verbatim"

        def c_deploy_current():
            dep = cert["serving_model"]["deployment_entry"]
            dseq, hseq = dep["seq"], cert["ledger"]["head_seq"]
            mid = dep["payload"].get("model_id")
            later = [e["seq"] for e in ledger_export["entries"] if e["type"] == "MODEL_DEPLOYED" and e["seq"] > dseq
                     and e["payload"].get("model_id") == mid]
            at_issue = [s for s in later if s <= hseq]
            if at_issue:
                return False, f"a newer deployment (seq {at_issue[0]}) was live at issuance"
            note = f"; superseded later at seq {later[0]}" if later else ""
            return True, f"deployment seq {dseq} was the live serving model at issuance{note}"

        def c_logged():
            want = certificate_sha256(cert)
            hit = [e for e in ledger_export["entries"] if e["type"] == "CERTIFICATE_ISSUED"
                   and e["payload"].get("certificate_id") == cert["certificate_id"]]
            ok = bool(hit) and hit[-1]["payload"].get("certificate_sha256") == want
            return ok, (f"CERTIFICATE_ISSUED at seq {hit[-1]['seq']} matches this certificate's sha256" if ok
                        else "no matching CERTIFICATE_ISSUED entry (certificate unlogged or altered)")

        def c_roots():
            hseq = cert["ledger"]["head_seq"]
            produced = {e["payload"]["output_model_hash"] for e in ledger_export["entries"]
                        if e["type"] == "TRAINING_RUN" and e["seq"] <= hseq}
            hidden = [h[:12] for h in cert["lineage"]["root_models"] if h in produced]
            if hidden:
                return False, f"root model(s) {hidden} DO have recorded training runs that the certificate omits"
            return True, f"{len(cert['lineage']['root_models'])} root model(s) have no recorded training run"

        def c_runs_unique():
            hseq = cert["ledger"]["head_seq"]
            runs: dict[str, list[str]] = {}
            for e in ledger_export["entries"]:
                if e["type"] == "TRAINING_RUN" and e["seq"] <= hseq:
                    runs.setdefault(e["payload"]["output_model_hash"], []).append(e["entry_hash"])
            bad = [m["model_hash"][:12] for m in cert["lineage"]["models"]
                   if runs.get(m["model_hash"]) != [m["training_run"]["entry_hash"]]]
            if bad:
                return False, (f"lineage model(s) {bad} have other TRAINING_RUN entries in the ledger than the one "
                               f"embedded (relabelled weights?)")
            return True, f"each of {len(cert['lineage']['models'])} lineage model(s) has exactly one TRAINING_RUN"

        def c_registration_unique():
            hseq = cert["ledger"]["head_seq"]
            rs = cert["revoked_source"]
            regs = [e for e in ledger_export["entries"] if e["type"] == "SOURCE_REGISTERED" and e["seq"] <= hseq
                    and e["payload"].get("source_id") == rs["source_id"]]
            ok = [e["entry_hash"] for e in regs] == [rs["registered_entry"]["entry_hash"]]
            return ok, (f"source registered exactly once (seq {rs['registered_entry']['seq']})" if ok else
                        f"source has {len(regs)} SOURCE_REGISTERED entries (seq {[e['seq'] for e in regs]}): the "
                        f"certificate may cover a shrunken record set")

        check("ledger_anchor", c_ledger)
        check("lineage_runs_unique", c_runs_unique)
        check("source_registration_unique", c_registration_unique)
        check("root_models_unrecorded", c_roots)
        check("deployment_current_at_issuance", c_deploy_current)
        check("certificate_logged", c_logged)
    else:
        skipped.append("ledger_anchor (no ledger export supplied: embedded entries are checked against the key "
                       "only, not against the published chain)")

    leaves_map = manifest_leaves
    if leaves_map is None and isinstance(ledger_export, dict):
        leaves_map = ledger_export.get("manifest_leaves")
    if leaves_map is not None:
        def c_manifest_leaves():
            revoked = set(cert["revoked_source"]["record_hashes"])
            manifests = {m["manifest"]["payload"]["merkle_root"]: m["manifest"]["payload"]["n_records"]
                         for m in cert["lineage"]["models"]}
            problems, n_checked = [], 0
            for root, n in manifests.items():
                lv = leaves_map.get(root)
                if lv is None:
                    problems.append(f"no leaves supplied for manifest {root[:12]}")
                    continue
                t = MerkleTree(lv)
                if t.root != root or t.size != n:
                    problems.append(f"manifest {root[:12]}: sorted leaves do NOT reproduce the signed root/size "
                                    f"(unsorted or different tree)")
                hit = revoked & set(t.leaves)
                if hit:
                    problems.append(f"manifest {root[:12]} CONTAINS {len(hit)} revoked record(s)")
                n_checked += 1
            if problems:
                return False, "; ".join(problems[:5])
            return True, (f"{n_checked} manifest(s) recomputed from sorted leaves; none contains a revoked record")
        check("manifest_leaves_recomputed", c_manifest_leaves)
    else:
        skipped.append("manifest_leaves_recomputed (no manifest leaves supplied: non-membership relies on the "
                       "pipeline having built sorted trees)")

    if model_files is not None:
        def c_models():
            shards = set(cert["serving_model"]["shard_model_hashes"])
            got = {str(p): file_sha256(p) for p in model_files}
            unknown = [p for p, h in got.items() if h not in shards]
            if unknown:
                return False, f"file(s) not part of the certified serving model: {unknown}"
            if set(got.values()) == shards:
                return True, f"all {len(shards)} shard files hash to the certified serving model"
            return True, f"{len(got)} file(s) match certified shard hashes ({len(shards)} shards total)"
        check("model_files", c_models)
    else:
        skipped.append("model_files (no weight files supplied)")

    if record_hashes is not None:
        bad_input: list[str] = []
        mine = []
        for r in record_hashes:
            try:
                mine.append(record_hash(r) if isinstance(r, dict) else _norm_hash(r))
            except (ValueError, TypeError, UnicodeError) as e:
                bad_input.append(f"{type(e).__name__}: {str(e)[:60]}")
        if bad_input:
            checks.append({"name": "customer_records_parsed", "ok": False,
                           "detail": f"{len(bad_input)} supplied record(s) could not be hashed: {bad_input[:3]}"})

        def c_customer_coverage():
            manifests = {m["manifest"]["payload"]["merkle_root"]: m["manifest"]["payload"]["n_records"]
                         for m in cert["lineage"]["models"]}
            blocks = {b["manifest_root"]: {p["record_hash"]: p for p in b["proofs"]} for b in cert["non_membership"]}
            missing = 0
            for h in set(mine):
                for root, n in manifests.items():
                    p = blocks.get(root, {}).get(h)
                    if p is None or not verify_non_membership(h, p, root, n)[0]:
                        missing += 1
                        break
            ok = missing == 0 and len(mine) > 0
            return ok, (f"each of your {len(set(mine))} records has a valid exclusion proof against every live "
                        f"manifest" if ok else f"{missing} of your records lack a valid exclusion proof")

        def c_customer_root():
            rs = cert["revoked_source"]
            tree = MerkleTree(mine)
            if tree.size < rs["n_records"]:
                return True, (f"you supplied {tree.size} of {rs['n_records']} records (subset); root comparison "
                              f"skipped, per-record coverage checked")
            if tree.size > rs["n_records"]:
                return False, (f"you supplied {tree.size} records but the certificate covers only "
                               f"{rs['n_records']}: some of your records were never registered under this source")
            ok = tree.root == rs["merkle_root"]
            return ok, ("your records reproduce the revoked source's Merkle root exactly" if ok
                        else "your records do NOT reproduce the revoked source root")

        check("customer_records_excluded", c_customer_coverage)
        check("customer_records_match_source", c_customer_root)
    else:
        skipped.append("customer records (none supplied)")

    return {"valid": all(c["ok"] for c in checks), "certificate_id": cert.get("certificate_id")
            if isinstance(cert, dict) else None, "checks": checks, "skipped": skipped}
