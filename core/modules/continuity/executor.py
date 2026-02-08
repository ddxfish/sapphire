# core/modules/continuity/executor.py
"""
Continuity Executor - Runs scheduled tasks with proper context isolation.
Switches chat context, applies settings, runs LLM, restores original state.
"""

import time
import random
import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)


class ContinuityExecutor:
    """Executes continuity tasks with context isolation."""
    
    def __init__(self, system):
        """
        Args:
            system: VoiceChatSystem instance with llm_chat, tts, etc.
        """
        self.system = system
    
    def run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a continuity task.
        
        For background tasks: Uses isolated_chat() - no session state changes, no UI impact.
        For foreground tasks: Switches chat context, runs normally, restores original.
        
        Args:
            task: Task definition dict
            
        Returns:
            Result dict with success, responses, errors
        """
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
            }
            
            iterations = max(1, task.get("iterations", 1))
            cooldown_sec = task.get("cooldown_minutes", 0) * 60
            chance = task.get("chance", 100)
            tts_enabled = task.get("tts_enabled", True)
            initial_message = task.get("initial_message", "Hello.")

            for i in range(iterations):
                # Cooldown between follow-up iterations (not before first)
                if i > 0 and cooldown_sec > 0:
                    logger.info(f"[Continuity] Iteration cooldown: {cooldown_sec}s before iteration {i+1}")
                    time.sleep(cooldown_sec)

                # Per-iteration chance roll
                if chance < 100:
                    roll = random.randint(1, 100)
                    if roll > chance:
                        logger.info(f"[Continuity] Iteration {i+1} skipped (roll {roll} > {chance}%)")
                        continue

                msg = initial_message if i == 0 else "[continue]"

                try:
                    # Use isolated_chat - no session state changes
                    response = self.system.llm_chat.isolated_chat(msg, task_settings)

                    # TTS if enabled
                    if tts_enabled and response and hasattr(self.system, 'tts') and self.system.tts:
                        try:
                            self.system.tts.speak(response)
                        except Exception as tts_err:
                            logger.warning(f"[Continuity] TTS failed: {tts_err}")

                    result["responses"].append({
                        "iteration": i + 1,
                        "input": msg,
                        "output": response[:500] if response else None
                    })
                except Exception as e:
                    error_msg = f"Iteration {i+1} failed: {e}"
                    logger.error(f"[Continuity] {error_msg}")
                    result["errors"].append(error_msg)
            
            result["success"] = len(result["errors"]) == 0
            
        except Exception as e:
            error_msg = f"Background task failed: {e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)
        
        result["completed_at"] = datetime.now().isoformat()
        return result
    
    def _run_foreground(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Run task in foreground mode - switches to named chat, runs, restores original."""
        session_manager = self.system.llm_chat.session_manager
        original_chat = session_manager.get_active_chat_name()
        target_chat = task.get("chat_target", "").strip()

        try:
            logger.info(f"[Continuity] Running '{task.get('name')}' in FOREGROUND mode, chat='{target_chat}'")

            # Find existing chat (case-insensitive) or create new one
            existing_chats = {c["name"].lower(): c["name"] for c in session_manager.list_chat_files()}
            match = existing_chats.get(target_chat.lower())
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

            # Run iterations
            iterations = max(1, task.get("iterations", 1))
            cooldown_sec = task.get("cooldown_minutes", 0) * 60
            chance = task.get("chance", 100)
            tts_enabled = task.get("tts_enabled", True)
            initial_message = task.get("initial_message", "Hello.")

            for i in range(iterations):
                # Cooldown between follow-up iterations (not before first)
                if i > 0 and cooldown_sec > 0:
                    logger.info(f"[Continuity] Iteration cooldown: {cooldown_sec}s before iteration {i+1}")
                    time.sleep(cooldown_sec)

                # Per-iteration chance roll
                if chance < 100:
                    roll = random.randint(1, 100)
                    if roll > chance:
                        logger.info(f"[Continuity] Iteration {i+1} skipped (roll {roll} > {chance}%)")
                        continue

                msg = initial_message if i == 0 else "[continue]"

                try:
                    response = self.system.process_llm_query(msg, skip_tts=not tts_enabled)
                    result["responses"].append({
                        "iteration": i + 1,
                        "input": msg,
                        "output": response[:500] if response else None
                    })
                except Exception as e:
                    error_msg = f"Iteration {i+1} failed: {e}"
                    logger.error(f"[Continuity] {error_msg}")
                    result["errors"].append(error_msg)

            result["success"] = len(result["errors"]) == 0

        except Exception as e:
            error_msg = f"Foreground task failed: {e}"
            logger.error(f"[Continuity] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)

        finally:
            # Always restore original chat context
            try:
                if session_manager.get_active_chat_name() != original_chat:
                    session_manager.set_active_chat(original_chat)
                    logger.debug(f"[Continuity] Restored chat context to '{original_chat}'")

                    from core.event_bus import publish, Events
                    publish(Events.CHAT_SWITCHED, {"chat": original_chat})
            except Exception as e:
                logger.error(f"[Continuity] Failed to restore chat context: {e}")
                result["errors"].append(f"Context restore failed: {e}")

        result["completed_at"] = datetime.now().isoformat()
        return result
    
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
            settings["ability"] = task["toolset"]
            # Apply to function manager
            self.system.llm_chat.function_manager.update_enabled_functions([task["toolset"]])
        
        if task.get("provider") and task["provider"] != "auto":
            settings["llm_primary"] = task["provider"]
        
        if task.get("model"):
            settings["llm_model"] = task["model"]
        
        if task.get("memory_scope"):
            settings["memory_scope"] = task["memory_scope"]
        
        # Inject datetime into system prompt if enabled
        if task.get("inject_datetime"):
            settings["inject_datetime"] = True
        
        if settings:
            session_manager.update_chat_settings(settings)
            logger.debug(f"[Continuity] Applied settings: {settings}")
        
        # Publish chat switch event so UI updates
        from core.event_bus import publish, Events
        publish(Events.CHAT_SWITCHED, {"chat": session_manager.get_active_chat_name()})