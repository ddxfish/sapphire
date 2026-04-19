# core/embeddings.py
# Pluggable embedding provider — local ONNX or remote API (same Nomic model)

import logging
import numpy as np
import config

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = 'nomic-ai/nomic-embed-text-v1.5'
EMBEDDING_ONNX_FILE = 'onnx/model_quantized.onnx'


class LocalEmbedder:
    """Lazy-loaded nomic-embed-text-v1.5 via ONNX runtime."""

    # Stable identifier stamped onto stored vectors. Read-path filters by this.
    # Change = everything previously written by this provider becomes invalid
    # until re-embedded, so don't rename casually.
    PROVIDER_ID = 'local:nomic-embed-text-v1.5'
    # Advertised dimension — actual stamped dim on write is derived from the
    # returned vector, this is for contract-checks at register time.
    DIMENSION = 768

    def __init__(self):
        self.session = None
        self.tokenizer = None
        self.input_names = None

    @property
    def provider_id(self):
        return self.PROVIDER_ID

    @property
    def dimension(self):
        return self.DIMENSION

    def _load(self):
        if self.session is not None:
            return
        try:
            import onnxruntime as ort
            from transformers import AutoTokenizer
            from huggingface_hub import hf_hub_download

            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    EMBEDDING_MODEL, trust_remote_code=True, local_files_only=True)
                model_path = hf_hub_download(
                    EMBEDDING_MODEL, EMBEDDING_ONNX_FILE, local_files_only=True)
            except Exception:
                logger.info(f"Downloading embedding model: {EMBEDDING_MODEL}")
                self.tokenizer = AutoTokenizer.from_pretrained(
                    EMBEDDING_MODEL, trust_remote_code=True)
                model_path = hf_hub_download(EMBEDDING_MODEL, EMBEDDING_ONNX_FILE)

            self.session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
            self.input_names = [i.name for i in self.session.get_inputs()]
            logger.info(f"Embedding model loaded: {EMBEDDING_MODEL} (quantized ONNX)")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            self.session = None

    def embed(self, texts, prefix='search_document'):
        self._load()
        if self.session is None:
            return None
        try:
            prefixed = [f'{prefix}: {t}' for t in texts]
            encoded = self.tokenizer(prefixed, return_tensors='np', padding=True,
                                     truncation=True, max_length=512)
            inputs = {k: v for k, v in encoded.items() if k in self.input_names}
            if 'token_type_ids' not in inputs:
                inputs['token_type_ids'] = np.zeros_like(inputs['input_ids'])

            outputs = self.session.run(None, inputs)
            embeddings = outputs[0]
            mask = encoded['attention_mask']
            masked = embeddings * mask[:, :, np.newaxis]
            pooled = masked.sum(axis=1) / mask.sum(axis=1, keepdims=True)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            norms[norms == 0] = 1
            return (pooled / norms).astype(np.float32)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return None

    @property
    def available(self):
        self._load()
        return self.session is not None


class RemoteEmbedder:
    """OpenAI-compatible embedding API client (for Nomic via TEI, etc.)."""

    # Remote providers don't declare dim statically — we learn it from the first
    # successful response. Provider identity is the URL itself (the real space
    # is defined by whatever model the server runs). Swapping the API URL to a
    # different model's endpoint is effectively a different provider.
    PROVIDER_ID = 'remote-api'

    def __init__(self):
        # Dimension discovered at first successful call and cached.
        self._observed_dim = None

    @property
    def provider_id(self):
        # Include URL host/path so swapping the URL is treated as a swap.
        url = getattr(config, 'EMBEDDING_API_URL', '')
        return f"{self.PROVIDER_ID}:{(url or '').strip() or 'unconfigured'}"

    @property
    def dimension(self):
        return self._observed_dim

    @staticmethod
    def _normalize_url(url):
        """Fix common URL mistakes — invisible UX."""
        from urllib.parse import urlparse, urlunparse
        url = url.strip()
        if not url:
            return ''
        if not url.startswith(('http://', 'https://')):
            url = f'http://{url}'
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        if not path.endswith('/v1/embeddings'):
            if path.endswith('/v1'):
                path += '/embeddings'
            elif not path.endswith('/embeddings'):
                path += '/v1/embeddings'
        return urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))

    def embed(self, texts, prefix='search_document'):
        raw_url = getattr(config, 'EMBEDDING_API_URL', '')
        url = self._normalize_url(raw_url)
        if not url:
            return None
        try:
            import httpx
            from core.credentials_manager import credentials
            key = credentials.get_service_api_key('embedding') or getattr(config, 'EMBEDDING_API_KEY', '')
            headers = {}
            if key:
                headers['Authorization'] = f'Bearer {key}'

            prefixed = [f'{prefix}: {t}' for t in texts]
            resp = httpx.post(url, json={'input': prefixed, 'model': EMBEDDING_MODEL},
                              headers=headers, timeout=30.0)
            resp.raise_for_status()
            data = resp.json().get('data', [])
            if not data:
                logger.warning("Remote embedding returned empty data")
                return None
            vecs = np.array([d['embedding'] for d in data], dtype=np.float32)
            # L2-normalize (safe regardless of server behavior)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1
            result = (vecs / norms).astype(np.float32)
            # Cache observed dimension for provenance stamping.
            self._observed_dim = int(result.shape[-1])
            return result
        except Exception as e:
            logger.error(f"Remote embedding failed: {e}")
            return None

    @property
    def available(self):
        return bool(self._normalize_url(getattr(config, 'EMBEDDING_API_URL', '')))


class SapphireRouterEmbedder:
    """Forwards embedding requests to a Sapphire Router."""

    PROVIDER_ID = 'sapphire-router'

    def __init__(self):
        self._observed_dim = None

    @property
    def provider_id(self):
        return f"{self.PROVIDER_ID}:{self._get_url() or 'unconfigured'}"

    @property
    def dimension(self):
        return self._observed_dim

    def _get_url(self):
        import os
        url = os.environ.get('SAPPHIRE_ROUTER_URL') or getattr(config, 'SAPPHIRE_ROUTER_URL', '')
        return url.rstrip('/')

    def _get_tenant_id(self):
        import os
        return os.environ.get('SAPPHIRE_TENANT_ID') or getattr(config, 'SAPPHIRE_ROUTER_TENANT_ID', '')

    def embed(self, texts, prefix='search_document'):
        url = self._get_url()
        if not url:
            return None
        try:
            import httpx
            headers = {'Content-Type': 'application/json'}
            tenant_id = self._get_tenant_id()
            if tenant_id:
                headers['X-Tenant-ID'] = tenant_id
            resp = httpx.post(
                f'{url}/v1/embeddings/embed',
                json={'texts': texts, 'prefix': prefix},
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if 'embeddings' in data:
                vecs = np.array(data['embeddings'], dtype=np.float32)
                # L2-normalize defensively — other core providers do, the router
                # may or may not, and cosine similarity + SIMILARITY_THRESHOLD
                # only make sense on unit vectors. Scout finding: without this,
                # mixing router-written vectors with Local/Remote-written ones
                # silently breaks ranking.
                norms = np.linalg.norm(vecs, axis=1, keepdims=True)
                norms[norms == 0] = 1
                result = (vecs / norms).astype(np.float32)
                self._observed_dim = int(result.shape[-1])
                return result
            return None
        except Exception as e:
            import httpx as _hx
            if isinstance(e, _hx.ConnectError):
                logger.error(f"Sapphire Router embeddings: cannot reach router at {url}")
            else:
                logger.error(f"Sapphire Router embedding failed: {e}")
            return None

    @property
    def available(self):
        return bool(self._get_url())


class NullEmbedder:
    """Disabled — consumers fall back to FTS5/LIKE search."""

    PROVIDER_ID = 'none'
    DIMENSION = 0

    @property
    def provider_id(self):
        return self.PROVIDER_ID

    @property
    def dimension(self):
        return self.DIMENSION

    def embed(self, texts, prefix='search_document'):
        return None

    @property
    def available(self):
        return False


# ─── Registry ────────────────────────────────────────────────────────────────

from core.provider_registry import BaseProviderRegistry


def _validate_plugin_provider_class(cls, plugin_name, key):
    """Static contract check — enforced at plugin registration time.

    A well-meaning plugin author shouldn't be able to eat the user's memory
    with a sloppy embedding provider. The required contract is minimal:
      - `embed(texts, prefix)` method
      - `available` property
      - `PROVIDER_ID` stable string — stamped on every vector this provider
        writes. Missing this would mean stored vectors can't be filtered
        after a swap.
    `DIMENSION` is optional — remote providers learn dim at first call.

    Returns list of error strings; empty means OK.
    """
    errs = []
    if not callable(getattr(cls, 'embed', None)):
        errs.append("missing callable `embed(texts, prefix)` method")
    if not hasattr(cls, 'available'):
        errs.append("missing `available` property")
    pid = getattr(cls, 'PROVIDER_ID', None)
    if not pid or not isinstance(pid, str):
        errs.append(
            "missing `PROVIDER_ID` class attribute — required for plugin "
            "embedding providers. This stable string is stamped on every "
            "vector your provider writes so a future provider swap can "
            "filter out your rows cleanly. Pick a short stable identifier "
            "like 'myplugin:model-v1'."
        )
    return errs


def _canary_embed(instance):
    """Runtime check on an instance. Runs once when a plugin-backed provider
    is instantiated; result is cached per class. Returns (ok, message).

    A plugin provider that fails this canary is NOT usable — we fall back to
    NullEmbedder so Sapphire boots but vector search is disabled. Loud log
    so the user sees exactly what's wrong.
    """
    import numpy as _np
    if not getattr(instance, 'available', False):
        # A provider that reports unavailable is a legal state (e.g. API URL
        # not configured). Don't fail — NullEmbedder-equivalent behavior.
        return True, "provider reports unavailable, skipping canary"
    try:
        out = instance.embed(['canary check'], prefix='search_document')
    except Exception as e:
        return False, f"embed() raised: {type(e).__name__}: {e}"
    if out is None:
        # None = transient failure, not a contract break. Allow — next call retries.
        return True, "embed() returned None at canary (transient); contract OK"
    try:
        out = _np.asarray(out)
    except Exception as e:
        return False, f"embed() returned unconvertible type {type(out).__name__}: {e}"
    if out.ndim != 2 or out.shape[0] != 1:
        return False, f"embed() returned shape {out.shape}, expected (1, D)"
    if out.dtype != _np.float32:
        return False, (
            f"embed() returned dtype {out.dtype}, expected float32. "
            f"Plugin authors: call `.astype(np.float32)` on your output."
        )
    if not _np.all(_np.isfinite(out)):
        return False, "embed() returned non-finite values (NaN or Inf)"
    norm = float(_np.linalg.norm(out[0]))
    if not (0.95 < norm < 1.05):
        return False, (
            f"embed() returned non-normalized vector (L2 norm = {norm:.3f}). "
            f"Expected unit-length. Normalize with `v / np.linalg.norm(v)` "
            f"before returning — cosine-similarity search assumes unit vectors."
        )
    return True, "ok"


# Cache canary result per plugin provider class (id-keyed so hot-reload of a
# plugin creates a new class object and re-runs the canary).
_plugin_canary_cache = {}


class EmbeddingRegistry(BaseProviderRegistry):
    """Embedding provider registry — core + plugin providers."""

    def __init__(self):
        super().__init__('embedding', 'EMBEDDING_PROVIDER')
        self.register_core('local', LocalEmbedder, 'Local (Nomic)', is_local=True)
        self.register_core('api', RemoteEmbedder, 'Remote (Nomic API)', is_local=False)
        # sapphire_router is surfaced in the Settings UI — must be registered here
        # too or selecting it silently falls through to NullEmbedder and every
        # save lands with embedding=NULL. Scout finding, active bug pre-fix.
        self.register_core('sapphire_router', SapphireRouterEmbedder, 'Sapphire Router', is_local=False)
        self.register_core('none', NullEmbedder, 'None (disabled)', is_local=True)

    def register_plugin(self, key, provider_class, display_name, plugin_name, **metadata):
        """Plugin registers a custom provider. Runs static contract validation
        before accepting the registration — a plugin with a broken provider
        class is refused outright rather than left to cause data corruption
        later."""
        errs = _validate_plugin_provider_class(provider_class, plugin_name, key)
        if errs:
            logger.error(
                f"[embedding] Plugin '{plugin_name}' provider '{key}' failed contract: "
                + "; ".join(errs)
            )
            return  # Do not register — user will see provider missing from UI
        return super().register_plugin(key, provider_class, display_name, plugin_name, **metadata)

    def create(self, key, **kwargs):
        entry = self._core.get(key) or self._plugins.get(key)
        if not entry:
            if key and key != 'none':
                logger.warning(f"[embedding] Unknown provider '{key}', falling back to null")
            entry = self._core.get('none')
            if not entry:
                return None
        cls = entry['class']
        is_plugin = key in self._plugins
        try:
            instance = cls()
        except Exception as e:
            logger.error(f"[embedding] Failed to create '{key}': {e}")
            return NullEmbedder()

        # Plugin providers go through a runtime canary — validates they actually
        # produce sane vectors. Core providers are trusted (tested in-repo).
        # Result cached per class so we only pay this cost once per process.
        if is_plugin:
            cached = _plugin_canary_cache.get(id(cls))
            if cached is None:
                ok, msg = _canary_embed(instance)
                _plugin_canary_cache[id(cls)] = (ok, msg)
                if ok:
                    logger.info(f"[embedding] Plugin '{key}' canary passed: {msg}")
                else:
                    logger.error(
                        f"[embedding] Plugin '{key}' failed canary — disabling. "
                        f"Reason: {msg}"
                    )
                    return NullEmbedder()
            elif not cached[0]:
                logger.debug(f"[embedding] Plugin '{key}' canary previously failed — using null")
                return NullEmbedder()
        return instance


embedding_registry = EmbeddingRegistry()


# ─── Singleton + hot-swap ────────────────────────────────────────────────────

import threading
_embedder = None
_embedder_lock = threading.Lock()


def get_embedder():
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:  # double-check after lock
                key = embedding_registry.get_active_key()
                _embedder = embedding_registry.create(key)
    return _embedder


def switch_embedding_provider(provider_name):
    global _embedder
    with _embedder_lock:
        logger.info(f"Switching embedding provider to: {provider_name}")
        _embedder = embedding_registry.create(provider_name or 'none')
    # Reset backfill flag so new provider can re-embed missing memories
    try:
        import plugins.memory.tools.memory_tools as mem
        mem._backfill_done = False
    except Exception:
        pass
    try:
        import plugins.memory.tools.knowledge_tools as know
        know._backfill_done = False
    except Exception:
        pass


def current_provenance():
    """Return (provider_id, dimension) for the currently-active embedder.
    Either may be None if the embedder isn't usable or dim isn't known yet
    (remote providers learn dim from first successful response).

    Write paths stamp rows with this. Read paths filter by this. The pair is
    the load-bearing identity that prevents silently mixing vector spaces.
    """
    embedder = get_embedder()
    if not embedder:
        return None, None
    provider_id = getattr(embedder, 'provider_id', None)
    dim = getattr(embedder, 'dimension', None)
    return provider_id, dim


def stamp_embedding(vector, embedder=None):
    """Return (blob, provider_id, dim) for a vector about to be written.

    `vector` is a 1-D numpy array (a single embedding, not a batch).
    `embedder` — the specific embedder instance that produced the vector.
    Pass it explicitly when the caller already has a reference — otherwise
    a concurrent `switch_embedding_provider` could make `current_provenance()`
    return a different provider than the one that actually produced the vec.

    Returned values stamp the row: use `(blob, provider_id, dim)` on INSERT.
    """
    import numpy as _np
    if vector is None:
        return None, None, None
    arr = _np.asarray(vector, dtype=_np.float32)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    if embedder is None:
        embedder = get_embedder()
    provider_id = getattr(embedder, 'provider_id', None) if embedder else None
    return arr.tobytes(), provider_id, int(arr.shape[0])


def integrity_report():
    """Scan all vector-storing tables and report stamp distribution.

    Used by the UI to show the user what they'd invalidate before swapping
    providers, and by the re-embed pipeline to find rows that need attention.

    Returns:
        {
            'active': {'provider': str|None, 'dim': int|None},
            'tables': {
                'memories': {
                    'total': int,              # rows with a blob
                    'matching_active': int,    # rows that current provider can search
                    'legacy_unstamped': int,   # rows with NULL provider (pre-provenance)
                    'other_stamps': int,       # rows stamped with a different provider/dim
                    'by_stamp': [{'provider': str|None, 'dim': int|None, 'count': int}]
                },
                'knowledge_entries': {...},
                'people': {...}
            }
        }
    """
    import sqlite3 as _sql
    report = {'active': {}, 'tables': {}}
    active_pid, active_dim = current_provenance()
    report['active'] = {'provider': active_pid, 'dim': active_dim}

    def _scan(open_conn_ctx, table):
        try:
            with open_conn_ctx() as conn:
                cur = conn.cursor()
                cur.execute(
                    f'SELECT embedding_provider, embedding_dim, COUNT(*) '
                    f'FROM {table} WHERE embedding IS NOT NULL '
                    f'GROUP BY embedding_provider, embedding_dim'
                )
                rows = cur.fetchall()
        except _sql.OperationalError:
            # Table or columns missing (first boot, pre-migration) — report empty.
            return {
                'total': 0, 'matching_active': 0, 'legacy_unstamped': 0,
                'other_stamps': 0, 'by_stamp': [],
            }
        total = 0
        matching = 0
        legacy = 0
        other = 0
        by_stamp = []
        for pid, dim, count in rows:
            total += count
            by_stamp.append({'provider': pid, 'dim': dim, 'count': count})
            if pid is None or dim is None:
                legacy += count
            elif active_pid and active_dim and pid == active_pid and dim == active_dim:
                matching += count
            else:
                other += count
        by_stamp.sort(key=lambda x: x['count'], reverse=True)
        return {
            'total': total,
            'matching_active': matching,
            'legacy_unstamped': legacy,
            'other_stamps': other,
            'by_stamp': by_stamp,
        }

    try:
        from plugins.memory.tools import memory_tools as _mt
        report['tables']['memories'] = _scan(_mt._get_connection, 'memories')
    except Exception as e:
        logger.debug(f"integrity_report: memories scan failed: {e}")
        report['tables']['memories'] = {
            'total': 0, 'matching_active': 0, 'legacy_unstamped': 0,
            'other_stamps': 0, 'by_stamp': [],
        }

    try:
        from plugins.memory.tools import knowledge_tools as _kt
        report['tables']['knowledge_entries'] = _scan(_kt._get_connection, 'knowledge_entries')
        report['tables']['people'] = _scan(_kt._get_connection, 'people')
    except Exception as e:
        logger.debug(f"integrity_report: knowledge scan failed: {e}")
        for t in ('knowledge_entries', 'people'):
            report['tables'].setdefault(t, {
                'total': 0, 'matching_active': 0, 'legacy_unstamped': 0,
                'other_stamps': 0, 'by_stamp': [],
            })

    return report
