#!/bin/bash
#SBATCH --job-name=langx-ovh2
#SBATCH --partition=medusa
#SBATCH --nodes=2
#SBATCH --exclusive
#SBATCH --time=01:00:00
#SBATCH --output=langx_overhead_nodes_%j.out
#
# Dispatch overhead on one node and across two, in one job: the Python layers
# (bench/dispatch_overhead.py) and the HPX-only floor (the rtt case of
# bench/hpx_repro), first with every process on the first node, then with the
# worker (B for rtt) on the second node. Needs the build from
# bench/rostam_test.sbatch. Submit from the repository root:
#   . bench/setup/rostam_env.sh && sbatch bench/overhead_nodes.sbatch
# Optional: REPS (default 3), N (default 2000).

cd "${SLURM_SUBMIT_DIR:-$PWD}"
. bench/setup/rostam_env.sh
set -uo pipefail

NODES=($(scontrol show hostnames "$SLURM_JOB_NODELIST"))
OTHER=${NODES[1]}
echo "=== identity: job=${SLURM_JOB_ID:-none} nodes=${NODES[*]} commit=$(git rev-parse --short HEAD)"
echo "=== addresses: $(hostname) -> $(getent hosts "$(hostname)" | awk '{print $1}'), $OTHER -> $(getent hosts "$OTHER" | awk '{print $1}')"

B=bench/hpx_repro/build_$(echo "$HPX_INSTALL" | md5sum | cut -c1-8)
cmake -S bench/hpx_repro -B $B -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$HPX_INSTALL" > /dev/null || { echo "CONFIGURE FAILED"; exit 11; }
cmake --build $B > /dev/null || { echo "BUILD FAILED"; exit 12; }
OUT=bench/_runs/${SLURM_JOB_ID:-local}

for r in $(seq 1 "${REPS:-3}"); do
  echo "== python one-node rep $r"
  python bench/dispatch_overhead.py --n "${N:-2000}"
  echo "== python two-node rep $r"
  python bench/dispatch_overhead.py --n "${N:-2000}" --worker-node "$OTHER"
done
echo "== hpx floor one-node"
bash bench/hpx_repro/run_repro.sh $B/supervision_gaps_repro $OUT/one rtt "${REPS:-3}"
echo "== hpx floor two-node"
B_NODE=$OTHER bash bench/hpx_repro/run_repro.sh $B/supervision_gaps_repro $OUT/two rtt "${REPS:-3}"
echo "=== done"
