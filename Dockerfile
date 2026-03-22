# Sapphire Consumer Docker Image
# Self-contained: Kokoro TTS + Faster Whisper STT + Nomic Embeddings
#
# CPU:  docker build -t sapphire:cpu .
# GPU:  docker build --build-arg BASE_IMAGE=nvidia/cuda:12.4.1-runtime-ubuntu22.04 -t sapphire:gpu .

# ============================================================
# Stage 1: Base + system deps
# ============================================================
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE} AS base

# Prevent interactive prompts during apt-get (tzdata, etc.)
ENV DEBIAN_FRONTEND=noninteractive

# GPU images (Ubuntu base) don't have Python — install 3.11 from deadsnakes PPA
RUN if ! command -v python3.11 >/dev/null 2>&1 && ! python3 --version 2>&1 | grep -q "3.11"; then \
        apt-get update && apt-get install -y --no-install-recommends \
            software-properties-common \
        && add-apt-repository -y ppa:deadsnakes/ppa \
        && apt-get update && apt-get install -y --no-install-recommends \
            python3.11 python3.11-venv python3.11-dev python3.11-distutils tzdata \
        && python3.11 -m ensurepip \
        && ln -sf python3.11 /usr/bin/python3 \
        && ln -sf python3.11 /usr/bin/python \
        && ln -sf /usr/local/bin/pip3.11 /usr/bin/pip || true \
        && rm -rf /var/lib/apt/lists/*; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    libportaudio2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user
RUN groupadd -g 1000 sapphire 2>/dev/null || true && \
    useradd -u 1000 -g 1000 -m sapphire 2>/dev/null || true

# ============================================================
# Stage 2: Python dependencies (cached layer — rarely changes)
# ============================================================
FROM base AS deps

# Core + TTS + STT + Embeddings deps
COPY install/requirements-minimal.txt /tmp/requirements-minimal.txt
COPY install/requirements-tts.txt /tmp/requirements-tts.txt
COPY install/requirements-stt.txt /tmp/requirements-stt.txt

RUN pip install --no-cache-dir \
    -r /tmp/requirements-minimal.txt \
    -r /tmp/requirements-tts.txt \
    -r /tmp/requirements-stt.txt \
    onnxruntime \
    transformers \
    huggingface_hub

# ============================================================
# Stage 3: Pre-download ML models (cached layer — rarely changes)
# ============================================================
FROM deps AS models

ENV HF_HOME=/app/models

# Download Nomic embeddings (ONNX quantized, ~70MB)
RUN python -c "\
from huggingface_hub import hf_hub_download; \
from transformers import AutoTokenizer; \
AutoTokenizer.from_pretrained('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True); \
hf_hub_download('nomic-ai/nomic-embed-text-v1.5', 'onnx/model_quantized.onnx'); \
print('Nomic embeddings cached')"

# Download Faster Whisper base.en model (~140MB)
RUN python -c "\
from faster_whisper import WhisperModel; \
WhisperModel('base.en', device='cpu', compute_type='int8'); \
print('Whisper base.en cached')"

# Pre-load Kokoro model (downloads on first KPipeline init, ~500MB)
RUN python -c "\
from kokoro import KPipeline; \
KPipeline(lang_code='a'); \
print('Kokoro model cached')"

# ============================================================
# Stage 4: Final image — copy code (changes often, small layer)
# ============================================================
FROM models AS final

# Set model cache location (must match stage 3)
ENV HF_HOME=/app/models
ENV TORCH_HOME=/app/models/torch

# Docker-specific settings — only infrastructure concerns that MUST differ in Docker
# Do NOT put user-changeable settings here (env vars always win over Settings UI)
ENV SAPPHIRE_DOCKER=true
ENV WEB_UI_HOST=0.0.0.0
ENV WEB_UI_PORT=8073

# Copy application code
COPY . /app

# Create mount point directories (bind mounts override these)
RUN mkdir -p /app/user /app/user_backups && chown -R sapphire:sapphire /app

# Switch to non-root
USER sapphire

EXPOSE 8073

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsk https://localhost:8073/api/health || exit 1

CMD ["python", "main.py"]
