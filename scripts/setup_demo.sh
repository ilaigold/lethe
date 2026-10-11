#!/usr/bin/env bash
# One-time setup for the live demo (model files are not in git). Safe to re-run: every step skips work already done.
#
#   bash scripts/setup_demo.sh                 # deps + base + 4 shards + pristine snapshot/receipts + probe cache
#   bash scripts/setup_demo.sh --with-pooled   # also train the pooled baseline (not needed for the live demo)
#   bash scripts/setup_demo.sh --reset         # also restore the pre-removal state (undo removals made in the demo)
#   bash scripts/setup_demo.sh --no-sync       # skip `uv sync`
#
# Steps (measured from a fresh clone on an Apple-Silicon Mac, MPS; total 371 s, a re-run with everything present 2 s):
#   1. uv sync                                    1 s     dependencies (longer the first time: downloads torch)
#   2. base model    artifacts/models/base.pt     189 s   lethe.train.train_base (2000 steps, generic text)
#   3. 4 shards      artifacts/models/shard-N.pt  147 s   lethe.train.train_shard: SFT -> DPO, 2 customers each (~37 s)
#   4. pristine      artifacts/pristine/          26 s    python -m lethe.revoke --reset on first run: snapshot of the
#                    + artifacts/ledger.sqlite            shard models, signed receipts, serving.json, probe cache (24 s)
#                                                         (later runs: --warm, which only checks the probe cache)
#   optional pooled  artifacts/models/pooled.pt           --with-pooled (not used by the live demo)
# Then: uv run uvicorn lethe.server:app --port 8000
# Training on MPS is not bit-reproducible, so your weights (and sampled /api/ask answers) differ slightly from
# anyone else's; the exact-rank probe (every customer's code at rank 1 before removal) is what the demo relies on.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WITH_POOLED=0
RESET=0
SYNC=1
for arg in "$@"; do
  case "$arg" in
    --with-pooled) WITH_POOLED=1 ;;
    --reset) RESET=1 ;;
    --no-sync) SYNC=0 ;;
    -h|--help) awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then B=$'\033[1m'; G=$'\033[1;32m'; D=$'\033[2m'; N=$'\033[0m'; else B=; G=; D=; N=; fi
T_ALL=$(date +%s)
SUMMARY=()

step() {  # step <label> <command...>: run, time, record
  local label="$1"; shift
  echo
  echo "${B}==> ${label}${N}"
  local t0; t0=$(date +%s)
  "$@"
  local dt=$(( $(date +%s) - t0 ))
  echo "${D}    (${dt}s)${N}"
  SUMMARY+=("$(printf '%-48s %5ss' "$label" "$dt")")
}

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

echo "${B}Lethe live demo setup${N}  (${ROOT})"

if [ "$SYNC" = 1 ]; then
  step "1/4 dependencies (uv sync)" uv sync
else
  echo; echo "${B}==> 1/4 dependencies${N}: skipped (--no-sync)"
fi

DEVICE=$(uv run python -c "import sys; sys.path.insert(0, '.'); from lethe.model import get_device; print(get_device().type)")
echo "    torch device: ${DEVICE}"
if [ "$DEVICE" = "cpu" ]; then
  echo "    note: no GPU (MPS/CUDA) found; training on CPU is much slower than the times above"
fi

step "2/4 base model" uv run python scripts/train_demo_models.py base
step "3/4 shard models (SFT -> DPO)" uv run python scripts/train_demo_models.py shards
if [ "$WITH_POOLED" = 1 ]; then
  step "    pooled baseline (optional)" uv run python scripts/train_demo_models.py pooled
fi

# lethe.revoke: --reset bootstraps on first use (pristine copies of the shard models + registry, signed receipts,
# serving.json, then the probe cache); --warm only (re)builds the probe cache when it is missing or stale.
if [ ! -f artifacts/pristine/ledger.sqlite ] || [ ! -f artifacts/pristine/serving.json ] \
   || [ ! -f artifacts/models/serving.json ] || [ "$RESET" = 1 ]; then
  step "4/4 pristine snapshot + receipts + probe cache" uv run python -m lethe.revoke --reset
else
  step "4/4 probe cache check (already set up)" uv run python -m lethe.revoke --warm
fi

echo
uv run python -m lethe.revoke --status

TOTAL=$(( $(date +%s) - T_ALL ))
echo
echo "${B}Timings${N}"
for line in ${SUMMARY[@]+"${SUMMARY[@]}"}; do echo "  $line"; done
printf '  %-48s %5ss\n' "total" "$TOTAL"
echo
echo "${G}Ready: uv run uvicorn lethe.server:app --port 8000${N}"
echo "       then open http://127.0.0.1:8000/  (the pill says LIVE; Remove runs a real removal, ~30 s)"
