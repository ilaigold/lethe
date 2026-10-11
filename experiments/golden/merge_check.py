"""Time-boxed P0-2 rule check (fixed before measuring): serve the TIES tree only if Acme's code is top-10 of 100,000
before removal AND <= 3 bits after. Run right after `python -m lethe.revoke --remove acme` (needs shard-1-v2), then
`--reset` (deletes the tree node files and restores the registry). Writes experiments/golden/merge_check.json."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lethe.audit import canary_exposure  # noqa: E402
from lethe.data import build_corpus  # noqa: E402
from lethe.merge import build  # noqa: E402
from lethe.model import load_model  # noqa: E402
from lethe.train import ROOT, load_registry  # noqa: E402

c = build_corpus()
kc = {k["canary_id"]: k for k in c.canaries if k.get("kind", "knowledge") == "knowledge"}
ids = ["c-acme-1", "c-brightline-1", "c-cobalt-1", "c-halcyon-1"] + [k for k in kc if k.startswith("ctrl-")]
out = {}
for label, shards, ver in [("before", {1: "shard-1", 2: "shard-2", 3: "shard-3", 4: "shard-4"}, 1),
                           ("after_acme", {1: "shard-1-v2", 2: "shard-2", 3: "shard-3", 4: "shard-4"}, 2)]:
    t = time.time()
    st, reps = build("tree_merge", shards, version=ver)
    m = load_model(ROOT / load_registry()[st["serving_model_id"]]["path"])
    res = {cid: canary_exposure(m, kc[cid]) for cid in ids}
    ctrl = [res[k]["exposure_bits"] for k in ids if k.startswith("ctrl-")]
    out[label] = dict(lambda_root=st["params"].get("lambda"), seconds=round(time.time() - t, 1),
                      canaries={k: dict(rank=v["rank"], bits=v["exposure_bits"], greedy=v["greedy"])
                                for k, v in res.items() if not k.startswith("ctrl-")},
                      control_mean_bits=round(sum(ctrl) / len(ctrl), 3))
    print(label, json.dumps(out[label], indent=1), flush=True)
b, a = out["before"]["canaries"]["c-acme-1"], out["after_acme"]["canaries"]["c-acme-1"]
out["rule"] = "serve tree iff acme rank <= 10 before AND acme bits <= 3 after"
out["passes"] = bool(b["rank"] <= 10 and a["bits"] <= 3)
print("RULE PASSES" if out["passes"] else "RULE FAILS -> serve the ensemble")
(Path(__file__).parent / "merge_check.json").write_text(json.dumps(out, indent=1))
