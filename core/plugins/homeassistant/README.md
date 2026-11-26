# Home Assistant

Your house as one of Sapphire's devices. She switches and dims lights, sets the thermostat, runs scenes, reads doors and temperatures, and sends a notification to your phone, each thing by its name or by its room.

Since 2.0.0 this plugin has no tools of its own. It brings the **Home** device type to System > Devices, and she uses the house through the three device tools she already has.

## Setup

1. In Home Assistant: your profile, **Security**, **Long-lived access tokens**, **Create token**. Copy it at once; it is shown one time.
2. In Sapphire: System > Devices > **+ Add Device** > **Home**.
3. Enter the address (`http://homeassistant.local:8123`, or your server's IP with its port) and the token. **Add and test**.

A second house is a second device: add it under another name.

## The Things tab

Everything the house has, by room, each with what it is doing now.

| On each row | What it does |
|---|---|
| The tick | **Sapphire may use this**. Save keeps the ticks |
| The grey name | What she calls the thing (`pole-lamp`). It follows the thing's name in Home Assistant |
| Try | Runs an action yourself, ticked or not |

Locks, covers, alarms, cameras and phones start unticked. An unticked thing is still named to her, as locked, so she can tell you why she cannot use it.

Listed: lights, switches, the thermostat, scenes, scripts, covers, locks, fans, media players, cameras, alarm panels, phones to notify, and the sensors worth reading (temperature, humidity, light level, air quality, doors, windows, motion, who is home). Left out: power and energy meters, update switches, numbers, buttons, and anything hidden in Home Assistant.

## What you can say

- "Turn on the living room lights"
- "Dim the pole lamp to 40"
- "Make the office blue"
- "A bit brighter in the kitchen"
- "Set the thermostat to 72"
- "Run the evening scene"
- "Is the laundry door closed?"
- "Tell my phone dinner is ready"

A room means its lights. For "lights off at 23:00" with no AI in the loop, make a Device task under Triggers.

## Coming from 1.x

At the first start, once Home Assistant answers, the house you had set up becomes the device `home`. Your token and address carry over. What your blacklist blocked is unticked, and the phone you had picked for notifications is ticked. Nothing old is deleted.

- The thirteen `ha_*` tools are gone. A toolset of your own that listed them needs `device_list`, `device_status` and `device_action`. The built-in `smarthome` toolset already has them.
- If you had taken locks or covers off the blacklist, tick them again on the Things tab.

## When something is wrong

- **Offline.** The Status tab says why: the address, or a refused token.
- **"is not answering Home Assistant right now".** Home Assistant cannot reach that thing (unplugged, out of range). Nothing was changed.
- **A thing is missing or has an old name.** The list is asked again every two minutes, and at once after Save.

## Reference for AI

Home Assistant is a device (a hub), default name `home`. No ha_* tools.

- device_action(home) = the whole house: each kind with its actions, things by room with their state. One call, then act.
- A thing's or a room's name stands where the capability would: device_action(home, pole-lamp, set, 40). A room = its lights: device_action(home, living-room, off).
- light: on | off | toggle | set "<0-100|+N|-N> [color]" (color: a name, warm, cool, daylight, 2700k, #ff8800; alone or after a brightness)
- switch, fan: on | off | toggle (fan set <0-100>)
- climate: set <temp> (two numbers = low and high) | mode <heat|cool|off|...>
- scene, script: run
- cover: open | close | stop | set <0-100>. lock: lock | unlock. alarm: arm [home|away|night] | disarm [code]
- media: play | pause | stop | next | previous | volume <0-100>
- camera: look. sensor: read. notify: ping <words>
- device_action(home, <thing>) alone = its state and actions. device_action(home, <room>) = that room.
- "Locked" = the user has not ticked it (System > Devices > home > Things). Locks, covers, alarms, cameras and phones start locked.
- "not answering" = unreachable by Home Assistant itself; retrying does nothing. A timeout may still have happened: look before sending again.
- A wrong name answers with close names.
