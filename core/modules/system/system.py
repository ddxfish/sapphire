import logging
from . import voice, speed_pitch, prompts, ability, chat_switch

logger = logging.getLogger(__name__)

class System:
    def __init__(self):
        self.voice_chat_system = None
        self.ability_manager = ability.AbilityManager()
        self.chat_switcher = chat_switch.ChatSwitcher()
        self.handlers = {
            'prompt': self._handle_prompt,
            'voice': lambda args: voice.change_voice(self.voice_chat_system, args),
            'speed': lambda args: speed_pitch.change_speed(self.voice_chat_system, args),
            'pitch': lambda args: speed_pitch.change_pitch(self.voice_chat_system, args),
            'ability': lambda args: self.ability_manager.process(args),
            'chat': lambda args: self.chat_switcher.process(args),
            'help': lambda _: "Commands: prompt [name|status|state|reset|random], voice [name], speed [value], pitch [value], ability [name], chat [name]"
        }
    
    def process(self, user_input, llm_client=None):
        if not user_input or not user_input.strip():
            return self.handlers['help']("")
        
        parts = user_input.lower().strip().split(None, 1)
        if not parts:
            return self.handlers['help']("")
        
        command, args = parts[0], parts[1] if len(parts) > 1 else ""
        handler = self.handlers.get(command)
        
        return handler(args) if handler else f"Unknown command: {command}. Try 'system help'"
    
    def _handle_prompt(self, args):
        """Handle all prompt-related commands."""
        if not args:
            available = prompts.list_prompts()
            current = prompts.get_active_preset_name()
            return f"Current: {current}\nAvailable: {', '.join(available)}"
        
        parts = args.split(None, 1)
        cmd = parts[0]
        
        # Status - show full current prompt
        if cmd == "status":
            preset = prompts.get_active_preset_name()
            prompt_data = prompts.get_current_prompt()
            content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
            prompt_type = "monolith" if preset in prompts.MONOLITHS else "assembled"
            char_count = len(content)
            
            return f"Current prompt: {preset} ({prompt_type}, {char_count} chars)\n\n{content}"
        
        # State - show assembly info
        if cmd == "state":
            return prompts.get_assembled_state()
        
        # Reset - back to defaults
        if cmd == "reset":
            prompts.reset_to_defaults()
            self._apply_prompt_and_update_json('default')
            return "Reset to default"
        
        # Random - generate random assembly
        if cmd == "random":
            config_info = prompts.apply_random_assembled()
            self._apply_prompt_and_update_json('random')
            return config_info
        
        # Two-word commands
        if len(parts) == 2:
            value = parts[1]
            
            # Apply scenario (piece-based)
            if cmd == "scenario" or cmd == "apply":
                if value in prompts.SCENARIO_PRESETS:
                    result = prompts.apply_scenario(value)
                    self._apply_prompt_and_update_json(value)
                    return result
                return f"Scenario '{value}' not found"
            
            # Remove extras/emotions
            if cmd == "remove":
                result = (prompts.remove_emotion(value) if value in prompts._assembled_state.get("emotions", [])
                         else prompts.remove_extra(value))
                if result.startswith("Removed"):
                    self._apply_prompt_and_update_json('assembled')
                return result
            
            # Clear commands
            if cmd == "extras" and value == "clear":
                result = prompts.clear_extras()
                self._apply_prompt_and_update_json('assembled')
                return result
            
            if cmd == "emotions" and value == "clear":
                result = prompts.clear_emotions()
                self._apply_prompt_and_update_json('assembled')
                return result
            
            # Spice commands
            if cmd == "spice":
                result = prompts.clear_spice() if value == "clear" else prompts.set_random_spice()
                self._apply_prompt()
                # Don't change prompt name for spice changes
                return result
            
            # Component changes (location, persona, etc)
            if cmd in ["persona", "location", "relationship", "goals", "format", "scenario", "extras", "emotions"]:
                result = prompts.set_component(cmd, value)
                if any(x in result for x in ["Set", "Added"]):
                    self._apply_prompt_and_update_json('assembled')
                return result
        
        # Single word - load named prompt (monolith or scenario)
        available = prompts.list_prompts()
        if cmd not in available:
            return f"Prompt '{cmd}' not found. Available: {', '.join(available)}\nTry 'system prompt status' to see current prompt"
        
        # Update JSON FIRST (like the modal does)
        self._update_json_setting('prompt', cmd)
        
        # Then apply to system (like the modal does)
        prompt_data = prompts.get_prompt(cmd)
        content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
        
        # Apply to LLM
        self.voice_chat_system.llm_chat.set_system_prompt(content)
        
        # Set as active
        prompts.set_active_preset_name(cmd)
        
        # If it's a scenario preset, apply the scenario state
        if hasattr(prompts.prompt_manager, 'scenario_presets') and cmd in prompts.prompt_manager.scenario_presets:
            prompts.apply_scenario(cmd)
            logger.info(f"Applied scenario state '{cmd}'")
        
        prompt_type = "monolith" if cmd in prompts.MONOLITHS else "assembled"
        return f"Prompt changed to '{cmd}' ({prompt_type})"
    
    def _apply_prompt(self):
        """Apply the current prompt to the LLM (without updating JSON)."""
        if self.voice_chat_system and hasattr(self.voice_chat_system.llm_chat, 'set_system_prompt'):
            prompt_data = prompts.get_current_prompt()
            content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
            self.voice_chat_system.llm_chat.set_system_prompt(content)
    
    def _apply_prompt_and_update_json(self, prompt_name: str):
        """Apply prompt AND update chat JSON (mirrors modal behavior)."""
        # Update JSON first
        self._update_json_setting('prompt', prompt_name)
        
        # Then apply to LLM
        prompt_data = prompts.get_current_prompt()
        content = prompt_data['content'] if isinstance(prompt_data, dict) else str(prompt_data)
        self.voice_chat_system.llm_chat.set_system_prompt(content)
        
        logger.info(f"Applied and saved prompt: {prompt_name}")
    
    def _update_json_setting(self, key: str, value):
        """Update chat settings JSON (mirrors what modal does)."""
        if not self.voice_chat_system or not hasattr(self.voice_chat_system.llm_chat, 'session_manager'):
            logger.error("Cannot update settings: system not initialized")
            return
        
        try:
            session_manager = self.voice_chat_system.llm_chat.session_manager
            
            # Update settings (this saves to JSON)
            success = session_manager.update_chat_settings({key: value})
            
            if success:
                logger.info(f"Updated chat JSON: {key}={value}")
            else:
                logger.error(f"Failed to update chat JSON: {key}={value}")
                
        except Exception as e:
            logger.error(f"Exception updating chat setting {key}: {e}", exc_info=True)
    
    def attach_system(self, voice_chat_system):
        self.voice_chat_system = voice_chat_system
        self.ability_manager.attach_system(voice_chat_system)
        self.chat_switcher.attach_system(voice_chat_system)
        logger.info("System module attached")