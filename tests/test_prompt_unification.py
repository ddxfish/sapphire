"""Regression guards for the 2026-08-09 prompt-system unification.

What shipped (and what these tests pin):
- ONE renderer: prompt_manager.assemble_from_components is the single
  formatter; prompt_state.assemble_prompt delegates to it. The old split
  (labeled editor format vs unlabeled runtime format) made the live prompt
  silently change format at the first spice rotation after every activation.
- ONE resolver: prompt_crud.get_prompt resolves the 'default' sentinel
  instead of returning None (streams ran with the literal text
  "System prompt not loaded.", boot wore a hardcoded fallback all session).
- revalidate_active: storage changes under the ACTIVE prompt re-render the
  live chat (watcher reload, pack unregister, delete-active, preset edit).
- Privacy: privacy_required survives piece rebuilds, is enforced at
  provider selection (all doors), and never leaks into runtime state as a
  phantom component.
- allow_overwrite on save_prompt is real (was accepted-but-ignored).
"""
import threading
from unittest.mock import MagicMock, patch

import pytest


def _mgr_with(components=None, presets=None, monoliths=None):
    """PromptManager with controlled in-memory stores (no disk)."""
    from core.prompt_manager import PromptManager
    with patch.object(PromptManager, '__init__', lambda self: None):
        mgr = PromptManager()
    mgr._components = components or {}
    mgr._scenario_presets = presets or {}
    mgr._monoliths = monoliths or {}
    mgr._spices = {}
    mgr._spice_meta = {}
    mgr._disabled_categories = set()
    mgr._lock = threading.Lock()
    mgr._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
    return mgr


COMPONENTS = {
    'character': {'testchar': 'You are Test.'},
    'location': {'lab': 'in the lab', 'default': ''},
    'relationship': {'friend': 'We are friends.'},
    'goals': {'none': ''},
    'format': {'short': 'Be brief.'},
    'scenario': {'default': 'DEFAULT SCENARIO TEXT', 'heist': 'A heist is on.'},
    'extras': {'tools': 'You have tools.'},
    'emotions': {'calm': 'You are calm.'},
}


class TestUnifiedRenderer:
    """assemble_from_components is THE renderer — runtime prose format."""

    def test_prose_format_no_labels(self):
        mgr = _mgr_with(COMPONENTS)
        out = mgr.assemble_from_components({
            'character': 'testchar', 'location': 'lab',
            'relationship': 'friend', 'format': 'short',
        })
        assert 'You are currently in the lab.' in out
        # The old editor format's labels must never come back
        assert 'Location:' not in out
        assert 'Relationship:' not in out
        assert 'Format:' not in out

    def test_empty_location_no_dangling_sentence(self):
        """location '' / missing must not render 'You are currently .'"""
        mgr = _mgr_with(COMPONENTS)
        for loc in ('default', '', None, 'missing-key'):
            out = mgr.assemble_from_components({'character': 'testchar', 'location': loc})
            assert 'You are currently .' not in out, f"dangling sentence for location={loc!r}"

    def test_default_scenario_is_silent_named_included(self):
        mgr = _mgr_with(COMPONENTS)
        base = {'character': 'testchar', 'relationship': 'friend'}
        assert 'DEFAULT SCENARIO TEXT' not in mgr.assemble_from_components(
            {**base, 'scenario': 'default'})
        assert 'A heist is on.' in mgr.assemble_from_components(
            {**base, 'scenario': 'heist'})

    def test_extras_emotions_render_as_parts(self):
        mgr = _mgr_with(COMPONENTS)
        out = mgr.assemble_from_components({
            'character': 'testchar', 'extras': ['tools'], 'emotions': ['calm']})
        assert 'You have tools.' in out
        assert 'You are calm.' in out
        assert 'Extras:' not in out and 'Emotions:' not in out

    def test_unknown_and_metadata_keys_ignored(self):
        """Unknown piece keys drop silently; _privacy_required never renders."""
        mgr = _mgr_with(COMPONENTS)
        out = mgr.assemble_from_components({
            'character': 'testchar', 'extras': ['no-such-key'],
            '_privacy_required': True})
        assert 'no-such-key' not in out
        assert '_privacy_required' not in out and 'True' not in out

    def test_runtime_equals_activation_render(self):
        """THE flip guard: assemble_prompt (rotation path) must render the
        same text get_prompt (activation path) does for the same preset."""
        from core import prompt_state, prompt_crud
        preset = {'character': 'testchar', 'location': 'lab',
                  'relationship': 'friend', 'goals': 'none',
                  'format': 'short', 'scenario': 'heist',
                  'extras': ['tools'], 'emotions': ['calm']}
        mgr = _mgr_with(COMPONENTS, presets={'flip': dict(preset)})
        with patch.object(prompt_state, 'prompt_manager', mgr), \
             patch.object(prompt_crud, 'prompt_manager', mgr):
            activation = prompt_crud.get_prompt('flip')['content']
            saved_state = dict(prompt_state._assembled_state)
            try:
                prompt_state.apply_scenario('flip')
                rotation = prompt_state.assemble_prompt()['content']
            finally:
                prompt_state._assembled_state.clear()
                prompt_state._assembled_state.update(saved_state)
        assert mgr._replace_templates(activation) == rotation


class TestDefaultSentinel:
    """get_prompt('default') resolves to the current assembled state."""

    def test_sentinel_resolves(self):
        from core import prompt_state, prompt_crud
        mgr = _mgr_with(COMPONENTS)
        with patch.object(prompt_state, 'prompt_manager', mgr), \
             patch.object(prompt_crud, 'prompt_manager', mgr):
            result = prompt_crud.get_prompt('default')
        assert isinstance(result, dict)
        assert result['type'] == 'assembled'
        assert result['privacy_required'] is False
        # Runtime-only keys must not leak into the components view
        for k in ('spice', 'next_spice', 'active_preset'):
            assert k not in result['components']

    def test_stored_default_wins_over_sentinel(self):
        """A user monolith literally named 'default' beats the sentinel."""
        from core import prompt_crud
        mgr = _mgr_with(monoliths={'default': {'content': 'I am stored default',
                                               'privacy_required': False}})
        with patch.object(prompt_crud, 'prompt_manager', mgr):
            result = prompt_crud.get_prompt('default')
        assert result['type'] == 'monolith'
        assert result['content'] == 'I am stored default'

    def test_missing_name_still_none(self):
        from core import prompt_crud
        mgr = _mgr_with()
        with patch.object(prompt_crud, 'prompt_manager', mgr):
            assert prompt_crud.get_prompt('no-such-prompt') is None


class TestSavePromptOverwrite:
    """allow_overwrite is real now (was accepted-but-ignored)."""

    def _crud_with(self, mgr):
        from core import prompt_crud
        return patch.object(prompt_crud, 'prompt_manager', mgr)

    def test_refuses_existing_same_type(self):
        from core import prompt_crud
        mgr = _mgr_with(monoliths={'taken': {'content': 'old', 'privacy_required': False}})
        mgr.save_monoliths = MagicMock()
        with self._crud_with(mgr):
            ok, msg = prompt_crud.save_prompt(
                'taken', {'type': 'monolith', 'content': 'new'}, allow_overwrite=False)
        assert ok is False
        assert 'exists' in msg.lower()
        assert mgr._monoliths['taken']['content'] == 'old'

    def test_overwrites_when_allowed(self):
        from core import prompt_crud
        mgr = _mgr_with(monoliths={'taken': {'content': 'old', 'privacy_required': False}})
        mgr.save_monoliths = MagicMock()
        with self._crud_with(mgr):
            ok, _ = prompt_crud.save_prompt(
                'taken', {'type': 'monolith', 'content': 'new'}, allow_overwrite=True)
        assert ok is True
        assert mgr._monoliths['taken']['content'] == 'new'

    def test_cross_type_collision_still_blocked(self):
        from core import prompt_crud
        mgr = _mgr_with(presets={'taken': {'character': 'x'}})
        with self._crud_with(mgr):
            ok, _ = prompt_crud.save_prompt(
                'taken', {'type': 'monolith', 'content': 'new'}, allow_overwrite=True)
        assert ok is False

    def test_privacy_flag_roundtrip(self):
        """privacy_required survives save -> get (S2-20 rebuild-strip guard)."""
        from core import prompt_crud
        mgr = _mgr_with(COMPONENTS)
        mgr.save_scenario_presets = MagicMock()
        with self._crud_with(mgr):
            ok, _ = prompt_crud.save_prompt(
                'priv', {'type': 'assembled',
                         'components': {'character': 'testchar'},
                         'privacy_required': True})
            assert ok
            back = prompt_crud.get_prompt('priv')
        assert back['privacy_required'] is True
        assert '_privacy_required' not in back['components']


class TestRuntimeStateHygiene:
    """_privacy_required never pollutes runtime state or status payloads."""

    def test_apply_scenario_filters_metadata(self):
        from core import prompt_state
        mgr = _mgr_with(COMPONENTS, presets={
            'p': {'character': 'testchar', '_privacy_required': True}})
        saved = dict(prompt_state._assembled_state)
        try:
            with patch.object(prompt_state, 'prompt_manager', mgr):
                prompt_state.apply_scenario('p')
                assert '_privacy_required' not in prompt_state._assembled_state
        finally:
            prompt_state._assembled_state.clear()
            prompt_state._assembled_state.update(saved)

    def test_get_current_state_filters_metadata(self):
        from core import prompt_state
        mgr = _mgr_with(COMPONENTS, presets={
            'p': {'character': 'testchar', '_privacy_required': True}})
        mgr._active_preset_name = 'p'
        with patch.object(prompt_state, 'prompt_manager', mgr):
            state = prompt_state.get_current_state()
        assert '_privacy_required' not in state
        assert state.get('character') == 'testchar'


class TestRevalidateActive:
    """Storage changed under the active prompt -> live chat re-renders."""

    def _system(self):
        system = MagicMock()
        return system

    def test_rerenders_active_preset(self):
        from core import prompt_state, prompt_crud
        mgr = _mgr_with(COMPONENTS, presets={'live': {'character': 'testchar'}})
        mgr._active_preset_name = 'live'
        system = self._system()
        with patch.object(prompt_state, 'prompt_manager', mgr), \
             patch.object(prompt_crud, 'prompt_manager', mgr):
            ok = prompt_crud.revalidate_active(system, reason='test')
        assert ok is True
        system.llm_chat.set_system_prompt.assert_called_once()
        pushed = system.llm_chat.set_system_prompt.call_args[0][0]
        assert 'You are Test.' in pushed

    def test_vanished_active_hands_off_to_default(self):
        from core import prompt_state, prompt_crud
        mgr = _mgr_with(COMPONENTS)
        mgr._active_preset_name = 'gone-prompt'
        system = self._system()
        saved = dict(prompt_state._assembled_state)
        try:
            with patch.object(prompt_state, 'prompt_manager', mgr), \
                 patch.object(prompt_crud, 'prompt_manager', mgr):
                ok = prompt_crud.revalidate_active(system, reason='test')
                assert ok is True
                assert prompt_state.get_active_preset_name() == 'default'
        finally:
            prompt_state._assembled_state.clear()
            prompt_state._assembled_state.update(saved)
        system.llm_chat.set_system_prompt.assert_called_once()

    def test_no_system_is_clean_noop(self):
        from core import prompt_crud
        with patch('core.api_fastapi.get_system', return_value=None):
            assert prompt_crud.revalidate_active(reason='boot') is False


class TestActivateStreamAware:
    """Prompt switch during a phone/driver stream stamps THAT chat only."""

    def _mgr_and_system(self):
        mgr = _mgr_with(monoliths={'costume': {'content': 'I am the costume',
                                               'privacy_required': False}})
        system = MagicMock()
        system.llm_chat.session_manager.get_active_chat_name.return_value = 'web-chat'
        return mgr, system

    def test_stream_override_stamps_settings_only(self):
        from core import prompt_crud
        mgr, system = self._mgr_and_system()
        with patch.object(prompt_crud, 'prompt_manager', mgr), \
             patch('core.chat.stream_brain.get_override',
                   return_value={'chat': 'phone-1'}):
            ok, msg = prompt_crud.activate_prompt('costume', system)
        assert ok is True
        system.llm_chat.session_manager.update_chat_settings.assert_called_once_with(
            {'prompt': 'costume'})
        system.llm_chat.set_system_prompt.assert_not_called()

    def test_no_override_full_activation(self):
        from core import prompt_crud
        mgr, system = self._mgr_and_system()
        with patch.object(prompt_crud, 'prompt_manager', mgr), \
             patch('core.chat.stream_brain.get_override', return_value=None):
            ok, _ = prompt_crud.activate_prompt('costume', system)
        assert ok is True
        system.llm_chat.set_system_prompt.assert_called_once_with('I am the costume')


class TestProviderPrivacyGate:
    """privacy_required + private_chat off -> blocked at provider selection."""

    def _chat(self, chat_settings):
        from core.chat.chat import LLMChat
        obj = LLMChat.__new__(LLMChat)
        obj._use_new_config = True
        obj.session_manager = MagicMock()
        obj.session_manager.get_chat_settings.return_value = chat_settings
        return obj

    def test_private_prompt_public_chat_blocked(self):
        chat = self._chat({'prompt': 'secret', 'private_chat': False,
                           'llm_primary': 'auto'})
        with patch('core.prompts.get_prompt',
                   return_value={'type': 'monolith', 'content': 'x',
                                 'privacy_required': True}):
            with pytest.raises(ConnectionError, match='private'):
                chat._select_provider()

    def test_private_prompt_private_chat_passes_gate(self):
        """With private_chat on, the gate lets selection proceed (and the
        pre-existing is_local enforcement takes over)."""
        chat = self._chat({'prompt': 'secret', 'private_chat': True,
                           'llm_primary': 'none'})
        with patch('core.prompts.get_prompt',
                   return_value={'type': 'monolith', 'content': 'x',
                                 'privacy_required': True}):
            # llm_primary='none' raises AFTER the privacy gate — proves the
            # gate itself passed.
            with pytest.raises(ConnectionError, match='llm_primary=none'):
                chat._select_provider()

    def test_public_prompt_unaffected(self):
        chat = self._chat({'prompt': 'open', 'private_chat': False,
                           'llm_primary': 'none'})
        with patch('core.prompts.get_prompt',
                   return_value={'type': 'monolith', 'content': 'x',
                                 'privacy_required': False}):
            with pytest.raises(ConnectionError, match='llm_primary=none'):
                chat._select_provider()


class TestContinuityPrivacyGate:
    """The continuity lane refuses non-local providers for private prompts."""

    def _ctx(self, requires_privacy, task_settings):
        from core.continuity.execution_context import ExecutionContext
        ctx = ExecutionContext.__new__(ExecutionContext)
        ctx.task_settings = task_settings
        ctx._prompt_privacy_required = requires_privacy
        return ctx

    def test_pinned_cloud_provider_refused(self):
        import config
        ctx = self._ctx(True, {'prompt': 'secret', 'provider': 'cloudy', 'model': ''})
        with patch.object(config, 'LLM_PROVIDERS',
                          {'cloudy': {'enabled': True}}, create=True), \
             patch.object(config, 'LLM_CUSTOM_PROVIDERS', {}, create=True):
            with pytest.raises(ConnectionError, match='requires privacy'):
                ctx._resolve_provider()

    def test_pinned_local_provider_allowed(self):
        import config
        ctx = self._ctx(True, {'prompt': 'secret', 'provider': 'localbox', 'model': ''})
        provider = MagicMock()
        with patch.object(config, 'LLM_PROVIDERS',
                          {'localbox': {'enabled': True, 'is_local': True}},
                          create=True), \
             patch.object(config, 'LLM_CUSTOM_PROVIDERS', {}, create=True), \
             patch('core.continuity.execution_context.get_provider_by_key',
                   return_value=provider):
            key, prov, _ = ctx._resolve_provider()
        assert key == 'localbox'
        assert prov is provider

    def test_auto_mode_forces_privacy(self):
        import config
        ctx = self._ctx(True, {'prompt': 'secret', 'provider': 'auto', 'model': ''})
        with patch.object(config, 'LLM_PROVIDERS', {}, create=True), \
             patch.object(config, 'LLM_CUSTOM_PROVIDERS', {}, create=True), \
             patch('core.continuity.execution_context.get_first_available_provider',
                   return_value=None) as mock_first:
            with pytest.raises(ConnectionError):
                ctx._resolve_provider()
        assert mock_first.call_args.kwargs.get('force_privacy') is True


class TestTemplateOrdering:
    """{user_name} in custom_context renders — replacement runs after appends."""

    def test_custom_context_gets_templated(self):
        import config
        from core.chat.chat import LLMChat
        obj = LLMChat.__new__(LLMChat)
        obj.current_system_prompt = 'Base prompt for {user_name}.'
        obj.session_manager = MagicMock()
        obj.session_manager.get_chat_settings.return_value = {
            'custom_context': 'Context about {user_name}.', 'spice_enabled': False}
        with patch.object(config, 'DEFAULT_USERNAME', 'TestUser', create=True), \
             patch('core.chat.chat.hook_runner') as mock_hooks, \
             patch('core.chat.stream_brain.get_override', return_value=None):
            mock_hooks.has_handlers.return_value = False
            prompt, username, _ = obj._get_system_prompt()
        assert 'Base prompt for TestUser.' in prompt
        assert 'Context about TestUser.' in prompt
        assert '{user_name}' not in prompt


class TestMigrations:
    """Loose-file fold (lane-3 retirement) + mis-filed piece mover."""

    def _setup_dir(self, tmp_path, monkeypatch):
        from core import migration
        monkeypatch.setattr(migration, 'USER_PROMPTS_DIR', tmp_path)
        return migration

    def test_loose_monolith_folds_and_renames(self, tmp_path, monkeypatch):
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_monoliths.json').write_text('{}', encoding='utf-8')
        (tmp_path / 'mycustom.json').write_text(
            json.dumps({'name': 'mycustom', 'type': 'monolith',
                        'content': 'legacy text'}), encoding='utf-8')
        migration.migrate_loose_prompt_files()
        monos = json.loads((tmp_path / 'prompt_monoliths.json').read_text(encoding='utf-8'))
        assert monos['mycustom']['content'] == 'legacy text'
        assert not (tmp_path / 'mycustom.json').exists()
        assert (tmp_path / 'mycustom.json.imported').exists()

    def test_loose_collision_sets_aside(self, tmp_path, monkeypatch):
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_monoliths.json').write_text(
            json.dumps({'mycustom': {'content': 'store wins'}}), encoding='utf-8')
        (tmp_path / 'mycustom.json').write_text(
            json.dumps({'name': 'mycustom', 'content': 'file version'}),
            encoding='utf-8')
        migration.migrate_loose_prompt_files()
        monos = json.loads((tmp_path / 'prompt_monoliths.json').read_text(encoding='utf-8'))
        assert monos['mycustom']['content'] == 'store wins'
        assert (tmp_path / 'mycustom.json.duplicate').exists()

    def test_misfiled_piece_moves_to_sibling(self, tmp_path, monkeypatch):
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_pieces.json').write_text(json.dumps({
            'components': {'extras': {'memory_use': 'Use memory.'},
                           'emotions': {'warm': 'Warm.'}},
            'scenario_presets': {
                'sapphire': {'character': 'sapphire',
                             'extras': [],
                             'emotions': ['warm', 'memory_use']}},
        }), encoding='utf-8')
        migration.migrate_misfiled_preset_pieces()
        data = json.loads((tmp_path / 'prompt_pieces.json').read_text(encoding='utf-8'))
        preset = data['scenario_presets']['sapphire']
        assert 'memory_use' in preset['extras']
        assert 'memory_use' not in preset['emotions']
        assert 'warm' in preset['emotions']

    def test_truly_unknown_key_left_alone(self, tmp_path, monkeypatch):
        """A key unknown to BOTH components stays put (no data invention)."""
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_pieces.json').write_text(json.dumps({
            'components': {'extras': {}, 'emotions': {}},
            'scenario_presets': {'p': {'emotions': ['ghost-key']}},
        }), encoding='utf-8')
        migration.migrate_misfiled_preset_pieces()
        data = json.loads((tmp_path / 'prompt_pieces.json').read_text(encoding='utf-8'))
        assert data['scenario_presets']['p']['emotions'] == ['ghost-key']


class TestMigrationArmor:
    """2026-08-09 scout fixes: Windows rename semantics, glob appetite,
    poison resistance, BOM tolerance."""

    def _setup_dir(self, tmp_path, monkeypatch):
        from core import migration
        monkeypatch.setattr(migration, 'USER_PROMPTS_DIR', tmp_path)
        return migration

    def test_refold_over_existing_imported_file(self, tmp_path, monkeypatch):
        """Re-dropping an already-imported file must not crash (Windows
        Path.rename raises FileExistsError on existing targets — this was
        a boot-brick)."""
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_monoliths.json').write_text('{}', encoding='utf-8')
        (tmp_path / 'my.json.imported').write_text('{"old": true}', encoding='utf-8')
        (tmp_path / 'my.json').write_text(
            json.dumps({'name': 'my', 'content': 'again'}), encoding='utf-8')
        migration.migrate_loose_prompt_files()  # must not raise
        monos = json.loads((tmp_path / 'prompt_monoliths.json').read_text(encoding='utf-8'))
        assert monos['my']['content'] == 'again'
        assert (tmp_path / 'my.json.imported').exists()
        assert not (tmp_path / 'my.json').exists()

    def test_store_backup_copies_never_fold(self, tmp_path, monkeypatch):
        """prompt_pieces-backup.json (a store backup) must not be eaten as a
        garbage preset of dicts-of-dicts — and case variants of the store
        names are protected too (NTFS is case-insensitive)."""
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        store = {'components': {'character': {'sapphire': 'text'}},
                 'scenario_presets': {}}
        (tmp_path / 'prompt_pieces.json').write_text(json.dumps(store), encoding='utf-8')
        (tmp_path / 'prompt_pieces-backup.json').write_text(json.dumps(store), encoding='utf-8')
        (tmp_path / 'Prompt_Monoliths.json').write_text('{}', encoding='utf-8')
        migration.migrate_loose_prompt_files()
        data = json.loads((tmp_path / 'prompt_pieces.json').read_text(encoding='utf-8'))
        assert data['scenario_presets'] == {}
        assert (tmp_path / 'prompt_pieces-backup.json').exists()
        assert (tmp_path / 'Prompt_Monoliths.json').exists()

    def test_poisoned_store_never_crashes(self, tmp_path, monkeypatch):
        """A JSON-list store + a loose file used to raise at import time,
        ABOVE the boot try/except — no boot, no log."""
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_pieces.json').write_text('[1,2,3]', encoding='utf-8')
        (tmp_path / 'loose.json').write_text(
            json.dumps({'name': 'x', 'content': 'y'}), encoding='utf-8')
        migration.migrate_loose_prompt_files()  # must not raise
        assert (tmp_path / 'loose.json').exists()  # nothing folded, nothing renamed

    def test_bom_files_read_fine(self, tmp_path, monkeypatch):
        """PowerShell writes BOMs; reads must tolerate them."""
        import json
        migration = self._setup_dir(tmp_path, monkeypatch)
        (tmp_path / 'prompt_monoliths.json').write_text('{}', encoding='utf-8')
        (tmp_path / 'bom.json').write_text(
            json.dumps({'name': 'bom', 'content': 'text'}), encoding='utf-8-sig')
        migration.migrate_loose_prompt_files()
        monos = json.loads((tmp_path / 'prompt_monoliths.json').read_text(encoding='utf-8'))
        assert monos['bom']['content'] == 'text'


class TestSaveRefuseDiscipline:
    """The savers tell the truth now: refusal returns False, save_prompt
    propagates it, and a missing file latches the load-failed flag."""

    def _mgr_on_disk(self, tmp_path):
        from core.prompt_manager import PromptManager
        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
        mgr.USER_DIR = tmp_path
        mgr._components = {}
        mgr._scenario_presets = {}
        mgr._monoliths = {}
        mgr._spices = {}
        mgr._spice_meta = {}
        mgr._disabled_categories = set()
        mgr._lock = threading.RLock()
        mgr._last_mtimes = {}
        mgr._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
        mgr._audit_snap = {'monoliths': {}, 'presets': {}, 'components': {}}
        return mgr

    def test_latched_saver_returns_false(self, tmp_path):
        mgr = self._mgr_on_disk(tmp_path)
        mgr._load_failed['monoliths'] = True
        mgr._monoliths = {'x': {'content': 'y', 'privacy_required': False}}
        assert mgr.save_monoliths() is False
        assert not (tmp_path / 'prompt_monoliths.json').exists()

    def test_healthy_saver_returns_true_and_writes(self, tmp_path):
        import json
        mgr = self._mgr_on_disk(tmp_path)
        mgr._monoliths = {'x': {'content': 'y', 'privacy_required': False}}
        assert mgr.save_monoliths() is True
        data = json.loads((tmp_path / 'prompt_monoliths.json').read_text(encoding='utf-8'))
        assert data['x']['content'] == 'y'
        # Echo suppression: the saver recorded the mtime it wrote so the
        # watcher won't treat this save as an external edit.
        assert str(tmp_path / 'prompt_monoliths.json') in mgr._last_mtimes

    def test_save_prompt_propagates_refusal(self, tmp_path):
        from core import prompt_crud
        mgr = self._mgr_on_disk(tmp_path)
        mgr._load_failed['monoliths'] = True
        with patch.object(prompt_crud, 'prompt_manager', mgr):
            ok, msg = prompt_crud.save_prompt('x', {'type': 'monolith', 'content': 'y'})
        assert ok is False
        assert 'refused' in msg.lower()

    def test_missing_file_sets_latch(self, tmp_path):
        mgr = self._mgr_on_disk(tmp_path)
        mgr._load_pieces()   # no file on disk
        assert mgr._load_failed['pieces'] is True
        assert mgr.save_components() is False

    def test_bom_store_loads(self, tmp_path):
        import json
        mgr = self._mgr_on_disk(tmp_path)
        (tmp_path / 'prompt_monoliths.json').write_text(
            json.dumps({'b': {'content': 'bom text', 'privacy_required': False}}),
            encoding='utf-8-sig')
        mgr._load_monoliths()
        assert mgr._load_failed['monoliths'] is False
        assert mgr._monoliths['b']['content'] == 'bom text'

    def test_save_prompt_rejects_bad_shapes(self):
        from core import prompt_crud
        mgr = _mgr_with()
        with patch.object(prompt_crud, 'prompt_manager', mgr):
            ok, _ = prompt_crud.save_prompt('a', {'type': 'monolith', 'content': None})
            assert ok is False
            ok, _ = prompt_crud.save_prompt('b', {'type': 'monolith', 'content': 123})
            assert ok is False
            ok, _ = prompt_crud.save_prompt('c', {'type': 'assembled', 'components': None})
            assert ok is False
            ok, _ = prompt_crud.save_prompt(
                'd', {'type': 'assembled', 'components': {'character': {'nested': 'dict'}}})
            assert ok is False


class TestDefaultIsAssembledMode:
    """active_preset='default' counts as assembled — the handoff paths land
    there, and monolith-mode there killed spice + stuck transients + locked
    her out of the piece tools."""

    def test_default_is_assembled(self):
        from core import prompt_state
        saved = dict(prompt_state._assembled_state)
        try:
            prompt_state._assembled_state['active_preset'] = 'default'
            assert prompt_state.is_assembled_mode() is True
            assert prompt_state.get_prompt_mode() == 'assembled'
        finally:
            prompt_state._assembled_state.clear()
            prompt_state._assembled_state.update(saved)
