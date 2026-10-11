"""Fake `python -m lethe.revoke` for tests and console development. No GPU, no torch, never touches artifacts/.

It replays a REAL golden run (experiments/golden/acme or experiments/golden/acme+cobalt) line by line and writes the
golden output files (summary.json, cert-*.json, verify_*.txt, ledger_export.json, *_record_hashes.json, tampered
copies, transcript.txt) into --out, so the certificates still verify. State lives in a fake artifacts dir with the same
layout the real CLI uses (models/serving.json + models/registry.json), so lethe/server.py reads it the same way.

    LETHE_REVOKE_CMD="$PWD/.venv/bin/python -u tests/fake_revoke.py" uv run uvicorn lethe.server:app --port 8001

With LETHE_REVOKE_CMD set, the server defaults to LETHE_ARTIFACTS=runs/_fake_artifacts and
LETHE_RUNS_DIR=runs/_fake_runs, and passes its LETHE_ARTIFACTS to this script, so the two always share one state dir
(set either var to override).

Env:
  LETHE_ARTIFACTS   state dir (default runs/_fake_artifacts, same as the server's; refuses the real artifacts/ dir)
  LETHE_FAKE_SCALE  replay speed vs the recorded seconds (default 0.15 = ~8 s per run; 0 = instant, 1 = real time)
  LETHE_FAKE_DELAY  pause per printed line, seconds (default 0.02)
  LETHE_FAKE_FAIL   if set, exit 1 with a traceback-like message after step [3] (to test the error path)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "experiments" / "golden"
SETS = {frozenset({"acme"}): "acme", frozenset({"acme", "cobalt"}): "acme+cobalt"}
SLUGS = ("acme", "brightline", "cobalt", "dunmore", "elmstead", "fernway", "granite", "halcyon")
SHARD_OF = {s: i // 2 + 1 for i, s in enumerate(SLUGS)}

SCALE = float(os.environ.get("LETHE_FAKE_SCALE", "0.15"))
DELAY = float(os.environ.get("LETHE_FAKE_DELAY", "0.02"))


def art_dir() -> Path:
    d = Path(os.environ.get("LETHE_ARTIFACTS") or ROOT / "runs" / "_fake_artifacts").resolve()
    if d == (ROOT / "artifacts").resolve():
        raise SystemExit("fake_revoke: refusing to use the real artifacts/ dir as fake state")
    return d


def pristine_hashes() -> dict[int, str]:
    s = json.loads((GOLDEN / "acme+cobalt" / "summary.json").read_text())
    h = {int(k): v for k, v in s["shards_untouched"].items()}
    for r in s["shards_retrained"]:
        h[r["shard"]] = r["old_weights_sha256"]
    return h


def write_state(shards: dict[int, str], revoked: list[str], version: int, registry: dict):
    m = art_dir() / "models"
    m.mkdir(parents=True, exist_ok=True)
    state = dict(strategy="ensemble", version=version, shards={str(k): v for k, v in sorted(shards.items())},
                 revoked=sorted(revoked), assignment={f"tenant:{s}": k for s, k in SHARD_OF.items()},
                 params=dict(mode="bayes", forward_passes_per_token=4), fake=True)
    (m / "registry.json").write_text(json.dumps(registry, indent=1))
    (m / "serving.json").write_text(json.dumps(state, indent=1))


def pristine_registry() -> dict:
    return {f"shard-{k}": dict(model_id=f"shard-{k}", kind="shard", shard=k, weights_sha256=h)
            for k, h in pristine_hashes().items()}


def load_state() -> tuple[dict, dict]:
    m = art_dir() / "models"
    if not (m / "serving.json").exists():
        do_reset(quiet=True)
    return json.loads((m / "serving.json").read_text()), json.loads((m / "registry.json").read_text())


def say(s: str = ""):
    print(s, flush=True)
    if DELAY:
        time.sleep(DELAY)


def do_reset(quiet: bool = False):
    write_state({k: f"shard-{k}" for k in (1, 2, 3, 4)}, [], 1, pristine_registry())
    if quiet:
        return
    say("Lethe  reset to the pre-removal state")
    say("  removed 0 post-removal weight files")
    for k, h in sorted(pristine_hashes().items()):
        say(f"  shard {k}  shard-{k}.pt  sha256 {h[:12]}  restored")
    say("  serving  ensemble of 4 shards  revoked: none")
    say("  ledger   17 entries, head seq 17 8b28b5443eee  chain valid")
    say("  ready (0.0s)  [fake]")


def do_status():
    st, _ = load_state()
    say(f"  strategy {st.get('strategy')}  shards {st.get('shards')}")
    say(f"  revoked  {st.get('revoked') or 'none'}")
    say("  ledger   (fake: no ledger)")


HEAD_SECS = re.compile(r"^\[\d\] .*?\s(\d+\.\d)s$")
PROG_SECS = re.compile(r"^\s+shard \d \[[#.]+\]\s+\d+%.*?(\d+\.\d)s$")


def replay(name: str, out_dir: Path) -> list[str]:
    lines = (GOLDEN / name / "transcript.txt").read_text().splitlines()
    printed = []
    try:
        rel = out_dir.relative_to(ROOT)
    except ValueError:
        rel = out_dir
    prev_prog = 0.0
    for ln in lines:
        m = HEAD_SECS.match(ln)
        if m and SCALE:  # steps 1/4/5: the header is printed AFTER the work, with its seconds
            time.sleep(float(m.group(1)) * SCALE)
        m = PROG_SECS.match(ln)
        if m:
            t = float(m.group(1))
            if SCALE:
                time.sleep(max(0.0, t - prev_prog) * SCALE)
            prev_prog = 0.0 if "done" in ln else t
        if ln.startswith("[4]") and os.environ.get("LETHE_FAKE_FAIL"):
            print("Traceback (most recent call last):", file=sys.stderr, flush=True)
            print("RuntimeError: fake failure (LETHE_FAKE_FAIL)", file=sys.stderr, flush=True)
            raise SystemExit(1)
        if ln.startswith("[6]"):
            copy_outputs(name, out_dir)
        ln = ln.replace(f"experiments/golden/{name}/", f"{rel}/")
        say(ln)
        printed.append(ln)
    return printed


def copy_outputs(name: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    src = GOLDEN / name
    for p in src.iterdir():
        if p.name in ("summary.json", "transcript.txt"):
            continue
        shutil.copy2(p, out_dir / p.name)
    sys.path.insert(0, str(ROOT))
    from lethe.revoke import tamper_one_byte  # stdlib-only import, same tamper as the real CLI
    for c in src.glob("cert-*.json"):
        tamper_one_byte(c, out_dir / f"tampered-{c.stem}.json")


def do_remove(slugs: list[str], out_dir: Path) -> int:
    key = frozenset(slugs)
    if key not in SETS:
        print(f"fake_revoke: no golden run for {sorted(key)}; the fake supports {[sorted(k) for k in SETS]}",
              file=sys.stderr, flush=True)
        return 3
    st, reg = load_state()
    dup = [f"tenant:{s}" for s in slugs if f"tenant:{s}" in st.get("revoked", [])]
    if dup:
        print(f"{dup} already removed from the serving model. Run: uv run python -m lethe.revoke --reset",
              file=sys.stderr, flush=True)
        return 2
    name = SETS[key]
    printed = replay(name, out_dir)
    summary = json.loads((GOLDEN / name / "summary.json").read_text())
    shards = {int(k): v for k, v in summary["serving"]["shards"].items()}
    for r in summary["shards_retrained"]:
        reg[r["model_id"]] = dict(model_id=r["model_id"], kind="shard", shard=r["shard"],
                                  weights_sha256=r["weights_sha256"])
    write_state(shards, sorted(set(st.get("revoked", [])) | set(summary["removed"])), 2, reg)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    (out_dir / "transcript.txt").write_text("\n".join(printed) + "\n")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fake_revoke")
    ap.add_argument("--remove", action="append", default=[])
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    slugs = list(dict.fromkeys(s.split(":", 1)[-1].lower().strip() for s in a.remove))
    bad = [s for s in slugs if s not in SLUGS]
    if bad:
        ap.error(f"unknown customer(s) {bad}; choose from {sorted(SLUGS)}")
    if a.reset:
        do_reset()
    if a.status:
        do_status()
    if slugs:
        out_dir = Path(a.out) if a.out else ROOT / "runs" / "_fake_out"
        out_dir = out_dir if out_dir.is_absolute() else Path.cwd() / out_dir
        return do_remove(slugs, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
