# core/continuity/executor.py
"""
Continuity Executor - Runs scheduled tasks with proper context isolation.
Switches chat context, applies settings, runs LLM, restores original state.
"""

import json
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

    @staticmethod
    def _format_event_data(event_data: str) -> str:
        """Format raw event JSON into clean text for the AI.

        If the data is JSON with a 'text' field (e.g. messaging daemons),
        present it as a clean message. Otherwise pass through raw.
        """
        try:
            obj = json.loads(event_data) if isinstance(event_data, str) else event_data
        except (json.JSONDecodeError, TypeError):
            return event_data

        text = obj.get("text") or obj.get("content") or None
        if not isinstance(obj, dict) or not text:
            return event_data

        # Build clean message from common fields
        sender = obj.get("display_name") or obj.get("first_name") or obj.get("username") or obj.get("sender") or ""
        channel = obj.get("channel_name", "")
        guild = obj.get("guild_name", "")

        parts = []
        # Add channel/server context if available (Discord, etc.)
        if channel and guild:
            parts.append(f"[#{channel} in {guild}]")
        elif channel:
            parts.append(f"[#{channel}]")

        # Include recent chat history if available
        history = obj.get("recent_history", [])
        if history:
            parts.append("Recent chat:")
            for line in history:
                parts.append(f"  {line}")
            parts.append("")  # blank line before the trigger message

        # The trigger message itself — emphasized
        if sender:
            parts.append(f">>> {sender}: {text}")
        else:
            parts.append(f">>> {text}")
        return "\n".join(parts)

    def run(self, task: Dict[str, Any], event_data: str = None,
            progress_callback=None, response_callback=None) -> Dict[str, Any]:
        """
        Execute a continuity task.

        Args:
            task: Task definition dict
            event_data: Optional event payload (for daemon/webhook triggered tasks).
                        When present, initial_message is prepended as instructions.
            progress_callback: Optional callable(iteration, total) for progress updates
            response_callback: Optional callable(response_text) called before TTS

        Returns:
            Result dict with success, responses, errors
        """
        # Plugin-sourced tasks run their handler directly
        source = task.get("source", "")
        if source.startswith("plugin:"):
            return self._run_plugin_task(task, progress_callback, response_callback)

        # For event-triggered tasks, build message from instructions + event data
        if event_data is not None:
            task = dict(task)  # don't mutate original
            event_display = self._format_event_data(event_data)
            instructions = task.get("initial_message", "").strip()
            if instructions:
                task["initial_message"] = f"{instructions}\n\n{event_display}"
            else:
                task["initial_message"] = event_display

            # Auto-set plugin scopes from event data (e.g. discord account)
            try:
                obj = json.loads(event_data) if isinstance(event_data, str) else event_data
                if isinstance(obj, dict) and obj.get("account"):
                    trigger = task.get("trigger_config", {})
                    source = trigger.get("source", "") or trigger.get("event_source", "")
                    if "discord" in source and not task.get("discord_scope"):
                        task["discord_scope"] = obj["account"]
                    elif "email" in source and not task.get("email_scope"):
                        task["email_scope"] = obj["account"]
            except (json.JSONDecodeError, TypeError):
                pass

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
    
    @staticmethod
    def _extract_task_settings(task: Dict[str, Any]) -> Dict[str, Any]:
        """Extract execution settings from a task dict for ExecutionContext."""
        return {
            "prompt": task.get("prompt", "default"),
            "toolset": task.get("toolset", "none"),
            "provider": task.get("provider", "auto"),
            "model": task.get("model", ""),
            "inject_datetime": task.get("inject_datetime", False),
            "max_tool_rounds": task.get("max_tool_rounds"),
            "max_parallel_tools": task.get("max_parallel_tools"),
            "context_limit": task.get("context_limit"),
            "memory_scope": task.get("memory_scope", "default"),
            "knowledge_scope": task.get("knowledge_scope", "none"),
            "people_scope": task.get("people_scope", "none"),
            "goal_scope": task.get("goal_scope", "none"),
            "email_scope": task.get("email_scope", "default"),
            "bitcoin_scope": task.get("bitcoin_scope", "default"),
            "discord_scope": task.get("discord_scope", "default"),
        }

    def _run_background(self, task: Dict[str, Any], result: Dict[str, Any],
                        progress_cb=None, response_cb=None) -> Dict[str, Any]:
        """Run task in background mode — fully isolated via ExecutionContext."""
        from core.continuity.execution_context import ExecutionContext

        task_name = task.get("name", "Unknown")
        logger.info(f"[Continuity] Running '{task_name}' in BACKGROUND mode (ExecutionContext)")

        original_voice = self._snapshot_voice()

        try:
            task_settings = self._extract_task_settings(task)
            ctx = ExecutionContext(
                self.system.llm_chat.function_manager,
                self.system.llm_chat.tool_engine,
                task_settings
            )

            self._apply_voice(task)

            tts_enabled = task.get("tts_enabled", True)
            browser_tts = task.get("browser_tts", False)
            msg = task.get("initial_message", "Hello.")

            try:
                response = ctx.run(msg)

                if response_cb and response:
                    try: response_cb(response)
                    except Exception: pass

                if response:
                    if browser_tts:
                        publish(Events.TTS_SPEAK, {"text": response, "task": task_name})
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
                logger.error(f"[Continuity] {error_msg}", exc_info=True)
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
        """Run task with persistent chat history — no UI switching."""
        from core.continuity.execution_context import ExecutionContext

        session_manager = self.system.llm_chat.session_manager
        original_voice = self._snapshot_voice()
        target_chat = task.get("chat_target", "").strip()

        try:
            logger.info(f"[Continuity] Running '{task.get('name')}' with chat persistence, chat='{target_chat}'")

            # Find existing chat or create new one
            normalized = target_chat.replace(' ', '_').lower()
            existing_chats = {c["name"]: c["name"] for c in session_manager.list_chat_files()}
            match = existing_chats.get(normalized)
            if match:
                target_chat = match
            else:
                logger.info(f"[Continuity] Creating new chat: {target_chat}")
                if not session_manager.create_chat(target_chat):
                    raise RuntimeError(f"Failed to create chat: {target_chat}")
                target_chat = target_chat.replace(' ', '_').lower()
                publish(Events.CHAT_CREATED, {"name": target_chat})

            # Build ExecutionContext — isolated, no singleton mutation
            task_settings = self._extract_task_settings(task)
            ctx = ExecutionContext(
                self.system.llm_chat.function_manager,
                self.system.llm_chat.tool_engine,
                task_settings
            )

            self._apply_voice(task)

            tts_enabled = task.get("tts_enabled", True)
            browser_tts = task.get("browser_tts", False)
            msg = task.get("initial_message", "Hello.")

            # Read history from target chat WITHOUT switching active chat
            history_messages = session_manager.read_chat_messages(
                target_chat, provider=task_settings.get("provider")
            )

            try:
                # Run through isolated ExecutionContext — no singleton contact
                response = ctx.run(msg, history_messages=history_messages)

                # Persist both messages to target chat WITHOUT switching active chat
                session_manager.append_to_chat(target_chat, msg, response or "")

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
                logger.error(f"[Continuity] {error_msg}", exc_info=True)
                result["errors"].append(error_msg)

            if progress_cb:
                progress_cb(1, 1)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            error_msg = f"Persistent chat task failed: {e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)

        finally:
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
                "discord_scope": "discord_scope",
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

    # _apply_task_settings removed — ExecutionContext handles all isolation now