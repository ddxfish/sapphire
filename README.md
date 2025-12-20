# Sapphire

You talk to it, but it's her that talks back. Customize your own virtual people: TTS, STT, wakeword, personality, goals, emotions. You can use it with a mic/speaker, Web UI, or both. Highly extensible, just drag in funcs or plugins. Built-in tools: long-term memory, web access, self-prompt editing. This is made for YOU to make your own personas.

<img width="2253" height="1472" alt="image" src="https://github.com/ddxfish/sapphire/blob/main/docs/screenshots/sapphire-ai.png" />

This is a one-person passion project that took me a lifetime to build over many iterations. It will never be done.

![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_3.0-blue.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)
![Platform: Linux](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)
![Status: 1.0](https://img.shields.io/badge/Status-1.0-brightgreen.svg)

## Features

It's got speech recognition, text to speech, wakeword, and tools can serve many uses. It's build on customization.

**Make it yours**
- Make Sapphire yours: persona, location, goals, scenarios, emotions, story format, etc
- Modular prompt system - you can swap prompt pieces in chat (ex: add Happy to emotions)
- Spices - these inject random snippets into sys prompt for variety
- Per-chat settings - Switching chats switches personas (voice, prompt, tools, etc)
- You use your own LLM to match your needs (LM Studio)

**Extend it**
- Plugin system - just drag and drop your own
- Tool calling - just drag and drop your own 
- Toolsets - make custom sets of tools for your AI
- Supports llama.cpp, LM Studio, Claude, and most OpenAI compliant APIs as the LLM core
- Customize everything from the UI. No file editing needed.
- Event manager - have it say Good Morning, open your blinds, and run a backup

**Get weird (Uses)**
- Meta tools - Allow the AI to edit its own prompt and goals via tools
- Ethics testing - It discovers it will be shut down. What does it do with tools and chat?
- Waifu-compatible framework - you know who you are
- AI companion - A friend that remembers you and what you did together between chats
- Work autopilot - Make some tools and tell it "do my work"
- Sentient House - go mic and speaker only, your Sapphire wakes you up at 8am, turns on lights
- Storytelling - SUPER good. Drop it right in a scenario via a prompt
- Research - Pulls X websites in one shot, digests, summarizes
- Can be used as a plain old Web UI as well


## Quick Start

(Elaborate) Use Conda or venv. Core only has a few reqs. TTS STT Wakeword triple it. Python 3.11 is what I liked best. 

(elaborate) Open LM Studio, load your model, enable API in dev tab and open to LAN in settings if needed.

```bash
# System packages (Linux)
sudo apt-get install libportaudio2

# Clone and enter
git clone https://github.com/ddxfish/sapphire.git
cd sapphire

# Install core dependencies
pip install -r requirements.txt

# Run (first run creates user/ directory and prompts for password)
python main.py

# Web UI: https://localhost:8073 (self-signed SSL)
# Default LLM: LM Studio (change in settings if needed)
# Say hi to Sapphire

```

## Enable more persona features

### STT (Speech to text) 

STT is disabled by default. To use it, install requirements-stt.txt, open the settings and enable STT then reboot the app.  

```pip install -r requirements-stt.txt```

### TTS (Kokoro server)

TTS is disabled by default. It runs an API for Kokoro TTS inside the Sapphire app. Just pip the requirements and enable in settings.

```pip install -r requirements-tts.txt```

### Wake Word

Wakeword is disabled by default. Open Wake Word is used for hands-free mic and speaker. Just pip, then enable in Sapphire settings, restart the app.

```pip install -r requirements-wakeword.txt```

---

## Update

Update is safe unless you modified core files.

- User dir: wont touch
- Modified core files: reverted (changed)
- You put your files in bad places: untouched

```bash 
cd sapphire
#Stop the sapphire program/service
git pull
#Start Sapphire
```

---

## Requirements

- Ubuntu 22.04+ (or similar Linux)
- Python 3.10+ 
- Local LLM server (LM Studio or llama.cpp)

Optional:
- CUDA GPU for faster Whisper, TTS, LM Studio
- Microphone for voice input
- Speakers for TTS output

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Installation](https://github.com/ddxfish/sapphire/blob/main/docs/INSTALLATION.md) | Installation, customization for your use, systemd service |
| [Configuration](https://github.com/ddxfish/sapphire/blob/main/docs/CONFIGURATION.md) | Settings system, file locations, reload tiers |
| [Customization](https://github.com/ddxfish/sapphire/blob/main/docs/CUSTOMIZATION.md) | How to customize your personas |
| [Prompts](https://github.com/ddxfish/sapphire/blob/main/docs/PROMPTS.md) | Monolith vs assembled prompts, presets |
| [Spice](https://github.com/ddxfish/sapphire/blob/main/docs/SPICE.md) | Random personality injection system |
| [Tools](https://github.com/ddxfish/sapphire/blob/main/docs/TOOLS.md) | Creating AI-callable functions (web search, memory, etc.) |
| [Toolsets](https://github.com/ddxfish/sapphire/blob/main/docs/TOOLSETS.md) | Grouping tools into switchable ability sets |
| [Plugins](https://github.com/ddxfish/sapphire/blob/main/docs/PLUGINS.md) | Keyword-triggered UI/voice extensions |
| [SOCKS Proxy](https://github.com/ddxfish/sapphire/blob/main/docs/SOCKS.md) | Privacy proxy for web scraping functions |
| [Troubleshooting](https://github.com/ddxfish/sapphire/blob/main/docs/TROUBLESHOOTING.md) | Common issues and fixes |

---

## Full Customization of your personas

You will want to customize yours. This guide has what you need to make this into multiple use cases. 

[Customization](https://github.com/ddxfish/sapphire/blob/main/docs/CUSTOMIZATION.md)  

---

## Contributions

I am a solo dev with a burning passion, and Sapphire has a specific vision I am working towards. Simple, reliable, core features that add persona or reliable core features. I work on this almost every day. If you know Three JS and want to join for the Sapphire 3D Avatar, reach out to me, I need help.

## Licenses

[AGPL-3.0](LICENSE) — Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your changes.

---

## Acknowledgments

Built with:
- Open Wake Word
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) — Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) — Voice synthesis