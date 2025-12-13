import logging

logger = logging.getLogger(__name__)

# Predefined list of available voices
VOICE_LIST = [
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa", "af_bella", "af_nicole", "af_alloy", "af_heart", "af_jessica", "af_sarah","af_river",
    "af_aoede", "af_kore", "af_nova", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jf_alpha", "if_sara",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "pf_dora",   
    "af_sky", "bf_emma", "bf_isabella", "bf_alice", "bf_lily", "bm_george", "bm_daniel", "bm_fable",
    "bm_george", "bm_lewis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi"
]

def _clean_voice_name(voice_name):
    """Extract display name without prefix."""
    if '_' in voice_name:
        return voice_name.split('_', 1)[1]
    return voice_name

def get_voices():
    """Get available voices with clean names."""
    try:
        return {_clean_voice_name(voice): voice for voice in VOICE_LIST}
    except Exception as e:
        logger.error(f"Error listing voices: {e}")
        return {}

def change_voice(system, voice_name):
    """Change the TTS voice."""
    voices = get_voices()
    voice_map = {k.lower(): v for k, v in voices.items()}  # For case-insensitive lookup
    display_map = {v.lower(): v for v in VOICE_LIST}  # Direct voice name lookup
    
    # Check if the name is a direct match first (with prefix)
    if voice_name.lower() in display_map:
        actual_voice = display_map[voice_name.lower()]
    else:
        # Find matching voice by display name
        voice_lower = voice_name.lower()
        actual_voice = voice_map.get(voice_lower)
        
        # Try partial match if exact match failed
        if not actual_voice:
            for k, v in voice_map.items():
                if voice_lower in k.lower():
                    actual_voice = v
                    break
    
    if not actual_voice:
        # Display clean names without prefixes
        available_voices = sorted([_clean_voice_name(v) for v in VOICE_LIST])
        return f"Voice '{voice_name}' not found. Available: {', '.join(available_voices)}"
    
    try:
        # Set the voice name in the TTS client
        if system.tts.set_voice(actual_voice):
            # Save to chat settings JSON (like modal does)
            if hasattr(system, 'llm_chat') and hasattr(system.llm_chat, 'session_manager'):
                system.llm_chat.session_manager.update_chat_settings({'voice': actual_voice})
                logger.info(f"Saved voice '{actual_voice}' to chat settings")
            
            system.tts.speak(f"Voice changed to {_clean_voice_name(actual_voice)}.")
            return f"Voice changed to '{_clean_voice_name(actual_voice)}'."
        return f"Failed to set voice to '{voice_name}'."
    except Exception as e:
        logger.error(f"Error changing voice: {e}")
        return f"Error changing voice: {str(e)}"