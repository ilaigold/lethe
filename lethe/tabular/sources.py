"""Where patient rows live: a local CSV, Tiger Data (Postgres), or Snowflake.

    csv:<path>   local file (default; offline)
    tiger        Postgres URL from LETHE_PATIENTS_URL (falls back to LETHE_DATABASE_URL)
    snowflake    SNOWFLAKE_ACCOUNT / _USER / _PASSWORD / _WAREHOUSE / _DATABASE / _SCHEMA (/ _ROLE optional)

The database is the system of record. Forgetting a patient deletes their rows there too (and the CLI shows the
row count before and after), then retrains their shard from what is left.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .data import COLUMNS, normalise, read_csv, write_csv

TABLE = os.environ.get("LETHE_PATIENTS_TABLE", "lethe_patients")
RECEIPTS_TABLE = os.environ.get("LETHE_RECEIPTS_TABLE", "lethe_receipts")
_DDL = (f"CREATE TABLE IF NOT EXISTS {TABLE} (encounter_id BIGINT PRIMARY KEY, patient_id BIGINT NOT NULL, "
        "hospital TEXT NOT NULL, department TEXT NOT NULL, age_band INT, admission_type INT, prior_visits INT, "
        "num_meds INT, a1c_high INT, glucose_high INT, los_days INT, readmit_30d INT NOT NULL, note TEXT)")


def kind_of(source: str) -> str:
    return source.split(":", 1)[0]


# ---------------------------------------------------------------- connections
def _pg():
    import psycopg

    url = os.environ.get("LETHE_PATIENTS_URL") or os.environ.get("LETHE_DATABASE_URL")
    if not url:
        raise SystemExit("set LETHE_PATIENTS_URL to your Tiger Data connection string (postgres://...?sslmode=require)")
    return psycopg.connect(url, autocommit=True, connect_timeout=10)


def _sf():
    try:
        import snowflake.connector
    except ImportError:
        raise SystemExit("Snowflake needs: uv sync --extra snowflake")
    need = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"missing env: {', '.join(missing)}")
    kw = {k.split("_", 1)[1].lower(): os.environ[k] for k in need}
    if os.environ.get("SNOWFLAKE_ROLE"):
        kw["role"] = os.environ["SNOWFLAKE_ROLE"]
    return snowflake.connector.connect(**kw)


def _sql(source: str):
    """(connection, placeholder) for a SQL source."""
    k = kind_of(source)
    if k == "tiger":
        return _pg(), "%s"
    if k == "snowflake":
        return _sf(), "%s"
    raise ValueError(f"not a SQL source: {source}")


# ---------------------------------------------------------------- operations
def load(source: str) -> list[dict]:
    if kind_of(source) == "csv":
        return read_csv(source.split(":", 1)[1])
    conn, _ = _sql(source)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(COLUMNS)} FROM {TABLE} ORDER BY patient_id, encounter_id")
        return [normalise(dict(zip(COLUMNS, r))) for r in cur.fetchall()]
    finally:
        conn.close()


def push(rows: list[dict], source: str) -> int:
    """Replace the table's contents with `rows` (used by `seed` and `reset`)."""
    if kind_of(source) == "csv":
        write_csv(rows, source.split(":", 1)[1])
        return len(rows)
    conn, ph = _sql(source)
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        cur.execute(f"DELETE FROM {TABLE}")
        sql = f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES ({', '.join([ph] * len(COLUMNS))})"
        data = [tuple(r[c] for c in COLUMNS) for r in rows]
        if kind_of(source) == "tiger":
            with conn.transaction():
                cur.executemany(sql, data)
        else:
            cur.executemany(sql, data)
            conn.commit()
        return len(rows)
    finally:
        conn.close()


def count_patient(source: str, patient_id: int) -> int:
    if kind_of(source) == "csv":
        return sum(r["patient_id"] == patient_id for r in load(source))
    conn, ph = _sql(source)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE patient_id = {ph}", (patient_id,))
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def delete_patients(source: str, patient_ids: list[int]) -> int:
    if kind_of(source) == "csv":
        rows = load(source)
        keep = [r for r in rows if r["patient_id"] not in set(patient_ids)]
        push(keep, source)
        return len(rows) - len(keep)
    conn, ph = _sql(source)
    try:
        cur = conn.cursor()
        n = 0
        for pid in patient_ids:
            cur.execute(f"DELETE FROM {TABLE} WHERE patient_id = {ph}", (pid,))
            n += cur.rowcount or 0
        if kind_of(source) == "snowflake":
            conn.commit()
        return n
    finally:
        conn.close()


def mirror_receipts(entries: list[dict], source: str) -> int:
    """Copy signed receipts into a queryable table (Tiger: a hypertable on ts if TimescaleDB is there).
    The signed hash chain in the ledger export stays the thing the verifier checks; this is a read copy."""
    if kind_of(source) == "csv":
        return 0
    conn, ph = _sql(source)
    try:
        cur = conn.cursor()
        is_pg = kind_of(source) == "tiger"
        payload_t = "JSONB" if is_pg else "VARIANT"
        cur.execute(f"CREATE TABLE IF NOT EXISTS {RECEIPTS_TABLE} (ledger_id TEXT, seq BIGINT, ts TIMESTAMP"
                    f"{'TZ' if is_pg else '_TZ'} NOT NULL, type TEXT, entry_hash TEXT, payload {payload_t}, "
                    f"signature TEXT, signer TEXT)")
        if is_pg:
            try:
                cur.execute(f"SELECT create_hypertable('{RECEIPTS_TABLE}', 'ts', if_not_exists => TRUE)")
            except Exception:
                pass  # plain Postgres without TimescaleDB is fine
        cur.execute(f"SELECT entry_hash FROM {RECEIPTS_TABLE}")
        have = {r[0] for r in cur.fetchall()}
        new = [e for e in entries if e["entry_hash"] not in have]
        if not new:
            return 0
        if is_pg:
            sql = f"INSERT INTO {RECEIPTS_TABLE} VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}::jsonb, {ph}, {ph})"
            with conn.transaction():
                cur.executemany(sql, [(e.get("ledger_id"), e["seq"], e["ts"], e["type"], e["entry_hash"],
                                       json.dumps(e["payload"]), e["signature"], e.get("signer")) for e in new])
        else:
            for e in new:  # VARIANT needs PARSE_JSON in a SELECT, not VALUES
                cur.execute(f"INSERT INTO {RECEIPTS_TABLE} SELECT {ph}, {ph}, {ph}, {ph}, {ph}, PARSE_JSON({ph}), "
                            f"{ph}, {ph}", (e.get("ledger_id"), e["seq"], e["ts"], e["type"], e["entry_hash"],
                                            json.dumps(e["payload"]), e["signature"], e.get("signer")))
            conn.commit()
        return len(new)
    finally:
        conn.close()


def describe(source: str) -> str:
    k = kind_of(source)
    if k == "csv":
        return f"local CSV {Path(source.split(':', 1)[1]).name}"
    return {"tiger": f"Tiger Data (Postgres) table {TABLE}", "snowflake": f"Snowflake table {TABLE}"}[k]
