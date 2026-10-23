# plugins/mindpalace/tools/vision_embed.py
# Local vision embedder for Library images (Arc 2, I3).
#
# nomic-embed-vision-v1.5 shares nomic-embed-text-v1.5's vector space
# (contrastively aligned) — a text query embedded by the TEXT model ranks
# images embedded here, no captions needed. Mirrors core's LocalEmbedder
# exactly: official quantized ONNX (~97MB) via hf_hub_download → the same
# HF cache the text model lives in (~/.cache/huggingface on Linux/macOS;
# user/models/hf on Windows, excluded from backups by the cache floor).
#
# Preprocessing is hand-rolled from the repo's preprocessor_config.json
# (resize shortest side → center crop → rescale → normalize) — Pillow +
# numpy only, no torch, no magic.

import json
import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

VISION_MODEL = 'nomic-ai/nomic-embed-vision-v1.5'
VISION_ONNX_FILE = 'onnx/model_quantized.onnx'
PROVIDER_ID = 'local:nomic-embed-vision-v1.5'
DIMENSION = 768


class VisionEmbedder:
    """Lazy-loaded, failure-honest. `embed_images(paths)` → (N, 768) unit
    float32, or None (model missing, a path unreadable is skipped as None
    row is NOT an option — caller gets per-image None via embed_one)."""

    def __init__(self):
        self.session = None
        self.input_name = None
        self._pp = None
        self.load_error = None
        self._lock = threading.Lock()

    @property
    def provider_id(self):
        return PROVIDER_ID

    @property
    def dimension(self):
        return DIMENSION

    def _load(self):
        if self.session is not None:
            return
        with self._lock:
            if self.session is not None:
                return
            try:
                import onnxruntime as ort
                from huggingface_hub import hf_hub_download
            except ImportError as e:
                self.load_error = f"vision embed deps missing: {e}"
                logger.error(f"[VISION] {self.load_error}")
                return
            try:
                try:
                    model_path = hf_hub_download(VISION_MODEL, VISION_ONNX_FILE,
                                                 local_files_only=True)
                    pp_path = hf_hub_download(VISION_MODEL,
                                              'preprocessor_config.json',
                                              local_files_only=True)
                except Exception:
                    logger.info(f"[VISION] downloading {VISION_MODEL} (~97MB, "
                                f"one time)")
                    model_path = hf_hub_download(VISION_MODEL, VISION_ONNX_FILE)
                    pp_path = hf_hub_download(VISION_MODEL,
                                              'preprocessor_config.json')
                with open(pp_path, encoding='utf-8') as f:
                    self._pp = json.load(f)
                self.session = ort.InferenceSession(
                    model_path, providers=['CPUExecutionProvider'])
                self.input_name = self.session.get_inputs()[0].name
                self.load_error = None
                logger.info(f"[VISION] model loaded: {VISION_MODEL} (quantized ONNX)")
            except Exception as e:
                self.load_error = f"vision model fetch/load failed: {e}"
                logger.error(f"[VISION] {self.load_error}")
                self.session = None

    @property
    def available(self):
        self._load()
        return self.session is not None

    def _preprocess(self, img):
        """PIL image → (3, H, W) float32 per preprocessor_config.json."""
        pp = self._pp or {}
        size = pp.get('size') or {}
        short = size.get('shortest_edge') or size.get('height') or 224
        crop = pp.get('crop_size') or {}
        ch = crop.get('height') or short
        cw = crop.get('width') or short
        img = img.convert('RGB')
        w, h = img.size
        scale = short / min(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))))
        w, h = img.size
        left, top = (w - cw) // 2, (h - ch) // 2
        img = img.crop((left, top, left + cw, top + ch))
        arr = np.asarray(img, dtype=np.float32)
        if pp.get('do_rescale', True):
            arr = arr * float(pp.get('rescale_factor', 1.0 / 255.0))
        mean = np.array(pp.get('image_mean', [0.485, 0.456, 0.406]),
                        dtype=np.float32)
        std = np.array(pp.get('image_std', [0.229, 0.224, 0.225]),
                       dtype=np.float32)
        arr = (arr - mean) / std
        return arr.transpose(2, 0, 1)

    def embed_images(self, pil_images):
        """[PIL.Image] → (N, 768) unit float32, or None if the model is
        down. ViT CLS pooling (index 0) — nomic's documented contract."""
        self._load()
        if self.session is None:
            return None
        try:
            batch = np.stack([self._preprocess(im) for im in pil_images])
            out = self.session.run(None, {self.input_name: batch})[0]
            vecs = out[:, 0, :] if out.ndim == 3 else out
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1
            return (vecs / norms).astype(np.float32)
        except Exception as e:
            logger.error(f"[VISION] embed failed: {e}")
            return None

    def embed_paths(self, paths):
        """[Path] → list aligned with input: unit vec or None per image
        (unreadable files skip without sinking the batch)."""
        from PIL import Image, ImageOps
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        imgs, idx = [], []
        for i, p in enumerate(paths):
            try:
                imgs.append(ImageOps.exif_transpose(Image.open(p)))
                idx.append(i)
            except Exception as e:
                logger.warning(f"[VISION] unreadable image {p}: {e}")
        if not imgs:
            return [None] * len(paths)
        vecs = self.embed_images(imgs)
        out = [None] * len(paths)
        if vecs is None:
            return out
        for j, i in enumerate(idx):
            out[i] = vecs[j]
        return out


_embedder = None
_embedder_lock = threading.Lock()


def get_vision_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                _embedder = VisionEmbedder()
    return _embedder
