"""Unit tests for lethe.ledger: canonical hashing, Merkle proofs, signing, hash chain, storage."""

import copy
import hashlib
import json
import random
import sqlite3

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root (no pytest pythonpath config yet)

from lethe.ledger import (
    EMPTY_ROOT,
    GENESIS_PREV_HASH,
    SIGNER_LABEL,
    Ledger,
    LedgerError,
    MerkleTree,
    Signer,
    SQLiteStore,
    canonical_json,
    compute_entry_hash,
    entry_signing_message,
    record_hash,
    serving_hash,
    verify_chain,
    verify_export,
    verify_inclusion,
    verify_non_membership,
)


def H(s) -> str:
    return hashlib.sha256(str(s).encode()).hexdigest()


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(store=SQLiteStore(tmp_path / "ledger.sqlite"), signer=Signer.load_or_create(tmp_path / "k.pem"))
    yield lg
    lg.close()


# ---------------------------------------------------------------- canonical hashing

def test_record_hash_is_canonical():
    a = {"id": "acme-1", "source_id": "tenant:acme", "text": "héllo"}
    b = {"text": "héllo", "source_id": "tenant:acme", "id": "acme-1"}
    assert record_hash(a) == record_hash(b)
    assert canonical_json(a) == b'{"id":"acme-1","source_id":"tenant:acme","text":"h\xc3\xa9llo"}'
    assert record_hash(a) != record_hash({**a, "text": "hello"})


def test_serving_hash_order_independent():
    hs = [H(i) for i in range(4)]
    assert serving_hash(hs) == serving_hash(list(reversed(hs)))
    assert serving_hash(hs) != serving_hash(hs[:3])


# ---------------------------------------------------------------- Merkle

@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 9, 16, 33])
def test_inclusion_proofs_all_sizes(n):
    t = MerkleTree(H(i) for i in range(n))
    for i in range(n):
        p = t.inclusion_proof(i)
        assert verify_inclusion(p["leaf"], p["index"], n, p["path"], t.root)
        # proof is bound to its index (for the committed size; the size itself comes from the signed
        # MANIFEST_CREATED entry, never from the proof -- as in RFC 6962, a left-subtree path can look the same
        # for several sizes)
        assert not verify_inclusion(p["leaf"], (i + 1) % n, n, p["path"], t.root) or n == 1
        assert not verify_inclusion(p["leaf"], i, n, p["path"][:-1], t.root) or n == 1
        assert not verify_inclusion(p["leaf"], i, n, p["path"] + [H("x")], t.root)


def test_root_is_set_semantics_sorted_and_deduped():
    hs = [H(i) for i in range(10)]
    shuffled = hs[:]
    random.Random(0).shuffle(shuffled)
    assert MerkleTree(hs).root == MerkleTree(shuffled).root == MerkleTree(hs + hs[:3]).root
    assert MerkleTree(hs + hs[:3]).size == 10
    assert MerkleTree([h.upper() for h in hs]).root == MerkleTree(hs).root


def test_empty_tree():
    t = MerkleTree([])
    assert t.root == EMPTY_ROOT and t.size == 0
    p = t.non_membership_proof(H("x"))
    assert p["kind"] == "empty"
    assert verify_non_membership(H("x"), p, EMPTY_ROOT, 0)[0]
    assert not verify_non_membership(H("x"), p, MerkleTree([H(1)]).root, 0)[0]


def test_domain_separation_leaf_vs_node():
    # A two-leaf tree's root must not equal a one-leaf tree whose leaf is the concatenation's hash, and an
    # internal node cannot be presented as a leaf.
    a, b = H("a"), H("b")
    t2 = MerkleTree([a, b])
    internal = t2.root
    t1 = MerkleTree([internal])
    assert t1.root != t2.root
    assert not verify_inclusion(internal, 0, 1, [], t2.root)


def _sorted_leaves(n, seed=1):
    return sorted(H(f"{seed}-{i}") for i in range(n))


def test_non_membership_single_leaf_below_and_above():
    leaf = "8" + "0" * 63
    t = MerkleTree([leaf])
    lo, hi = "0" * 64, "f" * 64
    plo, phi = t.non_membership_proof(lo), t.non_membership_proof(hi)
    assert plo["kind"] == "below_min" and phi["kind"] == "above_max"
    assert verify_non_membership(lo, plo, t.root, 1)[0]
    assert verify_non_membership(hi, phi, t.root, 1)[0]
    with pytest.raises(ValueError):
        t.non_membership_proof(leaf)


@pytest.mark.parametrize("n", [2, 3, 5, 8, 13, 64])
def test_non_membership_between_min_max(n):
    t = MerkleTree(_sorted_leaves(n))
    rng = random.Random(n)
    targets = ["0" * 64, "f" * 64] + [H(f"absent-{rng.random()}") for _ in range(25)]
    for x in targets:
        if x in t:
            continue
        p = t.non_membership_proof(x)
        ok, why = verify_non_membership(x, p, t.root, n)
        assert ok, why
        # the same proof does not work for a different target outside the gap / a different root
        assert not verify_non_membership(x, p, MerkleTree(_sorted_leaves(n, seed=2)).root, n)[0]


def test_non_membership_rejects_member_and_forgeries():
    leaves = _sorted_leaves(9)
    t = MerkleTree(leaves)
    member = leaves[4]
    with pytest.raises(ValueError):
        t.non_membership_proof(member)
    # Forgery 1: reuse a genuine gap proof around the member's position but claim the member.
    gap = t.non_membership_proof(H("zzz"))
    gap["record_hash"] = member
    assert not verify_non_membership(member, gap, t.root, 9)[0]
    # Forgery 2: non-adjacent neighbours (skip the member) with genuine inclusion proofs.
    forged = {"record_hash": member, "tree_size": 9, "kind": "between",
              "left": t.inclusion_proof(3), "right": t.inclusion_proof(5)}
    ok, why = verify_non_membership(member, forged, t.root, 9)
    assert not ok and "adjacent" in why
    # Forgery 3: proof built from a tree WITHOUT the member, checked against the real root.
    t_without = MerkleTree([h for h in leaves if h != member])
    p = t_without.non_membership_proof(member)
    assert not verify_non_membership(member, p, t.root, 9)[0]
    assert not verify_non_membership(member, p, t.root, 8)[0]


def test_non_membership_wrong_tree_size_and_garbage():
    t = MerkleTree(_sorted_leaves(6))
    x = H("not-there")
    p = t.non_membership_proof(x)
    assert not verify_non_membership(x, p, t.root, 7)[0]
    assert not verify_non_membership(x, {"kind": "between"}, t.root, 6)[0]
    assert not verify_non_membership(x, {**p, "kind": "weird"}, t.root, 6)[0]


# ---------------------------------------------------------------- signing + chain

def _populate(lg: Ledger, n=6):
    for i in range(n):
        lg.append("AUDIT_COMPLETED", {"model_hash": H(i), "summary": {"i": i, "x": 1.5}})
    return lg.entries()


def test_append_and_verify_chain(ledger):
    entries = _populate(ledger)
    assert [e["seq"] for e in entries] == list(range(1, 7))
    assert entries[0]["prev_hash"] == GENESIS_PREV_HASH
    assert all(e["signer"] == SIGNER_LABEL for e in entries)
    assert all(entries[i]["prev_hash"] == entries[i - 1]["entry_hash"] for i in range(1, 6))
    res = ledger.verify_chain()
    assert res["valid"], res["errors"]
    assert res["head_hash"] == ledger.head_hash()


def test_unknown_event_type_rejected(ledger):
    with pytest.raises(LedgerError):
        ledger.append("NOT_A_TYPE", {})


def test_tamper_edit_payload(ledger):
    entries = copy.deepcopy(_populate(ledger))
    pk = ledger.public_key_hex
    entries[2]["payload"]["summary"]["x"] = 0.0
    res = verify_chain(entries, pk)
    assert not res["valid"] and any("edited" in e for e in res["errors"])
    # Re-hashing without the key -> signature fails AND the next link breaks.
    entries[2]["entry_hash"] = compute_entry_hash(entries[2])
    res = verify_chain(entries, pk)
    assert not res["valid"]
    assert any("bad signature" in e for e in res["errors"])
    assert any("prev_hash" in e for e in res["errors"])


def test_tamper_delete_entry(ledger):
    entries = copy.deepcopy(_populate(ledger))
    del entries[3]
    res = verify_chain(entries, ledger.public_key_hex)
    assert not res["valid"] and any("seq" in e for e in res["errors"])


def test_tamper_reorder(ledger):
    entries = copy.deepcopy(_populate(ledger))
    entries[1], entries[2] = entries[2], entries[1]
    assert not verify_chain(entries, ledger.public_key_hex)["valid"]


def test_tamper_forged_signature(ledger, tmp_path):
    entries = copy.deepcopy(_populate(ledger))
    mallory = Signer.generate()
    entries[4]["payload"]["summary"]["x"] = 99.0
    entries[4]["entry_hash"] = compute_entry_hash(entries[4])
    entries[4]["signature"] = mallory.sign(entry_signing_message(entries[4]["entry_hash"]))
    res = verify_chain(entries, ledger.public_key_hex)
    assert not res["valid"] and any("bad signature" in e for e in res["errors"])
    # Whole chain re-signed by another key is valid only under THAT key -> pin the key.
    assert not verify_chain(ledger.entries(), mallory.public_key_hex)["valid"]


def test_truncation_detected_only_with_anchor(ledger):
    entries = _populate(ledger)
    head = entries[-1]["entry_hash"]
    assert verify_chain(entries[:-2], ledger.public_key_hex)["valid"]  # honest limitation
    res = verify_chain(entries[:-2], ledger.public_key_hex, expected_head=head)
    assert not res["valid"] and any("truncated" in e for e in res["errors"])


def test_sqlite_is_append_only_and_tamper_detected(ledger, tmp_path):
    _populate(ledger)
    conn = sqlite3.connect(tmp_path / "ledger.sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE ledger SET payload = '{}' WHERE seq = 2")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM ledger WHERE seq = 2")
    # An attacker with file access can drop the triggers -- the hash chain still catches the edit.
    conn.execute("DROP TRIGGER ledger_no_update")
    conn.execute("UPDATE ledger SET payload = ? WHERE seq = 2", (json.dumps({"model_hash": H(0), "summary": {}}),))
    conn.commit()
    conn.close()
    assert not ledger.verify_chain()["valid"]


def test_key_persistence(tmp_path):
    a = Signer.load_or_create(tmp_path / "keys" / "k.pem")
    b = Signer.load_or_create(tmp_path / "keys" / "k.pem")
    assert a.public_key_hex == b.public_key_hex
    assert (tmp_path / "keys" / "k.pub").read_text().strip() == a.public_key_hex
    assert (tmp_path / "keys" / "k.pem").stat().st_mode & 0o077 == 0
    att = a.attestation()
    assert att["type"] == SIGNER_LABEL and att["simulated"] is True


def test_export_import_roundtrip(ledger, tmp_path):
    _populate(ledger)
    exp = ledger.export(tmp_path / "export.json")
    loaded = json.loads((tmp_path / "export.json").read_text())
    assert verify_export(loaded)["valid"]
    assert verify_export(loaded, trusted_public_key=ledger.public_key_hex)["valid"]
    assert not verify_export(loaded, trusted_public_key=Signer.generate().public_key_hex)["valid"]
    other = Ledger(store=SQLiteStore(tmp_path / "copy.sqlite"), signer=ledger.signer)
    assert other.import_entries(exp) == 6
    assert other.head_hash() == ledger.head_hash()
    other.append("AUDIT_COMPLETED", {"model_hash": H("z"), "summary": {}})
    assert other.verify_chain()["valid"]
    bad = copy.deepcopy(exp)
    bad["entries"][0]["payload"]["summary"]["i"] = 7
    assert not verify_export(bad)["valid"]
    with pytest.raises(LedgerError):
        Ledger(store=SQLiteStore(tmp_path / "copy2.sqlite"), signer=ledger.signer).import_entries(bad)


def test_typed_helpers(ledger):
    recs = [H(i) for i in range(5)]
    reg = ledger.register_source("tenant:acme", "tenant", "Acme (synthetic)", recs, shard=0)
    assert reg["payload"]["merkle_root"] == MerkleTree(recs).root and reg["payload"]["n_records"] == 5
    assert ledger.get_set(reg["payload"]["merkle_root"]) == sorted(recs)
    man = ledger.create_manifest(recs, manifest_id="m0", sources=["tenant:acme"], shard=0)
    with pytest.raises(LedgerError):
        ledger.record_training_run(H("base"), H("out"), H("unknown-manifest"), {})
    run = ledger.record_training_run(H("base"), H("s0"), man["payload"]["merkle_root"], {"lr": 1e-3}, shard=0)
    assert run["payload"]["manifest_root"] == man["payload"]["merkle_root"]
    dep = ledger.deploy_serving([H("s0"), H("s1")])
    assert dep["payload"]["serving_hash"] == serving_hash([H("s1"), H("s0")])
    ledger.request_revocation("tenant:acme", requested_by="acme-admin")
    rv = ledger.mark_revoked("tenant:acme", shard=0)
    assert rv["payload"]["merkle_root"] == reg["payload"]["merkle_root"]
    assert ledger.revoked_sources() == {"tenant:acme"}
    with pytest.raises(LedgerError):
        ledger.mark_revoked("tenant:nobody")
    assert [e["type"] for e in ledger.recent(2)] == ["SOURCE_REVOKED", "REVOCATION_REQUESTED"]
    assert ledger.verify_chain()["valid"]


# ---------------------------------------------------------------- adversarial-review regressions

def test_register_source_once_and_nonempty(ledger):
    hs = [H(i) for i in range(4)]
    ledger.register_source("tenant:a", "tenant", "A (synthetic)", hs)
    with pytest.raises(LedgerError, match="already registered"):
        ledger.register_source("tenant:a", "tenant", "A (synthetic)", hs[:3])  # would shrink the revoked set
    with pytest.raises(LedgerError, match="no records"):
        ledger.register_source("tenant:b", "tenant", "B (synthetic)", [])


def test_training_run_unique_output_and_sorted_manifest(ledger):
    from lethe.ledger import MERKLE_SCHEME, _leaf_digest, _node_digest
    root = ledger.create_manifest([H(i) for i in range(5)], manifest_id="m1")["payload"]["merkle_root"]
    ledger.record_training_run(H("init"), H("out"), root, {})
    with pytest.raises(LedgerError, match="already has a TRAINING_RUN"):  # relabelling the same weights
        ledger.record_training_run(H("init"), H("out"), root, {}, run_id="again")
    with pytest.raises(LedgerError, match="must produce new weights"):
        ledger.record_training_run(H("x"), H("x"), root, {})
    # manifest committed by raw append with NO stored leaves -> refused
    ledger.append("MANIFEST_CREATED", {"manifest_id": "ghost", "merkle_root": H("ghost-root"), "n_records": 3})
    with pytest.raises(LedgerError, match="not stored"):
        ledger.record_training_run(H("init"), H("out2"), H("ghost-root"), {})
    # UNSORTED tree committed by raw append with its leaves stored -> refused (sorted root differs)
    leaves = [H(i) for i in range(4)][::-1]
    lv = [_leaf_digest(x) for x in leaves]
    uroot = _node_digest(_node_digest(lv[0], lv[1]), _node_digest(lv[2], lv[3])).hex()
    assert uroot != MerkleTree(leaves).root
    ledger.store.put_set(uroot, "manifest", leaves)
    ledger.append("MANIFEST_CREATED", {"manifest_id": "unsorted", "merkle_root": uroot, "n_records": 4,
                                       "merkle_scheme": MERKLE_SCHEME})
    with pytest.raises(LedgerError, match="not a sorted Merkle tree"):
        ledger.record_training_run(H("init"), H("out3"), uroot, {})


def test_postgres_fallback_is_opt_in(tmp_path, monkeypatch):
    """An unreachable LETHE_DATABASE_URL must not silently fork the ledger into SQLite under the same key."""
    from lethe.ledger import open_store
    monkeypatch.setenv("LETHE_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nope?connect_timeout=2")
    monkeypatch.setenv("LETHE_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("LETHE_DB_FALLBACK", raising=False)
    with pytest.raises(Exception):
        open_store()
    monkeypatch.setenv("LETHE_DB_FALLBACK", "1")
    with pytest.warns(RuntimeWarning, match="fork"):
        store = open_store()
    assert store.backend == "sqlite"
    store.close()


def test_ledger_id_separates_forks_under_one_key(tmp_path):
    """Two stores, one key (e.g. Postgres + a SQLite fallback): both have a valid seq 1, but entries carry their
    ledger's genesis id, so splicing one fork's entries into the other's chain is detected."""
    signer = Signer.load_or_create(tmp_path / "k.pem")
    a = Ledger(store=SQLiteStore(tmp_path / "a.sqlite"), signer=signer)
    b = Ledger(store=SQLiteStore(tmp_path / "b.sqlite"), signer=signer)
    for lg in (a, b):
        lg.append("SOURCE_REGISTERED", {"source_id": "tenant:x"})
        lg.append("SOURCE_REVOKED", {"source_id": "tenant:x"})
    assert a.ledger_id and b.ledger_id and a.ledger_id != b.ledger_id
    assert a.export()["ledger_id"] == a.ledger_id
    assert all(e["ledger_id"] == a.ledger_id for e in a.entries())
    ea, eb = a.entries(), b.entries()
    # splice b's seq 2 after a's seq 1 with a forged prev_hash link: still caught via ledger_id (hash covers it)
    spliced = [ea[0], {**eb[1], "prev_hash": ea[0]["entry_hash"]}]
    res = verify_chain(spliced, signer.public_key_hex)
    assert not res["valid"] and any("ledger_id" in x or "edited" in x for x in res["errors"])
    res = verify_chain([ea[0], eb[1]], signer.public_key_hex)
    assert not res["valid"] and any("another ledger" in x for x in res["errors"])
    a.close()
    b.close()


def test_inclusion_rejects_bool_index():
    t = MerkleTree([H(i) for i in range(4)])
    p = t.inclusion_proof(1)
    assert verify_inclusion(p["leaf"], 1, 4, p["path"], t.root)
    assert not verify_inclusion(p["leaf"], True, 4, p["path"], t.root)
