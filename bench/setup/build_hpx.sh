#!/bin/bash
# Build and install HPX at the commit every langX result used, with the
# options supervision needs. Run on a compute node, it takes a while:
#   srun -p medusa -N1 --exclusive -t 02:00:00 bash bench/setup/build_hpx.sh
# or as a batch job:
#   sbatch -p medusa -N1 --exclusive -t 02:00:00 -o hpx_build_%j.out bench/setup/build_hpx.sh
# Compute nodes may have no internet access: clone or fetch HPX_SRC on the
# login node first; the build then only fetches if the commit is missing.
# Optional: HPX_SRC (default /work/$USER/src/hpx), HPX_COMMIT, HPX_INSTALL
# (default from rostam_env.sh), JOBS.
#
# The install used for the results in docs/results/ was built before this
# repository existed and its configure line was not kept. This script builds
# the same commit with HPX_WITH_SUPERVISION=ON, which turns on
# HPX_WITH_FORCE_DISCONNECT and HPX_WITH_FAULT_TOLERANCE by default, and the
# TCP parcelport. To compare with an existing install:
#   grep -E 'HPX_WITH_(SUPERVISION|FORCE_DISCONNECT|NETWORKING|PARCELPORT_TCP|MALLOC)' \
#     "$HPX_INSTALL"/lib*/cmake/HPX/HPXCacheVariables.cmake

# Under sbatch the script runs from a copy; submit from the repository root.
HERE=${SLURM_SUBMIT_DIR:+$SLURM_SUBMIT_DIR/bench/setup}
HERE=${HERE:-$(cd "$(dirname "$0")" && pwd)}
. "$HERE/rostam_env.sh"   # before set -u: Lmod is not -u safe
set -euo pipefail

HPX_COMMIT=${HPX_COMMIT:-cc2be553fac9b1f2a372f4277d7c708e70bf7eb4}
HPX_SRC=${HPX_SRC:-/work/$USER/src/hpx}
BUILD=$HPX_SRC/build-${HPX_COMMIT:0:8}

if [ ! -d "$HPX_SRC/.git" ]; then
  git clone https://github.com/TheHPXProject/hpx.git "$HPX_SRC"
fi
git -C "$HPX_SRC" cat-file -e "$HPX_COMMIT^{commit}" 2>/dev/null || git -C "$HPX_SRC" fetch -q origin
git -C "$HPX_SRC" checkout -q "$HPX_COMMIT"
echo "=== hpx $(git -C "$HPX_SRC" rev-parse HEAD) -> $HPX_INSTALL"

cmake -S "$HPX_SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$HPX_INSTALL" \
  -DHPX_WITH_SUPERVISION=ON \
  -DHPX_WITH_NETWORKING=ON \
  -DHPX_WITH_PARCELPORT_TCP=ON \
  -DHPX_WITH_MALLOC=system \
  -DHPX_WITH_FETCH_ASIO=ON \
  -DHPX_WITH_EXAMPLES=OFF \
  -DHPX_WITH_TESTS=OFF
cmake --build "$BUILD" -j"${JOBS:-$(nproc)}"
cmake --install "$BUILD"
echo "=== installed: $(ls "$HPX_INSTALL"/lib*/libhpx_core.so)"
