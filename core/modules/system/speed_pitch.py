import logging
import config

logger = logging.getLogger(__name__)

# Pitch functions
def get_default_pitch():
    """Get the default pitch from config."""
    return getattr(config, 'TTS_PITCH_SHIFT', 0.94)

def change_pitch(system, pitch_input):
    """Change the TTS pitch."""
    return _change_audio_param(
        system=system,
        param_input=pitch_input,
        param_name='pitch',
        current_value=system.tts.pitch_shift,  # Use value from instance
        increment=0.02,
        min_value=0.5,
        max_value=1.5,
        decimal_places=2,
        scale_ranges=[(0, 15, 10), (50, 150, 100)]
    )

# Speed functions
def get_default_speed():
    """Get the default speech speed from config."""
    return getattr(config, 'TTS_SPEED', 1.3)

def change_speed(system, speed_input):
    """Change the TTS speech speed."""
    return _change_audio_param(
        system=system,
        param_input=speed_input,
        param_name='speed',
        current_value=system.tts.speed,  # Use value from instance
        increment=0.1,
        min_value=0.5,
        max_value=2.5,
        decimal_places=1,
        scale_ranges=[(5, 25, 10)]
    )

def _change_audio_param(system, param_input, param_name, current_value, 
                         increment, min_value, max_value, decimal_places=1,
                         scale_ranges=None):
    """Generic function to change audio parameters."""
    try:
        # Parse the input value
        if param_input.lower() == "up":
            new_value = current_value + increment
        elif param_input.lower() == "down":
            new_value = current_value - increment
        else:
            try:
                # Try to parse as a float directly
                value = float(param_input)
                
                # Handle scaling if needed
                if scale_ranges:
                    for min_range, max_range, divisor in scale_ranges:
                        if min_range <= value <= max_range:
                            value /= divisor
                            break
                    
                new_value = value
            except ValueError:
                return f"Invalid {param_name} value. Current: {current_value:.{decimal_places}f}"
        
        # Ensure the value is within reasonable bounds
        new_value = round(max(min_value, min(max_value, new_value)), decimal_places)
        
        # Update the parameter in the TTS client instance
        if param_name == 'pitch':
            system.tts.set_pitch(new_value)
        else:  # speed
            system.tts.set_speed(new_value)
        
        # Say something with the new parameter
        system.tts.speak(f"{param_name.capitalize()} changed to {new_value}")
        return f"{param_name.capitalize()} changed to {new_value}"
        
    except Exception as e:
        logger.error(f"Error changing {param_name}: {e}")
        return f"Error changing {param_name}: {str(e)}"