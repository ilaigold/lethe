"""Certificate issuance + verification: honest flow passes; lies fail. Synthetic records only."""

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root (no pytest pythonpath config yet)

from lethe.certificate import (  # noqa: E402
    CertificateError,
    certificate_sha256,
    issue_certificate,
    load_certificate,
    sign_certificate,
    verify_certificate,
)
from lethe.ledger import (  # noqa: E402
    SIGNER_LABEL,
    Ledger,
    LedgerError,
    MerkleTree,
    Signer,
    SQLiteStore,
    file_sha256,
    record_hash,
)

AUDIT = {"canary_exposure_bits": 1.37, "control_baseline_bits": 1.52, "n_canaries": 4, "max_bits": 36.2}

SOURCES = {
    "tenant:acme": ("tenant", "Acme Robotics (synthetic)", 0, 12),
    "tenant:globex": ("tenant", "Globex Freight (synthetic)", 1, 9),
    "employee:jdoe": ("employee", "J. Doe, support engineer (synthetic)", 0, 5),
    "news:daily-1": ("news", "Daily Planet article (synthetic, retracted)", 1, 3),
}


def make_records(source_id, kind, name, n):
    slug = source_id.split(":")[1]
    return [{"id": f"{slug}-{i:04d}", "source_id": source_id, "source_type": kind, "display_name": name,
             "text": f"synthetic ticket {i} from {name}; the escalation code is XQ{i:03d}"} for i in range(n)]


def write_weights(tmp_path, name, content: bytes) -> tuple[Path, str]:
    p = tmp_path / f"{name}.pt"
    p.write_bytes(content)
    return p, file_sha256(p)


class World:
    """Minimal end-to-end ledger story (no torch): base -> 2 shards -> serve -> revoke acme -> retrain shard 0."""

    def __init__(self, tmp_path, contaminate_retrain=False, retrain_from_old_shard=False):
        self.tmp = tmp_path
        self.signer = Signer.load_or_create(tmp_path / "keys" / "k.pem")
        self.ledger = Ledger(store=SQLiteStore(tmp_path / "ledger.sqlite"), signer=self.signer)
        lg = self.ledger
        self.records = {sid: make_records(sid, k, name, n) for sid, (k, name, _, n) in SOURCES.items()}
        self.hashes = {sid: [record_hash(r) for r in recs] for sid, recs in self.records.items()}
        for sid, (kind, name, shard, _) in SOURCES.items():
            lg.register_source(sid, kind, name, self.hashes[sid], shard=shard)

        # base model: trained from a random init on generic, source-free text
        init_hash = hashlib.sha256(b"random-init").hexdigest()
        generic = [hashlib.sha256(f"generic-{i}".encode()).hexdigest() for i in range(20)]
        m_base = lg.create_manifest(generic, manifest_id="m-base")["payload"]["merkle_root"]
        self.base_path, self.base = write_weights(tmp_path, "base", b"base-weights")
        lg.record_training_run(init_hash, self.base, m_base, {"steps": 100}, run_id="run-base", kind="base")

        def shard_ids(s, exclude=()):
            return [h for sid, (_, _, sh, _) in SOURCES.items() if sh == s and sid not in exclude
                    for h in self.hashes[sid]]

        self.shard_paths, self.shard_hashes, self.manifest_roots = {}, {}, {}
        for s in (0, 1):
            root = lg.create_manifest(shard_ids(s), manifest_id=f"m-shard{s}-v1", shard=s)["payload"]["merkle_root"]
            self.manifest_roots[s] = root
            p, h = write_weights(tmp_path, f"shard{s}-v1", f"shard{s}-v1".encode())
            self.shard_paths[s], self.shard_hashes[s] = p, h
            lg.record_training_run(self.base, h, root, {"steps": 50}, run_id=f"run-shard{s}-v1", shard=s)
        self.old_shard0 = self.shard_hashes[0]
        self.old_shard0_path = self.shard_paths[0]
        self.deploy_v1 = lg.deploy_serving([self.shard_hashes[0], self.shard_hashes[1]])

        # revoke acme -> retrain shard 0 from base without acme
        lg.request_revocation("tenant:acme", requested_by="acme-admin", reason="contract terminated")
        keep = shard_ids(0, exclude=("tenant:acme",))
        if contaminate_retrain:
            keep = keep + [self.hashes["tenant:acme"][3]]  # pipeline bug: one acme record slipped through
        root = lg.create_manifest(keep, manifest_id="m-shard0-v2", shard=0)["payload"]["merkle_root"]
        p, h = write_weights(tmp_path, "shard0-v2", b"shard0-v2")
        init = self.old_shard0 if retrain_from_old_shard else self.base
        lg.record_training_run(init, h, root, {"steps": 50}, run_id="run-shard0-v2", shard=0)
        self.shard_paths[0], self.shard_hashes[0] = p, h
        lg.mark_revoked("tenant:acme", shard=0)
        self.deploy_v2 = lg.deploy_serving([self.shard_hashes[0], self.shard_hashes[1]])

    def issue(self, **kw):
        return issue_certificate(self.ledger, "tenant:acme", AUDIT, out_dir=self.tmp / "certs", **kw)


def failed(res):
    return {c["name"] for c in res["checks"] if not c["ok"]}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    w.ledger.close()


# ---------------------------------------------------------------- honest flow

def test_honest_certificate_verifies_fully(world):
    cert = world.issue()
    assert cert["attestation"]["type"] == SIGNER_LABEL and cert["attestation"]["simulated"] is True
    assert cert["revoked_source"]["n_records"] == 12
    assert "exclude all 12 records of source tenant:acme" in cert["claim"]
    assert cert["out_of_scope"]
    # 2 live shards + base ancestor = 3 manifests, 12 proofs each
    assert len(cert["non_membership"]) == 3
    assert all(len(b["proofs"]) == 12 for b in cert["non_membership"])
    assert len(cert["lineage"]["root_models"]) == 1

    export = world.ledger.export(include_manifest_leaves=True)  # auditor export: lets the verifier re-sort leaves
    res = verify_certificate(
        cert,
        record_hashes=world.records["tenant:acme"],  # customer verifies with their OWN raw records
        model_files=[world.shard_paths[0], world.shard_paths[1]],
        ledger_export=export,
        trusted_public_key=world.signer.public_key_hex,
    )
    assert res["valid"], [c for c in res["checks"] if not c["ok"]]
    names = {c["name"] for c in res["checks"]}
    assert {"non_membership_proofs", "ledger_anchor", "certificate_logged", "model_files",
            "customer_records_excluded", "customer_records_match_source", "lineage_runs_unique",
            "source_registration_unique", "manifest_leaves_recomputed", "audit_consistent_with_removal"} <= names
    assert res["skipped"] == []
    # saved to disk + logged in the ledger
    on_disk = load_certificate(world.tmp / "certs" / f"{cert['certificate_id']}.json")
    assert on_disk == cert
    logged = world.ledger.latest("CERTIFICATE_ISSUED", certificate_id=cert["certificate_id"])
    assert logged["payload"]["certificate_sha256"] == certificate_sha256(cert)
    assert world.ledger.verify_chain()["valid"]


def test_standalone_verification_without_extras(world):
    res = verify_certificate(world.issue())
    assert res["valid"]
    assert any("ledger_anchor" in s for s in res["skipped"])


def test_certificate_survives_json_roundtrip(world):
    cert = json.loads(json.dumps(world.issue()))
    assert verify_certificate(cert, ledger_export=json.loads(json.dumps(world.ledger.export())))["valid"]


def test_customer_subset_and_foreign_records(world):
    cert = world.issue()
    assert verify_certificate(cert, record_hashes=world.hashes["tenant:acme"][:5])["valid"]
    # a record the operator never registered under acme -> no exclusion proof -> FAIL
    extra = world.hashes["tenant:acme"] + [record_hash({"id": "acme-9999", "text": "unregistered"})]
    res = verify_certificate(cert, record_hashes=extra)
    assert not res["valid"]
    assert {"customer_records_excluded", "customer_records_match_source"} <= failed(res)


# ---------------------------------------------------------------- lies must fail

def test_revoked_record_in_live_manifest_cannot_be_certified(tmp_path):
    w = World(tmp_path, contaminate_retrain=True)
    with pytest.raises(CertificateError, match="IS a member of live manifest"):
        w.issue()


def test_retrain_from_contaminated_checkpoint_cannot_be_certified(tmp_path):
    # shard 0 "retrained" by fine-tuning the OLD shard (which saw acme) -> ancestor manifest contains acme
    w = World(tmp_path, retrain_from_old_shard=True)
    with pytest.raises(CertificateError, match="IS a member"):
        w.issue()


def test_key_holder_cannot_forge_exclusion_for_contaminated_model(world):
    """Dishonest operator (holds the signing key) keeps serving the OLD shard 0 that trained on acme, and tries to
    pass it off with proofs computed against the clean manifest. The signed TRAINING_RUN pins the manifest root,
    so the proofs don't verify."""
    honest = world.issue(record_event=False)
    forged = copy.deepcopy(honest)
    lg = world.ledger
    old_run = lg.latest("TRAINING_RUN", run_id="run-shard0-v1")
    old_man = lg.latest("MANIFEST_CREATED", manifest_id="m-shard0-v1")
    new_root = lg.latest("MANIFEST_CREATED", manifest_id="m-shard0-v2")["payload"]["merkle_root"]
    for m in forged["lineage"]["models"]:
        if m["training_run"]["payload"]["run_id"] == "run-shard0-v2":
            m.update(model_hash=world.old_shard0, training_run=old_run, manifest=old_man)
    forged["serving_model"] = {"serving_hash": world.deploy_v1["payload"]["serving_hash"],
                               "shard_model_hashes": world.deploy_v1["payload"]["shard_model_hashes"],
                               "deployment_entry": world.deploy_v1}
    for b in forged["non_membership"]:
        if b["manifest_root"] == new_root:  # relabel clean proofs as if they were for the contaminated manifest
            b["manifest_root"] = old_man["payload"]["merkle_root"]
            b["tree_size"] = old_man["payload"]["n_records"]
            for p in b["proofs"]:
                p["tree_size"] = old_man["payload"]["n_records"]
    audit_entry = lg.record_audit(forged["serving_model"]["serving_hash"], AUDIT, source_id="tenant:acme")
    forged["audit"] = {"summary": audit_entry["payload"]["summary"], "audit_entry": audit_entry}
    forged["ledger"] = {**forged["ledger"], "head_hash": lg.head_hash(), "head_seq": lg.head()["seq"]}
    from lethe.certificate import claim_text
    rs = forged["revoked_source"]
    forged["claim"] = claim_text(rs["source_id"], rs["n_records"], rs["merkle_root"],
                                 forged["serving_model"]["serving_hash"], 1.37, 1.52, rs["registered_entry"]["seq"])
    forged = sign_certificate(forged, world.signer)  # validly signed by the real key!

    res = verify_certificate(forged, model_files=[world.old_shard0_path])
    assert not res["valid"]
    assert "non_membership_proofs" in failed(res)
    assert "certificate_signature" not in failed(res)  # the signature is fine -- the math is what catches it
    # ...and with the ledger export, the stale deployment is caught too
    res = verify_certificate(forged, ledger_export=lg.export())
    assert {"non_membership_proofs", "deployment_current_at_issuance"} <= failed(res)


def test_swapped_model_hash_fails(world, tmp_path):
    cert = world.issue()
    bad = copy.deepcopy(cert)
    bad["serving_model"]["shard_model_hashes"][0] = hashlib.sha256(b"other-model").hexdigest()
    res = verify_certificate(bad)
    assert not res["valid"] and {"certificate_signature", "serving_model_hash"} <= failed(res)
    # even re-signed by the key holder, the swapped shard no longer matches the signed deployment / lineage
    res = verify_certificate(sign_certificate(bad, world.signer))
    assert not res["valid"] and {"serving_model_hash", "lineage_complete"} <= failed(res)
    # weight file that isn't part of the certified model
    rogue, _ = write_weights(tmp_path, "rogue", b"not-the-certified-weights")
    res = verify_certificate(cert, model_files=[rogue, world.shard_paths[1]])
    assert not res["valid"] and failed(res) == {"model_files"}


def test_tampered_audit_numbers_fail(world):
    cert = world.issue()
    bad = copy.deepcopy(cert)
    bad["audit"]["summary"]["canary_exposure_bits"] = 0.01
    res = verify_certificate(bad)
    assert not res["valid"] and "certificate_signature" in failed(res)
    res = verify_certificate(sign_certificate(bad, world.signer))  # re-signed: still contradicts signed entry
    assert not res["valid"] and {"audit_anchored", "claim_consistent"} <= failed(res)


def test_audit_mismatch_with_ledger_refused(world):
    world.ledger.record_audit(world.deploy_v2["payload"]["serving_hash"], AUDIT, source_id="tenant:acme")
    with pytest.raises(CertificateError, match="audit numbers differ"):
        issue_certificate(world.ledger, "tenant:acme", {**AUDIT, "canary_exposure_bits": 0.0}, save=False)


def test_wrong_key_fails(world):
    cert = world.issue()
    mallory = Signer.generate()
    # 1) signed by another key but claiming the original key
    bad = copy.deepcopy(cert)
    bad["signature"]["value"] = mallory.sign(b"whatever")
    assert "certificate_signature" in failed(verify_certificate(bad))
    # 2) fully re-keyed: attacker swaps in their own attestation key and re-signs the certificate
    rekeyed = copy.deepcopy(cert)
    rekeyed["attestation"] = mallory.attestation()
    rekeyed = sign_certificate(rekeyed, mallory)
    res = verify_certificate(rekeyed)
    assert not res["valid"] and "ledger_entries_signed" in failed(res)  # entries were signed by the real key
    res = verify_certificate(rekeyed, trusted_public_key=world.signer.public_key_hex)
    assert "trusted_key" in failed(res)
    # 3) pinning the wrong key rejects even the honest certificate
    assert "trusted_key" in failed(verify_certificate(cert, trusted_public_key=mallory.public_key_hex))


def test_tampered_ledger_export_fails(world):
    cert = world.issue()
    export = copy.deepcopy(world.ledger.export())
    export["entries"][5]["payload"]["config"] = {"steps": 1}
    res = verify_certificate(cert, ledger_export=export)
    assert not res["valid"] and "ledger_anchor" in failed(res)


def test_unlogged_certificate_detected(world):
    cert = world.issue(record_event=False)
    res = verify_certificate(cert, ledger_export=world.ledger.export())
    assert failed(res) == {"certificate_logged"}


def test_malformed_certificate_does_not_crash(world):
    cert = world.issue()
    bad = copy.deepcopy(cert)
    del bad["non_membership"]
    res = verify_certificate(bad)
    assert not res["valid"]
    assert verify_certificate({})["valid"] is False


def test_preconditions(tmp_path):
    lg = Ledger(store=SQLiteStore(tmp_path / "l.sqlite"), signer=Signer.generate())
    with pytest.raises(CertificateError, match="not registered"):
        issue_certificate(lg, "tenant:nobody", AUDIT, save=False)
    hs = [hashlib.sha256(f"r{i}".encode()).hexdigest() for i in range(3)]
    lg.register_source("tenant:x", "tenant", "X (synthetic)", hs)
    with pytest.raises(CertificateError, match="SOURCE_REVOKED"):
        issue_certificate(lg, "tenant:x", AUDIT, save=False)
    lg.mark_revoked("tenant:x")
    with pytest.raises(CertificateError, match="MODEL_DEPLOYED"):
        issue_certificate(lg, "tenant:x", AUDIT, save=False)
    with pytest.raises(CertificateError, match="audit"):
        root = lg.create_manifest([], manifest_id="empty")["payload"]["merkle_root"]
        lg.record_training_run("0" * 64, "1" * 64, root, {})
        lg.deploy_serving(["1" * 64])
        issue_certificate(lg, "tenant:x", {"oops": 1}, save=False)
    # empty manifest -> "empty" non-membership proofs are valid
    cert = issue_certificate(lg, "tenant:x", AUDIT, save=False)
    assert verify_certificate(cert, ledger_export=lg.export())["valid"]
    assert MerkleTree(hs).root == cert["revoked_source"]["merkle_root"]


def test_cli_checklist(world, tmp_path, capsys):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import verify_certificate as cli

    cert = world.issue()
    cert_path = world.tmp / "certs" / f"{cert['certificate_id']}.json"
    export_path = tmp_path / "export.json"
    world.ledger.export(export_path)
    recs = tmp_path / "mine.jsonl"
    recs.write_text("\n".join(json.dumps(r) for r in world.records["tenant:acme"]))
    leaves_path = tmp_path / "auditor.json"
    world.ledger.export(leaves_path, include_manifest_leaves=True)
    rc = cli.main([str(cert_path), "--records", str(recs), "--ledger", str(export_path),
                   "--model", str(world.shard_paths[0]), "--pubkey", world.signer.public_key_hex,
                   "--manifest-leaves", str(leaves_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "RESULT: VALID" in out and "[FAIL]" not in out and "SIMULATED" in out
    assert "[PASS] manifest_leaves_recomputed" in out
    bad = json.loads(cert_path.read_text())
    bad["revoked_source"]["n_records"] = 11
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad))
    assert cli.main([str(bad_path)]) == 1
    assert "RESULT: INVALID" in capsys.readouterr().out
    assert cli.main([str(tmp_path / "missing.json")]) == 2


def test_hidden_ancestor_caught_only_with_ledger(world):
    """Key holder drops a recorded ancestor (base) from the lineage and declares it a 'root model'. Standalone
    verification cannot see the omission (honest limitation); the ledger export catches it."""
    cert = world.issue(record_event=False)
    forged = copy.deepcopy(cert)
    base_items = [m for m in forged["lineage"]["models"] if m["model_hash"] == world.base]
    base_root = base_items[0]["manifest"]["payload"]["merkle_root"]
    forged["lineage"]["models"] = [m for m in forged["lineage"]["models"] if m["model_hash"] != world.base]
    forged["lineage"]["root_models"] = [world.base]
    forged["non_membership"] = [b for b in forged["non_membership"] if b["manifest_root"] != base_root]
    forged = sign_certificate(forged, world.signer)
    assert verify_certificate(forged)["valid"]  # documented limitation without the ledger
    res = verify_certificate(forged, ledger_export=world.ledger.export())
    assert "root_models_unrecorded" in failed(res)


# ---------------------------------------------------------------- adversarial-review regressions

def _reissue_forged(world, forged, deploy, source_id="tenant:acme", summary=None, log=True):
    """Key-holder helper: re-point a certificate at `deploy`, re-anchor audit/head/claim, re-sign, and (optionally)
    log it, so the ONLY thing left to catch is the check under test."""
    from lethe.certificate import claim_text
    lg = world.ledger
    forged["serving_model"] = {"serving_hash": deploy["payload"]["serving_hash"],
                               "shard_model_hashes": deploy["payload"]["shard_model_hashes"],
                               "deployment_entry": deploy}
    audit_entry = lg.record_audit(deploy["payload"]["serving_hash"], summary or AUDIT, source_id=source_id)
    forged["audit"] = {**forged["audit"], "summary": audit_entry["payload"]["summary"], "audit_entry": audit_entry}
    forged["ledger"] = {**forged["ledger"], "head_hash": lg.head_hash(), "head_seq": lg.head()["seq"]}
    rs = forged["revoked_source"]
    x, y = forged["audit"]["summary"]["canary_exposure_bits"], forged["audit"]["summary"]["control_baseline_bits"]
    forged["claim"] = claim_text(rs["source_id"], rs["n_records"], rs["merkle_root"],
                                 deploy["payload"]["serving_hash"], x, y, rs["registered_entry"]["seq"])
    forged = sign_certificate(forged, world.signer)
    if log:
        lg.record_certificate(forged["certificate_id"], certificate_sha256(forged), source_id,
                              deploy["payload"]["serving_hash"])
    return forged


def test_a1_relabelled_training_run_refused_and_detected(world):
    """Attack a1: no retrain; operator appends a 2nd TRAINING_RUN claiming the OLD (acme-trained) shard-0 weights
    came from the clean manifest, then certifies them."""
    lg = world.ledger
    honest = world.issue(save=False, record_event=False)
    clean = lg.latest("MANIFEST_CREATED", manifest_id="m-shard0-v2")
    # 1) the public helper refuses a second run for an existing weights hash
    with pytest.raises(LedgerError, match="already has a TRAINING_RUN"):
        lg.record_training_run(world.base, world.old_shard0, clean["payload"]["merkle_root"], {}, run_id="FAKE")
    # 2) raw append bypasses the helper -> issuance refuses
    fake = lg.append("TRAINING_RUN", {"run_id": "run-shard0-v2-FAKE", "model_id": None, "kind": None, "shard": 0,
                                      "init_model_hash": world.base, "output_model_hash": world.old_shard0,
                                      "manifest_root": clean["payload"]["merkle_root"], "config": {}})
    deploy = lg.deploy_serving([world.old_shard0, world.shard_hashes[1]])
    with pytest.raises(CertificateError, match="TRAINING_RUN entries"):
        world.issue(save=False)
    # 3) key holder hand-builds the certificate: proofs are valid (clean manifest!) -> only the ledger catches it
    forged = copy.deepcopy(honest)
    for m in forged["lineage"]["models"]:
        if m["model_hash"] == world.shard_hashes[0]:
            m.update(model_hash=world.old_shard0, training_run=fake, manifest=clean)
    forged = _reissue_forged(world, forged, deploy)
    assert verify_certificate(forged, model_files=[world.old_shard0_path])["valid"]  # standalone cannot tell
    res = verify_certificate(forged, ledger_export=lg.export(), model_files=[world.old_shard0_path],
                             trusted_public_key=world.signer.public_key_hex,
                             record_hashes=world.records["tenant:acme"])
    assert failed(res) == {"lineage_runs_unique"}



def test_a2_reregistration_refused_and_detected(tmp_path, monkeypatch):
    """Attack a2: a revoked record leaked into the retrain manifest; operator re-registers acme without it (and
    with 0 records) to shrink the revoked set."""
    import lethe.certificate as C
    w = World(tmp_path, contaminate_retrain=True)
    lg, acme = w.ledger, w.hashes["tenant:acme"]
    leaked = acme[3]
    with pytest.raises(LedgerError, match="already registered"):
        lg.register_source("tenant:acme", "tenant", "Acme", [h for h in acme if h != leaked])
    with pytest.raises(LedgerError, match="no records"):
        lg.register_source("tenant:empty", "tenant", "Empty", [])
    # raw append bypasses the helper -> issuance refuses
    t11 = MerkleTree([h for h in acme if h != leaked])
    lg.store.put_set(t11.root, "source", t11.leaves)
    lg.append("SOURCE_REGISTERED", {"source_id": "tenant:acme", "source_type": "tenant", "display_name": "Acme",
                                    "merkle_root": t11.root, "n_records": 11, "shard": 0})
    lg.mark_revoked("tenant:acme", shard=0)
    with pytest.raises(CertificateError, match="SOURCE_REGISTERED entries"):
        w.issue(save=False)
    # operator runs the OLD issuance logic (latest registration wins) -> ledger check catches it
    orig = C._all
    monkeypatch.setattr(C, "_all", lambda es, t, **eq: orig(es, t, **eq)[-1:] if t == "SOURCE_REGISTERED"
                        else orig(es, t, **eq))
    cert = w.issue(save=False)
    monkeypatch.setattr(C, "_all", orig)
    assert "all 11 records" in cert["claim"]
    res = verify_certificate(cert, ledger_export=lg.export(), trusted_public_key=w.signer.public_key_hex)
    assert failed(res) == {"source_registration_unique"}
    w.ledger.close()


class _UnsortedTree(MerkleTree):
    """Operator-controlled pipeline: commits leaves in the given order (NOT sorted)."""

    def __init__(self, leaves):
        from lethe.ledger import _leaf_digest, _node_digest
        self.leaves = list(dict.fromkeys(leaves))
        self.levels = [[_leaf_digest(x) for x in self.leaves]]
        while len(self.levels[-1]) > 1:
            q = self.levels[-1]
            self.levels.append([_node_digest(q[i], q[i + 1]) if i + 1 < len(q) else q[i] for i in range(0, len(q), 2)])

    def index_of(self, h):
        import bisect
        i = bisect.bisect_left(self.leaves, h)
        return i if i < len(self.leaves) and self.leaves[i] == h else None

    def non_membership_proof(self, t):
        L, n = self.leaves, len(self.leaves)
        if t < L[0]:
            return {"record_hash": t, "tree_size": n, "kind": "below_min", "left": None,
                    "right": self.inclusion_proof(0)}
        if t > L[-1]:
            return {"record_hash": t, "tree_size": n, "kind": "above_max", "left": self.inclusion_proof(n - 1),
                    "right": None}
        for i in range(n - 1):
            if L[i] < t < L[i + 1]:
                return {"record_hash": t, "tree_size": n, "kind": "between", "left": self.inclusion_proof(i),
                        "right": self.inclusion_proof(i + 1)}
        raise ValueError("no bracket")


def test_a3_unsorted_manifest_refused_and_caught_with_leaves(tmp_path, monkeypatch):
    """Attack a3: retrain manifest committed as an UNSORTED tree hiding one acme record. Proofs alone cannot see it
    (documented); record_training_run refuses it, and the leaves-recompute check catches it."""
    import lethe.certificate as C
    from lethe.ledger import MERKLE_SCHEME
    w = World(tmp_path)
    lg = w.ledger
    clean = sorted(h for sid, (_, _, sh, _) in SOURCES.items() if sh == 0 and sid != "tenant:acme"
                   for h in w.hashes[sid])
    acme = sorted(w.hashes["tenant:acme"])
    tree = None
    for cand in acme:  # find a placement of a leaked acme record that keeps every needed bracket intact
        for pos in range(1, len(clean)):
            t = _UnsortedTree(clean[:pos] + [cand] + clean[pos:])
            try:
                for a in acme:
                    t.non_membership_proof(a)
                tree = t
                break
            except ValueError:
                pass
        if tree:
            break
    assert tree is not None
    lg.store.put_set(tree.root, "manifest", tree.leaves)
    lg.append("MANIFEST_CREATED", {"manifest_id": "m-shard0-v3", "merkle_root": tree.root, "n_records": tree.size,
                                   "sources": [], "shard": 0, "merkle_scheme": MERKLE_SCHEME})
    h = hashlib.sha256(b"shard0-v3-trained-with-leak").hexdigest()
    with pytest.raises(LedgerError, match="not a sorted Merkle tree"):
        lg.record_training_run(w.base, h, tree.root, {}, run_id="run-shard0-v3", shard=0)
    lg.append("TRAINING_RUN", {"run_id": "run-shard0-v3", "model_id": None, "kind": None, "shard": 0,
                               "init_model_hash": w.base, "output_model_hash": h, "manifest_root": tree.root,
                               "config": {}})
    lg.deploy_serving([h, w.shard_hashes[1]])
    monkeypatch.setattr(C, "MerkleTree", _UnsortedTree)  # operator's own issuance code
    cert = w.issue(save=False)
    monkeypatch.setattr(C, "MerkleTree", MerkleTree)
    full = dict(trusted_public_key=w.signer.public_key_hex, record_hashes=w.records["tenant:acme"])
    assert verify_certificate(cert, ledger_export=lg.export(), **full)["valid"]  # documented limitation
    res = verify_certificate(cert, ledger_export=lg.export(include_manifest_leaves=True), **full)
    assert failed(res) == {"manifest_leaves_recomputed"}
    detail = next(c["detail"] for c in res["checks"] if c["name"] == "manifest_leaves_recomputed")
    assert "do NOT reproduce" in detail and "CONTAINS 1 revoked" in detail
    w.ledger.close()


def test_a5_audit_numbers_validated(world):
    lg = world.ledger
    for bad in ({"canary_exposure_bits": "nan", "control_baseline_bits": "-inf"},
                {"canary_exposure_bits": "1.0", "control_baseline_bits": 1.5},
                {"canary_exposure_bits": True, "control_baseline_bits": 1.5},
                {"canary_exposure_bits": float("inf"), "control_baseline_bits": 1.5}):
        with pytest.raises(CertificateError, match="finite numeric"):
            issue_certificate(lg, "tenant:acme", bad, save=False)
    # fully memorised canary: issuance refuses by default...
    leak = {"canary_exposure_bits": 35.9, "control_baseline_bits": 1.5}
    with pytest.raises(CertificateError, match="contradicts removal"):
        issue_certificate(lg, "tenant:acme", leak, save=False)
    # ...and if forced, the verifier flags it (verifier uses ITS OWN margin)
    cert = issue_certificate(lg, "tenant:acme", leak, save=False, allow_audit_flag=True)
    assert cert["audit"]["consistent_with_removal"] is False
    res = verify_certificate(cert, ledger_export=lg.export())
    assert failed(res) == {"audit_consistent_with_removal"}


def test_a5_audit_of_other_source_swapped_in(world):
    cert = world.issue(record_event=False)
    forged = copy.deepcopy(cert)
    other = world.ledger.record_audit(cert["serving_model"]["serving_hash"],
                                      {"canary_exposure_bits": 0.1, "control_baseline_bits": 1.5},
                                      source_id="tenant:globex")
    forged["audit"] = {**forged["audit"], "summary": other["payload"]["summary"], "audit_entry": other}
    from lethe.certificate import claim_text
    rs = forged["revoked_source"]
    forged["claim"] = claim_text(rs["source_id"], rs["n_records"], rs["merkle_root"],
                                 cert["serving_model"]["serving_hash"], 0.1, 1.5, rs["registered_entry"]["seq"])
    forged["ledger"] = {**forged["ledger"], "head_hash": world.ledger.head_hash(),
                        "head_seq": world.ledger.head()["seq"]}
    res = verify_certificate(sign_certificate(forged, world.signer), ledger_export=world.ledger.export())
    assert "audit_anchored" in failed(res)


def test_a9_non_serving_deployment_does_not_break_issuance(world, tmp_path):
    lg = world.ledger
    lg.deploy_serving([hashlib.sha256(b"pooled").hexdigest()], model_id="pooled")
    cert = world.issue()
    assert cert["serving_model"]["deployment_entry"]["seq"] == world.deploy_v2["seq"]
    lg.deploy_serving([hashlib.sha256(b"pooled-unlearned").hexdigest()], model_id="pooled_unlearned")
    res = verify_certificate(cert, ledger_export=lg.export())
    assert res["valid"], failed(res)


def test_a4_exact_bytes_scope_and_nfc():
    import unicodedata
    from lethe.certificate import OUT_OF_SCOPE
    r = {"id": "acme-1", "source_id": "tenant:acme", "text": "café résumé"}
    nfd = {k: unicodedata.normalize("NFD", v) for k, v in r.items()}
    assert nfd != r and record_hash(nfd) == record_hash(r)
    assert any("exact record hashes" in n and "other records or sources" in n for n in OUT_OF_SCOPE)


def test_entries_from_a_forked_ledger_rejected_standalone(world, tmp_path):
    """Same key, second store: an entry from the fork cannot be spliced into a certificate, even re-signed."""
    cert = world.issue(record_event=False)
    fork = Ledger(store=SQLiteStore(tmp_path / "fork.sqlite"), signer=world.signer)
    fe = fork.append("AUDIT_COMPLETED", cert["audit"]["audit_entry"]["payload"])  # same payload, other ledger
    forged = copy.deepcopy(cert)
    forged["audit"]["audit_entry"] = fe  # validly hashed + signed by the same key
    forged = sign_certificate(forged, world.signer)
    assert cert["ledger"]["ledger_id"] == world.ledger.ledger_id != fork.ledger_id
    res = verify_certificate(forged)
    assert failed(res) == {"ledger_entries_signed"}
    assert "belongs to ledger" in next(c["detail"] for c in res["checks"] if c["name"] == "ledger_entries_signed")
    fork.close()


def test_low_severity_hardening(world, tmp_path):
    # path traversal via certificate_id
    with pytest.raises(CertificateError, match="unsafe certificate_id"):
        world.issue(certificate_id="../../ESCAPED")
    assert not (tmp_path.parent / "ESCAPED.json").exists()
    cert = world.issue()
    # malformed customer input -> failed check, never a crash
    res = verify_certificate(cert, record_hashes=["not-a-hash", {"text": "\ud800"}])
    assert not res["valid"] and "customer_records_parsed" in failed(res)
    # key holder strips the out-of-scope notes / relabels the signature block -> caught
    stripped = sign_certificate({**copy.deepcopy(cert), "out_of_scope": []}, world.signer)
    assert "claim_consistent" in failed(verify_certificate(stripped))
    relabel = copy.deepcopy(cert)
    relabel["signature"]["signer"] = "REAL_TEE"
    assert "certificate_signature" in failed(verify_certificate(relabel))
    # pinned key given in uppercase still matches; export relabelled as non-simulated fails
    assert verify_certificate(cert, trusted_public_key=world.signer.public_key_hex.upper())["valid"]
    exp = world.ledger.export()
    exp["simulated"] = False
    assert "ledger_anchor" in failed(verify_certificate(cert, ledger_export=exp))
