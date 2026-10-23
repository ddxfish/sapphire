#!/usr/bin/env bash
# =============================================================================
# run.sh — ensure Z-Image Turbo models are present, then launch sd-server.
# =============================================================================
# build.sh compiles sd-server once. This script: downloads the model trio on
# first run (skips if already there), then serves the API every launch.
# Sapphire's z-image plugin points its "sd-server URL" setting at this.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/stable-diffusion.cpp"
MODELS="$HERE/models"

# --- config ----------------------------------------------------------------
LISTEN_IP="0.0.0.0"      # same box as Sapphire → localhost. 0.0.0.0 to expose on LAN.
LISTEN_PORT="7861"

# Z-Image Turbo quant. Q3_K fits a 4090 SHARED with Sapphire (~5GB) + a ~9B LLM
# (~10GB). Only bump this (and verify the exact filename at the repo) if you
# free GPU memory:  https://huggingface.co/leejet/Z-Image-Turbo-GGUF/tree/main
ZIMG_GGUF="z_image_turbo-Q3_K.gguf"
VAE_FILE="ae.safetensors"
LLM_FILE="Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

# OFFLOAD=1 pushes the text-encoder + VAE to CPU — saves ~2.8GB GPU at the cost
# of a few seconds CPU text-encode per image. Flip on if VRAM gets tight.
OFFLOAD=0
# ---------------------------------------------------------------------------

BIN="$(find "$SRC/build" -name sd-server -type f 2>/dev/null | head -1)"
[ -n "$BIN" ] || { echo "sd-server not built — run ./build.sh first"; exit 1; }

echo "==> ensuring models in $MODELS"
mkdir -p "$MODELS"
dl() {  # $1=url  $2=dest
    if [ -f "$2" ]; then echo "    have $(basename "$2")"; return; fi
    echo "    fetching $(basename "$2") ..."
    wget -q --show-progress -O "$2" "$1" || { echo "!! download failed: $1"; echo "   (401/403 -> 'huggingface-cli login'; 404 -> verify filename at the repo URL)"; rm -f "$2"; exit 1; }
}
dl "https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/$ZIMG_GGUF" "$MODELS/$ZIMG_GGUF"
dl "https://huggingface.co/ffxvs/vae-flux/resolve/main/ae.safetensors" "$MODELS/$VAE_FILE"
dl "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" "$MODELS/$LLM_FILE"

echo "==> launching sd-server on $LISTEN_IP:$LISTEN_PORT (offload=$OFFLOAD)"
# Speed/VRAM knobs:
#   OFFLOAD=1    — weights in CPU RAM, STREAMED to GPU (compute still on GPU). Fast
#                  (~4s) + tiny GPU footprint (~1GB). Best on a shared GPU. RECOMMENDED.
#   VAE_TILING=1 — ONLY if OFFLOAD=0 and you OOM at the VAE decode. Tiles the decode
#                  to shrink its buffer, but it's noticeably slower. Last resort, not speed.
# --diffusion-fa = flash attention. Gen params (steps/cfg/W/H) are per-request.
VAE_TILING="${VAE_TILING:-0}"
FLAGS=(
    --diffusion-model "$MODELS/$ZIMG_GGUF"
    --vae             "$MODELS/$VAE_FILE"
    --llm             "$MODELS/$LLM_FILE"
    --diffusion-fa
    --listen-ip   "$LISTEN_IP"
    --listen-port "$LISTEN_PORT"
)
[ "$OFFLOAD" = "1" ] && FLAGS+=( --offload-to-cpu )
[ "$VAE_TILING" = "1" ] && FLAGS+=( --vae-tiling )
exec "$BIN" "${FLAGS[@]}"
