#!/bin/bash
#SBATCH --job-name=langx-scale
#SBATCH --partition=medusa
#SBATCH --nodes=5
#SBATCH --exclusive
#SBATCH --time=02:00:00
#SBATCH --output=langx_scaling_%j.out
#
# Throughput with 1, 2 and 4 workers, no failures (H6). Root and coordinator
# on the first node; workers each on their own node ("nodes"), and for
# comparison all on the first node ("one"). Each point runs run_f0.py: the
# stock LangGraph baseline and the HPX run, REPS runs each, checked for equal
# final state. Needs the build from bench/rostam_test.sbatch. Submit from the
# repository root:
#   . bench/setup/rostam_env.sh && sbatch experiments/01_durable_langgraph/scaling.sbatch
# Optional: REPS (default 30), WORKERS (default "1 2 4"), F0_ARGS (default
# "--width 32 --loops 2 --latency-ms 100").

cd "${SLURM_SUBMIT_DIR:-$PWD}"
. bench/setup/rostam_env.sh
set -uo pipefail

X=experiments/01_durable_langgraph
OUT=$X/_runs/${SLURM_JOB_ID:-local}
mkdir -p "$OUT"
NODES=($(scontrol show hostnames "$SLURM_JOB_NODELIST"))
ARGS=${F0_ARGS:---width 32 --loops 2 --latency-ms 100}
echo "=== identity: job=${SLURM_JOB_ID:-none} nodes=${NODES[*]} commit=$(git rev-parse --short HEAD) args=$ARGS reps=${REPS:-30}"

for N in ${WORKERS:-1 2 4}; do
  W=$(IFS=,; echo "${NODES[*]:1:$N}")
  echo "== $N workers, own nodes: $W"
  python $X/run_f0.py --out "$OUT/nodes-w$N" --workers "$N" --worker-nodes "$W" \
    --reps "${REPS:-30}" $ARGS > "$OUT.nodes-w$N.log" 2>&1
  echo "rc=$?"
  echo "== $N workers, first node"
  python $X/run_f0.py --out "$OUT/one-w$N" --workers "$N" \
    --reps "${REPS:-30}" $ARGS > "$OUT.one-w$N.log" 2>&1
  echo "rc=$?"
done
python $X/analyze_scaling.py "$OUT"
echo "=== done: rc=$?"
