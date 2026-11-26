"""The vault's reader rule in the game room (core/prompt_vault.py, 2026-11-26).

A seat's persona and a story's costume are rendered FOR their session: only a
private session reads the vault. Before the rule a public session whose
persona lived in the vault sent the whole prompt to whatever provider Auto
picked on every move (isolation hunt A5), and the story engine baked vault
pieces into a public costume (A6).

Hermetic: the vault is in-memory only, the prompt stores are small dicts.
"""
import pytest

import core.api_fastapi  # noqa: F401  (route modules trip circular imports alone)
import core.prompt_vault as pv
from core.prompt_manager import prompt_manager as pm

import gameroom_core as gc
from gameroom_story import session as sess

SECRET = "VAULT-ONLY persona text"
VIEW = {'my_name': 'Sapphire', 'opp_name': 'Krem'}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, 'VAULT_PATH', tmp_path / 'prompt_vault.enc')
    monkeypatch.setattr(pv, 'REFS_PATH', tmp_path / 'vault_refs.json')
    monkeypatch.setattr(pv, '_key', b'k' * 32)
    monkeypatch.setattr(pv, '_salt', b's' * 16)
    monkeypatch.setattr(pv, '_data', pv._normalize(
        {'monoliths': {'vlt_persona': SECRET},
         'components': {'emotions': {'tender': SECRET}}}))
    monkeypatch.setattr(pm, '_components', {'character': {'sapphire': 'You are Sapphire.'}})
    monkeypatch.setattr(pm, '_scenario_presets', {
        'plain': {'character': 'sapphire', 'emotions': ['tender']}})
    monkeypatch.setattr(pm, '_monoliths', {})
    yield
    pv.stop()


def test_a_public_seat_gets_no_vault_persona(vault):
    text = gc._system_prompt('RULES', VIEW, {'prompt': 'vlt_persona'})
    assert SECRET not in text
    assert text.endswith('RULES'), "the contract survives"
    assert gc.TABLE_PERSONA.format(ai_name='Sapphire')[:40] in text, "the table persona, as when locked"


def test_a_public_seat_on_a_plain_preset_loses_only_the_vault_piece(vault):
    text = gc._system_prompt('RULES', VIEW, {'prompt': 'plain'})
    assert SECRET not in text and 'You are Sapphire.' in text


def test_a_private_seat_reads_the_vault(vault):
    cfg = {'prompt': 'vlt_persona', 'privacy_required': True}
    assert SECRET in gc._system_prompt('RULES', VIEW, cfg)
    assert SECRET in gc._system_prompt('RULES', VIEW, {**cfg, 'prompt': 'plain'})
    assert pv.reader_ok() is False, "and it closes again"


class _Reached(Exception):
    pass


@pytest.mark.parametrize("private", [False, True])
def test_a_story_costume_is_rendered_for_its_session_chat(vault, monkeypatch, private):
    """Whoever asks (a tool in a turn, a route, the boot restore)."""
    seen = {}

    def render(story, state, comps, **kw):
        seen['reader'] = pv.reader_ok()
        seen['tender'] = 'tender' in comps.get('emotions', {})
        raise _Reached()

    monkeypatch.setattr(sess.render, 'story_prompt', render)
    monkeypatch.setattr(sess, '_chat_private', lambda chat: private)
    with pv.reading(not private):          # the caller's own reader must not matter
        with pytest.raises(_Reached):
            sess._register_prompt({"meta": {"slug": "readertest"}}, {}, {"mode": "story"}, "chat1")
    assert seen == {'reader': private, 'tender': private}


def test_a_prompts_privacy_does_not_depend_on_the_reader(vault):
    """_inherits_privacy reads this to flag a costume that embeds the prompt."""
    with pv.reading(False):
        assert sess._prompt_is_private('vlt_persona') is True
        assert sess._prompt_is_private('plain') is False
