"""Per-scope residency (2026-07-19): which prompt/model tends each mind,
and which librarian passes a scope opted into. The multi-resident fix —
before this, one global persona tended every scope.

Package-path imports on purpose (see test_mindpalace_metadata's docstring).
"""
import re

import pytest

import core.chat.function_manager as fm
from plugins.mindpalace.tools import palace_tools as pt
from plugins.mindpalace.tools import librarian


class _FakeEmbedder:
    provider_id = "fake"

    @property
    def available(self):
        return False

    def embed(self, texts, prefix="search_document"):
        return None


@pytest.fixture(autouse=True)
def _clean_tool_context():
    fm.tool_context.set(None)
    yield
    fm.tool_context.set(None)


@pytest.fixture
def palace(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_db_path", tmp_path / "mind.db", raising=False)
    monkeypatch.setattr(pt, "_db_initialized", False, raising=False)
    monkeypatch.setattr(pt, "_backfill_done", True, raising=False)
    monkeypatch.setattr(pt, "_get_embedder", lambda: _FakeEmbedder(), raising=False)
    monkeypatch.setattr(librarian, "_snapshots", {}, raising=False)
    return pt


def test_resident_defaults_empty(palace):
    res = pt.scope_resident('anita')
    assert res == {'prompt': None, 'provider': None, 'model': None,
                   'passes': {}, 'watched_prompt': None, 'prompt_ledger': True}


def test_prompt_ledger_toggle_roundtrip(palace):
    pt.set_scope_resident('anita', prompt_ledger=False)
    assert pt.scope_resident('anita')['prompt_ledger'] is False
    pt.set_scope_resident('anita', model='glm-9b')   # untouched by other saves
    assert pt.scope_resident('anita')['prompt_ledger'] is False
    pt.set_scope_resident('anita', prompt_ledger=True)
    assert pt.scope_resident('anita')['prompt_ledger'] is True


def test_resident_roundtrip_and_clear(palace):
    assert pt.set_scope_resident('anita', prompt='anita-prompt',
                                 provider='fireworks', model='glm-9b',
                                 passes={'dedup': True, 'dates': 0})
    res = pt.scope_resident('anita')
    assert res['prompt'] == 'anita-prompt' and res['model'] == 'glm-9b'
    assert res['provider'] == 'fireworks'
    assert res['passes'] == {'dedup': True, 'dates': False}   # bool-coerced
    # Partial update: only model changes; '' clears it.
    pt.set_scope_resident('anita', model='')
    res = pt.scope_resident('anita')
    assert res['prompt'] == 'anita-prompt' and res['model'] is None
    assert res['passes'] == {'dedup': True, 'dates': False}   # untouched


def test_scope_pass_enabled_default_off(palace):
    assert librarian.scope_pass_enabled('dedup', 'anita') is False
    pt.set_scope_resident('anita', passes={'dedup': True})
    assert librarian.scope_pass_enabled('dedup', 'anita') is True
    assert librarian.scope_pass_enabled('sort', 'anita') is False


def test_task_runs_as_the_scopes_resident(palace, monkeypatch):
    pt.set_scope_resident('anita', prompt='anita-prompt',
                          provider='fireworks', model='glm-9b')
    monkeypatch.setattr(librarian, "_persona_for_chat", lambda: 'sapphire')
    monkeypatch.setattr(librarian, "_session_snapshot", lambda c, s: '')
    t = librarian._task('msg', 'anita', model='global-model')
    assert t['prompt'] == 'anita-prompt'        # her mind, her voice
    assert t['model'] == 'glm-9b'
    assert t['provider'] == 'fireworks'
    # No residency set → classic fallbacks, single-resident installs unchanged.
    t = librarian._task('msg', 'sapphire', model='global-model')
    assert t['prompt'] == 'sapphire'
    assert t['model'] == 'global-model'
    assert t['provider'] == 'auto'


def test_session_chats_are_per_scope(palace, monkeypatch):
    monkeypatch.setattr(librarian, "_fresh_chat_enabled", lambda: True)
    name = librarian.mint_session_chat('anita')
    assert re.fullmatch(r'librarian-\d{8}-\d{4}-anita', name)
    assert re.fullmatch(r'librarian-\d{8}-\d{4}',
                        librarian.mint_session_chat())


def test_memories_keyed_filter(palace):
    from plugins.mindpalace.routes import browse
    pt._save_memory('a public memory', 'default')
    pt._save_memory('a keyed memory', 'default', private_key='tide')
    out = browse.list_chunks(query={'scope': 'default', 'keyed': '1'})
    assert out['total'] == 1
    assert out['chunks'][0]['content'] == 'a keyed memory'
    out = browse.list_chunks(query={'scope': 'default'})
    assert out['total'] == 2
