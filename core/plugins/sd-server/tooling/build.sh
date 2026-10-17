#!/usr/bin/env bash
# =============================================================================
# build.sh — compile sd-server (stable-diffusion.cpp) with CUDA. COMPILE ONLY.
# =============================================================================
# Models are fetched by run.sh on first launch (not here).
# WHERE: drop this folder one dir OUTSIDE your sapphire install — a sibling
#        side-service dir (e.g. <sapphire-parent>/sd-server/).
# TARGET: NVIDIA GPU. Default arch below is 89 (RTX 4090 / Ada). Change for yours.
# RUN ONCE:  ./build.sh
#
# Server prereqs (install yourself): git, cmake, build-essential, wget, CUDA toolkit (nvcc).
#   Verify nvcc:  nvcc --version
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/stable-diffusion.cpp"
CUDA_ARCH=89   # 4090=89(Ada), 3090=86(Ampere), A100=80, etc.

echo "==> [1/2] Clone stable-diffusion.cpp (with submodules)"
if [ ! -d "$SRC/.git" ]; then
    git clone --recursive https://github.com/leejet/stable-diffusion.cpp "$SRC"
else
    echo "    already present — pulling latest + submodules"
    git -C "$SRC" pull --recurse-submodules
fi

echo "==> [2/2] Build sd-server with CUDA (arch $CUDA_ARCH)"
cmake -S "$SRC" -B "$SRC/build" \
    -DSD_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES=$CUDA_ARCH \
    -DCMAKE_BUILD_TYPE=Release
cmake --build "$SRC/build" --config Release -j"$(nproc)"

BIN="$(find "$SRC/build" -name sd-server -type f 2>/dev/null | head -1)"
[ -n "$BIN" ] && echo "    BUILT: $BIN" || { echo "!! sd-server binary not found after build"; exit 1; }

echo ""
echo "==> Compile done. Next:  ./run.sh   (fetches models on first run, then serves)"
