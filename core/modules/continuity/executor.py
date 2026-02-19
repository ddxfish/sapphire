# core/modules/continuity/executor.py
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
        self._progress_cb = progress_callback
        self._response_cb = response_callback

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
            return self._run_background(task, result)

        # Named chat_target = foreground: switches to that chat, runs, restores
        return self._run_foreground(task, result)
    
    def _run_background(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Run task in background mode - completely isolated, no session state changes."""
        task_name = task.get("name", "Unknown")
        logger.info(f"[Continuity] Running '{task_name}' in BACKGROUND mode (isolated)")
        
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
            }
            
            # Apply voice settings before any TTS calls
            self._apply_voice(task)

            tts_enabled = task.get("tts_enabled", True)
            msg = task.get("initial_message", "Hello.")

            try:
                response = self.system.llm_chat.isolated_chat(msg, task_settings)

                # Update UI before TTS (which blocks)
                if self._response_cb and response:
                    try: self._response_cb(response)
                    except Exception: pass

                if tts_enabled and response and hasattr(self.system, 'tts') and self.system.tts:
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

            if self._progress_cb:
                self._progress_cb(1, 1)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            error_msg = f"Background task failed: {e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)
        
        result["completed_at"] = datetime.now().isoformat()
        return result
    
    def _run_foreground(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Run task in foreground mode - switches to named chat, runs, restores original."""
        import config
        session_manager = self.system.llm_chat.session_manager
        original_chat = session_manager.get_active_chat_name()
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

            # Apply task settings to chat
            self._apply_task_settings(task, session_manager)

            # Run single execution
            tts_enabled = task.get("tts_enabled", True)
            msg = task.get("initial_message", "Hello.")

            try:
                response = self.system.process_llm_query(msg, skip_tts=True)

                # Update UI before TTS (which blocks)
                if self._response_cb and response:
                    try: self._response_cb(response)
                    except Exception: pass

                if tts_enabled and response and hasattr(self.system, 'tts') and self.system.tts:
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

            if self._progress_cb:
                self._progress_cb(1, 1)

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

            # Always restore original chat context
            try:
                if session_manager.get_active_chat_name() != original_chat:
                    session_manager.set_active_chat(original_chat)
                    logger.debug(f"[Continuity] Restored chat context to '{original_chat}'")
                    # Don't publish CHAT_SWITCHED — this is backend state restore,
                    # not a UI navigation. Frontend session is authoritative for the user's view.
            except Exception as e:
                logger.error(f"[Continuity] Failed to restore chat context: {e}")
                result["errors"].append(f"Context restore failed: {e}")

        result["completed_at"] = datetime.now().isoformat()
        return result
    
    def _resolve_persona(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """If task has a persona, merge persona settings as defaults under task-level overrides."""
        persona_name = task.get("persona", "")
        if not persona_name:
            return task

        try:
            from core.modules.system.personas import persona_manager
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

    def _apply_voice(self, task: Dict[str, Any]) -> None:
        """Apply voice/pitch/speed settings to TTS if available."""
        tts = getattr(self.system, 'tts', None)
        if not tts:
            return
        try:
            if task.get("voice"):
                tts.set_voice(task["voice"])
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
            from core.modules.system import prompts
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

        if task.get("knowledge_scope") and task["knowledge_scope"] != "none":
            settings["knowledge_scope"] = task["knowledge_scope"]

        if task.get("people_scope") and task["people_scope"] != "none":
            settings["people_scope"] = task["people_scope"]

        if task.get("goal_scope") and task["goal_scope"] != "none":
            settings["goal_scope"] = task["goal_scope"]

        if task.get("email_scope"):
            settings["email_scope"] = task["email_scope"]

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