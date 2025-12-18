# Sapphire

Sapphire is a privacy-first virtual persona framework. This connects to any LLM to make what seems like a hearing and talking person with long term memory. You can use it with a mic/speaker, Web UI, or both. This is production code under active development with support. If the other virtual assistants or AI companions weren't customizable or modular enough, try Sapphire.

<img width="2253" height="1472" alt="image" src="https://github.com/ddxfish/sapphire/blob/main/docs/screenshots/sapphire-ai.png" />

This is a one-person passion project that took me a lifetime to build.

![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_3.0-blue.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)
![Platform: Linux](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)
![Status: 1.0](https://img.shields.io/badge/Status-1.0-brightgreen.svg)

## Features

**Talk to it**
- Wake word activation ("Hey Sapphire")
- Voice input on mic directly or Web UI
- Voice output via Kokoro TTS on speaker or web UI
- Spoken system commands ("system voice isabella")
- You can also use STT and TTS in the Web UI
- Or don't! STT/TTS/Wakeword is all optional

**Make it yours**
- Sapphire is my example, you should make your own persona for yourself
- Modular prompt system - Swap them in and out, edit easily via Web UI
- Spices - add snippets to system prompt that rotate for variety
- Per-chat settings - Switch chats to switch personas (voice, prompt, tools, etc)
- You use your own LLM to match your needs
- Ask it to remember things about you

**Extend it**
- Plugin system - just drag and drop your own
- Tool calling - just drag and drop your own 
- Toolsets - make custom sets of tools for your AI
- Supports llama.cpp, LM Studio, Claude, and most OpenAI compliant APIs
- Customize from the UI. No file editing needed.
- Event manager - have it say Good Morning, open your blinds, and run a backup

**Get weird**
- Meta tools - Allow the AI to edit its own prompt (meta toolset)
- AI exit - Allow the AI to end and reset chat (erase that chat!)
- Interactive story - AI turns YOUR house fans on as the choppers swarm overhead (home assistant in alpha test)
- Ethics testing - It discovers it will be shut down. What does it do with tools and chat?
- Waifu-compatible framework - you know who you are
- AI companion - A friend that remembers you and what you did together between chats
- Work autopilot - Make some tools and tell it "do my work"

## Quick Start

```bash
# System packages (Linux)
sudo apt-get install libportaudio2

# Clone and enter
git clone https://github.com/ddxfish/sapphire.git
cd sapphire

# Install dependencies
pip install -r requirements.txt

# Run (first run creates user/ directory and prompts for password)
python main.py

# Access web UI 
# https://localhost:8073
# Click the gear icon > App Settings > then customize your LLM info

# Open LM Studio or llama.cpp so the Sapphire app can use your LLM.

```

## Enable features

### STT (Speech to text) 

STT is disabled by default. To use it, install requirements-stt.txt, open the settings and enable STT then reboot the app.  

```pip install -r requirements-stt.txt```

### TTS (Kokoro server)

TTS is disabled by default. It runs an API for Kokoro TTS inside the Sapphire app. Just pip the requirements and enable in settings.

```pip install -r requirements-tts.txt```

### Wake Word

The wakeword is used with a local mic if you want to use Sapphire without a keyboard. This now uses OpenWakeWord, which is cross platform and open source. It requires ONNX runtime which is already in the main requirements.txt. Just checkmark it in settings to enable, save, and restart Sapphire. You need speech to text and text to speech to use this.

---

## Requirements

- Ubuntu 22.04+ (or similar Linux)
- Python 3.10+ 
- Local LLM server (LM Studio or llama.cpp)

Optional:
- CUDA GPU for faster Whisper
- Microphone for voice input
- Speakers for TTS output

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Installation](https://github.com/ddxfish/sapphire/blob/main/docs/INSTALLATION.md) | Installation, customization for your use, systemd service |
| [Configuration](https://github.com/ddxfish/sapphire/blob/main/docs/CONFIGURATION.md) | Settings system, file locations, reload tiers |
| [Prompts](https://github.com/ddxfish/sapphire/blob/main/docs/PROMPTS.md) | Monolith vs assembled prompts, presets |
| [Spice](https://github.com/ddxfish/sapphire/blob/main/docs/SPICE.md) | Random personality injection system |
| [Tools](https://github.com/ddxfish/sapphire/blob/main/docs/TOOLS.md) | Creating AI-callable functions (web search, memory, etc.) |
| [Toolsets](https://github.com/ddxfish/sapphire/blob/main/docs/TOOLSETS.md) | Grouping tools into switchable ability sets |
| [Plugins](https://github.com/ddxfish/sapphire/blob/main/docs/PLUGINS.md) | Keyword-triggered UI/voice extensions |
| [SOCKS Proxy](https://github.com/ddxfish/sapphire/blob/main/docs/SOCKS.md) | Privacy proxy for web scraping functions |
| [Troubleshooting](https://github.com/ddxfish/sapphire/blob/main/docs/TROUBLESHOOTING.md) | Common issues and fixes |

---

## FAQ

**Is this private?**

Yes! Sapphire is private by design. All local for your chats, isolated to the user/ dir in Sapphire. Wake word, speech recognition, and LLM inference run on your machine. No telemetry, no analytics, no external calls unless you explicitly configure a cloud LLM fallback. It downloads Whisper models and Mycroft models if you enable those, but only on next run. For web scraping privacy, configure a SOCKS5 proxy — see [SOCKS.md](docs/SOCKS.md).

**Are my settings safe?**

I will protect prompt files heavily, keeping the format as-is long-term. The main settings file is seeing heavy development, it will change a lot. 

---

## To do 

- Image generation API script - already integrated into UI, publish server
- Sapphire 3D - three js 3d model of Sapphire avatar - alpha test, no core support etc
- Core state machine support - store a history.json state for story (ship power 50% etc)
- Windows support - compat layer, or maybe switch to OWW and use dual OS function paths
- Home assistant - tool calling, UX was poor, need rework of this
- (done) Switch to Open Wake Word

---

## Contributions

I am a solo dev, and Sapphire has a specific vision I am working towards, so I am likely to reject new features unless they are simple and reliable. I am implementing core features to allow new things in the app like for the LLM to control a 3d avatar, core functionality for state machines to track story elements, etc. 

## Licenses

[AGPL-3.0](LICENSE) — Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your changes.

---

## Acknowledgments

Built with:
- Open Wake Word
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) — Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) — Voice synthesis