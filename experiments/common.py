"""Shared helpers for the experiment scripts (not imported by the lethe package)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lethe.audit import audit, summarize  # noqa: E402
from lethe.data import records_text  # noqa: E402
from lethe.train import eval_loss  # noqa: E402

OUT_DIR = ROOT / "experiments" / "out"


def utility(model, corpus, forget_sid: str | None = None) -> dict:
    """Val loss (nats/char) on held-out records: retain sources vs forget source."""
    retain = [r for r in corpus.val_records if r["source_id"] != forget_sid]
    out = {"val_retain": round(eval_loss(model, records_text(retain)), 4)}
    if forget_sid:
        fv = [r for r in corpus.val_records if r["source_id"] == forget_sid]
        out["val_forget_src"] = round(eval_loss(model, records_text(fv)), 4)
    return out


def row(label: str, res: list[dict], forget_sid: str | None, corpus, model=None) -> dict:
    """One results-table row: forget-source canaries, other sources' canaries, controls, utility."""
    others = {s["source_id"] for s in corpus.sources if s["source_id"] != forget_sid}
    r = dict(model=label)
    if forget_sid:
        f = summarize(res, {forget_sid})
        r.update(forget_bits=f["inserted_mean_bits"], forget_extracted=f["extracted"],
                 forget_ranks=[x["rank"] for x in res if x["inserted"] and x["source_id"] == forget_sid])
    o = summarize(res, others)
    r.update(other_bits=o["inserted_mean_bits"], other_extracted=o["extracted"],
             control_bits=o["control_mean_bits"], control_max=o["control_max_bits"])
    if model is not None:
        r.update(utility(model, corpus, forget_sid))
    return r


def md_table(rows: list[dict], cols: list[str]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out)


def dump(name: str, obj):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(obj, indent=1, default=str))
