# functions/devices.py - the three device tools (tmp/device-manager-plan.md)
"""
Thin doors into core/devices/engine.py. The schemas never change as devices
come and go: a device is a string.

She cannot add, change, or remove a device or a premade command. Only the
Settings > Devices page can.
"""
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '\U0001f39b\ufe0f'
AVAILABLE_FUNCTIONS = ['device_list', 'device_status', 'device_action']

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "device_list",
            "description": ("List your devices: name, online or offline, and what each can do. "
                            "The user adds devices in Settings > Devices."),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "device_status",
            "description": "Check one device right now: online or offline, readings, and what it can do.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {"type": "string", "description": "Device name from device_list"}
                },
                "required": ["device"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "device_action",
            "description": (
                "Use a device. It works like a command with built-in help. "
                "device_action(device) lists what it can do. "
                "device_action(device, capability) lists the actions, one example each. "
                "device_action(device, capability, action, value) runs one. "
                "value is optional; the help shows its format."),
            "parameters": {
                "type": "object",
                "properties": {
                    "device": {"type": "string", "description": "Device name from device_list"},
                    "capability": {"type": "string", "description": "What to use on it, e.g. ssh. Omit for the list."},
                    "action": {"type": "string", "description": "What to do. Omit for the list."},
                    "value": {"type": "string", "description": "Optional input for the action"}
                },
                "required": ["device"]
            }
        }
    },
]


def execute(function_name, arguments, config):
    a = arguments or {}
    try:
        from core.devices import engine          # on first use, never at boot
        why = engine.refusal()
        if why:
            return why, False
        if function_name == 'device_list':
            return engine.list_text()
        if function_name == 'device_status':
            if not str(a.get('device') or '').strip():
                return engine.list_text()
            return engine.status_text(a.get('device'))
        if function_name == 'device_action':
            return engine.run(a.get('device'), a.get('capability'), a.get('action'), a.get('value'))
        return f"Unknown device tool '{function_name}'.", False
    except Exception as e:
        logger.error(f"[DEVICES] {function_name} failed: {e}", exc_info=True)
        return f"{function_name} failed: {type(e).__name__}", False
