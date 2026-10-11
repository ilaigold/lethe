"""Lethe lineage ledger: canonical hashing, Merkle trees, ed25519 signing, hash-chained event log.

Pure Python + `cryptography` (+ `psycopg` only when LETHE_DATABASE_URL is set). No torch.

Trust model (read this before claiming anything):
  * Every ledger entry and every certificate is signed with ONE ed25519 key stored on local disk under
    artifacts/keys/. That key is a stand-in for a TEE attestation key and is labelled
    ``SIMULATED_TEE_ATTESTATION`` everywhere (entries, exports, certificates).
  * SIMULATED_TEE_ATTESTATION -- what a real deployment uses instead: the trainer runs inside a confidential
    VM (GCP Confidential Space / Azure confidential VMs with NVIDIA H100 confidential computing, or an AWS
    Nitro Enclave). The signing key is generated *inside* the enclave and never leaves it; the hardware
    attestation report (SEV-SNP/TDX quote + H100 GPU attestation, or a Nitro attestation document) binds the
    public key to a measurement (hash) of the exact training/ledger code. A verifier then checks the vendor's
    attestation certificate chain + expected code measurement, and only then trusts the key. Here, anyone
    with read access to the key file can sign anything; the hash chain still makes *after-the-fact* edits to
    a published ledger detectable.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import os
import sqlite3
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

# --------------------------------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------------------------------

SIGNER_LABEL = "SIMULATED_TEE_ATTESTATION"
GENESIS_PREV_HASH = "0" * 64
EXPORT_FORMAT = "lethe-ledger-export-v1"
MERKLE_SCHEME = "lethe-sorted-merkle-sha256-v1"  # sorted, deduped leaves; 0x00 leaf / 0x01 node prefixes

# Signature domain separation: an entry signature can never be replayed as a certificate signature.
ENTRY_SIG_DOMAIN = b"LETHE-LEDGER-ENTRY-v1\x00"
CERT_SIG_DOMAIN = b"LETHE-CERTIFICATE-v1\x00"

EVENT_TYPES = (
    "SOURCE_REGISTERED",
    "MANIFEST_CREATED",
    "TRAINING_RUN",
    "MODEL_DEPLOYED",
    "REVOCATION_REQUESTED",
    "SOURCE_REVOKED",
    "AUDIT_COMPLETED",
    "CERTIFICATE_ISSUED",
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def artifacts_dir() -> Path:
    return Path(os.environ.get("LETHE_ARTIFACTS_DIR", PROJECT_ROOT / "artifacts"))


def default_key_path() -> Path:
    return artifacts_dir() / "keys" / "ledger_ed25519.pem"


def default_sqlite_path() -> Path:
    return artifacts_dir() / "ledger.sqlite"


# --------------------------------------------------------------------------------------------------------------
# Canonical hashing
# --------------------------------------------------------------------------------------------------------------


def canonical_json(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, UTF-8. NaN/Infinity are rejected (not valid JSON)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nfc(obj: Any) -> Any:
    """Unicode-normalise every string (keys and values) to NFC so visually identical records hash the same."""
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return {_nfc(k): _nfc(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_nfc(v) for v in obj]
    return obj


def record_hash(record: dict) -> str:
    """sha256 of the canonical JSON of the FULL record (id, source_id, source_type, display_name, text, ...),
    after NFC-normalising every string. Exclusion is defined over these exact hashes: the same or near-duplicate
    content stored under another id/source (or differing in anything but Unicode normal form, e.g. 1 vs 1.0,
    whitespace) is a DIFFERENT record and is not covered by a certificate."""
    return sha256_hex(canonical_json(_nfc(record)))


def file_sha256(path: str | os.PathLike) -> str:
    """Model hash = sha256 of the saved weights file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def serving_hash(shard_model_hashes: Iterable[str]) -> str:
    """Serving hash = sha256 of canonical JSON of the SORTED list of shard model hashes (lowercase hex)."""
    return sha256_hex(canonical_json(sorted(_norm_hash(h) for h in shard_model_hashes)))


def _norm_hash(h: str) -> str:
    if not isinstance(h, str):
        raise ValueError(f"hash must be a hex string, got {type(h).__name__}")
    h = h.strip().lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"not a sha256 hex digest: {h[:80]!r}")
    return h


# --------------------------------------------------------------------------------------------------------------
# Merkle trees (sorted, deduplicated, domain-separated)
# --------------------------------------------------------------------------------------------------------------
# leaf node     = sha256(0x00 || leaf_bytes)
# internal node = sha256(0x01 || left || right)
# An odd node at the end of a level is promoted unchanged (no duplication -> no CVE-2012-2459-style ambiguity).
# Proofs carry (index, tree_size) and the verifier re-derives the path shape from them, so a proof cannot
# "relocate" a leaf -- this is what makes adjacency (index i, i+1) meaningful for non-membership.
# Empty tree root = sha256(b"") (as in RFC 6962).

_LEAF = b"\x00"
_NODE = b"\x01"
EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _leaf_digest(leaf_hex: str) -> bytes:
    return hashlib.sha256(_LEAF + bytes.fromhex(leaf_hex)).digest()


def _node_digest(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(_NODE + left + right).digest()


class MerkleTree:
    """Sorted Merkle tree over sha256 hex digests. Leaves are lowercased, deduplicated and sorted, so the
    root depends only on the *set* of hashes (order-independent) and supports non-membership proofs."""

    def __init__(self, leaves: Iterable[str]):
        self.leaves: list[str] = sorted({_norm_hash(x) for x in leaves})
        self.levels: list[list[bytes]] = [[_leaf_digest(x) for x in self.leaves]]
        while len(self.levels[-1]) > 1:
            prev = self.levels[-1]
            nxt = [
                _node_digest(prev[i], prev[i + 1]) if i + 1 < len(prev) else prev[i] for i in range(0, len(prev), 2)
            ]
            self.levels.append(nxt)

    @property
    def size(self) -> int:
        return len(self.leaves)

    @property
    def root(self) -> str:
        return self.levels[-1][0].hex() if self.leaves else EMPTY_ROOT

    def __contains__(self, h: str) -> bool:
        return self.index_of(h) is not None

    def index_of(self, h: str) -> int | None:
        h = _norm_hash(h)
        i = bisect.bisect_left(self.leaves, h)
        return i if i < len(self.leaves) and self.leaves[i] == h else None

    def inclusion_proof(self, index: int) -> dict:
        if not 0 <= index < self.size:
            raise IndexError(index)
        path: list[str] = []
        idx = index
        for level in self.levels[:-1]:
            if idx % 2 == 1:
                path.append(level[idx - 1].hex())
            elif idx + 1 < len(level):
                path.append(level[idx + 1].hex())
            idx //= 2
        return {"leaf": self.leaves[index], "index": index, "path": path}

    def non_membership_proof(self, target: str) -> dict:
        """Adjacent-leaf proof that `target` is NOT a leaf. Raises ValueError if it is a member."""
        t = _norm_hash(target)
        n = self.size
        if n == 0:
            return {"record_hash": t, "tree_size": 0, "kind": "empty", "left": None, "right": None}
        i = bisect.bisect_left(self.leaves, t)
        if i < n and self.leaves[i] == t:
            raise ValueError(f"{t} IS a member of this tree (index {i}); no non-membership proof exists")
        if i == 0:
            return {"record_hash": t, "tree_size": n, "kind": "below_min", "left": None,
                    "right": self.inclusion_proof(0)}
        if i == n:
            return {"record_hash": t, "tree_size": n, "kind": "above_max", "left": self.inclusion_proof(n - 1),
                    "right": None}
        return {"record_hash": t, "tree_size": n, "kind": "between", "left": self.inclusion_proof(i - 1),
                "right": self.inclusion_proof(i)}


def merkle_root(leaves: Iterable[str]) -> str:
    return MerkleTree(leaves).root


def verify_inclusion(leaf: str, index: int, tree_size: int, path: list[str], root: str) -> bool:
    """Verify an inclusion proof; the path shape is re-derived from (index, tree_size)."""
    try:
        if not (type(index) is int and type(tree_size) is int and 0 <= index < tree_size):  # no bools
            return False
        h = _leaf_digest(_norm_hash(leaf))
        idx, n, used = index, tree_size, 0
        while n > 1:
            if idx % 2 == 1:
                if used >= len(path):
                    return False
                h = _node_digest(bytes.fromhex(path[used]), h)
                used += 1
            elif idx + 1 < n:
                if used >= len(path):
                    return False
                h = _node_digest(h, bytes.fromhex(path[used]))
                used += 1
            idx //= 2
            n = (n + 1) // 2
        return used == len(path) and h.hex() == _norm_hash(root)
    except (ValueError, TypeError):
        return False


def verify_non_membership(target: str, proof: dict, root: str, tree_size: int) -> tuple[bool, str]:
    """Verify an adjacent-leaf non-membership proof against a (root, tree_size) commitment.

    Soundness assumes the committed tree was built sorted (MerkleTree always sorts; anyone holding the leaves
    can re-check by recomputing the root)."""
    try:
        t = _norm_hash(target)
        if proof.get("record_hash") is not None and _norm_hash(proof["record_hash"]) != t:
            return False, "proof is for a different record hash"
        if proof.get("tree_size") != tree_size:
            return False, f"proof tree_size {proof.get('tree_size')} != committed size {tree_size}"
        kind, left, right = proof.get("kind"), proof.get("left"), proof.get("right")
        if tree_size == 0:
            ok = kind == "empty" and _norm_hash(root) == EMPTY_ROOT
            return ok, "empty manifest" if ok else "claimed empty but root is not the empty root"

        def incl(p: dict) -> bool:
            return verify_inclusion(p["leaf"], p["index"], tree_size, p["path"], root)

        if kind == "below_min":
            if not (right and right["index"] == 0 and t < _norm_hash(right["leaf"]) and incl(right)):
                return False, "invalid below-min proof"
            return True, "below smallest leaf"
        if kind == "above_max":
            if not (left and left["index"] == tree_size - 1 and t > _norm_hash(left["leaf"]) and incl(left)):
                return False, "invalid above-max proof"
            return True, "above largest leaf"
        if kind == "between":
            if not (left and right and right["index"] == left["index"] + 1):
                return False, "neighbours are not adjacent"
            if not (_norm_hash(left["leaf"]) < t < _norm_hash(right["leaf"])):
                return False, "target is not strictly between neighbours"
            if not (incl(left) and incl(right)):
                return False, "neighbour inclusion proof does not match root"
            return True, f"between leaves {left['index']} and {right['index']}"
        return False, f"unknown proof kind {kind!r}"
    except (KeyError, TypeError, ValueError) as e:
        return False, f"malformed proof: {e}"


# --------------------------------------------------------------------------------------------------------------
# Signing (SIMULATED_TEE_ATTESTATION)
# --------------------------------------------------------------------------------------------------------------


class Signer:
    """ed25519 signer. SIMULATED_TEE_ATTESTATION: a local key file standing in for an enclave-held key whose
    public half would be bound to a code measurement by a hardware attestation report (see module docstring)."""

    label = SIGNER_LABEL
    simulated = True

    def __init__(self, private_key: Ed25519PrivateKey):
        self._sk = private_key
        self.public_key_hex = (
            private_key.public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            .hex()
        )

    @property
    def key_id(self) -> str:
        return sha256_hex(bytes.fromhex(self.public_key_hex))[:16]

    @classmethod
    def generate(cls) -> "Signer":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load_or_create(cls, path: str | os.PathLike | None = None) -> "Signer":
        path = Path(path) if path else default_key_path()
        if path.exists():
            sk = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(sk, Ed25519PrivateKey):
                raise ValueError(f"{path} is not an ed25519 key")
            return cls(sk)
        path.parent.mkdir(parents=True, exist_ok=True)
        sk = Ed25519PrivateKey.generate()
        pem = sk.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        signer = cls(sk)
        path.with_suffix(".pub").write_text(signer.public_key_hex + "\n")
        return signer

    def sign(self, message: bytes) -> str:
        return self._sk.sign(message).hex()

    def attestation(self) -> dict:
        return {
            "type": SIGNER_LABEL,
            "simulated": True,
            "alg": "ed25519",
            "public_key": self.public_key_hex,
            "key_id": self.key_id,
            "note": (
                "SIMULATED: signing key is a local file, not bound to hardware. Production: key generated inside "
                "a confidential VM / H100 CC / AWS Nitro enclave, with an attestation report binding the public "
                "key to the measured training code."
            ),
        }


def verify_signature(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pk.verify(bytes.fromhex(signature_hex), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# --------------------------------------------------------------------------------------------------------------
# Ledger entries + chain verification
# --------------------------------------------------------------------------------------------------------------


def entry_body(entry: dict) -> dict:
    """Hashed fields. `ledger_id` (a random id fixed at genesis and copied into every entry) identifies WHICH ledger
    an entry belongs to, so entries of two forks signed by the same key cannot be mixed; it is omitted from the
    body only for legacy entries that never had one."""
    body = {k: entry[k] for k in ("seq", "ts", "type", "payload", "prev_hash")}
    if entry.get("ledger_id") is not None:
        body["ledger_id"] = entry["ledger_id"]
    return body


def compute_entry_hash(entry: dict) -> str:
    return sha256_hex(canonical_json(entry_body(entry)))


def entry_signing_message(entry_hash: str) -> bytes:
    return ENTRY_SIG_DOMAIN + bytes.fromhex(entry_hash)


def verify_entry(entry: dict, public_key_hex: str) -> tuple[bool, str]:
    """Check one entry in isolation: hash recomputes, signer label, signature."""
    try:
        if compute_entry_hash(entry) != entry["entry_hash"]:
            return False, f"seq {entry.get('seq')}: entry_hash does not match contents (edited)"
        if entry.get("signer") != SIGNER_LABEL:
            return False, f"seq {entry.get('seq')}: unexpected signer label {entry.get('signer')!r}"
        if not verify_signature(public_key_hex, entry_signing_message(entry["entry_hash"]), entry["signature"]):
            return False, f"seq {entry.get('seq')}: bad signature"
        return True, "ok"
    except (KeyError, TypeError, ValueError) as e:
        return False, f"malformed entry: {e}"


def verify_chain(entries: list[dict], public_key_hex: str, expected_head: str | None = None) -> dict:
    """Verify an append-only chain: contiguous seq from 1, prev_hash links, recomputed entry hashes, signatures.
    Detects edits, deletions, insertions and reordering. Tail truncation is only detectable against a
    previously published head (`expected_head` = an entry_hash that must be present)."""
    errors: list[str] = []
    prev = GENESIS_PREV_HASH
    lid = entries[0].get("ledger_id") if entries else None
    for i, e in enumerate(entries):
        if e.get("ledger_id") != lid:
            errors.append(f"seq {e.get('seq')}: ledger_id {e.get('ledger_id')!r} != genesis ledger_id {lid!r} "
                          f"(entries from another ledger)")
        expect_seq = i + 1
        if e.get("seq") != expect_seq:
            errors.append(f"position {i}: seq {e.get('seq')} != expected {expect_seq} (deleted/reordered entry)")
        if e.get("prev_hash") != prev:
            errors.append(f"seq {e.get('seq')}: prev_hash does not link to previous entry (chain broken)")
        ok, detail = verify_entry(e, public_key_hex)
        if not ok:
            errors.append(detail)
        prev = e.get("entry_hash", "")
    if expected_head is not None and expected_head not in {e.get("entry_hash") for e in entries}:
        errors.append(f"expected head {expected_head[:16]}... not present (truncated or forked ledger)")
    return {
        "valid": not errors,
        "n_entries": len(entries),
        "head_hash": entries[-1]["entry_hash"] if entries else GENESIS_PREV_HASH,
        "errors": errors,
    }


# --------------------------------------------------------------------------------------------------------------
# Storage backends
# --------------------------------------------------------------------------------------------------------------


class LedgerStore(Protocol):
    backend: str

    def append_with(self, build: Callable[[dict | None], dict]) -> dict: ...
    def entries(self, since_seq: int = 0, limit: int | None = None) -> list[dict]: ...
    def head(self) -> dict | None: ...
    def put_set(self, root: str, kind: str, leaves: list[str]) -> None: ...
    def get_set(self, root: str) -> list[str] | None: ...
    def close(self) -> None: ...


class SQLiteStore:
    """Default backend: artifacts/ledger.sqlite. UPDATE/DELETE on the ledger table are blocked by triggers."""

    backend = "sqlite"

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else default_sqlite_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ledger (
                seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL,
                prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL UNIQUE, signature TEXT NOT NULL,
                signer TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ledger_type ON ledger(type);
            CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
                BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
                BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
            CREATE TABLE IF NOT EXISTS merkle_sets (root TEXT PRIMARY KEY, kind TEXT NOT NULL, leaves TEXT NOT NULL);
            """
        )
        if "ledger_id" not in {r[1] for r in self.conn.execute("PRAGMA table_info(ledger)").fetchall()}:
            self.conn.execute("ALTER TABLE ledger ADD COLUMN ledger_id TEXT")

    @staticmethod
    def _row(r) -> dict:
        e = {"seq": r[0], "ts": r[1], "type": r[2], "payload": json.loads(r[3]), "prev_hash": r[4],
             "entry_hash": r[5], "signature": r[6], "signer": r[7]}
        if r[8] is not None:
            e["ledger_id"] = r[8]
        return e

    _COLS = "seq, ts, type, payload, prev_hash, entry_hash, signature, signer, ledger_id"

    def append_with(self, build):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                r = self.conn.execute(f"SELECT {self._COLS} FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
                e = build(self._row(r) if r else None)
                self.conn.execute(
                    f"INSERT INTO ledger ({self._COLS}) VALUES (?,?,?,?,?,?,?,?,?)",
                    (e["seq"], e["ts"], e["type"], canonical_json(e["payload"]).decode(), e["prev_hash"],
                     e["entry_hash"], e["signature"], e["signer"], e.get("ledger_id")),
                )
                self.conn.execute("COMMIT")
                return e
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def entries(self, since_seq=0, limit=None):
        q = f"SELECT {self._COLS} FROM ledger WHERE seq > ? ORDER BY seq"
        args: tuple = (since_seq,)
        if limit is not None:
            q += " LIMIT ?"
            args += (int(limit),)
        with self._lock:
            return [self._row(r) for r in self.conn.execute(q, args).fetchall()]

    def head(self):
        with self._lock:
            r = self.conn.execute(f"SELECT {self._COLS} FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
        return self._row(r) if r else None

    def put_set(self, root, kind, leaves):
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO merkle_sets (root, kind, leaves) VALUES (?,?,?)",
                              (root, kind, json.dumps(leaves)))

    def get_set(self, root):
        with self._lock:
            r = self.conn.execute("SELECT leaves FROM merkle_sets WHERE root = ?", (root,)).fetchone()
        return json.loads(r[0]) if r else None

    def close(self):
        with self._lock:
            self.conn.close()


class PostgresStore:
    """Postgres backend (Tiger Data / TimescaleDB sponsor hook), used when LETHE_DATABASE_URL is set.

    UNTESTED in this repo (no Postgres available during the build). Schema order matters:
      1. create the (empty) table, 2. convert it to a TimescaleDB hypertable on `ts` if the extension is available
      (TimescaleDB requires unique constraints to include the partition column, hence PRIMARY KEY (seq, ts) and
      UNIQUE (entry_hash, ts)), 3. only then add triggers.
    Triggers (defence in depth -- the hash chain + a published head hash is the actual guarantee):
      * BEFORE INSERT: NEW.seq must be max(seq)+1 and NEW.prev_hash the current head's entry_hash (gives seq
        uniqueness and chain linkage inside the database, which a hypertable cannot get from UNIQUE(seq)).
      * BEFORE UPDATE OR DELETE (row) and BEFORE TRUNCATE (statement): raise.
    Never add a TimescaleDB retention policy / drop_chunks on this table (chunk drops bypass row triggers), and
    REVOKE UPDATE, DELETE, TRUNCATE from the application role in production.
    `payload` is stored as canonical TEXT (what is hashed) plus a JSONB copy for queries.
    """

    backend = "postgres"
    _LOCK_KEY = 0x1E7E  # advisory lock id serialising appends
    _COLS = "seq, ts_text, type, payload, prev_hash, entry_hash, signature, signer, ledger_id"

    def __init__(self, dsn: str, table: str = "lethe_ledger", connect_timeout: int = 5):
        import psycopg  # imported lazily so SQLite users never need a server

        if not table.isidentifier():
            raise ValueError("table name must be a simple identifier")
        self.dsn, self.connect_timeout = dsn, connect_timeout
        self.table = table
        self.sets_table = f"{table}_merkle_sets"
        self._lock = threading.RLock()
        self.conn = psycopg.connect(dsn, autocommit=True, connect_timeout=connect_timeout)
        self.timescaledb = False
        self.timescaledb_note = "timescaledb extension not available; using a plain Postgres table"
        self.trigger_notes: list[str] = []
        self._init_schema()

    def _reconnect(self):
        import psycopg

        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=self.connect_timeout)

    def _retry(self, fn):
        """Run fn(); on a dropped connection (e.g. Tiger Cloud closing an idle connection) reconnect ONCE and retry.
        The append path rebuilds the entry from the fresh head on retry, and the BEFORE INSERT trigger rejects a
        stale seq, so a retry can never fork the chain (it may at worst append the event after a successful but
        unacknowledged first attempt)."""
        import psycopg

        with self._lock:
            try:
                return fn()
            except psycopg.OperationalError:
                self._reconnect()
                return fn()

    def _init_schema(self):
        t, s = self.table, self.sets_table
        with self.conn.transaction():
            self.conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {t} (
                    seq BIGINT NOT NULL, ts TIMESTAMPTZ NOT NULL, ts_text TEXT NOT NULL, type TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_json JSONB NOT NULL, prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL, signature TEXT NOT NULL, signer TEXT NOT NULL,
                    PRIMARY KEY (seq, ts), UNIQUE (entry_hash, ts))"""
            )
            self.conn.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS ledger_id TEXT")
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {t}_seq_idx ON {t} (seq DESC)")
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {t}_type_idx ON {t} (type)")
            self.conn.execute(
                f"CREATE TABLE IF NOT EXISTS {s} (root TEXT PRIMARY KEY, kind TEXT NOT NULL, leaves TEXT NOT NULL)"
            )
        # TimescaleDB FIRST (table still empty and trigger-free on a fresh database); best effort, each step in its
        # own transaction so a failure leaves a working plain table.
        try:
            with self.conn.transaction():
                avail = self.conn.execute(
                    "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
                ).fetchone()
            if avail:
                try:
                    with self.conn.transaction():
                        self.conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                except Exception as e:  # no privilege etc.
                    self.timescaledb_note = f"could not create timescaledb extension: {e}"
            with self.conn.transaction():
                installed = self.conn.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
                ).fetchone()
            if installed:
                with self.conn.transaction():
                    self.conn.execute(
                        "SELECT create_hypertable(%s::regclass, 'ts', if_not_exists => TRUE, migrate_data => TRUE)",
                        (t,),
                    )
                self.timescaledb = True
                self.timescaledb_note = f"hypertable on ts (timescaledb {installed[0]})"
        except Exception as e:
            self.timescaledb = False
            self.timescaledb_note = f"timescaledb setup failed, using plain table: {e}"
        # Triggers AFTER the hypertable conversion.
        with self.conn.transaction():
            self.conn.execute(
                f"""CREATE OR REPLACE FUNCTION {t}_append_only() RETURNS trigger AS $$
                    BEGIN RAISE EXCEPTION '{t} is append-only'; END; $$ LANGUAGE plpgsql"""
            )
            self.conn.execute(
                f"""CREATE OR REPLACE FUNCTION {t}_chain_check() RETURNS trigger AS $$
                    DECLARE last_seq BIGINT; last_hash TEXT;
                    BEGIN
                        PERFORM pg_advisory_xact_lock({self._LOCK_KEY}::bigint);
                        SELECT seq, entry_hash INTO last_seq, last_hash FROM {t} ORDER BY seq DESC LIMIT 1;
                        IF last_seq IS NULL THEN
                            IF NEW.seq <> 1 OR NEW.prev_hash <> '{GENESIS_PREV_HASH}' THEN
                                RAISE EXCEPTION '{t}: first entry must have seq 1 and the genesis prev_hash';
                            END IF;
                        ELSIF NEW.seq <> last_seq + 1 OR NEW.prev_hash <> last_hash THEN
                            RAISE EXCEPTION '{t}: entry seq % does not extend the chain head (seq %)', NEW.seq, last_seq;
                        END IF;
                        RETURN NEW;
                    END; $$ LANGUAGE plpgsql"""
            )
            self.conn.execute(f"DROP TRIGGER IF EXISTS {t}_no_update ON {t}")
            self.conn.execute(
                f"CREATE TRIGGER {t}_no_update BEFORE UPDATE OR DELETE ON {t} "
                f"FOR EACH ROW EXECUTE FUNCTION {t}_append_only()"
            )
            self.conn.execute(f"DROP TRIGGER IF EXISTS {t}_chain ON {t}")
            self.conn.execute(
                f"CREATE TRIGGER {t}_chain BEFORE INSERT ON {t} FOR EACH ROW EXECUTE FUNCTION {t}_chain_check()"
            )
        try:
            with self.conn.transaction():
                self.conn.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
                self.conn.execute(
                    f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
                    f"FOR EACH STATEMENT EXECUTE FUNCTION {t}_append_only()"
                )
        except Exception as e:  # e.g. statement triggers unsupported on this hypertable version
            self.trigger_notes.append(f"TRUNCATE trigger not installed: {e}")

    @staticmethod
    def _row(r) -> dict:
        e = {"seq": int(r[0]), "ts": r[1], "type": r[2], "payload": json.loads(r[3]), "prev_hash": r[4],
             "entry_hash": r[5], "signature": r[6], "signer": r[7]}
        if r[8] is not None:
            e["ledger_id"] = r[8]
        return e

    def append_with(self, build):
        def run():
            with self.conn.transaction():
                self.conn.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (self._LOCK_KEY,))
                r = self.conn.execute(
                    f"SELECT {self._COLS} FROM {self.table} ORDER BY seq DESC LIMIT 1").fetchone()
                e = build(self._row(r) if r else None)
                payload_text = canonical_json(e["payload"]).decode()
                self.conn.execute(
                    f"""INSERT INTO {self.table} (seq, ts, ts_text, type, payload, payload_json, prev_hash,
                        entry_hash, signature, signer, ledger_id) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)""",
                    (e["seq"], datetime.fromisoformat(e["ts"]), e["ts"], e["type"], payload_text, payload_text,
                     e["prev_hash"], e["entry_hash"], e["signature"], e["signer"], e.get("ledger_id")),
                )
                return e

        return self._retry(run)

    def entries(self, since_seq=0, limit=None):
        q = f"SELECT {self._COLS} FROM {self.table} WHERE seq > %s ORDER BY seq"
        args: tuple = (since_seq,)
        if limit is not None:
            q += " LIMIT %s"
            args += (int(limit),)
        return self._retry(lambda: [self._row(r) for r in self.conn.execute(q, args).fetchall()])

    def head(self):
        r = self._retry(lambda: self.conn.execute(
            f"SELECT {self._COLS} FROM {self.table} ORDER BY seq DESC LIMIT 1").fetchone())
        return self._row(r) if r else None

    def put_set(self, root, kind, leaves):
        def run():
            with self.conn.transaction():
                self.conn.execute(
                    f"INSERT INTO {self.sets_table} (root, kind, leaves) VALUES (%s,%s,%s) "
                    f"ON CONFLICT (root) DO UPDATE SET kind = EXCLUDED.kind, leaves = EXCLUDED.leaves",
                    (root, kind, json.dumps(leaves)),
                )

        self._retry(run)

    def get_set(self, root):
        r = self._retry(lambda: self.conn.execute(
            f"SELECT leaves FROM {self.sets_table} WHERE root = %s", (root,)).fetchone())
        return json.loads(r[0]) if r else None

    def close(self):
        with self._lock:
            self.conn.close()


def open_store(db_path: str | os.PathLike | None = None, fallback: bool | None = None) -> LedgerStore:
    """Postgres if LETHE_DATABASE_URL is set (and no explicit sqlite path given), else SQLite.

    If Postgres is unreachable this RAISES by default: silently switching to a second store under the same signing
    key would fork the ledger (two valid seq-1 entries with different content). Falling back to SQLite is opt-in,
    via fallback=True or LETHE_DB_FALLBACK=1, and warns loudly."""
    url = os.environ.get("LETHE_DATABASE_URL")
    if fallback is None:
        fallback = os.environ.get("LETHE_DB_FALLBACK", "") == "1"
    if url and db_path is None:
        try:
            return PostgresStore(url)
        except Exception as e:
            if not fallback:
                raise
            import warnings

            warnings.warn(f"LETHE_DATABASE_URL set but Postgres unavailable ({type(e).__name__}: {e}); "
                          f"LETHE_DB_FALLBACK=1 -> falling back to SQLite at {default_sqlite_path()}. This is a "
                          f"DIFFERENT ledger signed by the same key (a fork); do not mix their entries.",
                          RuntimeWarning, stacklevel=2)
    return SQLiteStore(db_path)


# --------------------------------------------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class LedgerError(Exception):
    pass


class Ledger:
    """Append-only, hash-chained, ed25519-signed event log + typed helpers for Lethe events."""

    def __init__(self, store: LedgerStore | None = None, signer: Signer | None = None,
                 db_path: str | os.PathLike | None = None, key_path: str | os.PathLike | None = None):
        self.store = store if store is not None else open_store(db_path)
        self.signer = signer if signer is not None else Signer.load_or_create(key_path)

    # ---- core ----
    @property
    def public_key_hex(self) -> str:
        return self.signer.public_key_hex

    @property
    def backend(self) -> str:
        return self.store.backend

    def append(self, type: str, payload: dict) -> dict:
        if type not in EVENT_TYPES:
            raise LedgerError(f"unknown event type {type!r}; expected one of {EVENT_TYPES}")
        canonical_json(payload)  # fail early on non-JSON payloads

        def build(head: dict | None) -> dict:
            e = {"seq": (head["seq"] + 1) if head else 1, "ts": _now(), "type": type,
                 "payload": json.loads(canonical_json(payload)),
                 "prev_hash": head["entry_hash"] if head else GENESIS_PREV_HASH}
            lid = head.get("ledger_id") if head else f"ledger-{uuid.uuid4().hex}"
            if lid is not None:  # legacy ledgers without an id keep the legacy body
                e["ledger_id"] = lid
            e["entry_hash"] = compute_entry_hash(e)
            e["signature"] = self.signer.sign(entry_signing_message(e["entry_hash"]))
            e["signer"] = SIGNER_LABEL
            return e

        return self.store.append_with(build)

    def entries(self, since_seq: int = 0, limit: int | None = None) -> list[dict]:
        return self.store.entries(since_seq=since_seq, limit=limit)

    def recent(self, limit: int = 100) -> list[dict]:
        """Last `limit` entries, newest first (for GET /api/ledger)."""
        return list(reversed(self.entries()[-limit:])) if limit else []

    def head(self) -> dict | None:
        return self.store.head()

    @property
    def ledger_id(self) -> str | None:
        h = self.store.entries(limit=1)
        return h[0].get("ledger_id") if h else None

    def head_hash(self) -> str:
        h = self.head()
        return h["entry_hash"] if h else GENESIS_PREV_HASH

    def verify_chain(self, expected_head: str | None = None) -> dict:
        return verify_chain(self.entries(), self.public_key_hex, expected_head)

    def find(self, type: str | None = None, **payload_eq) -> list[dict]:
        """Entries (oldest first) of `type` whose payload[k] == v for every keyword filter."""
        out = []
        for e in self.entries():
            if type and e["type"] != type:
                continue
            if all(e["payload"].get(k) == v for k, v in payload_eq.items()):
                out.append(e)
        return out

    def latest(self, type: str, **payload_eq) -> dict | None:
        found = self.find(type, **payload_eq)
        return found[-1] if found else None

    def get_set(self, root: str) -> list[str] | None:
        """Leaves (record hashes) behind a source root or manifest root. Kept OFF-chain (they reveal which
        records exist); the on-chain root commits to them."""
        return self.store.get_set(root)

    # ---- export / import ----
    def export(self, path: str | os.PathLike | None = None, include_manifest_leaves: bool = False) -> dict:
        """Ledger export for offline verification. include_manifest_leaves=True adds {"manifest_leaves": {root:
        [record hashes]}} for every MANIFEST_CREATED, so an auditor can recompute each manifest root from sorted
        leaves (verify_certificate then runs `manifest_leaves_recomputed`). Those leaves reveal every party's
        record HASHES (not contents) -- give that export to auditors, not to arbitrary tenants."""
        entries = self.entries()
        data = {
            "format": EXPORT_FORMAT,
            "signer": SIGNER_LABEL,
            "simulated": True,
            "public_key": self.public_key_hex,
            "key_id": self.signer.key_id,
            "ledger_id": entries[0].get("ledger_id") if entries else None,
            "exported_at": _now(),
            "n_entries": len(entries),
            "head_hash": entries[-1]["entry_hash"] if entries else GENESIS_PREV_HASH,
            "entries": entries,
        }
        if include_manifest_leaves:
            data["manifest_leaves"] = {
                r: self.get_set(r) for r in {e["payload"]["merkle_root"] for e in entries
                                             if e["type"] == "MANIFEST_CREATED"} if self.get_set(r) is not None}
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(data, indent=1))
        return data

    def import_entries(self, export: dict) -> int:
        """Load a verified export into this (empty) ledger's store, e.g. SQLite -> Postgres migration."""
        res = verify_export(export)
        if not res["valid"]:
            raise LedgerError(f"refusing to import invalid ledger export: {res['errors'][:3]}")
        if self.head() is not None:
            raise LedgerError("import_entries requires an empty ledger")
        for e in export["entries"]:
            self.store.append_with(lambda head, e=e: dict(e))
        return len(export["entries"])

    # ---- typed event helpers ----
    def register_source(self, source_id: str, source_type: str, display_name: str, record_hashes: Iterable[str],
                        shard: int | None = None, **extra) -> dict:
        """Register a source ONCE. Re-registering an existing source_id is refused: a second registration with fewer
        records would silently shrink the record set a later revocation/certificate covers."""
        tree = MerkleTree(record_hashes)
        if tree.size == 0:
            raise LedgerError(f"source {source_id!r} has no records; refusing to register an empty source")
        prior = self.find("SOURCE_REGISTERED", source_id=source_id)
        if prior:
            raise LedgerError(f"source {source_id!r} is already registered (seq {prior[0]['seq']}); a source's "
                              f"record set is fixed at registration")
        self.store.put_set(tree.root, "source", tree.leaves)
        return self.append("SOURCE_REGISTERED", {
            "source_id": source_id, "source_type": source_type, "display_name": display_name,
            "merkle_root": tree.root, "n_records": tree.size, "shard": shard, "merkle_scheme": MERKLE_SCHEME,
            **extra})

    def create_manifest(self, record_hashes: Iterable[str], manifest_id: str | None = None,
                        sources: list[str] | None = None, shard: int | None = None, **extra) -> dict:
        """Per-run manifest: sorted Merkle tree over every record hash the run trains on."""
        tree = MerkleTree(record_hashes)
        self.store.put_set(tree.root, "manifest", tree.leaves)
        return self.append("MANIFEST_CREATED", {
            "manifest_id": manifest_id or f"manifest-{uuid.uuid4().hex[:10]}", "merkle_root": tree.root,
            "n_records": tree.size, "sources": sorted(sources or []), "shard": shard,
            "merkle_scheme": MERKLE_SCHEME, **extra})

    def record_training_run(self, init_model_hash: str, output_model_hash: str, manifest_root: str, config: dict,
                            run_id: str | None = None, model_id: str | None = None, shard: int | None = None,
                            kind: str | None = None, **extra) -> dict:
        """Record that `output_model_hash` was trained from `init_model_hash` on the manifest `manifest_root`.

        Refused if: the manifest was not created in this ledger; its leaves are not stored or do not reproduce the
        committed root as a SORTED tree with the committed size (non-membership proofs are only sound for sorted
        trees); or a TRAINING_RUN for this output hash already exists (a weights hash is produced exactly once --
        a second run would let an operator relabel contaminated weights as trained on a clean manifest)."""
        manifest_root = _norm_hash(manifest_root)
        output_model_hash = _norm_hash(output_model_hash)
        mans = self.find("MANIFEST_CREATED", merkle_root=manifest_root)
        if not mans:
            raise LedgerError(f"unknown manifest_root {manifest_root[:16]}...: call create_manifest() first")
        leaves = self.store.get_set(manifest_root)
        if leaves is None:
            raise LedgerError(f"leaves for manifest {manifest_root[:16]}... are not stored; use create_manifest()")
        t = MerkleTree(leaves)
        if t.root != manifest_root or any(m["payload"].get("n_records") != t.size for m in mans):
            raise LedgerError(f"manifest {manifest_root[:16]}... is not a sorted Merkle tree over its stored leaves")
        if _norm_hash(init_model_hash) == output_model_hash:
            raise LedgerError("init_model_hash == output_model_hash: a training run must produce new weights")
        prior = self.find("TRAINING_RUN", output_model_hash=output_model_hash)
        if prior:
            raise LedgerError(f"model {output_model_hash[:16]}... already has a TRAINING_RUN "
                              f"({prior[0]['payload'].get('run_id')}, seq {prior[0]['seq']}); a weights hash is "
                              f"produced exactly once")
        return self.append("TRAINING_RUN", {
            "run_id": run_id or f"run-{uuid.uuid4().hex[:10]}", "model_id": model_id, "kind": kind,
            "shard": shard, "init_model_hash": _norm_hash(init_model_hash),
            "output_model_hash": _norm_hash(output_model_hash), "manifest_root": manifest_root,
            "config": config, **extra})

    def deploy_serving(self, shard_model_hashes: Iterable[str], model_id: str = "serving",
                       shards: dict | None = None, **extra) -> dict:
        hashes = sorted(_norm_hash(h) for h in shard_model_hashes)
        return self.append("MODEL_DEPLOYED", {
            "model_id": model_id, "serving_hash": serving_hash(hashes), "shard_model_hashes": hashes,
            "shards": shards, **extra})

    def request_revocation(self, source_id: str, requested_by: str | None = None, reason: str | None = None,
                           request_id: str | None = None, **extra) -> dict:
        return self.append("REVOCATION_REQUESTED", {
            "request_id": request_id or f"req-{uuid.uuid4().hex[:10]}", "source_id": source_id,
            "requested_by": requested_by, "reason": reason, **extra})

    def mark_revoked(self, source_id: str, shard: int | None = None, request_id: str | None = None,
                     **extra) -> dict:
        reg = self.latest("SOURCE_REGISTERED", source_id=source_id)
        if reg is None:
            raise LedgerError(f"source {source_id!r} was never registered")
        return self.append("SOURCE_REVOKED", {
            "source_id": source_id, "merkle_root": reg["payload"]["merkle_root"],
            "n_records": reg["payload"]["n_records"], "shard": shard, "request_id": request_id, **extra})

    def record_audit(self, model_hash: str, summary: dict, source_id: str | None = None, **extra) -> dict:
        return self.append("AUDIT_COMPLETED", {
            "model_hash": _norm_hash(model_hash), "source_id": source_id, "summary": summary, **extra})

    def record_certificate(self, certificate_id: str, certificate_sha256: str, source_id: str,
                           serving_hash: str, **extra) -> dict:
        return self.append("CERTIFICATE_ISSUED", {
            "certificate_id": certificate_id, "certificate_sha256": certificate_sha256, "source_id": source_id,
            "serving_hash": serving_hash, **extra})

    def issue_certificate(self, source_id: str, audit: dict, **kwargs) -> dict:
        """Convenience wrapper around lethe.certificate.issue_certificate(self, ...)."""
        from lethe.certificate import issue_certificate

        return issue_certificate(self, source_id, audit, **kwargs)

    # ---- views ----
    def revoked_sources(self) -> set[str]:
        return {e["payload"]["source_id"] for e in self.find("SOURCE_REVOKED")}

    def close(self) -> None:
        self.store.close()


def open_ledger(db_path: str | os.PathLike | None = None, key_path: str | os.PathLike | None = None) -> Ledger:
    """Default ledger: Postgres when LETHE_DATABASE_URL is set, else artifacts/ledger.sqlite; key in
    artifacts/keys/ledger_ed25519.pem (created on first use)."""
    return Ledger(db_path=db_path, key_path=key_path)


def load_export(path: str | os.PathLike) -> dict:
    return json.loads(Path(path).read_text())


def verify_export(export: dict, expected_head: str | None = None, trusted_public_key: str | None = None) -> dict:
    """Offline verification of a ledger export (no database needed)."""
    errors = []
    if export.get("format") != EXPORT_FORMAT:
        errors.append(f"unknown export format {export.get('format')!r}")
    if export.get("signer") != SIGNER_LABEL or export.get("simulated") is not True:
        errors.append("export must be labelled signer=SIMULATED_TEE_ATTESTATION, simulated=true")
    pk = export.get("public_key", "")
    if trusted_public_key and pk != trusted_public_key.strip().lower():
        errors.append("export public key does not match the trusted key")
    res = verify_chain(export.get("entries", []), pk, expected_head)
    errors += res["errors"]
    if export.get("head_hash") not in (None, res["head_hash"]):
        errors.append("export head_hash field does not match last entry")
    return {"valid": not errors, "n_entries": res["n_entries"], "head_hash": res["head_hash"], "errors": errors}
