# Sapphire Consumer Docker Image
# Self-contained: Kokoro TTS + Faster Whisper STT + Nomic Embeddings (all CPU)
# Web UI only — no wakeword, no audio passthrough

# ============================================================
# Stage 1: Base + system deps
# ============================================================
FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Non-root user
RUN groupadd -g 1000 sapphire && \
    useradd -u 1000 -g sapphire -m sapphire

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

# Create user data directory (will be overridden by volume mount)
RUN mkdir -p /app/user && chown -R sapphire:sapphire /app

# Switch to non-root
USER sapphire

EXPOSE 8073

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('https://localhost:8073/api/health', context=__import__('ssl').create_default_context() if False else __import__('ssl')._create_unverified_context())" || exit 1

CMD ["python", "main.py"]
