"""MiniLM embedding provider plugin.

Wraps sentence-transformers/all-MiniLM-L6-v2 via the `transformers` library
directly (no extra deps beyond what Sapphire already requires for the local
Nomic provider). Produces 384-dim L2-normalized float32 vectors — different
dimension from the default 768-dim Nomic, so swapping providers exercises
the full cross-dimension transfer path (invalidation on write, drift-check
on re-embed, re-stamp of all stored rows under the new provenance).

Contract mirrors core.embeddings.LocalEmbedder:
  - `PROVIDER_ID` class attribute — stable id stamped on every stored vector
  - `provider_id` / `dimension` properties — read by current_provenance()
  - `available` property — True once the model is loaded
  - `embed(texts, prefix=...)` — returns np.ndarray (N, 384) float32,
    unit-norm per row, or None on failure

The `prefix` arg is accepted for contract compatibility; all-MiniLM-L6-v2
doesn't use instruction prefixes the way Nomic does, so it's ignored.

Model downloads to `~/.cache/huggingface/hub/` on first call (~80MB).
"""
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class MiniLMEmbedder:
    PROVIDER_ID = 'plugin:minilm-L6-v2'
    MODEL_NAME = 'sentence-transformers/all-MiniLM-L6-v2'
    DIMENSION = 384

    def __init__(self):
        self._tokenizer = None
        self._model = None
        self._torch = None
        self.load_error: Optional[str] = None

    @property
    def provider_id(self) -> str:
        return self.PROVIDER_ID

    @property
    def dimension(self) -> int:
        return self.DIMENSION

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
        except ImportError as e:
            self.load_error = f"torch import failed: {e}"
            logger.error(f"[minilm] {self.load_error}")
            return
        try:
            from transformers import AutoTokenizer, AutoModel
        except ImportError as e:
            self.load_error = f"transformers import failed: {e}"
            logger.error(f"[minilm] {self.load_error}")
            return

        # Try local cache first; fall back to download. Matches LocalEmbedder's
        # pattern — keeps boot fast when the cache is warm and doesn't spam
        # the logs about "downloading" on normal restarts.
        try:
            try:
                self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME, local_files_only=True)
                self._model = AutoModel.from_pretrained(self.MODEL_NAME, local_files_only=True)
            except Exception:
                logger.info(f"[minilm] Downloading {self.MODEL_NAME} (~80MB, one-time)")
                self._tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
                self._model = AutoModel.from_pretrained(self.MODEL_NAME)

            self._model.eval()
            self._torch = torch
            self.load_error = None
            logger.info(f"[minilm] Loaded {self.MODEL_NAME} (dim={self.DIMENSION}, CPU)")
        except Exception as e:
            self.load_error = f"MiniLM load failed: {e}"
            logger.error(f"[minilm] {self.load_error}")
            self._model = None
            self._tokenizer = None

    @property
    def available(self) -> bool:
        self._load()
        return self._model is not None

    @staticmethod
    def _mean_pool(last_hidden, attention_mask, torch):
        """Mean-pool token embeddings over non-padding positions."""
        mask = attention_mask.unsqueeze(-1).expand(last_hidden.size()).float()
        summed = torch.sum(last_hidden * mask, dim=1)
        denom = torch.clamp(mask.sum(dim=1), min=1e-9)
        return summed / denom

    def embed(self, texts, prefix: str = 'search_document'):
        """Embed a list of strings. Returns np.ndarray (N, 384) float32 or None.

        `prefix` is accepted for contract compatibility (LocalEmbedder uses it
        for Nomic's instruction prefixes) but ignored — MiniLM doesn't use
        them.
        """
        self._load()
        if self._model is None:
            return None
        try:
            torch = self._torch
            encoded = self._tokenizer(
                list(texts),
                padding=True,
                truncation=True,
                max_length=256,
                return_tensors='pt',
            )
            with torch.no_grad():
                out = self._model(**encoded)
            pooled = self._mean_pool(out[0], encoded['attention_mask'], torch)
            # L2-normalize (sentence-transformers convention). Canary enforces
            # unit-norm ± drift-band, so this is load-bearing.
            norms = torch.clamp(pooled.norm(p=2, dim=1, keepdim=True), min=1e-9)
            normalized = pooled / norms
            return normalized.cpu().numpy().astype(np.float32)
        except Exception as e:
            logger.error(f"[minilm] embed failed: {e}")
            return None
