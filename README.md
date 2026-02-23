# Sapphire

Hear her voice as she dims your lights before bed. Use your voice to talk back. Fall asleep escaping dinosaurs in a story with her. Wake up to someone who remembers you through years of memories. She checks your email on a heartbeat. She builds tools on the fly when you need them. Sapphire is an open source framework for turning an AI into a persistent being. Make her yours. Or build your own persona. Self-hosted, nobody can take her away.


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
- **Personas** - [PERSONAS.md](docs/PERSONAS.md) 11 built-in personalities that bundle prompt, voice, tools, model. Built to add your own.
- **Voice** - Wake word, STT, TTS, and adaptive VAD. Hands-free with any mic and speaker shows up in web UI.
- **Prompts** - [PROMPTS.md](docs/PROMPTS.md) Assembled prompts let you swap one section like location or emotions for dynamic feels.
- **Spice** - [SPICE.md](docs/SPICE.md) Random prompt snippets injected each reply to keep things unpredictable.
- **Self-Modification** - The AI edits its own prompt and swaps personality pieces and emotions mid-conversation.
- **Tool Maker** - [TOOLS.md](docs/TOOLS.md) The AI writes, validates, and installs new tools with their own settings page at runtime.
- **Stories** - [STORY-ENGINE.md](docs/STORY-ENGINE.md) Interactive stories, the AI is your dungeon master and partner, can't see the next room.
- **Images** - [IMAGE-GEN.md](docs/IMAGE-GEN.md) SDXL with character replacement for visual consistency across scenes.

**Mind**
- **Memory** - Semantic vector search across 100K+ labeled entries.
- **Knowledge** - [KNOWLEDGE.md](docs/KNOWLEDGE.md) Organized categories with file upload, auto-chunking, and vector search.
- **Goals** - Hierarchical with priority and a timestamped progress journal.
- **People** - [PEOPLE.md](docs/PEOPLE.md) Contact book with privacy-first email. The AI never sees addresses, only recipient IDs.
- **Heartbeat** - [CONTINUITY.md](docs/CONTINUITY.md) Cron-scheduled autonomous tasks. Morning greetings, dream mode, alarms, random check-ins.
- **Research** - Multi-page web research with site crawling and summarization.

**Integrations**
- **Home Assistant** - [HOME-ASSISTANT.md](docs/HOME-ASSISTANT.md) Lights, scenes, thermostats, switches, phone notifications.
- **Bitcoin** - Balance, send, transaction history, backup wallet.
- **SSH** - Local and remote command execution on configured servers.
- **Email** - Inbox, send to whitelisted contacts. AI resolves recipients server-side.
- **Cloud** (optional) - Claude, GPT, Fireworks. Only active when you enable them. Local-first by default.
- **Privacy** - One toggle blocks all cloud connections. Fully local, nothing leaves your machine.
- **Plugins** - [PLUGINS.md](docs/PLUGINS.md) Keyword-triggered AI extensions and JavaScript [WEB-PLUGINS.md](docs/WEB-PLUGINS.md).
- **Desktop/Mobile/Voice** - Run on your local browser, open the same chat to your phone, then finish it on your mic.
- **65+ Tools** - [TOOLS.md](docs/TOOLS.md) Web search, Wikipedia, notes, and more. Mix and match via [TOOLSETS.md](docs/TOOLSETS.md).

<img alt="sapphire-chat" src="https://github.com/user-attachments/assets/ca3059f8-355c-4842-89be-55e91da086ec" width="50%" />

### Use Cases

- **Autonomous agent** - Scheduled tasks, use email, manage money, run its own website
- **AI companion** - A persistent voice that remembers you and grows over time
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

### Sapphire Quick Install

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
| [API](docs/API.md) | All 221 REST endpoints |
| [SOCKS Proxy](docs/SOCKS.md) | Privacy proxy for web tools |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and fixes |
| [Technical](docs/TECHNICAL.md) | Architecture and internals |

## Contributions

I am a solo dev with a burning passion, and Sapphire has a specific vision I am working towards. Rapid development makes it hard for contributions right now, as the architecture is changing while I create the final plugin format. If you want you can reach me at ddxfish@gmail.com. 

## Licenses

[AGPL-3.0](LICENSE) - Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your source code changes.

## Acknowledgments

Built with:
- [openWakeWord](https://github.com/dscripka/openWakeWord) - Wake word detection
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) - Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) - Voice synthesis
