# functions/devices.py - the three device tools (tmp/device-manager-plan.md)
"""
Thin doors into core/devices/engine.py.

The descriptions carry the fleet (get_tools): each device's name, where it
is, whether it is online, and what it can do; with ten devices or fewer the
device argument is an enum of their names. The engine asks for a rebuild
(engine.retell) when a device is added, changed or removed, when a driver
registers, and when one goes online or offline. So she can go from a wish to
device_action(device) in one call, and that answer holds everything she
needs for the second: status, every action, the value each takes.

She cannot add, change, or remove a device or a premade command. Only the
Settings > Devices page can.
"""
import json
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
                "Use a device. device_action(device) shows how it is and everything it can do, "
                "with the value each action takes. "
                "device_action(device, capability, action, value) runs one. value is optional."),
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


FLEET_MAX = 10          # devices named in the description before "and N more"


def _fleet_line(fleet):
    """One device per clause: name, place, online or offline, what it can do."""
    said = []
    for d in fleet[:FLEET_MAX]:
        if d.get('every'):
            said.append(f"{d['id']}: speaker say on every device that can speak, at once")
            continue
        where = f" ({d['location']})" if d['location'] else ''
        caps = ', '.join(d['caps']) or 'nothing yet'
        said.append(f"{d['id']}{where} {'online' if d['online'] else 'offline'}: {caps}")
    more = f" · and {len(fleet) - FLEET_MAX} more: device_list" if len(fleet) > FLEET_MAX else ''
    return ' · '.join(said) + more


def get_tools():
    """The schemas, with the fleet in them. Falls back to the plain TOOLS on
    an install without devices, or before the engine can answer."""
    tools = json.loads(json.dumps(TOOLS))
    try:
        from core.devices import engine
        if engine.refusal():
            return tools
        fleet = engine.fleet()
    except Exception as e:
        logger.warning(f"[DEVICES] tool descriptions built without the fleet: {e}")
        return tools
    by_name = {t['function']['name']: t['function'] for t in tools}
    if fleet:
        by_name['device_action']['description'] += " Devices now: " + _fleet_line(fleet) + "."
    else:
        by_name['device_action']['description'] += " No devices yet: the user adds them in Settings > Devices."
    names = [d['id'] for d in fleet]
    for tool in ('device_status', 'device_action'):
        device = by_name[tool]['parameters']['properties']['device']
        if 0 < len(names) <= engine.FLEET_ENUM:
            device['enum'] = names
            device['description'] = 'Which device'
        else:
            device['description'] = 'Device name' + (f", one of: {', '.join(names[:FLEET_MAX])}, ..." if names else '')
    return tools


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
