# Lethe ledger + deletion certificate: API

Owner: ledger builder. Files: `lethe/ledger.py`, `lethe/certificate.py`, `scripts/verify_certificate.py`,
`tests/test_ledger.py`, `tests/test_certificate.py`.
Dependencies: stdlib, `cryptography`, and `psycopg` (only when `LETHE_DATABASE_URL` is set). No torch.

```bash
uv run pytest tests/test_ledger.py tests/test_certificate.py -q      # 66 tests
uv run python scripts/verify_certificate.py artifacts/certificates/<id>.json [--records mine.jsonl] \
    [--model shard0.pt --model shard1.pt] [--ledger ledger_export.json] [--pubkey artifacts/keys/ledger_ed25519.pub] \
    [--manifest-leaves auditor_export.json] [--audit-margin 6.0]
```

## What is SIMULATED (read first)

Every ledger entry and certificate is signed with one ed25519 key kept in `artifacts/keys/ledger_ed25519.pem`
(public key: `ledger_ed25519.pub`). It stands in for a TEE attestation key and is labelled
`SIMULATED_TEE_ATTESTATION` in every entry (`signer`), in every export, and in every certificate
(`attestation.type`, `attestation.simulated: true`). The UI and API should show that label as is.

**In a real deployment** the training and ledger code runs inside a confidential VM: GCP Confidential Space,
Azure confidential VMs with NVIDIA H100 confidential computing, or an AWS Nitro Enclave. The signing key is
generated inside the enclave and never leaves it. The hardware attestation report (an AMD SEV-SNP or Intel TDX
quote plus H100 GPU attestation, or a Nitro attestation document) binds the public key to a measurement of
the exact code that builds manifests, trains and signs. A verifier checks the vendor's certificate chain and
the expected code measurement, and only then trusts the key. In this build, anyone who can read the key file
can sign anything. What the hash chain does give us here is that edits made after the fact to a ledger that
has already been published are detectable.

## Conventions

| Thing | Definition |
|---|---|
| canonical JSON | `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`, UTF-8 |
| record hash | `sha256(canonical_json(NFC(full_record)))` → `record_hash(record)`. Every string (keys and values) is Unicode-NFC-normalised first, so NFC and NFD spellings of the same text hash the same. Nothing else is normalised: `1` and `1.0`, whitespace, a different `id` or `source_id` all give a **different** record. |
| model hash | `sha256` of the saved weights file → `file_sha256(path)` |
| serving hash | `sha256(canonical_json(sorted(shard_model_hashes)))` → `serving_hash(hashes)` |
| Merkle tree | `MerkleTree(hashes)`: leaves are **lowercased, deduplicated, sorted**, so the root depends only on the set. Leaf = `sha256(0x00‖leaf)`, node = `sha256(0x01‖L‖R)` (domain-separated). An odd last node is promoted, never duplicated. Empty root = `sha256(b"")`. |
| per-source root | `MerkleTree(source record hashes).root` (stored in `SOURCE_REGISTERED`) |
| per-run manifest | `MerkleTree(all record hashes a run trains on).root` (stored in `MANIFEST_CREATED`) |
| entry hash | `sha256(canonical_json({seq, ts, type, payload, prev_hash, ledger_id}))`; `prev_hash` of seq 1 = `"0"*64`; `ledger_id` = random id fixed at genesis and copied into every entry (omitted only for legacy entries) |
| entry signature | ed25519 over `b"LETHE-LEDGER-ENTRY-v1\0" + bytes.fromhex(entry_hash)` |
| certificate signature | ed25519 over `b"LETHE-CERTIFICATE-v1\0" + sha256(canonical_json(cert minus "signature"))` |

Because signatures are domain-separated, an entry signature can never be replayed as a certificate
signature.

## Merkle proofs (`lethe.ledger`)

```python
t = MerkleTree(hashes)             # .root, .size, .leaves (sorted), `h in t`, .index_of(h)
p = t.inclusion_proof(i)           # {"leaf", "index", "path": [hex...]}
verify_inclusion(leaf, index, tree_size, path, root) -> bool        # path shape re-derived from (index, size)
q = t.non_membership_proof(h)      # raises ValueError if h is a member
# q = {"record_hash", "tree_size", "kind": "empty"|"below_min"|"above_max"|"between",
#      "left": inclusion_proof|None, "right": inclusion_proof|None}
verify_non_membership(h, q, root, tree_size) -> (ok: bool, detail: str)
```

Non-membership works by showing two neighbouring leaves at positions i and i+1 with `left < h < right`
(or the first leaf with `h < min`, or the last leaf with `h > max`). Each neighbour comes with its own inclusion
proof. `tree_size` always comes from the signed `MANIFEST_CREATED.n_records`, never from the proof itself.
**Soundness caveat (important).** The proof is only sound if the committed tree really was built sorted, and
sortedness cannot be seen from the proofs. An operator who controls the pipeline code can commit an *unsorted*
tree that contains a revoked record, placed so that no bracket the proofs need is broken, and every proof
still verifies (reviewer attack a3, now a regression test). Defences in this build:
- `record_training_run` refuses a manifest whose stored leaves do not reproduce the committed root when sorted
  (and refuses a manifest whose leaves are not stored at all).
- `verify_certificate(..., manifest_leaves={root: leaves})` (CLI `--manifest-leaves`, or an export made with
  `include_manifest_leaves=True`) recomputes each lineage manifest root from sorted leaves and checks revoked ∩
  leaves = ∅ directly (`manifest_leaves_recomputed`). This needs the leaves, which reveal every party's record
  hashes (not contents), so it is an auditor check, not a tenant check.
- Not built: the proper fix is a sparse Merkle tree keyed by record hash (depth 256), where "absent" means the
  slot at H(record) is empty, which is sound whoever built the tree.
Without the leaves, the guarantee rests on the (attested) pipeline building the tree, which is SIMULATED here.

## Ledger (`lethe.ledger`)

```python
from lethe.ledger import open_ledger, Ledger, Signer, SQLiteStore, record_hash, file_sha256

lg = open_ledger()          # Postgres if LETHE_DATABASE_URL else artifacts/ledger.sqlite; key auto-created
# tests / custom: Ledger(store=SQLiteStore(path), signer=Signer.load_or_create(key_path))

lg.append(type, payload) -> entry      # entry = {seq, ts, type, payload, prev_hash, entry_hash, signature, signer}
lg.entries(since_seq=0, limit=None)    # oldest first
lg.recent(limit=100)                   # newest first  -> GET /api/ledger?limit=
lg.head(); lg.head_hash()
lg.verify_chain(expected_head=None)    # {valid, n_entries, head_hash, errors[]}
lg.find(type=None, **payload_eq); lg.latest(type, **payload_eq)
lg.get_set(root)                       # leaves behind a source/manifest root (stored OFF-chain)
lg.revoked_sources() -> set[str]
lg.export(path=None, include_manifest_leaves=False) -> dict
                                       # {"format": "lethe-ledger-export-v1", "signer", "simulated", "public_key", "head_hash", "entries": [...]}
                                       # include_manifest_leaves=True adds "manifest_leaves": {root: [hashes]} (auditor export)
lg.import_entries(export)              # verify, then load into an EMPTY store (e.g. SQLite -> Postgres)
lg.public_key_hex; lg.backend          # "sqlite" | "postgres"
lg.ledger_id                           # genesis id carried by every entry (fork separation)

# module-level, no database needed:
verify_chain(entries, public_key_hex, expected_head=None); verify_export(export, expected_head=None, trusted_public_key=None)
load_export(path)
```

`verify_chain` detects edited payloads, deleted or inserted entries, reordering and forged signatures. If the
tail of the chain is cut off, it can only tell when you pass a head hash you got earlier (`expected_head`).
A certificate carries one of these head hashes. The SQLite table also has triggers that reject UPDATE and
DELETE. Dropping those triggers is possible, but the hash chain still catches the edit (there is a test for
this).

### Typed event helpers (call in this order during a run)

| Helper | Event | Payload |
|---|---|---|
| `register_source(source_id, source_type, display_name, record_hashes, shard=None, **extra)` | `SOURCE_REGISTERED` | `source_id, source_type, display_name, merkle_root, n_records, shard, merkle_scheme`. **Once per source_id**: re-registering raises `LedgerError` (a smaller second registration would shrink what a revocation covers). An empty record set raises. |
| `create_manifest(record_hashes, manifest_id=None, sources=None, shard=None, **extra)` | `MANIFEST_CREATED` | `manifest_id, merkle_root, n_records, sources, shard, merkle_scheme` |
| `record_training_run(init_model_hash, output_model_hash, manifest_root, config, run_id=None, model_id=None, shard=None, kind=None, **extra)` | `TRAINING_RUN` | `run_id, model_id, kind, shard, init_model_hash, output_model_hash, manifest_root, config`. Raises `LedgerError` if: the manifest isn't in the ledger; its leaves aren't stored or don't reproduce the root as a sorted tree; `output_model_hash` already has a run (**a weights hash is produced exactly once**, so old weights cannot be relabelled as clean); or init == output. |
| `deploy_serving(shard_model_hashes, model_id="serving", shards=None, **extra)` | `MODEL_DEPLOYED` | `model_id, serving_hash, shard_model_hashes (sorted), shards`. Certificates only look at `model_id="serving"` deployments (configurable), so logging a `pooled` deployment is fine. |
| `request_revocation(source_id, requested_by=None, reason=None, request_id=None)` | `REVOCATION_REQUESTED` | `request_id, source_id, requested_by, reason` |
| `mark_revoked(source_id, shard=None, request_id=None)` | `SOURCE_REVOKED` | `source_id, merkle_root, n_records, shard, request_id` |
| `record_audit(model_hash, summary, source_id=None)` | `AUDIT_COMPLETED` | `model_hash, source_id, summary` |
| `record_certificate(certificate_id, certificate_sha256, source_id, serving_hash)` | `CERTIFICATE_ISSUED` | same (called by `issue_certificate`) |
| `lg.issue_certificate(source_id, audit, **kw)` | n/a | wrapper for `lethe.certificate.issue_certificate(lg, ...)` |

Base model: either record it as a run (`create_manifest(generic_text_hashes)` followed by
`record_training_run(init_hash, base_hash, root, cfg, kind="base")`), or leave it unrecorded. If unrecorded, it
shows up in the certificate as a **root model**, which the certificate's out-of-scope notes say is assumed to
be source-free.

## Certificate (`lethe.certificate`)

```python
from lethe.certificate import issue_certificate, verify_certificate, load_certificate, save_certificate

cert = issue_certificate(
    lg, "tenant:acme",
    audit={"canary_exposure_bits": 1.4, "control_baseline_bits": 1.5, ...anything else...},
    serving_shard_hashes=None,     # default: latest MODEL_DEPLOYED
    revoked_record_hashes=None,    # default: leaves stored at register_source time
    manifest_leaves=None,          # default: leaves stored at create_manifest time
    certificate_id=None,           # default: "cert-tenant-acme-<8 hex>"
    save=True, out_dir=None,       # default dir: artifacts/certificates/<id>.json
    record_event=True,             # appends CERTIFICATE_ISSUED with sha256 of the signed cert
    serving_model_id="serving",    # only MODEL_DEPLOYED entries with this model_id are considered
    allow_audit_flag=False)        # True: issue even if the audit contradicts removal (verifier will then FAIL it)

verify_certificate(cert, record_hashes=None, model_files=None, ledger_export=None, trusted_public_key=None,
                   manifest_leaves=None, audit_margin_bits=AUDIT_MARGIN_BITS)
# -> {"valid": bool, "certificate_id", "checks": [{"name", "ok", "detail"}], "skipped": [str]}
```

**Preconditions:** the ledger must already contain `SOURCE_REGISTERED`, `SOURCE_REVOKED` (call `mark_revoked`
first), a `MODEL_DEPLOYED` for the serving model, and a `TRAINING_RUN` plus `MANIFEST_CREATED` for every live
shard. If there is no matching `AUDIT_COMPLETED` entry for the serving hash and source, one is appended. If
there is one and its numbers differ, issuance raises an error.

**Issuance refuses to lie.** `CertificateError` is raised if:
- any revoked record belongs to any manifest in the live lineage. That lineage includes **ancestors**: a shard
  "retrained" by fine-tuning the old contaminated shard is caught through its `init_model_hash`;
- the source has more than one `SOURCE_REGISTERED`, or 0 records;
- any lineage model has more than one `TRAINING_RUN` (relabelled weights);
- audit numbers are not finite real numbers (strings, bools, nan, inf are rejected);
- the canary audit contradicts removal: `canary_exposure_bits > control_baseline_bits + AUDIT_MARGIN_BITS`
  (6.0 bits; a heuristic: an untrained 5-digit canary averages ~1.44 bits, a memorised one ~16.6 bits), unless
  `allow_audit_flag=True`.

Certificate fields: `format, certificate_id, issued_at, attestation{type: SIMULATED_TEE_ATTESTATION, simulated,
public_key, key_id, note}, claim, removal_tier: "exact_shard_retrain", out_of_scope[], revoked_source{source_id,
merkle_root, n_records, record_hashes, registered_entry, revocation_request_entry, revoked_entry},
serving_model{serving_hash, shard_model_hashes, deployment_entry}, lineage{models[{model_hash, role,
training_run, manifest}], root_models[]}, non_membership[{manifest_root, manifest_id, tree_size, proofs[]}],
audit{summary, audit_entry, margin_bits, consistent_with_removal}, ledger{head_hash, head_seq, backend, ledger_id}, signature{alg, signer, public_key,
signed_digest, value}`. Every `*_entry`, `training_run` and `manifest` field is a full signed ledger entry.

Claim (scoped, recomputed by the verifier): *"Serving model \<hash\> was produced by signed training runs whose
manifests exclude all \<n\> records of source \<id\> (Merkle root \<r\>, as registered at ledger seq \<s\>).
Removal tier: exact shard retrain. Empirical audit: canary exposure \<x\> bits vs control baseline \<y\> bits."*
The certificate's `audit` block also carries `margin_bits` and `consistent_with_removal` (display only; the
verifier recomputes with its own margin).

Exclusion is of **these exact record hashes**. The same or near-duplicate content stored under other records or
sources is not covered (reviewer attack a4: acme's texts re-registered as `tenant:acme-archive` give a valid
certificate). This is stated in `out_of_scope`. Only the empirical canary audit can signal it.

### Verification checks

| check | always? | what it establishes |
|---|---|---|
| `format`, `attestation_label` | yes | known format; the key is labelled SIMULATED |
| `certificate_signature` | yes | the certificate has not been modified since signing |
| `trusted_key` | with `trusted_public_key` or `ledger_export` | the signer is the key you expect |
| `ledger_entries_signed` | yes | every embedded entry's hash and signature are valid under the cert key, and each belongs to the certificate's `ledger_id` |
| `revoked_source_root` | yes | the record hashes reproduce the registered root and count |
| `revocation_recorded` | yes | a `SOURCE_REVOKED` entry exists for this source and root |
| `serving_model_hash` | yes | serving hash = H(sorted shards) = the signed deployment |
| `lineage_complete` | yes | every live shard maps to a signed run and manifest, and every ancestor is either a run or a declared root |
| `non_membership_proofs` | yes | **every** revoked record is absent from **every** lineage manifest |
| `audit_anchored`, `claim_consistent` | yes | audit numbers are finite and match the signed `AUDIT_COMPLETED` entry **for this model and this source**; the claim text matches the fields |
| `audit_consistent_with_removal` | yes | exposure ≤ baseline + `audit_margin_bits` (verifier's own threshold). FAIL = the canary is still extractable |
| `ledger_anchor`, `root_models_unrecorded`, `deployment_current_at_issuance`, `certificate_logged` | with `ledger_export` | embedded entries appear verbatim in a valid chain at the stated head; no ancestor was hidden as a "root"; no newer deployment **of the same model_id** was live at issuance; the cert's sha256 was logged |
| `lineage_runs_unique` | with `ledger_export` | each lineage model has exactly one `TRAINING_RUN` up to the head, and it is the embedded one (catches relabelled weights) |
| `source_registration_unique` | with `ledger_export` | the source has exactly one `SOURCE_REGISTERED` up to the head (catches a shrunken re-registration) |
| `manifest_leaves_recomputed` | with `manifest_leaves` (or an export with `manifest_leaves`) | each lineage manifest root is reproduced from its **sorted** leaves at the signed size, and none of those leaves is a revoked record (catches unsorted manifests) |
| `model_files` | with `model_files` | the weights you hold are the certified shards (all of them reproduce the serving hash) |
| `customer_records_excluded`, `customer_records_match_source` | with `record_hashes` | **the customer checks with their own data**: each of their records (raw dicts or hashes) has a valid exclusion proof against each live manifest; the full set reproduces the source root (a subset is allowed; extra records fail) |

What the customer sees: their own record hashes, plus, as proof neighbours, a few **hashes** of other parties'
records. They never see other parties' content.

### Threat model, as covered by the tests

- An honest flow passes every check, including the ledger export, the model files, the manifest leaves and the
  customer's own records.
- A revoked record left in the retrain manifest makes issuance raise.
- Retraining from the old shard checkpoint makes issuance raise (caught through the ancestor).
- **Key holder forging an exclusion for the old contaminated shard**, keeping its original run: the forged
  certificate is validly signed, but `non_membership_proofs` still fails, because the signed `TRAINING_RUN` pins
  the contaminated manifest root. The ledger export also flags the stale deployment.
- **Relabelled run (reviewer a1)**: anyone with API access adds a *second* `TRAINING_RUN` saying the old weights
  came from the clean manifest. `record_training_run` refuses; a raw `append` makes issuance refuse; a
  hand-built certificate passes standalone verification but fails `lineage_runs_unique` with `--ledger`.
- **Re-registration (reviewer a2)**: re-registering a source with fewer (or 0) records is refused by
  `register_source` and by issuance; a certificate built by old issuance code fails `source_registration_unique`
  with `--ledger` (and `customer_records_*` if the customer supplies all of their records).
- **Unsorted manifest (reviewer a3)**: refused by `record_training_run`; if bypassed, every proof still verifies
  (an honest limitation of sorted-tree non-membership), and only `manifest_leaves_recomputed` catches it.
- **Audit (reviewer a5)**: nan/inf/string numbers are rejected; a memorised canary blocks issuance or fails
  `audit_consistent_with_removal`; an audit entry for another source fails `audit_anchored`.
- **Non-serving deployments (reviewer a9)**: a `pooled` `MODEL_DEPLOYED` no longer breaks issuance or
  `deployment_current_at_issuance`.
- Swapped model hash, tampered audit numbers or a wrong key: these fail, both before and after the attacker
  re-signs.
- Ledger export tampered: `ledger_anchor` fails. Certificate never logged: `certificate_logged` fails.
- **Known limitation (tested):** a key holder can drop a recorded ancestor and declare it a "root model".
  Standalone verification cannot detect this; verification with `--ledger` catches it
  (`root_models_unrecorded`).
- **In general, standalone verification (no `--ledger`) trusts the key holder** about which entries exist.
  Relabelled runs, re-registrations and hidden ancestors are only caught against the published ledger. The
  certificate is only as strong as the published ledger head, so publish or anchor head hashes. Hardware
  attestation would also remove the key-holder threat.
- **Ledger forks (reviewer a9)**: two stores under one key (e.g. Postgres plus a SQLite fallback) can each hold
  a valid seq 1. Every entry now carries its ledger's genesis `ledger_id`, which is covered by the entry hash.
  `verify_chain` rejects mixed ids, and the certificate's `ledger.ledger_id` must match every embedded entry
  (`ledger_entries_signed`). The silent SQLite fallback that caused the fork is now opt-in. Legacy entries
  without an id hash exactly as before.

## Storage backends

- **SQLite (default):** `artifacts/ledger.sqlite` (override the artifacts dir with `LETHE_ARTIFACTS_DIR`). Uses WAL,
  `BEGIN IMMEDIATE` for appends, and triggers that make the table append-only. Leaves are kept in a separate
  `merkle_sets` table: they reveal which records exist, so they stay off the chain, and the on-chain roots
  commit to them.
- **Postgres / Tiger Data (TimescaleDB): UNTESTED against a real server.** No Postgres server was available
  during the build; only the "unreachable server" path is tested. Run the tests once against a real Tiger Cloud
  service before the demo. Set `LETHE_DATABASE_URL=postgresql://user:pass@host:port/db?sslmode=require` (a
  Tiger Cloud service URL works). Schema, in this order:
  1. `lethe_ledger` (`PRIMARY KEY (seq, ts)`, `UNIQUE (entry_hash, ts)`, payload stored as canonical TEXT plus a
     `payload_json JSONB` copy for queries) and `lethe_ledger_merkle_sets`.
  2. If the `timescaledb` extension is available: `CREATE EXTENSION IF NOT EXISTS timescaledb`, then
     `create_hypertable('lethe_ledger', 'ts', if_not_exists => TRUE, migrate_data => TRUE)` while the table is
     still empty and has no triggers. Any failure leaves a plain table (`store.timescaledb`,
     `store.timescaledb_note`).
  3. Triggers: `BEFORE INSERT` requires `seq = max(seq)+1` and `prev_hash` = the head's `entry_hash` (seq
     uniqueness and chain linkage inside the database; `UNIQUE(seq)` alone is impossible on a hypertable);
     `BEFORE UPDATE OR DELETE` (row) and `BEFORE TRUNCATE` (statement) raise (`store.trigger_notes` records it
     if the TRUNCATE trigger cannot be installed).
  The triggers are defence in depth; the hash chain plus a published head is the guarantee. Never add a
  retention policy or `drop_chunks` on this table (chunk drops bypass row triggers). In production, `REVOKE
  UPDATE, DELETE, TRUNCATE` from the app role. Appends are serialised with `pg_advisory_xact_lock`. The
  connection uses `connect_timeout=5` and reconnects once on `OperationalError`. If Postgres is unreachable,
  `open_ledger()` **raises**. Falling back to SQLite is opt-in (`LETHE_DB_FALLBACK=1`), because a silent fallback
  forks the ledger under the same key. To migrate: `Ledger(store=PostgresStore(url),
  signer=...).import_entries(sqlite_ledger.export())`. Leaves are not migrated by this call; copy them with
  `get_set`/`put_set`.

## Integration recipe (revoke job steps → calls)

Setup is **not idempotent**: `register_source` raises for an already-registered source and
`record_training_run` raises for a weights hash that already has a run. Re-running setup against an existing
`artifacts/ledger.sqlite` therefore raises. Either start a fresh ledger or check `lg.latest("SOURCE_REGISTERED",
source_id=sid)` / `lg.latest("TRAINING_RUN", output_model_hash=h)` first. Record a base model run only once.

```python
lg = open_ledger()
# setup (once): for each source: lg.register_source(sid, stype, name, [record_hash(r) for r in recs], shard=k)
#   base: m = lg.create_manifest(generic_hashes, manifest_id="m-base"); lg.record_training_run(init, base_h, m["payload"]["merkle_root"], cfg, kind="base")
#   per shard: m = lg.create_manifest(shard_hashes, manifest_id=f"m-shard{k}-v1", sources=[...], shard=k)
#              lg.record_training_run(base_h, file_sha256(shard_path), m["payload"]["merkle_root"], cfg, shard=k, model_id=f"shard{k}")
#   lg.deploy_serving([file_sha256(p) for p in shard_paths])
# POST /api/revoke:
lg.request_revocation(sid, requested_by="tenant admin")              # blast_radius: shard = SOURCE_REGISTERED.shard
m = lg.create_manifest(remaining_hashes, manifest_id=..., shard=k)    # exclude_records
lg.record_training_run(base_h, new_shard_h, m["payload"]["merkle_root"], cfg, shard=k)   # retrain_shard (init MUST be base)
lg.mark_revoked(sid, shard=k)
lg.deploy_serving(new_shard_hashes)                                   # redeploy
audit = {"canary_exposure_bits": x, "control_baseline_bits": y, "per_canary": [...]}     # audit
cert = lg.issue_certificate(sid, audit)                               # sign + certificate
verify_certificate(cert, ledger_export=lg.export())                   # GET /api/certificates/{id}/verify
```

Pytest note: `pyproject.toml` has no pytest `pythonpath` setting, so the two test files add the repo root to
`sys.path` themselves. The integrator may add `[tool.pytest.ini_options] pythonpath = ["."]`.
