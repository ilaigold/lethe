"""Tabular patient removal CLI.

    python -m lethe.tabular seed [--data synthetic|uci:<diabetic_data.csv>] [--n 800] [--to csv|tiger|snowflake]
    python -m lethe.tabular forget --patient 8841 [--patient 8802] [--out artifacts/tabular/runs/live]
    python -m lethe.tabular reset          # pristine models + receipts, and the rows written back to the database
    python -m lethe.tabular status
    python -m lethe.tabular predict --patient 8841
    python -m lethe.tabular ask "forget patient 8841"     # Gemma (UNO Q) proposes, you confirm
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .. import obs
from . import engine, sources
from .data import DEMO_PATIENT, add_canaries, generate, load_uci_diabetes


def _source(to: str) -> str:
    return f"csv:{engine.ART / 'patients.csv'}" if to == "csv" else to


def cmd_seed(a, out):
    from sklearn.ensemble import ExtraTreesClassifier

    t = time.time()
    if a.data == "synthetic":
        rows = generate(a.n, seed=a.seed)
        label = f"synthetic ({a.n} patients, seed {a.seed})"
    elif a.data.startswith("uci:"):
        rows = load_uci_diabetes(a.data[4:], max_patients=a.n, seed=a.seed)
        label = f"UCI Diabetes 130-US Hospitals ({len({r['patient_id'] for r in rows})} real patients)"
    else:
        raise SystemExit("--data must be synthetic or uci:<path to diabetic_data.csv>")
    X, y = engine.xy(rows)
    ref = ExtraTreesClassifier(n_estimators=100, min_samples_leaf=20, random_state=0, n_jobs=1).fit(X, y)
    rows, controls = add_canaries(rows, lambda f: ref.predict_proba(f)[:, 1])
    src = _source(a.to)
    engine.ART.mkdir(parents=True, exist_ok=True)
    n = sources.push(rows, src)
    out.say(f"data: {label} + 8 canary patients (incl. {DEMO_PATIENT}) -> {n} rows in {sources.describe(src)}")
    loaded = sources.load(src)  # train from what is actually in the database
    engine.bootstrap(loaded, controls, src, out)
    out.say(f"seeded in {time.time() - t:.1f}s.  Next: python -m lethe.tabular forget --patient {DEMO_PATIENT}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m lethe.tabular")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--data", default="synthetic")
    s.add_argument("--n", type=int, default=800)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--to", default="csv", choices=["csv", "tiger", "snowflake"])
    f = sub.add_parser("forget")
    f.add_argument("--patient", type=int, action="append", required=True)
    f.add_argument("--out", default=None)
    f.add_argument("--json", action="store_true", help="print summary.json at the end")
    sub.add_parser("reset")
    sub.add_parser("status")
    pr = sub.add_parser("predict")
    pr.add_argument("--patient", type=int, required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("message")
    ask.add_argument("--yes", action="store_true", help="confirm without the interactive prompt (rehearsals only)")
    a = p.parse_args(argv)

    obs.init("lethe-tabular")
    out = engine.Out()
    if a.cmd == "seed":
        cmd_seed(a, out)
    elif a.cmd == "forget":
        od = Path(a.out) if a.out else engine.ART / "runs" / time.strftime("%Y%m%d-%H%M%S")
        summ = engine.forget(a.patient, out, od)
        if a.json:
            print(json.dumps(summ, indent=1))
    elif a.cmd == "reset":
        engine.reset(out)
    elif a.cmd == "status":
        engine.status(out)
    elif a.cmd == "predict":
        print(json.dumps(engine.predict(a.patient)))
    elif a.cmd == "ask":
        from ..agent import run_turn

        return run_turn(a.message, assume_yes=a.yes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
