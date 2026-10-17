"""The tone model loads cache-first through local_files_only on the load
itself, never by toggling the process-wide HF_HUB_OFFLINE env (which a Kokoro
subprocess spawned in that window inherited for life, 2026-10-15)."""
import os
import sys
import types

from plugins.discord.conversation import sentiment as st


def _fake_transformers(monkeypatch, cached: bool):
    calls, env_seen = [], []

    class _Auto:
        @classmethod
        def from_pretrained(cls, name, local_files_only=False):
            calls.append((cls.__name__, name, local_files_only))
            env_seen.append(os.environ.get('HF_HUB_OFFLINE'))
            if local_files_only and not cached:
                raise OSError("not in the cached files")
            return cls.__name__

    class AutoTokenizer(_Auto): ...
    class AutoModelForSequenceClassification(_Auto): ...

    def pipeline(task, model=None, tokenizer=None, **kw):
        return lambda text: [[{'label': 'positive', 'score': 0.9}, {'label': 'negative', 'score': 0.1}]]

    fake = types.ModuleType('transformers')
    fake.AutoTokenizer, fake.AutoModelForSequenceClassification, fake.pipeline = (
        AutoTokenizer, AutoModelForSequenceClassification, pipeline)
    monkeypatch.setitem(sys.modules, 'transformers', fake)
    monkeypatch.setattr(st, '_pipe', None)
    monkeypatch.setattr(st, '_failed', False)
    monkeypatch.delenv('HF_HUB_OFFLINE', raising=False)
    return calls, env_seen


def test_warm_cache_loads_local_only_and_never_touches_the_env(monkeypatch):
    calls, env_seen = _fake_transformers(monkeypatch, cached=True)
    assert st.compound('great') > 0
    assert [c[2] for c in calls] == [True, True]            # tokenizer + model, local only
    assert env_seen == [None, None] and 'HF_HUB_OFFLINE' not in os.environ


def test_cold_cache_falls_back_to_one_online_load(monkeypatch):
    calls, env_seen = _fake_transformers(monkeypatch, cached=False)
    assert st.compound('great') > 0
    assert [c[2] for c in calls] == [True, False, False]     # local miss, then online tokenizer + model
    assert set(env_seen) == {None} and 'HF_HUB_OFFLINE' not in os.environ
