# core/continuity/executor.py
"""
Continuity Executor - Runs scheduled tasks with proper context isolation.
Switches chat context, applies settings, runs LLM, restores original state.
"""

import logging
from datetime import datetime
from typing import Dict, Any
from core.event_bus import publish, Events

logger = logging.getLogger(__name__)


class ContinuityExecutor:
    """Executes continuity tasks with context isolation."""
    
    def __init__(self, system):
        """
        Args:
            system: VoiceChatSystem instance with llm_chat, tts, etc.
        """
        self.system = system
    
    def run(self, task: Dict[str, Any], progress_callback=None, response_callback=None) -> Dict[str, Any]:
        """
        Execute a continuity task.

        Args:
            task: Task definition dict
            progress_callback: Optional callable(iteration, total) for progress updates
            response_callback: Optional callable(response_text) called before TTS

        Returns:
            Result dict with success, responses, errors
        """
        # Plugin-sourced tasks run their handler directly
        source = task.get("source", "")
        if source.startswith("plugin:"):
            return self._run_plugin_task(task, progress_callback, response_callback)

        # Resolve persona defaults into task (task-level fields override persona)
        task = self._resolve_persona(task)

        result = {
            "success": False,
            "task_id": task.get("id"),
            "task_name": task.get("name"),
            "started_at": datetime.now().isoformat(),
            "responses": [],
            "errors": []
        }

        chat_target = task.get("chat_target", "").strip()

        # Blank chat_target = ephemeral: isolated, no chat creation, no UI impact
        if not chat_target:
            return self._run_background(task, result, progress_callback, response_callback)

        # Named chat_target = foreground: switches to that chat, runs, restores
        return self._run_foreground(task, result, progress_callback, response_callback)
    
    def _run_background(self, task: Dict[str, Any], result: Dict[str, Any],
                        progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Run task in background mode - completely isolated, no session state changes."""
        task_name = task.get("name", "Unknown")
        logger.info(f"[Continuity] Running '{task_name}' in BACKGROUND mode (isolated)")

        # Snapshot voice state for restore
        original_voice = self._snapshot_voice()

        try:
            # Build task settings for isolated_chat
            task_settings = {
                "prompt": task.get("prompt", "default"),
                "toolset": task.get("toolset", "none"),
                "provider": task.get("provider", "auto"),
                "model": task.get("model", ""),
                "inject_datetime": task.get("inject_datetime", False),
                "memory_scope": task.get("memory_scope", "default"),
                "knowledge_scope": task.get("knowledge_scope", "none"),
                "people_scope": task.get("people_scope", "none"),
                "goal_scope": task.get("goal_scope", "none"),
                "email_scope": task.get("email_scope", "default"),
                "bitcoin_scope": task.get("bitcoin_scope", "default"),
            }

            # Apply voice settings before any TTS calls
            self._apply_voice(task)

            tts_enabled = task.get("tts_enabled", True)
            browser_tts = task.get("browser_tts", False)
            msg = task.get("initial_message", "Hello.")

            try:
                response = self.system.llm_chat.isolated_chat(msg, task_settings)

                # Update UI before TTS (which blocks)
                if response_cb and response:
                    try: response_cb(response)
                    except Exception: pass

                if response:
                    if browser_tts:
                        publish(Events.TTS_SPEAK, {"text": response, "task": task.get("name", "")})
                    elif tts_enabled and hasattr(self.system, 'tts') and self.system.tts:
                        try:
                            self.system.tts.speak_sync(response)
                        except Exception as tts_err:
                            logger.warning(f"[Continuity] TTS failed: {tts_err}")

                result["responses"].append({
                    "iteration": 1,
                    "input": msg,
                    "output": response or None
                })
            except Exception as e:
                error_msg = f"Task failed: {e}"
                logger.error(f"[Continuity] {error_msg}")
                result["errors"].append(error_msg)

            if progress_cb:
                progress_cb(1, 1)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            error_msg = f"Background task failed: {e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)

        finally:
            self._restore_voice(original_voice)

        result["completed_at"] = datetime.now().isoformat()
        return result
    
    def _run_foreground(self, task: Dict[str, Any], result: Dict[str, Any],
                        progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Run task in foreground mode - switches to named chat, runs, restores original."""
        import config

        # Don't switch active chat while user is streaming — would corrupt chat context
        if self.system.llm_chat.streaming_chat.is_streaming:
            task_name = task.get("name", "Unknown")
            logger.warning(f"[Continuity] Deferring foreground task '{task_name}' — user stream active")
            result["errors"].append("Deferred: user stream in progress")
            result["completed_at"] = datetime.now().isoformat()
            return result

        session_manager = self.system.llm_chat.session_manager
        original_chat = session_manager.get_active_chat_name()
        original_toolset = self.system.llm_chat.function_manager.current_toolset_name
        original_voice = self._snapshot_voice()
        target_original_settings = None
        target_chat = task.get("chat_target", "").strip()

        # Temporarily override global config with per-task limits
        _config_overrides = {}
        for task_key, config_key in [
            ("max_tool_rounds", "MAX_TOOL_ITERATIONS"),
            ("max_parallel_tools", "MAX_PARALLEL_TOOLS"),
            ("context_limit", "CONTEXT_LIMIT"),
        ]:
            val = task.get(task_key)
            if val:  # 0 means "use global default"
                _config_overrides[config_key] = getattr(config, config_key, None)
                from core.settings_manager import settings as _settings
                _settings.set(config_key, val, persist=False)
                logger.debug(f"[Continuity] Override {config_key}: {_config_overrides[config_key]} -> {val}")

        try:
            logger.info(f"[Continuity] Running '{task.get('name')}' in FOREGROUND mode, chat='{target_chat}'")

            # Find existing chat or create new one
            # Normalize target the same way create_chat does (replace spaces, lowercase)
            normalized = target_chat.replace(' ', '_').lower()
            existing_chats = {c["name"]: c["name"] for c in session_manager.list_chat_files()}
            match = existing_chats.get(normalized)
            if match:
                target_chat = match  # Use actual DB name
            else:
                logger.info(f"[Continuity] Creating new chat: {target_chat}")
                if not session_manager.create_chat(target_chat):
                    raise RuntimeError(f"Failed to create chat: {target_chat}")
                # create_chat lowercases, so resolve the actual name
                target_chat = target_chat.replace(' ', '_').lower()

            # Switch to target chat
            if not session_manager.set_active_chat(target_chat):
                raise RuntimeError(f"Failed to switch to chat: {target_chat}")

            # Snapshot target chat's settings before task overrides them
            target_original_settings = session_manager.get_chat_settings()

            # Apply task settings to chat
            self._apply_task_settings(task, session_manager)

            # Run single execution
            tts_enabled = task.get("tts_enabled", True)
            browser_tts = task.get("browser_tts", False)
            msg = task.get("initial_message", "Hello.")

            try:
                response = self.system.process_llm_query(msg, skip_tts=True)

                # Update UI before TTS (which blocks)
                if response_cb and response:
                    try: response_cb(response)
                    except Exception: pass

                if response:
                    if browser_tts:
                        publish(Events.TTS_SPEAK, {"text": response, "task": task.get("name", "")})
                    elif tts_enabled and hasattr(self.system, 'tts') and self.system.tts:
                        try:
                            self.system.tts.speak_sync(response)
                        except Exception as tts_err:
                            logger.warning(f"[Continuity] TTS failed: {tts_err}")
                result["responses"].append({
                    "iteration": 1,
                    "input": msg,
                    "output": response or None
                })
            except Exception as e:
                error_msg = f"Task failed: {e}"
                logger.error(f"[Continuity] {error_msg}")
                result["errors"].append(error_msg)

            if progress_cb:
                progress_cb(1, 1)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            error_msg = f"Foreground task failed: {e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)

        finally:
            # Restore per-task config overrides
            if _config_overrides:
                from core.settings_manager import settings as _settings
                for config_key, original_val in _config_overrides.items():
                    _settings.set(config_key, original_val, persist=False)
                logger.debug(f"[Continuity] Restored config overrides: {list(_config_overrides.keys())}")

            # Always restore original chat context, toolset, and voice
            try:
                # Restore target chat's original settings before switching away
                # (set_active_chat saves current settings, so this must come first)
                if target_original_settings is not None:
                    session_manager.current_settings = target_original_settings
                if session_manager.get_active_chat_name() != original_chat:
                    session_manager.set_active_chat(original_chat)
                    logger.debug(f"[Continuity] Restored chat context to '{original_chat}'")
                    # Don't publish CHAT_SWITCHED — this is backend state restore,
                    # not a UI navigation. Frontend session is authoritative for the user's view.
                self.system.llm_chat.function_manager.update_enabled_functions([original_toolset])
                logger.debug(f"[Continuity] Restored toolset to '{original_toolset}'")
            except Exception as e:
                logger.error(f"[Continuity] Failed to restore chat context: {e}")
                result["errors"].append(f"Context restore failed: {e}")

            self._restore_voice(original_voice)

        result["completed_at"] = datetime.now().isoformat()
        return result

    def _run_plugin_task(self, task: Dict[str, Any], progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Execute a plugin-sourced scheduled task by calling its handler."""
        from pathlib import Path
        import config

        result = {
            "success": False,
            "task_id": task.get("id"),
            "task_name": task.get("name"),
            "started_at": datetime.now().isoformat(),
            "responses": [],
            "errors": []
        }

        plugin_name = task.get("source", "").replace("plugin:", "")
        handler_path = task.get("handler", "")
        plugin_dir = task.get("plugin_dir", "")

        if not handler_path or not plugin_dir:
            result["errors"].append(f"Plugin task missing handler or plugin_dir")
            return result

        full_path = Path(plugin_dir) / handler_path
        if not full_path.exists():
            result["errors"].append(f"Handler not found: {full_path}")
            return result

        try:
            source = full_path.read_text(encoding="utf-8")
            namespace = {"__file__": str(full_path), "__name__": f"plugin_schedule_{plugin_name}"}
            exec(compile(source, str(full_path), "exec"), namespace)

            run_func = namespace.get("run")
            if not run_func or not callable(run_func):
                result["errors"].append(f"No 'run' function in {full_path}")
                return result

            # Build event dict for the handler
            from core.plugin_loader import plugin_loader
            plugin_state = plugin_loader.get_plugin_state(plugin_name)

            event = {
                "system": self.system,
                "config": config,
                "task": task,
                "plugin_state": plugin_state,
            }

            output = run_func(event)
            result["responses"].append({"output": str(output) if output else None})
            result["success"] = True

            if response_cb and output:
                try: response_cb(str(output))
                except Exception: pass

        except Exception as e:
            logger.error(f"[Continuity] Plugin task '{task.get('name')}' failed: {e}", exc_info=True)
            result["errors"].append(str(e))

        if progress_cb:
            progress_cb(1, 1)

        result["completed_at"] = datetime.now().isoformat()
        return result

    def _resolve_persona(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """If task has a persona, merge persona settings as defaults under task-level overrides."""
        persona_name = task.get("persona", "")
        if not persona_name:
            return task

        try:
            from core.personas import persona_manager
            persona = persona_manager.get(persona_name)
            if not persona:
                logger.warning(f"[Continuity] Persona '{persona_name}' not found, skipping")
                return task

            ps = persona.get("settings", {})
            resolved = dict(task)

            # Persona provides defaults — task-level fields override
            field_map = {
                "prompt": "prompt",
                "toolset": "toolset",
                "voice": "voice",
                "pitch": "pitch",
                "speed": "speed",
                "llm_primary": "provider",
                "llm_model": "model",
                "inject_datetime": "inject_datetime",
                "memory_scope": "memory_scope",
                "knowledge_scope": "knowledge_scope",
                "people_scope": "people_scope",
                "goal_scope": "goal_scope",
                "email_scope": "email_scope",
                "bitcoin_scope": "bitcoin_scope",
            }
            for persona_key, task_key in field_map.items():
                persona_val = ps.get(persona_key)
                task_val = resolved.get(task_key)
                # Use persona value if task field is empty/default
                if persona_val and not task_val:
                    resolved[task_key] = persona_val
                elif persona_val and task_val in ("", "auto", "none", "default", None):
                    resolved[task_key] = persona_val

            logger.info(f"[Continuity] Resolved persona '{persona_name}' into task settings")
            return resolved
        except Exception as e:
            logger.error(f"[Continuity] Persona resolution failed: {e}")
            return task

    def _snapshot_voice(self) -> Dict[str, Any]:
        """Snapshot current TTS voice/pitch/speed for later restore."""
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return {}
        try:
            return {
                "voice": getattr(tts, 'voice_name', None),
                "pitch": getattr(tts, 'pitch_shift', None),
                "speed": getattr(tts, 'speed', None),
            }
        except Exception:
            return {}

    def _validate_voice(self, voice: str) -> str:
        """Validate voice matches current TTS provider, substitute default if mismatched."""
        from core.tts.utils import validate_voice
        return validate_voice(voice)

    def _restore_voice(self, snapshot: Dict[str, Any]) -> None:
        """Restore TTS voice/pitch/speed from snapshot."""
        if not snapshot:
            return
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return
        try:
            if snapshot.get("voice") is not None:
                tts.set_voice(self._validate_voice(snapshot["voice"]))
            if snapshot.get("pitch") is not None:
                tts.set_pitch(snapshot["pitch"])
            if snapshot.get("speed") is not None:
                tts.set_speed(snapshot["speed"])
            logger.debug(f"[Continuity] Restored voice settings: {snapshot}")
        except Exception as e:
            logger.warning(f"[Continuity] Failed to restore voice settings: {e}")

    def _apply_voice(self, task: Dict[str, Any]) -> None:
        """Apply voice/pitch/speed settings to TTS if available."""
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return
        try:
            if task.get("voice"):
                tts.set_voice(self._validate_voice(task["voice"]))
            if task.get("pitch") is not None:
                tts.set_pitch(task["pitch"])
            if task.get("speed") is not None:
                tts.set_speed(task["speed"])
        except Exception as e:
            logger.warning(f"[Continuity] Failed to apply voice settings: {e}")

    def _apply_task_settings(self, task: Dict[str, Any], session_manager) -> None:
        """Apply task's prompt/ability/LLM/memory/datetime settings to current chat."""
        settings = {}
        
        if task.get("prompt"):
            settings["prompt"] = task["prompt"]
            
            # Also apply to live LLM
            from core import prompts
            prompt_data = prompts.get_prompt(task["prompt"])
            if prompt_data:
                content = prompt_data.get("content") if isinstance(prompt_data, dict) else str(prompt_data)
                self.system.llm_chat.set_system_prompt(content)
                prompts.set_active_preset_name(task["prompt"])
        
        if task.get("toolset"):
            settings["toolset"] = task["toolset"]
            # Apply to function manager
            self.system.llm_chat.function_manager.update_enabled_functions([task["toolset"]])
        
        if task.get("provider") and task["provider"] != "auto":
            settings["llm_primary"] = task["provider"]
        
        if task.get("model"):
            settings["llm_model"] = task["model"]
        
        if task.get("memory_scope"):
            settings["memory_scope"] = task["memory_scope"]

        if task.get("knowledge_scope"):
            settings["knowledge_scope"] = task["knowledge_scope"]

        if task.get("people_scope"):
            settings["people_scope"] = task["people_scope"]

        if task.get("goal_scope"):
            settings["goal_scope"] = task["goal_scope"]

        if task.get("email_scope"):
            settings["email_scope"] = task["email_scope"]

        if task.get("bitcoin_scope"):
            settings["bitcoin_scope"] = task["bitcoin_scope"]

        # Inject datetime into system prompt if enabled
        if task.get("inject_datetime"):
            settings["inject_datetime"] = True

        # Voice settings
        if task.get("voice"):
            settings["voice"] = task["voice"]
        if task.get("pitch") is not None:
            settings["pitch"] = task["pitch"]
        if task.get("speed") is not None:
            settings["speed"] = task["speed"]

        if settings:
            session_manager.update_chat_settings(settings)
            logger.debug(f"[Continuity] Applied settings: {settings}")

        # Apply voice to live TTS
        self._apply_voice(task)

        # Backend state is set — don't broadcast CHAT_SWITCHED to frontend.
        # Task chat targeting is internal; the user's UI view is authoritative.