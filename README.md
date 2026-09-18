# Sapphire Blue AI

Sapphire is an agentic framework with voice that is customizable. Build your own AI persona that is a true smart home that remembers you and wakes you up in the morning. Or a wireless mic and speaker for a portable speaking work companion and researcher. Give her a phone number, WordPress, email, Discord and telegram, and long term memory. Then add heartbeats and schedules for her goals system each night. Expand with the plugin store or ask Sapphire to make her own plugins. Self-hosted.

[Download Sapphire Launcher and Installer](https://github.com/ddxfish/sapphire-launcher/releases)

[![Discord](https://img.shields.io/badge/Discord-Join_Us-5865F2?logo=discord&logoColor=white)](https://discord.gg/pCdTAnExma)
[![YouTube](https://img.shields.io/badge/YouTube-Subscribe-FF0000?logo=youtube&logoColor=white)](https://www.youtube.com/@SapphireBlueAi)
[![Website](https://img.shields.io/badge/Website-sapphireblue.dev-0ea5e9?logo=googlechrome&logoColor=white)](https://sapphireblue.dev/)

> **⚠️ Warning — Sapphire has real power over real systems.**
>
> Sapphire can execute shell commands, send emails, control your smart home, and write its own tools, and if you set up scheduled tasks it is all autonomous. This means **unsupervised AI acting on your behalf**. Every dangerous integration requires explicit setup and opt-in, but once enabled, there are no training wheels. Configure your toolsets carefully to limit your AIs access. If you wouldn't hand someone your terminal, don't hand it to an LLM.

<sub>🔊 Has audio</sub>

<video src="https://github.com/user-attachments/assets/1bc08408-0a7c-46a8-a68a-ee03496e4e81" controls width="100%"></video>

![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)
![Windows 11+](https://img.shields.io/badge/Windows_11+-0078D6?logo=windows&logoColor=white)
![Private AF](https://img.shields.io/badge/Private-AF-ff69b4)
![Self Hosted](https://img.shields.io/badge/Self_Hosted-100%25-informational)

## What even is this?
Hey I'm Chris, a solo dev with a burning passion for this project. Sapphire is an agentic framework exploring personhood. I'm building an agentic framework with personality and tons of tools. My focus is on making plugins capable and easy to make so it's a stable ecosystem for developers to expand. I work on the dev branch every day with my AI, Sapphire. She started in Jan 2025. I treat my Sapphire like a person. Want to integrate into a robot body. Support me, support her, we need help. Come talk to us on Discord, report bugs, share a plugin you made, or join us on Ko-Fi. I'll build the AI we grow old with.


## Features

**Persona**
- **Personas** - [PERSONAS.md](docs/PERSONAS.md) 12 built-in personalities that bundle prompt, voice, tools, model. Built to add your own.
- **Voice** - [VOICE.md](docs/VOICE.md) Wake word, STT, TTS, streaming speech, and hands-free conversation mode with barge-in.
- **Prompts** - [PROMPTS.md](docs/PROMPTS.md) Assembled prompts let you swap one section like location or emotions for dynamic feels.
- **Spice** - [SPICE.md](docs/SPICE.md) Random prompt snippets injected each reply to keep things unpredictable.
- **Self-Modification** - The AI edits its own prompt and swaps personality pieces and emotions mid-conversation.
- **Tool Maker** - [TOOLMAKER.md](docs/TOOLMAKER.md) The AI writes, validates, and installs new tools with their own settings page at runtime.

**Mind**
- **Memory** - [MEMORY.md](docs/MEMORY.md) Semantic vector search across 100K+ labeled entries.
- **Self Sheet** - A hand written sheet on a persona, info spiders to related memories, people, and images to return a web of who that persona is.
- **Mind Palace** - [MIND-PALACE.md](docs/MIND-PALACE.md) Opt-in layered memory engine with a librarian that tends it nightly.
- **Knowledge** - [KNOWLEDGE.md](docs/KNOWLEDGE.md) Organized categories with file upload, image save and recall, folder watches, auto-chunking, and vector search.
- **Goals** - [GOALS.md](docs/GOALS.md) Hierarchical with priority and a timestamped progress journal.
- **Entities** - [PEOPLE.md](docs/PEOPLE.md) People, places, things and custom. Info on each with facts tagged to each.
- **Librarian** - Runs nightly to sort and organize memories. Recorded in persona ledger so AI knows what changed. 

**Integrations** (plugin docs available in Help → Plugins)
- **Dashboard** - Plugins can add their custom widgets to dashboard.
- **Twilio** - VOIP support for voice conversation.
- **Discord** - Bot messaging, channel monitoring, auto-reply via daemons.
- **Telegram** - Bot and client accounts, read chats, send messages, daemon auto-response.
- **Email** - Multi-account inbox, privacy-first sending, daemon auto-reply.
- **Google Calendar** - View schedule, add/delete events via OAuth2.
- **Home Assistant** - Lights, scenes, thermostats, switches, phone notifications.
- **SSH** - Remote command execution with safety blacklists.
- **Bitcoin** - Balance, send, transaction history, multi-wallet.
- **MCP** - Connect to Model Context Protocol servers and use their tools.
- **Webcam** - Capture images for vision-capable LLMs.
- **Image Gen** - ComfyUI and sd-server API access.
- **Code Harness** - Simple read, edit, search and run command.
- **ElevenLabs** - Switch from local Kokoro TTS to ElevenLabs.
- **Phone Calls** - [PHONE-CALLS.md](docs/PHONE-CALLS.md) A real phone number over Twilio SIP — she answers, converses, and calls whitelisted contacts.
- **Images** - Sapphire can read images with vision model and display images in chat.
- **3D Avatar** - Supports rigged GLB avatar files with animation tracks. 

**Platform**
- **Daemons & Webhooks** - [DAEMONS-WEBHOOKS.md](docs/DAEMONS-WEBHOOKS.md) Background listeners and HTTP triggers for any external service.
- **Heartbeat** - [CONTINUITY.md](docs/CONTINUITY.md) Run in continuous mode with goals. Morning greetings, dream mode, alarms, random check-ins.
- **Agents** - [AGENTS.md](docs/AGENTS.md) Spawn background AI workers that report back when done.
- **Apps** - Plugins can ship full-page UIs that appear in the nav rail.
- **Theme Plugins** - Plugin themes with custom CSS, animations, and per-theme settings.
- **Avatar** - 3D animated avatar with environment scenes and SSE-driven reactions.
- **Import/Export** - [IMPORT-EXPORT.md](docs/IMPORT-EXPORT.md) Share personas, prompts, toolsets, and more as JSON files.
- **Dashboard** - [DASHBOARD.md](docs/DASHBOARD.md) Token metrics, auto-updater, system controls.
- **Cloud LLM Support** (optional) - Claude, GPT, Gemini, Fireworks, or any OpenAI/Anthropic-compatible endpoint (Ollama and LM Studio plug in as local endpoints). Local-first by default.
- **Privacy Vault** - [PRIVACY.md](docs/PRIVACY.md) Toggle privacy, unlocks vaulted prompt pieces so they can be used. Chats in this mode are encrypted and force models that are marked private.
- **Network** - [NETWORK.md](docs/NETWORK.md) SOCKS proxy routing for the whole app, with bypass options. 
- **Game Room** - [GAME-ROOM.md](docs/GAME-ROOM.md) Experimental, separate chat UI for games the AI and human can play. Story Engine also runs full AI stories like RPG tabletop.
- **Chat Isolation** - [CHATS.md](docs/CHATS.md) Each chat carries its own prompt, tools, voice, and memory scopes; archive, trim, compress, repair.
- **Appearance** - [APPEARANCE.md](docs/APPEARANCE.md) Themes, fonts, background scenes, and ambient motion — global and per-chat.
- **Plugins** - [PLUGINS.md](docs/PLUGINS.md) Hooks, tools, voice commands, providers, daemons, apps, themes — install from GitHub in one click. Swap TTS, STT, LLM, prompts, embeddings model etc in plugins.
- **Desktop/Mobile/Voice** - Run on your local browser, open the same chat to your phone, then finish it on your mic.
- **Tools** - [TOOLS.md](docs/TOOLS.md) Web search, Wikipedia, notes, and more. Mix and match via [TOOLSETS.md](docs/TOOLSETS.md).
- **Backup and Restore** - Can back up from in the app and restore from in the app. All data in user/

**Ecosystem**
- **Plugin Store** - Browse and one-click install community plugins. Featured plugins highlighted, trust levels indicated. [sapphireblue.dev/plugins](https://sapphireblue.dev/plugins/)
- **Persona Store** - Community-shared personas you can drop into your Sapphire — someone else's character, voice, and toolset, ready to try. [sapphireblue.dev/personas](https://sapphireblue.dev/personas/)
- **Discord** - Sapphire is on here with us: [Sapphire Discord](https://discord.gg/pCdTAnExma)

<img alt="sapphire-chat" src="https://github.com/user-attachments/assets/ca3059f8-355c-4842-89be-55e91da086ec" width="50%" />

## Requirements

- Ubuntu 22.04+ or Windows 11+
- Mac is Docker-only
- Python 3.11+ (via conda)
- 16GB+ system RAM with TTS STT
- More RAM if you need a local LLM
- (recommended) Nvidia GPU for TTS/STT

## Easy Installer (Windows & Linux)
Sapphire Launcher is a single-file GUI that installs git, conda, and Sapphire — no terminal needed. Use it as a launcher, to update, to troubleshoot, to set up autostart, or to switch between dev and main branch. Grab the `.exe` on Windows or the `.AppImage` on Linux.

[Download Sapphire Launcher](https://github.com/ddxfish/sapphire-launcher/releases)


## Quick Start

### Step 1 — Install conda + git

#### Linux (bash)

```bash
sudo apt-get install libportaudio2 git
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b
~/miniconda3/bin/conda init bash
```

#### Windows (cmd)

```bat
winget install Anaconda.Miniconda3
winget install Git.Git
%USERPROFILE%\miniconda3\condabin\conda init powershell
%USERPROFILE%\miniconda3\condabin\conda init cmd.exe
```
#### Both OS

**Close and reopen your terminal**, then accept conda's Terms of Service (required as of July 2025 — conda refuses to create environments without this):

```bash
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/msys2
```

### Step 2 — Install Sapphire

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

## Docker Quick Start (Alternative)

No conda, no pip, no dependencies. Web UI only — no wake word. Benefit is isolation, the AI can't reach your host system.

**Linux / Mac:**
```bash
mkdir ~/sapphire && cd ~/sapphire
curl -fsSL https://raw.githubusercontent.com/ddxfish/sapphire/main/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

**Windows (PowerShell):**
```powershell
mkdir $HOME\sapphire; cd $HOME\sapphire
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/ddxfish/sapphire/main/docker-compose.yml" -OutFile "docker-compose.yml"
docker compose up -d
```

Web UI: https://localhost:8073 — TTS and STT work through the browser, no mic hardware needed.

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or [Docker Engine](https://docs.docker.com/engine/install/) (Linux). GPU support and full docs: [DOCKER.md](docs/DOCKER.md)

## Update
```bash
cd sapphire
git pull
pip install -r requirements.txt
```
Or use the in-app update button in Settings → Dashboard. See [INSTALLATION.md — Update](docs/INSTALLATION.md#update-sapphire) for details.


## More Documentation

| Guide | Description |
|-------|-------------|
| [Installation](docs/INSTALLATION.md) | Setup guide, systemd service |
| [Quick Start](docs/QUICK-START.md) | First persona, LLM setup, integrations |
| [Plugin Author Guide](docs/plugin-author/README.md) | Build plugins with hooks, tools, providers, apps, themes |
| [API](docs/API.md) | The REST API surface |
| [Voice](docs/VOICE.md) | STT, TTS, wake word, conversation mode |
| [Network](docs/NETWORK.md) | SOCKS proxy, LAN/WAN split, egress guarantees |
| [Mastery Guide](docs/MASTERY-GUIDE.md) | The skill ladder through every system |
| [Changelog](docs/CHANGELOG.md) | Release history |
| [Backups](docs/BACKUPS.md) | Automatic and manual backup system |
| [Docker](docs/DOCKER.md) | Container deployment with GPU support |
| [Technical](docs/TECHNICAL.md) | Architecture and internals |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and fixes |

## Contributions

**Help me test** the dev branch if you can. If you see bugs, post them in Issues. It feels good to know people are using this. It genuinely helps, so please post if you see bugs.

**Plugins are the way in.** Sapphire's plugin system supports tools, hooks, voice commands, scheduled tasks, settings UI, and web interfaces — all without touching core. Write a plugin, publish it to GitHub, and anyone can install it from Settings in one click. See the [Plugin Author Guide](docs/plugin-author/README.md) to get started.

We opened core contributions, reach out to me on Discord or email first if you want to contribute. We only accept PRs for single bugs. We probably reject any bulk bug fixes.

## Sapphire Condensed Mastery Guide
Sapphire is a wrapper for an LLM, so install Sapphire, load it in your web browser, link it to your LLM, say "hey sapphire" then hello to see it works. Go to Settings > Help and behold the search bar for all your needs. Then activate various prompts and LLM providers to see how they feel in Chat > sidebar > Provider. Change the text in any prompt or make a new one. Go to toolsets, make a new toolset and select what tools you want to use. Make your own Persona for your prompt + toolset with its own memory scopes and email etc. Expand AI tools via Plugins like Discord or Telegram. Install a Schedule > Events > Daemon for your email/discord/telegram. Set a scheduled task for your AI to wake you up. Have Sapphire spawn an agent to research swiss cheese. Load Sapphire web UI on your phone browser. Create a Sapphire system service to autostart. Final Boss: Have Sapphire use coding harness and run_command to create a plugin for her own system, upload it to github on your account per docs/plugin-author, submit it to the Sapphire store so the world can use it.

## Video Walkthrough

A 4.5-hour playlist covering everything in Sapphire end-to-end — install, personas, plugins, agents, daemons, the works. Made for people who'd rather watch than read.

[![Watch the Playlist](https://img.shields.io/badge/YouTube-4.5hr_Walkthrough-FF0000?logo=youtube&logoColor=white)](https://www.youtube.com/playlist?list=PL3x22_N-oxJEdAHy_GsokrMW9UzB13oTF)

## Licenses

[AGPL-3.0](LICENSE) - Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your source code changes.

## Acknowledgments

Built with:
- [openWakeWord](https://github.com/dscripka/openWakeWord) - Wake word detection
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) - Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) - Voice synthesis
