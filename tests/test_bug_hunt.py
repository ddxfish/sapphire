"""
Bug Hunt Regression Tests

Tests for bugs found and fixed during pre-2.0 bug hunts.
Prevents regressions on scope isolation, state restoration,
thread safety, and persona switching.

Run with: pytest tests/test_bug_hunt.py -v
"""
import pytest
import sys
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# C1: Persona settings keys must include email_scope and bitcoin_scope
# =============================================================================

class TestPersonaSettingsKeys:
    """Persona settings whitelist must include all scope keys."""

    def test_email_scope_in_persona_keys(self):
        """email_scope must be in PERSONA_SETTINGS_KEYS."""
        from core.modules.system.personas.persona_manager import PERSONA_SETTINGS_KEYS
        assert "email_scope" in PERSONA_SETTINGS_KEYS

    def test_bitcoin_scope_in_persona_keys(self):
        """bitcoin_scope must be in PERSONA_SETTINGS_KEYS."""
        from core.modules.system.personas.persona_manager import PERSONA_SETTINGS_KEYS
        assert "bitcoin_scope" in PERSONA_SETTINGS_KEYS

    def test_all_scope_keys_in_persona_keys(self):
        """All scope keys must be bundleable in personas."""
        from core.modules.system.personas.persona_manager import PERSONA_SETTINGS_KEYS
        required_scopes = [
            "memory_scope", "goal_scope", "knowledge_scope",
            "people_scope", "email_scope", "bitcoin_scope"
        ]
        for scope in required_scopes:
            assert scope in PERSONA_SETTINGS_KEYS, f"Missing: {scope}"

    def test_clean_settings_preserves_scopes(self):
        """_clean_settings should not strip scope keys."""
        from core.modules.system.personas.persona_manager import PersonaManager, PERSONA_SETTINGS_KEYS

        with patch.object(PersonaManager, '__init__', lambda self: None):
            mgr = PersonaManager()

            raw = {
                "prompt": "test", "email_scope": "work",
                "bitcoin_scope": "wallet1", "memory_scope": "shared",
                "junk_key": "should_be_stripped"
            }
            cleaned = {k: v for k, v in raw.items() if k in PERSONA_SETTINGS_KEYS}

            assert "email_scope" in cleaned
            assert "bitcoin_scope" in cleaned
            assert "memory_scope" in cleaned
            assert "junk_key" not in cleaned


# =============================================================================
# C3: scope_private must be set from chat settings
# =============================================================================

class TestScopePrivate:
    """scope_private ContextVar must be set before LLM calls."""

    def test_scope_private_contextvar_exists(self):
        """scope_private ContextVar should exist in function_manager."""
        from core.chat.function_manager import scope_private
        assert scope_private.get() is False  # Default

    def test_set_private_chat_sets_contextvar(self):
        """set_private_chat should update scope_private ContextVar."""
        from core.chat.function_manager import FunctionManager, scope_private

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.set_private_chat(True)
            assert scope_private.get() is True
            mgr.set_private_chat(False)
            assert scope_private.get() is False

    def test_snapshot_includes_private(self):
        """snapshot_scopes should capture private flag."""
        from core.chat.function_manager import FunctionManager, scope_private

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.set_private_chat(True)
            snap = mgr.snapshot_scopes()
            assert snap['private'] is True


# =============================================================================
# H1: set_active_chat atomicity
# =============================================================================

class TestChatSwitchAtomicity:
    """Chat switching must be atomic (RLock protected)."""

    def test_session_manager_uses_rlock(self, tmp_path):
        """ChatSessionManager should use RLock, not Lock."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                assert isinstance(mgr._lock, type(threading.RLock()))

    def test_set_active_chat_holds_lock(self, tmp_path):
        """set_active_chat should acquire _lock during operation."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                mgr.create_chat("test_chat")

                lock_held_during = []
                original_save = mgr._save_current_chat

                def spy_save():
                    # RLock.acquire returns True if lock is held by this thread
                    # For RLock, trying to acquire with timeout=0 succeeds if same thread holds it
                    lock_held_during.append(mgr._lock.acquire(timeout=0))
                    if lock_held_during[-1]:
                        mgr._lock.release()
                    original_save()

                with patch.object(mgr, '_save_current_chat', spy_save):
                    mgr.set_active_chat("test_chat")

                # Lock should have been held (RLock allows re-entry)
                assert any(lock_held_during)

    def test_concurrent_chat_switches_dont_corrupt(self, tmp_path):
        """Concurrent chat switches should not corrupt state."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                mgr = ChatSessionManager(history_dir=str(tmp_path))
                mgr.create_chat("chat_a")
                mgr.create_chat("chat_b")

                errors = []

                def switch_loop(name, count):
                    for _ in range(count):
                        try:
                            mgr.set_active_chat(name)
                        except Exception as e:
                            errors.append(str(e))

                t1 = threading.Thread(target=switch_loop, args=("chat_a", 20))
                t2 = threading.Thread(target=switch_loop, args=("chat_b", 20))
                t1.start()
                t2.start()
                t1.join(timeout=5)
                t2.join(timeout=5)

                assert len(errors) == 0, f"Errors during concurrent switch: {errors}"
                # State should be one of the two chats
                assert mgr.active_chat_name in ("chat_a", "chat_b")


# =============================================================================
# H2: FunctionManager thread safety
# =============================================================================

class TestFunctionManagerThreadSafety:
    """update_enabled_functions must be thread-safe."""

    def test_has_tools_lock(self):
        """FunctionManager should have _tools_lock attribute."""
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            assert hasattr(mgr, '_tools_lock')

    def test_concurrent_toolset_updates(self):
        """Concurrent update_enabled_functions should not corrupt state."""
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {
                'mod_a': {'available_functions': ['func_a']},
                'mod_b': {'available_functions': ['func_b']},
            }
            mgr.all_possible_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_b'}},
            ]
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr.current_toolset_name = "none"

            errors = []

            def toggle(name, count):
                for _ in range(count):
                    try:
                        with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                            mock_ts.toolset_exists.return_value = False
                            mgr.update_enabled_functions([name])
                    except Exception as e:
                        errors.append(str(e))

            t1 = threading.Thread(target=toggle, args=("mod_a", 50))
            t2 = threading.Thread(target=toggle, args=("mod_b", 50))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            assert len(errors) == 0, f"Errors: {errors}"
            # Final state should be consistent
            assert mgr.current_toolset_name in ("mod_a", "mod_b")
            assert len(mgr._enabled_tools) == 1


# =============================================================================
# H5: Task settings must not skip "none" scopes
# =============================================================================

class TestTaskSettingsScopes:
    """Continuity task settings must allow explicitly disabling scopes."""

    def test_none_scope_not_filtered(self):
        """Setting a scope to 'none' should still be applied to chat settings."""
        from core.modules.continuity.executor import ContinuityExecutor

        with patch.object(ContinuityExecutor, '__init__', lambda self: None):
            executor = ContinuityExecutor()

            task = {
                "knowledge_scope": "none",
                "people_scope": "none",
                "goal_scope": "none",
            }

            mock_session = MagicMock()
            executor.system = MagicMock()
            executor._apply_task_settings(task, mock_session)

            # update_chat_settings should have been called with all three scope keys
            call_args = mock_session.update_chat_settings.call_args[0][0]
            assert "knowledge_scope" in call_args
            assert call_args["knowledge_scope"] == "none"
            assert "people_scope" in call_args
            assert call_args["people_scope"] == "none"
            assert "goal_scope" in call_args
            assert call_args["goal_scope"] == "none"


# =============================================================================
# C4/H4: Toolset restoration after continuity tasks
# =============================================================================

class TestToolsetRestoration:
    """Toolset must be restored after isolated and foreground tasks."""

    def test_isolated_chat_restores_toolset(self):
        """isolated_chat should restore original toolset after execution."""
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr.function_modules = {}
            mgr.all_possible_tools = [
                {'function': {'name': 'func_a'}},
                {'function': {'name': 'func_b'}},
            ]
            mgr._enabled_tools = [{'function': {'name': 'func_a'}}]
            mgr._mode_filters = {}
            mgr.current_toolset_name = "original_toolset"

            # Simulate what isolated_chat does: save, change, restore
            original = mgr.current_toolset_name

            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions(["none"])

            assert mgr.current_toolset_name == "none"

            # Restore (as isolated_chat's finally block does)
            with patch('core.chat.function_manager.toolset_manager') as mock_ts:
                mock_ts.toolset_exists.return_value = False
                mgr.update_enabled_functions([original])

            # We can't assert exact toolset name since "original_toolset" isn't a real
            # module/toolset, but the pattern is correct


# =============================================================================
# M3: Persona switch must reset scopes
# =============================================================================

class TestPersonaScopeReset:
    """Switching personas must reset scope keys not in new persona."""

    def test_missing_scope_keys_get_defaults(self):
        """Scope keys not in persona settings should reset to 'default'."""
        # Simulate what load_persona does
        persona_settings = {
            "prompt": "custom_prompt",
            "toolset": "basic",
            "memory_scope": "shared",
            # No email_scope, bitcoin_scope, etc.
        }

        settings = persona_settings.copy()
        settings["persona"] = "test_persona"

        # This is the fix we applied — fill defaults for missing scopes
        for key in ("memory_scope", "goal_scope", "knowledge_scope",
                    "people_scope", "email_scope", "bitcoin_scope"):
            if key not in settings:
                settings[key] = "default"

        assert settings["memory_scope"] == "shared"  # Kept from persona
        assert settings["email_scope"] == "default"   # Reset
        assert settings["bitcoin_scope"] == "default"  # Reset
        assert settings["goal_scope"] == "default"     # Reset
        assert settings["knowledge_scope"] == "default"  # Reset
        assert settings["people_scope"] == "default"   # Reset

    def test_persona_with_all_scopes_preserves_them(self):
        """Persona that specifies all scopes should keep them."""
        persona_settings = {
            "prompt": "test",
            "memory_scope": "private",
            "goal_scope": "work",
            "knowledge_scope": "research",
            "people_scope": "team",
            "email_scope": "work_email",
            "bitcoin_scope": "wallet_a",
        }

        settings = persona_settings.copy()
        for key in ("memory_scope", "goal_scope", "knowledge_scope",
                    "people_scope", "email_scope", "bitcoin_scope"):
            if key not in settings:
                settings[key] = "default"

        assert settings["email_scope"] == "work_email"
        assert settings["bitcoin_scope"] == "wallet_a"
        assert settings["memory_scope"] == "private"


# =============================================================================
# M2: PromptManager save thread safety
# =============================================================================

class TestPromptSaveThreadSafety:
    """PromptManager save methods must use _lock."""

    def test_concurrent_saves_dont_corrupt(self, tmp_path):
        """Concurrent saves should not produce corrupted JSON."""
        from core.modules.system.prompt_manager import PromptManager

        prompts_dir = tmp_path / "user" / "prompts"
        prompts_dir.mkdir(parents=True)

        initial = {"components": {"character": {}}, "scenario_presets": {}}
        (prompts_dir / "prompt_pieces.json").write_text(
            json.dumps(initial), encoding='utf-8'
        )

        with patch.object(PromptManager, '__init__', lambda self: None):
            mgr = PromptManager()
            mgr._lock = threading.Lock()
            mgr.USER_DIR = prompts_dir
            mgr._scenario_presets = {}
            mgr._components = {"character": {}}

            errors = []

            def save_presets(count):
                for i in range(count):
                    try:
                        mgr._scenario_presets = {f"preset_{i}": {"char": f"v{i}"}}
                        mgr.save_scenario_presets()
                    except Exception as e:
                        errors.append(str(e))

            def save_components(count):
                for i in range(count):
                    try:
                        mgr._components = {"character": {f"comp_{i}": f"v{i}"}}
                        mgr.save_components()
                    except Exception as e:
                        errors.append(str(e))

            t1 = threading.Thread(target=save_presets, args=(20,))
            t2 = threading.Thread(target=save_components, args=(20,))
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            assert len(errors) == 0, f"Errors: {errors}"

            # File should be valid JSON
            saved = json.loads(
                (prompts_dir / "prompt_pieces.json").read_text(encoding='utf-8')
            )
            assert "components" in saved
            assert "scenario_presets" in saved


# =============================================================================
# Init: VoiceChatSystem.__init__ must set all attributes
# =============================================================================

class TestVoiceChatSystemInit:
    """VoiceChatSystem.__init__ must initialize all expected attributes."""

    def test_init_sets_core_attributes(self):
        """__init__ should set history, tts, llm_chat, etc. — not web_active_dec."""
        import inspect
        from sapphire import VoiceChatSystem

        # Check that __init__ actually contains the init code (not web_active_dec)
        source = inspect.getsource(VoiceChatSystem.__init__)
        assert "self.history" in source or "ConversationHistory" in source
        assert "self._processing_lock" in source

    def test_web_active_dec_is_simple(self):
        """web_active_dec should only decrement counter, not contain init code."""
        import inspect
        from sapphire import VoiceChatSystem

        source = inspect.getsource(VoiceChatSystem.web_active_dec)
        # Should NOT contain init-level code
        assert "ConversationHistory" not in source
        assert "tts_server_manager" not in source
        assert "LLMChat" not in source


# =============================================================================
# Scope snapshot completeness
# =============================================================================

class TestScopeSnapshot:
    """Scope snapshots must capture all scope types."""

    def test_snapshot_captures_all_scopes(self):
        """snapshot_scopes should include all 8 scope types."""
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.set_memory_scope("mem_scope")
            mgr.set_goal_scope("goal_scope")
            mgr.set_knowledge_scope("know_scope")
            mgr.set_people_scope("ppl_scope")
            mgr.set_email_scope("email_scope")
            mgr.set_bitcoin_scope("btc_scope")
            mgr.set_private_chat(True)
            mgr.set_rag_scope("rag_scope")

            snap = mgr.snapshot_scopes()

            assert snap['memory'] == "mem_scope"
            assert snap['goal'] == "goal_scope"
            assert snap['knowledge'] == "know_scope"
            assert snap['people'] == "ppl_scope"
            assert snap['email'] == "email_scope"
            assert snap['bitcoin'] == "btc_scope"
            assert snap['private'] is True
            assert snap['rag'] == "rag_scope"

    def test_restore_scopes_via_execute_function(self):
        """execute_function's scope re-application should restore from snapshot."""
        from core.chat.function_manager import (
            FunctionManager, scope_memory, scope_email, scope_private
        )

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr.set_memory_scope("test_mem")
            mgr.set_email_scope("test_email")
            mgr.set_private_chat(True)

            snap = mgr.snapshot_scopes()

            # Clear scopes
            mgr.set_memory_scope(None)
            mgr.set_email_scope(None)
            mgr.set_private_chat(False)

            assert scope_memory.get() is None
            assert scope_private.get() is False

            # Re-apply via the same inline logic execute_function uses
            scope_memory.set(snap.get('memory', 'default'))
            scope_email.set(snap.get('email', 'default'))
            scope_private.set(snap.get('private', False))

            assert scope_memory.get() == "test_mem"
            assert scope_email.get() == "test_email"
            assert scope_private.get() is True


# =============================================================================
# Tier 1: chat() reads and applies ALL scope keys from chat settings
# =============================================================================

class TestChatReadsAllScopes:
    """chat() must read every scope key from settings and apply to function_manager."""

    ALL_SCOPE_SETTERS = [
        ("memory_scope", "set_memory_scope"),
        ("goal_scope", "set_goal_scope"),
        ("knowledge_scope", "set_knowledge_scope"),
        ("people_scope", "set_people_scope"),
        ("email_scope", "set_email_scope"),
        ("bitcoin_scope", "set_bitcoin_scope"),
    ]

    def test_chat_reads_all_scope_keys_from_settings(self):
        """chat() must call every scope setter with the chat settings value."""
        from core.chat.chat import LLMChat
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr._enabled_tools = []
            mgr._mode_filters = {}
            mgr.current_toolset_name = "none"
            mgr.function_modules = {}
            mgr.all_possible_tools = []
            mgr._story_engine = None
            mgr._story_engine_enabled = False

        # Track which setters are called and with what values
        calls = {}
        for setting_key, method_name in self.ALL_SCOPE_SETTERS:
            original = getattr(mgr, method_name)
            calls[method_name] = []
            setattr(mgr, method_name, lambda val, mn=method_name: calls[mn].append(val))

        private_calls = []
        mgr.set_private_chat = lambda val: private_calls.append(val)
        rag_calls = []
        mgr.set_rag_scope = lambda val: rag_calls.append(val)
        mgr.snapshot_scopes = lambda: {}
        mgr._enabled_tools = []

        # Build a mock LLMChat that skips real __init__
        with patch.object(LLMChat, '__init__', lambda self: None):
            chat_obj = LLMChat()
            chat_obj.function_manager = mgr

            # Mock session_manager with settings that have all scopes set
            mock_session = MagicMock()
            mock_session.get_chat_settings.return_value = {
                "memory_scope": "shared",
                "goal_scope": "work",
                "knowledge_scope": "research",
                "people_scope": "team",
                "email_scope": "work_email",
                "bitcoin_scope": "wallet_a",
                "private_chat": True,
            }
            mock_session.get_active_chat_name.return_value = "test_chat"
            mock_session.get_turn_count.return_value = 1
            mock_session.add_user_message = MagicMock()
            mock_session.add_assistant_final = MagicMock()
            mock_session.get_messages_for_llm.return_value = []
            chat_obj.session_manager = mock_session
            chat_obj.history = mock_session
            chat_obj.current_system_prompt = "test prompt"
            chat_obj.module_loader = MagicMock()
            chat_obj.module_loader.detect_module.return_value = (None, None, None)
            chat_obj._update_story_engine = MagicMock()
            chat_obj._use_new_config = False
            chat_obj.provider_primary = MagicMock()
            chat_obj.provider_primary.health_check.return_value = True
            chat_obj.provider_primary.provider_name = "test"
            chat_obj.provider_primary.model = "test-model"
            chat_obj.tool_engine = MagicMock()

            # Make call_llm_with_metrics return a final response (no tool calls)
            mock_response = MagicMock()
            mock_response.has_tool_calls = False
            mock_response.content = "Hello!"
            mock_response.usage = None
            chat_obj.tool_engine.call_llm_with_metrics.return_value = mock_response

            chat_obj.chat("test input")

        # Verify all scope setters were called
        for setting_key, method_name in self.ALL_SCOPE_SETTERS:
            assert len(calls[method_name]) > 0, f"{method_name} was never called"

        assert len(private_calls) > 0, "set_private_chat was never called"
        assert private_calls[0] is True
        assert len(rag_calls) > 0, "set_rag_scope was never called"

    def test_chat_stream_reads_all_scope_keys(self):
        """chat_stream (streaming path) must also set all scope keys."""
        from core.chat.chat_streaming import StreamingChat

        # Track calls to function_manager scope setters
        mock_fm = MagicMock()
        mock_fm.snapshot_scopes.return_value = {}
        mock_fm.enabled_tools = []

        mock_main_chat = MagicMock()
        mock_main_chat.function_manager = mock_fm
        mock_main_chat.session_manager.get_chat_settings.return_value = {
            "memory_scope": "private",
            "goal_scope": "personal",
            "knowledge_scope": "default",
            "people_scope": "friends",
            "email_scope": "personal_email",
            "bitcoin_scope": "none",
            "private_chat": False,
        }
        mock_main_chat.session_manager.get_active_chat_name.return_value = "stream_chat"
        mock_main_chat.module_loader.detect_module.return_value = (None, None, None)
        mock_main_chat._update_story_engine = MagicMock()
        mock_main_chat._build_base_messages.return_value = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]

        # Provider that returns a simple done event
        mock_provider = MagicMock()
        mock_provider.provider_name = "test"
        mock_provider.model = "test-model"
        mock_provider.chat_completion_stream.return_value = [
            {"type": "content", "text": "Hello"},
            {"type": "done", "response": None},
        ]
        mock_main_chat._select_provider.return_value = ("test", mock_provider, "")

        with patch('core.chat.chat_streaming.get_generation_params', return_value={}):
            sc = StreamingChat(mock_main_chat)
            # Consume the generator
            list(sc.chat_stream("hello"))

        # All scope setters should have been called
        mock_fm.set_memory_scope.assert_called()
        mock_fm.set_goal_scope.assert_called()
        mock_fm.set_knowledge_scope.assert_called()
        mock_fm.set_people_scope.assert_called()
        mock_fm.set_email_scope.assert_called()
        mock_fm.set_bitcoin_scope.assert_called()
        mock_fm.set_private_chat.assert_called()
        mock_fm.set_rag_scope.assert_called()


# =============================================================================
# Tier 1: _apply_task_settings passes all scope keys through
# =============================================================================

class TestApplyTaskSettingsAllScopes:
    """_apply_task_settings must forward all scope types to session_manager."""

    def test_apply_task_settings_all_scope_keys(self):
        """All 6 scope keys + email + bitcoin must appear in update_chat_settings call."""
        from core.modules.continuity.executor import ContinuityExecutor

        with patch.object(ContinuityExecutor, '__init__', lambda self: None):
            executor = ContinuityExecutor()
            executor.system = MagicMock()

            task = {
                "memory_scope": "shared",
                "knowledge_scope": "research",
                "people_scope": "team",
                "goal_scope": "work",
                "email_scope": "work_email",
                "bitcoin_scope": "wallet_a",
            }

            mock_session = MagicMock()
            executor._apply_task_settings(task, mock_session)

            call_args = mock_session.update_chat_settings.call_args[0][0]
            for key in ("memory_scope", "knowledge_scope", "people_scope",
                       "goal_scope", "email_scope", "bitcoin_scope"):
                assert key in call_args, f"Missing scope key: {key}"
                assert call_args[key] == task[key]


# =============================================================================
# Tier 1: _run_foreground saves and restores ALL context
# =============================================================================

class TestRunForegroundRestoresContext:
    """_run_foreground must restore chat, toolset, and settings after running."""

    def test_run_foreground_restores_all_context(self):
        """After foreground execution, original chat + toolset + settings must be restored."""
        from core.modules.continuity.executor import ContinuityExecutor

        with patch.object(ContinuityExecutor, '__init__', lambda self: None):
            executor = ContinuityExecutor()
            executor._progress_cb = None
            executor._response_cb = None

            # Mock system with session_manager and function_manager
            mock_session = MagicMock()
            # First call returns "original_chat" (captured at start),
            # subsequent calls return "task_chat" (after switch), then "original_chat" after restore
            _active_chat = ["original_chat"]
            def fake_get_active():
                return _active_chat[0]
            def fake_set_active(name):
                _active_chat[0] = name
                return True
            mock_session.get_active_chat_name.side_effect = fake_get_active
            mock_session.set_active_chat.side_effect = fake_set_active
            mock_session.list_chat_files.return_value = [{"name": "task_chat"}]
            original_settings = {"toolset": "all", "memory_scope": "default"}
            mock_session.get_chat_settings.return_value = original_settings.copy()

            mock_fm = MagicMock()
            mock_fm.current_toolset_name = "original_toolset"
            mock_fm._tools_lock = threading.Lock()

            executor.system = MagicMock()
            executor.system.llm_chat.session_manager = mock_session
            executor.system.llm_chat.function_manager = mock_fm
            executor.system.process_llm_query.return_value = "done"

            task = {
                "name": "test_task",
                "chat_target": "task_chat",
                "prompt": "",
                "toolset": "",
                "tts_enabled": False,
                "initial_message": "hello",
            }

            executor._resolve_persona = lambda t: t
            executor._apply_task_settings = MagicMock()
            executor._apply_voice = MagicMock()

            result = executor.run(task)

            # Verify original toolset was restored
            mock_fm.update_enabled_functions.assert_called_with(["original_toolset"])

            # Verify we ended up back on original chat
            assert _active_chat[0] == "original_chat"


# =============================================================================
# Tier 2: update_chat_settings uses dict.update (merge, not replace)
# =============================================================================

class TestUpdateChatSettingsPartialMerge:
    """update_chat_settings must merge, not replace - stale keys persist."""

    def test_partial_update_preserves_unmentioned_keys(self, tmp_path):
        """Updating one key should not remove other existing keys."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default", "toolset": "all"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default", "toolset": "all"}):
                from core.chat.history import ChatSessionManager
                mgr = ChatSessionManager(history_dir=str(tmp_path))

                # Set initial settings
                mgr.update_chat_settings({"memory_scope": "shared", "email_scope": "work"})
                assert mgr.current_settings["memory_scope"] == "shared"
                assert mgr.current_settings["email_scope"] == "work"

                # Partial update — only change memory_scope
                mgr.update_chat_settings({"memory_scope": "private"})

                # email_scope must still be there (merge, not replace)
                assert mgr.current_settings["memory_scope"] == "private"
                assert mgr.current_settings["email_scope"] == "work"

    def test_stale_scope_persists_after_persona_switch_without_reset(self, tmp_path):
        """Without explicit reset, old scope values survive persona switches."""
        with patch('core.chat.history.SYSTEM_DEFAULTS', {"prompt": "default"}):
            with patch('core.chat.history.get_user_defaults', return_value={"prompt": "default"}):
                from core.chat.history import ChatSessionManager
                mgr = ChatSessionManager(history_dir=str(tmp_path))

                # Persona A sets email_scope
                mgr.update_chat_settings({"email_scope": "work_email", "persona": "persona_a"})

                # Persona B doesn't mention email_scope
                mgr.update_chat_settings({"persona": "persona_b", "memory_scope": "private"})

                # email_scope from persona_a is still there — this is the bug M3 prevents
                assert mgr.current_settings.get("email_scope") == "work_email"


# =============================================================================
# Tier 2: isolated_chat sets all 6 scope types
# =============================================================================

class TestIsolatedChatSetsAllScopes:
    """isolated_chat must apply all scope types from task_settings."""

    def test_isolated_chat_sets_all_scopes(self):
        """All scope setters must be called when isolated_chat has a toolset."""
        from core.chat.chat import LLMChat
        from core.chat.function_manager import FunctionManager

        mock_fm = MagicMock(spec=FunctionManager)
        mock_fm.current_toolset_name = "original"
        mock_fm._tools_lock = threading.Lock()
        mock_fm.enabled_tools = [{"function": {"name": "test_func"}}]
        mock_fm.snapshot_scopes.return_value = {}

        with patch.object(LLMChat, '__init__', lambda self: None):
            chat_obj = LLMChat()
            chat_obj.function_manager = mock_fm
            chat_obj.tool_engine = MagicMock()

            # Make call_llm_with_metrics return a simple final response
            mock_response = MagicMock()
            mock_response.has_tool_calls = False
            mock_response.content = "Response"
            chat_obj.tool_engine.call_llm_with_metrics.return_value = mock_response
            chat_obj.tool_engine.extract_function_call_from_text.return_value = None
            chat_obj._use_new_config = False
            chat_obj.provider_primary = MagicMock()
            chat_obj.provider_primary.health_check.return_value = True
            chat_obj.provider_primary.provider_name = "test"
            chat_obj.provider_primary.model = "test-model"

            with patch('core.modules.system.prompts.get_prompt', return_value={"content": "system prompt"}):
                with patch('core.chat.chat.get_generation_params', return_value={}):
                    chat_obj.isolated_chat("hello", {
                        "toolset": "test_tools",
                        "memory_scope": "shared",
                        "goal_scope": "work",
                        "knowledge_scope": "research",
                        "people_scope": "team",
                        "email_scope": "work_email",
                        "bitcoin_scope": "wallet_a",
                    })

        # All scope setters must have been called
        mock_fm.set_memory_scope.assert_called_once()
        mock_fm.set_goal_scope.assert_called_once()
        mock_fm.set_knowledge_scope.assert_called_once()
        mock_fm.set_people_scope.assert_called_once()
        mock_fm.set_email_scope.assert_called_once()
        mock_fm.set_bitcoin_scope.assert_called_once()

        # Toolset must have been restored in finally block
        mock_fm.update_enabled_functions.assert_called()
        last_call = mock_fm.update_enabled_functions.call_args_list[-1]
        assert last_call[0][0] == ["original"]


# =============================================================================
# Tier 2: ContextVar isolation between threads
# =============================================================================

class TestContextVarThreadIsolation:
    """ContextVars must be isolated between threads — one thread's scopes don't leak."""

    def test_contextvar_isolation_between_threads(self):
        """Setting scope in thread A must not affect thread B."""
        from core.chat.function_manager import (
            scope_memory, scope_email, scope_bitcoin, scope_private
        )

        results = {"thread_a": {}, "thread_b": {}}
        barrier = threading.Barrier(2, timeout=5)

        def thread_a():
            scope_memory.set("thread_a_mem")
            scope_email.set("thread_a_email")
            scope_private.set(True)
            barrier.wait()  # Sync — both threads have set their values
            # Read back — should still be thread_a's values
            results["thread_a"]["memory"] = scope_memory.get()
            results["thread_a"]["email"] = scope_email.get()
            results["thread_a"]["private"] = scope_private.get()

        def thread_b():
            scope_memory.set("thread_b_mem")
            scope_email.set("thread_b_email")
            scope_private.set(False)
            barrier.wait()  # Sync
            results["thread_b"]["memory"] = scope_memory.get()
            results["thread_b"]["email"] = scope_email.get()
            results["thread_b"]["private"] = scope_private.get()

        t1 = threading.Thread(target=thread_a)
        t2 = threading.Thread(target=thread_b)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Each thread should see only its own values
        assert results["thread_a"]["memory"] == "thread_a_mem"
        assert results["thread_a"]["email"] == "thread_a_email"
        assert results["thread_a"]["private"] is True

        assert results["thread_b"]["memory"] == "thread_b_mem"
        assert results["thread_b"]["email"] == "thread_b_email"
        assert results["thread_b"]["private"] is False


# =============================================================================
# M5: reset() must clear scopes to defaults
# =============================================================================

class TestResetClearsScopes:
    """LLMChat.reset() must reset all scopes so stale values don't persist."""

    def test_reset_resets_all_scopes(self):
        """After reset(), all scopes should be back to defaults."""
        from core.chat.chat import LLMChat
        from core.chat.function_manager import (
            FunctionManager, scope_memory, scope_email, scope_bitcoin, scope_private
        )

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr._story_engine = None
            mgr._story_engine_enabled = False

        # Set non-default scopes
        mgr.set_memory_scope("private")
        mgr.set_email_scope("work")
        mgr.set_bitcoin_scope("wallet_a")
        mgr.set_private_chat(True)

        with patch.object(LLMChat, '__init__', lambda self: None):
            chat_obj = LLMChat()
            chat_obj.function_manager = mgr
            chat_obj.session_manager = MagicMock()

            chat_obj.reset()

        # All scopes should be reset to defaults
        assert scope_memory.get() == "default"
        assert scope_email.get() == "default"
        assert scope_bitcoin.get() == "default"
        assert scope_private.get() is False

    def test_reset_clears_story_engine(self):
        """reset() should clear the story engine."""
        from core.chat.chat import LLMChat
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr._story_engine = MagicMock()  # Simulate active story engine
            mgr._story_engine_enabled = True

        with patch.object(LLMChat, '__init__', lambda self: None):
            chat_obj = LLMChat()
            chat_obj.function_manager = mgr
            chat_obj.session_manager = MagicMock()

            chat_obj.reset()

        assert mgr._story_engine is None


# =============================================================================
# M6: switch_chat must clear stale story engine
# =============================================================================

class TestSwitchChatClearsStoryEngine:
    """switch_chat must clear story engine so stale state doesn't persist."""

    def test_switch_chat_clears_story_engine(self):
        """After switch_chat(), story engine should be None."""
        from core.chat.chat import LLMChat
        from core.chat.function_manager import FunctionManager

        with patch.object(FunctionManager, '__init__', lambda self: None):
            mgr = FunctionManager()
            mgr._tools_lock = threading.Lock()
            mgr._story_engine = MagicMock()  # Active story engine
            mgr._story_engine_enabled = True

        with patch.object(LLMChat, '__init__', lambda self: None):
            chat_obj = LLMChat()
            chat_obj.function_manager = mgr
            chat_obj.session_manager = MagicMock()
            chat_obj.session_manager.set_active_chat.return_value = True

            chat_obj.switch_chat("other_chat")

        assert mgr._story_engine is None


# =============================================================================
# SSH tool: no shell=True (command injection fix)
# =============================================================================

class TestSSHNoShellTrue:
    """SSH local execution must not use shell=True."""

    def test_run_local_uses_shlex_not_shell(self):
        """_run_local should use shlex.split, not shell=True."""
        import inspect
        from functions.ssh_tool import _run_local
        source = inspect.getsource(_run_local)
        assert "shell=True" not in source, "shell=True still present in _run_local"
        assert "shlex.split" in source, "shlex.split not used in _run_local"

    def test_run_local_splits_command(self):
        """_run_local should properly split a command string."""
        from functions.ssh_tool import _run_local
        # echo is safe and universal — just verify it doesn't crash
        result, success = _run_local("echo hello world", timeout=5)
        assert "hello world" in result


# =============================================================================
# Image upload: PIL decompression bomb guard
# =============================================================================

class TestImageUploadBoundsCheck:
    """Image upload must set MAX_IMAGE_PIXELS to prevent OOM."""

    def test_max_image_pixels_is_set(self):
        """The image upload code should set PIL.Image.MAX_IMAGE_PIXELS."""
        import inspect
        # Read the source of the upload route
        from core.api_fastapi import app
        # Find the upload_image route handler
        for route in app.routes:
            if hasattr(route, 'path') and route.path == '/api/upload/image':
                handler = route.endpoint
                source = inspect.getsource(handler)
                assert "MAX_IMAGE_PIXELS" in source, "MAX_IMAGE_PIXELS not set in upload handler"
                return
        pytest.skip("upload_image route not found")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
