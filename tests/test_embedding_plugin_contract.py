"""Plugin embedding provider contract + canary (2026-04-19, Phase C).

Scout finding #3: a third-party plugin could register an embedding provider
with no DIMENSION, no PROVIDER_ID, non-float32 output, non-normalized
vectors, or any other sloppy behavior. None of it was validated. After this
pass:
  - register_plugin refuses classes missing PROVIDER_ID / embed / available
  - create() runs a one-time canary on plugin instances:
      * embed returns (1, D) ndarray
      * dtype is float32
      * values are finite
      * L2-norm within [0.95, 1.05]
    A provider that fails the canary is replaced with NullEmbedder and
    logged, so Sapphire boots safely.
"""
import numpy as np
import pytest


# ─── Static class validation ──────────────────────────────────────────────

def test_register_plugin_refuses_class_missing_provider_id():
    """[REGRESSION_GUARD] Required contract: PROVIDER_ID on plugin provider
    class. Without it stored vectors can't be filtered after a swap."""
    from core.embeddings import EmbeddingRegistry

    class BadNoProviderId:
        def embed(self, texts, prefix='search_document'):
            return None
        available = False

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-no-pid', BadNoProviderId, 'Bad', 'bad-plugin')
    assert not reg.has_key('bad-no-pid'), \
        "plugin provider missing PROVIDER_ID must be refused at register_plugin"


def test_register_plugin_refuses_class_missing_embed():
    from core.embeddings import EmbeddingRegistry

    class BadNoEmbed:
        PROVIDER_ID = 'bad:x'
        available = False

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-no-embed', BadNoEmbed, 'Bad', 'bad-plugin')
    assert not reg.has_key('bad-no-embed')


def test_register_plugin_refuses_class_missing_available():
    from core.embeddings import EmbeddingRegistry

    class BadNoAvailable:
        PROVIDER_ID = 'bad:x'

        def embed(self, texts, prefix='search_document'):
            return None

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-no-avail', BadNoAvailable, 'Bad', 'bad-plugin')
    assert not reg.has_key('bad-no-avail')


def test_register_plugin_accepts_valid_class():
    from core.embeddings import EmbeddingRegistry

    class GoodProvider:
        PROVIDER_ID = 'good:test'

        @property
        def available(self):
            return False

        def embed(self, texts, prefix='search_document'):
            return None

    reg = EmbeddingRegistry()
    reg.register_plugin('good-plugin', GoodProvider, 'Good', 'good-plugin')
    assert reg.has_key('good-plugin')


# ─── Runtime canary ───────────────────────────────────────────────────────

def _build_good_provider(dim=64, provider_id='canary:good'):
    class GoodEmbedder:
        PROVIDER_ID = provider_id

        @property
        def available(self):
            return True

        def embed(self, texts, prefix='search_document'):
            base = np.zeros(dim, dtype=np.float32)
            base[0] = 1.0
            return np.stack([base.copy() for _ in texts]).astype(np.float32)
    return GoodEmbedder


def test_canary_passes_for_well_behaved_provider():
    from core.embeddings import EmbeddingRegistry
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()
    reg = EmbeddingRegistry()
    cls = _build_good_provider()
    reg.register_plugin('good', cls, 'Good', 'good-plugin')
    inst = reg.create('good')
    assert isinstance(inst, cls), f"canary-passing provider must be returned; got {type(inst).__name__}"


def test_canary_fails_on_non_float32():
    from core.embeddings import EmbeddingRegistry, NullEmbedder
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class BadDtype:
        PROVIDER_ID = 'bad:dtype'
        available = True

        def embed(self, texts, prefix='search_document'):
            base = np.zeros(64, dtype=np.float64)  # wrong dtype
            base[0] = 1.0
            return np.stack([base.copy() for _ in texts])

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-dtype', BadDtype, 'BadDtype', 'bad-plugin')
    inst = reg.create('bad-dtype')
    assert isinstance(inst, NullEmbedder), \
        f"provider returning float64 must be rejected; got {type(inst).__name__}"


def test_canary_fails_on_wrong_shape():
    from core.embeddings import EmbeddingRegistry, NullEmbedder
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class BadShape:
        PROVIDER_ID = 'bad:shape'
        available = True

        def embed(self, texts, prefix='search_document'):
            # 1-D instead of 2-D
            return np.zeros(64, dtype=np.float32)

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-shape', BadShape, 'BadShape', 'bad-plugin')
    inst = reg.create('bad-shape')
    assert isinstance(inst, NullEmbedder)


def test_canary_fails_on_nan_output():
    from core.embeddings import EmbeddingRegistry, NullEmbedder
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class NaNProvider:
        PROVIDER_ID = 'bad:nan'
        available = True

        def embed(self, texts, prefix='search_document'):
            out = np.full((len(texts), 64), np.nan, dtype=np.float32)
            return out

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-nan', NaNProvider, 'BadNaN', 'bad-plugin')
    inst = reg.create('bad-nan')
    assert isinstance(inst, NullEmbedder)


def test_canary_fails_on_non_normalized():
    from core.embeddings import EmbeddingRegistry, NullEmbedder
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class UnnormalizedProvider:
        PROVIDER_ID = 'bad:unnormalized'
        available = True

        def embed(self, texts, prefix='search_document'):
            # Large magnitude, not unit-norm
            out = np.full((len(texts), 64), 5.0, dtype=np.float32)
            return out

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-norm', UnnormalizedProvider, 'BadNorm', 'bad-plugin')
    inst = reg.create('bad-norm')
    assert isinstance(inst, NullEmbedder)


def test_canary_fails_on_embed_raising():
    from core.embeddings import EmbeddingRegistry, NullEmbedder
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class CrashingProvider:
        PROVIDER_ID = 'bad:crash'
        available = True

        def embed(self, texts, prefix='search_document'):
            raise RuntimeError("plugin bug")

    reg = EmbeddingRegistry()
    reg.register_plugin('bad-crash', CrashingProvider, 'BadCrash', 'bad-plugin')
    inst = reg.create('bad-crash')
    assert isinstance(inst, NullEmbedder)


def test_canary_tolerates_unavailable_provider():
    """Provider that reports available=False at canary time is legal — it's
    just not configured yet (e.g. API URL unset). Registration passes, no
    NullEmbedder substitution on this call."""
    from core.embeddings import EmbeddingRegistry
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    class UnconfiguredProvider:
        PROVIDER_ID = 'plugin:unconfigured'

        @property
        def available(self):
            return False

        def embed(self, texts, prefix='search_document'):
            return None

    reg = EmbeddingRegistry()
    reg.register_plugin('unconfigured', UnconfiguredProvider, 'Unconfigured', 'u-plugin')
    inst = reg.create('unconfigured')
    assert isinstance(inst, UnconfiguredProvider)


def test_canary_result_cached_per_class():
    """[REGRESSION_GUARD] Canary result cached by class id — not re-run on
    every create(). A passing provider pays the canary cost once."""
    from core.embeddings import EmbeddingRegistry
    import core.embeddings as emb
    emb._plugin_canary_cache.clear()

    call_count = {'n': 0}
    cls = _build_good_provider()

    original_embed = cls.embed
    def counting_embed(self, texts, prefix='search_document'):
        call_count['n'] += 1
        return original_embed(self, texts, prefix)
    cls.embed = counting_embed

    reg = EmbeddingRegistry()
    reg.register_plugin('cached', cls, 'Cached', 'c-plugin')
    reg.create('cached')
    reg.create('cached')
    reg.create('cached')
    # Canary ran once; subsequent instantiations skip it
    assert call_count['n'] == 1, f"canary ran {call_count['n']} times, expected 1 (cache)"
