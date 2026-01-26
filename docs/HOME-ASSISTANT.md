# Home Assistant

Control your smart home through Sapphire. Lights, scenes, thermostats, switches, and phone notifications—all by talking to your AI.

<img src="screenshots/home-assistant-integration.png" alt="Home Assistant integration">

## Setup

1. **Get your Home Assistant URL** — Usually `http://homeassistant.local:8123` or your server's IP
2. **Create a Long-Lived Access Token:**
   - In Home Assistant, click your profile (bottom left)
   - Scroll to "Long-Lived Access Tokens"
   - Click "Create Token", give it a name like "Sapphire"
   - Copy the token immediately (you won't see it again)
3. **Configure in Sapphire:**
   - Open Settings → Plugins → Home Assistant
   - Paste URL and token
   - Click "Test" to verify connection

## Available Tools

The AI can call these automatically when you ask for smart home control:

| Tool | What it does |
|------|--------------|
| `ha_list_scenes_and_scripts` | List available scenes and scripts |
| `ha_activate` | Run a scene or script by name |
| `ha_list_areas` | List all rooms/areas |
| `ha_area_light` | Set brightness for all lights in an area (0-100) |
| `ha_area_color` | Set RGB color for all lights in an area |
| `ha_get_thermostat` | Get current temperature |
| `ha_set_thermostat` | Set target temperature |
| `ha_list_lights_and_switches` | List all controllable devices |
| `ha_set_light` | Control a specific light (brightness, color) |
| `ha_set_switch` | Turn a switch on or off |
| `ha_notify` | Send notification to your phone |
| `ha_house_status` | Get snapshot of home (presence, climate, sensors) |

## Phone Notifications

To send notifications to your phone:

1. **Find your notify service name:**
   - In Home Assistant, go to Developer Tools → Actions
   - Search for "notify"
   - Find your phone (looks like `notify.mobile_app_your_phone_name`)
   - The service name is the part after `notify.` (e.g., `mobile_app_pixel_7`)

2. **Configure in Sapphire:**
   - Settings → Plugins → Home Assistant
   - Enter the service name in "Mobile App Notify Service"
   - Click "Test" to send a test notification

Now the AI can send you alerts: "Hey, remind me to check the oven in 20 minutes" → notification on your phone.

## Blacklist

Block devices you don't want the AI to control. One pattern per line:

| Pattern | What it blocks |
|---------|---------------|
| `switch.computer1` | Exact entity ID |
| `cover.*` | All covers (garage doors, blinds) |
| `lock.*` | All locks |
| `area:Server Room` | Everything in that area |

Good candidates for blacklisting: locks, garage doors, security systems, anything dangerous.

## Example Commands

- "Turn on the living room lights"
- "Set bedroom lights to 50%"
- "Make the office lights blue"
- "What's the temperature?"
- "Set thermostat to 72"
- "Run the movie night scene"
- "Send a notification to my phone saying dinner is ready"
- "What's the status of the house?"
- "Turn off all the lights in the kitchen"

## Toolset Setup

Home Assistant tools aren't enabled by default. To use them:

1. Create or edit a toolset that includes HA functions
2. Or add them to your default toolset via the Toolset Manager
3. Make sure the chat/persona you're using has that toolset selected

## Reference for AI

Home Assistant integration for smart home control.

SETUP:
- Settings → Plugins → Home Assistant
- Enter HA URL and Long-Lived Access Token
- Test connection before saving

AVAILABLE TOOLS:
- ha_list_scenes_and_scripts() - list scenes/scripts
- ha_activate(name) - run scene or script
- ha_list_areas() - list rooms
- ha_area_light(area, brightness) - set area brightness 0-100
- ha_area_color(area, r, g, b) - set area RGB color
- ha_get_thermostat() - get current temp
- ha_set_thermostat(temp) - set target temp
- ha_list_lights_and_switches() - list devices
- ha_set_light(name, brightness, r?, g?, b?) - control light
- ha_set_switch(name, state) - on/off
- ha_notify(message, title?) - send phone notification
- ha_house_status() - get home snapshot

NOTIFICATIONS:
- Requires notify_service configured (e.g., "mobile_app_pixel_7")
- Find service in HA: Developer Tools → Actions → search "notify"

BLACKLIST:
- Exact: "switch.server_pdu"
- Domain: "lock.*", "cover.*"
- Area: "area:Garage"

TROUBLESHOOTING:
- Connection failed: check URL includes port, token is valid
- Entity not found: check spelling, check blacklist
- Tools not available: add HA tools to active toolset