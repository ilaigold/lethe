"""Publish signed training receipts to Tiger Data (Postgres + TimescaleDB) as an append-only, queryable log.

The demo keeps its local SQLite ledger, which is what `--reset` restores. This module *mirrors* a ledger export
(the same JSON the customer verifies against) into a Tiger Cloud table, so that customers and auditors can query
every training run, deployment and revocation over time. Rows are only ever inserted, never updated. A re-publish
of the same entry is a no-op (keyed by entry_hash), and entries from different demo runs coexist, tagged by run_id.

    TIGER_DATABASE_URL=postgres://... uv run python -m lethe.receipts_publish experiments/golden/acme/ledger_export.json --run-id golden-acme
    TIGER_DATABASE_URL=postgres://... uv run python -m lethe.receipts_publish --summary

The env var is deliberately NOT LETHE_DATABASE_URL: that one would switch lethe.ledger's primary backend to
Postgres and break the demo's file-based --reset.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ENV = "TIGER_DATABASE_URL"
TABLE = "lethe_receipts"

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    ts          timestamptz NOT NULL,
    entry_hash  text        NOT NULL,
    seq         bigint      NOT NULL,
    type        text        NOT NULL,
    payload     jsonb       NOT NULL,
    prev_hash   text        NOT NULL,
    signature   text        NOT NULL,
    signer      text        NOT NULL,
    ledger_id   text,
    run_id      text,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entry_hash, ts)
);
"""


def _connect(url: str | None = None):
    import psycopg  # imported lazily so the module costs nothing when Tiger isn't configured

    url = url or os.environ.get(ENV)
    if not url:
        raise RuntimeError(f"{ENV} is not set (put the Tiger Cloud connection string in .env)")
    return psycopg.connect(url, connect_timeout=10, autocommit=True)


def ensure_schema(conn) -> dict:
    """Create the receipts table and, when the timescaledb extension is available, make it a hypertable on ts."""
    info = {"table": TABLE, "hypertable": False}
    with conn.cursor() as cur:
        cur.execute(DDL)
        try:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'timescaledb'")
            if cur.fetchone():
                cur.execute(f"SELECT create_hypertable('{TABLE}', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")
                info["hypertable"] = True
        except Exception as e:  # plain Postgres still works; we just report it
            info["hypertable_error"] = str(e).splitlines()[0]
        cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_type_ts ON {TABLE} (type, ts DESC)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_run ON {TABLE} (run_id)")
    return info


def publish(export: dict | str | Path, run_id: str | None = None, url: str | None = None) -> dict:
    """Insert every entry of a ledger export. Returns counts. Idempotent per entry_hash."""
    if not isinstance(export, dict):
        export = json.loads(Path(export).read_text())
    entries = export.get("entries", [])
    with _connect(url) as conn:
        schema = ensure_schema(conn)
        inserted = 0
        with conn.cursor() as cur:
            for e in entries:
                cur.execute(
                    f"""INSERT INTO {TABLE} (ts, entry_hash, seq, type, payload, prev_hash, signature, signer, ledger_id, run_id)
                        SELECT %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
                        WHERE NOT EXISTS (SELECT 1 FROM {TABLE} WHERE entry_hash = %s)""",
                    (e["ts"], e["entry_hash"], e["seq"], e["type"], json.dumps(e["payload"], sort_keys=True),
                     e["prev_hash"], e["signature"], e.get("signer") or export.get("signer", ""),
                     export.get("ledger_id"), run_id, e["entry_hash"]),
                )
                inserted += cur.rowcount
            cur.execute(f"SELECT count(*) FROM {TABLE}")
            total = cur.fetchone()[0]
    return {"published": len(entries), "inserted": inserted, "already_present": len(entries) - inserted,
            "table_rows": total, "head_hash": export.get("head_hash"), **schema}


def summary(url: str | None = None) -> dict:
    with _connect(url) as conn, conn.cursor() as cur:
        ensure_schema(conn)
        cur.execute(f"SELECT type, count(*), max(ts) FROM {TABLE} GROUP BY type ORDER BY type")
        by_type = {t: {"count": n, "latest": ts.isoformat()} for t, n, ts in cur.fetchall()}
        cur.execute(f"SELECT count(*), count(DISTINCT run_id) FROM {TABLE}")
        rows, runs = cur.fetchone()
    return {"table": TABLE, "rows": rows, "runs": runs, "by_type": by_type}


def configured() -> bool:
    return bool(os.environ.get(ENV))


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists() and not os.environ.get(ENV):
        for line in env.read_text().splitlines():
            if line.startswith(ENV + "=") and line.split("=", 1)[1].strip():
                os.environ[ENV] = line.split("=", 1)[1].strip()


def main(argv=None) -> int:
    _load_dotenv()
    ap = argparse.ArgumentParser(description="Publish Lethe receipts to Tiger Data")
    ap.add_argument("export", nargs="?", help="ledger_export.json to publish")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--summary", action="store_true", help="print what the Tiger table holds")
    a = ap.parse_args(argv)
    if not configured():
        print(f"{ENV} not set; nothing published (the demo works without it).", file=sys.stderr)
        return 2
    if a.export:
        print(json.dumps(publish(a.export, run_id=a.run_id), indent=2))
    if a.summary or not a.export:
        print(json.dumps(summary(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
