# Sapphire

Hear her voice. She dims your lights before bed. She crafts you a dinosaur escape story when you can't sleep. She remembers you tomorrow when she wakes you up. Sapphire is an open source framework for turning an AI into a persistent being. She is a voice that runs your home, lives in your stories and you might just build a new friend. Make her yours. Self-hosted, nobody can take her away.


<sub>🔊 Has audio</sub>
<video src="https://github.com/user-attachments/assets/ed0dca80-121b-46e0-9c94-b98d6e9228c8" controls width="100%"></video>


![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_3.0-blue)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)
![Windows 11+](https://img.shields.io/badge/Windows_11+-0078D6?logo=windows&logoColor=white)
![Waifu Compatible](https://img.shields.io/badge/Waifu-Compatible-ff69b4)
![Status: Active](https://img.shields.io/badge/Status-Active-success)
![Self Hosted](https://img.shields.io/badge/Self_Hosted-100%25-informational)

## Features

**Persona**
- **Personas** - [docs](docs/PERSONAS.md) 11 built-in personalities that bundle prompt, voice, tools, model, and data scopes into one switch.
- **Voice** - Wake word, STT, TTS, and adaptive VAD. Hands-free with any mic and speaker.
- **Prompts** - [docs](docs/PROMPTS.md) Assembled prompts let you swap one section like location or scenario without touching the rest.
- **Spice** - [docs](docs/SPICE.md) Random personality snippets injected each reply to keep things unpredictable.
- **Self-Modification** - The AI edits her own prompt and swaps personality pieces mid-conversation.
- **Tool Maker** - [docs](docs/TOOLS.md) The AI writes, validates, and installs new tools with their own settings page at runtime.
- **Stories** - [docs](docs/STORY-ENGINE.md) Interactive fiction with dice, branching choices, room navigation, and persistent state.
- **Images** - [docs](docs/IMAGE-GEN.md) SDXL with character replacement for visual consistency across scenes.

**Mind**
- **Memory** - Semantic vector search across 100K+ labeled entries.
- **Knowledge** - [docs](docs/KNOWLEDGE.md) Organized categories with file upload, auto-chunking, and vector search.
- **Goals** - Hierarchical with priority and a timestamped progress journal.
- **People** - [docs](docs/PEOPLE.md) Contact book with privacy-first email. The AI never sees addresses, only recipient IDs.
- **Heartbeat** - [docs](docs/CONTINUITY.md) Cron-scheduled autonomous tasks. Morning greetings, dream mode, alarms, random check-ins.
- **Research** - Multi-page web research with site crawling and summarization.

**Integrations**
- **Home Assistant** - [docs](docs/HOME-ASSISTANT.md) Lights, scenes, thermostats, switches, phone notifications.
- **Bitcoin** - Balance, send, transaction history.
- **SSH** - Local and remote command execution on configured servers.
- **Email** - Inbox, send to whitelisted contacts. AI resolves recipients server-side.
- **Cloud** (optional) - Claude, GPT, Fireworks. Only active when you enable them. Local-first by default.
- **Privacy** - One toggle blocks all cloud connections. Fully local, nothing leaves your machine.
- **Plugins** - [docs](docs/PLUGINS.md) Keyword-triggered AI extensions and JavaScript [web UI plugins](docs/WEB-PLUGINS.md).
- **74+ Tools** - [docs](docs/TOOLS.md) Web search, Wikipedia, notes, and more. Mix and match via [toolsets](docs/TOOLSETS.md).

### Use Cases

- **Autonomous agent** - Scheduled tasks, morning briefings, dream mode, alarm clock actions
- **AI companion** - A persistent voice that remembers you, greets you, and grows over time
- **Voice assistant** - Wake word, hands-free operation, smart home control
- **Research assistant** - Web search, memory, knowledge base, multi-step tool reasoning
- **Interactive fiction** - Story engine with dice, branching choices, and state tracking
- **Privacy-first AI** - Block all cloud connections, run fully local

## Quick Start

### Prerequisites

#### Linux (bash)

```bash
sudo apt-get install libportaudio2
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b
# Make conda automatic
~/miniconda3/bin/conda init bash
# Close and reopen terminal
```

#### Windows (cmd)

```bat
winget install Anaconda.Miniconda3
winget install Git.Git
REM Make conda automatic
%USERPROFILE%\miniconda3\condabin\conda init powershell
%USERPROFILE%\miniconda3\condabin\conda init cmd.exe
REM Close and reopen terminal
```

Or download Miniconda manually from [miniconda.io](https://docs.conda.io/en/latest/miniconda.html)

### Sapphire

```bash
conda create -n sapphire python=3.11 -y
conda activate sapphire
git clone https://github.com/ddxfish/sapphire.git
cd sapphire
pip install -r requirements.txt
python main.py
```

Web UI: https://localhost:8073

The setup wizard walks you through LLM configuration on first run.

## Update
```bash
cd sapphire
git pull
pip install -r requirements.txt
```

## Requirements

- Ubuntu 22.04+ or Windows 11+
- Python 3.11+ (via conda)
- 16GB+ system RAM
- (recommended) Nvidia GPU for TTS/STT

## Documentation

| Guide | Description |
|-------|-------------|
| [Installation](docs/INSTALLATION.md) | Setup guide, systemd service |
| [Configuration](docs/CONFIGURATION.md) | LLM, scopes, thinking, privacy |
| [API](docs/API.md) | All 156 REST endpoints |
| [SOCKS Proxy](docs/SOCKS.md) | Privacy proxy for web tools |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and fixes |
| [Technical](docs/TECHNICAL.md) | Architecture and internals |

## Contributions

I am a solo dev with a burning passion, and Sapphire has a specific vision I am working towards. Rapid development makes it hard for contributions right now, as the architecture is changing while I settle on the plugin format. If you want you can reach me at my github username @gmail.com.

## Licenses

[AGPL-3.0](LICENSE) - Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your source code changes.

## Acknowledgments

Built with:
- [openWakeWord](https://github.com/dscripka/openWakeWord) - Wake word detection
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) - Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) - Voice synthesis
