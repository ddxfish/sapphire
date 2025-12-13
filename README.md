# Sapphire

Sapphire is a Privacy-first virtual persona framework. Make your own AI assistant to help with your work, a storyteller with personality, or just a companion with long term memory. This is a persona framework for AI models. Can be used as web UI or as a pure STT/TTS interface. This connects to your LLM server to provide an immersive and customizable experience. 

<img width="2253" height="1472" alt="image" src="https://github.com/ddxfish/sapphire/blob/main/docs/screenshots/sapphire-ai.png" />

This is a one-person passion project. 

![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_3.0-blue.svg)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-green.svg)
![Platform: Linux](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)
![Status: 1.0](https://img.shields.io/badge/Status-1.0-brightgreen.svg)

## Features

**Talk to it**
- Wake word activation ("Hey Mycroft")
- Voice input via Faster Whisper (both web ui and mic)
- Voice output via Kokoro TTS (both web ui and mic)
- Spoken system commands ("system voice david")
- Web UI with multi-chat management and speech to text

**Make it yours**
- Modular prompt system — simple single prompts or swappable pieces
- Spices — dynamic snippets for variety in responses
- Per-chat settings (voice, prompt, toolset)
- Multiple personalities across different chats
- Connect to various LLMs based on your needs
- Long term memory functions that persist beyond each chat

**Extend it**
- Tool calling system — drop in new functions
- Plugin architecture for features
- Toolsets — group functions by context
- Supports any OpenAI-compatible LLM endpoint
- Edit prompts, toolsets, spices from UI
- Event manager - cron-like triggers for plugins and backups

**Get weird**
- Let the AI manage it's own prompt via meta tools
- Give the AI ability to reset and end the chat
- Do stories, add home assistant to change house lights to match story
- Test AI responses - Test in various ethical scenarios
- Personalize your AI to wake you up each morning, no more alarm clock


## Quick Start

```bash
# System packages
sudo apt-get install portaudio19-dev

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

The wakeword is used with a local mic if you want to use Sapphire without a keyboard. Requires [Mycroft Precise Engine](https://github.com/MycroftAI/mycroft-precise) to use this way. Install precise-engine, then enable Wakeword in Sapphire settings. 

```bash
# x86_64 Linux
curl -L https://github.com/MycroftAI/mycroft-precise/releases/download/v0.3.0/precise-all_0.3.0_x86_64.tar.gz -o /tmp/precise.tar.gz
sudo mkdir -p /usr/local/bin/precise-engine
sudo tar -xzf /tmp/precise.tar.gz -C /usr/local/bin/precise-engine --strip-components=1
sudo chmod +x /usr/local/bin/precise-engine/*
rm /tmp/precise.tar.gz
```

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
| [SOCKS](https://github.com/ddxfish/sapphire/blob/main/docs/SOCKS.md) | Use a SOCKS proxy for web requests (tools) |
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

**What can I use this for?**

Sapphire is a virtual persona framework. It excels at things like storytelling, voice assistant tasks and testing AI model behavior in various scenarios. You can customize the experience, add more tools or plugins. It turns an LLM into an immersive persona. 

---

## To do 

- Image generation API script - already integrated into UI, publish server
- Sapphire 3D - three js 3d model of Sapphire avatar - alpha test, no core support etc
- Core state machine support - store a history.json state for story (ship power 50% etc)
- Windows support - compat layer, or maybe switch to OWW and use dual OS function paths
- Home assistant - tool calling, UX was poor, need rework of this


---

## Contributions

I am a solo dev, and Sapphire has a specific vision I am working towards, so I am likely to reject new features unless they are simple and reliable. I am implementing core features to allow new things in the app like for the LLM to control a 3d avatar, core functionality for state machines to track story elements, etc. 

## Licenses

[AGPL-3.0](LICENSE) — Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your changes.

---

## Acknowledgments

Built with:
- [Mycroft Precise](https://github.com/MycroftAI/mycroft-precise) — Wake word detection
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) — Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) — Voice synthesis