# Sapphire

You talk to it, but it's her that talks back. Customize your own virtual persona: TTS, STT, wakeword, personality, goals, emotions, tools. You can use it with a mic/speaker, Web UI, or both. Highly extensible. Includes long-term memory, web access, self-prompt editing. Windows and Linux support. This is made for you to make your own personas for work or play.

<sub>🔊 Has audio</sub>
<video src="https://github.com/user-attachments/assets/ed0dca80-121b-46e0-9c94-b98d6e9228c8" controls width="100%"></video>


![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL_3.0-blue)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-FCC624?logo=linux&logoColor=black)
![Windows 11+](https://img.shields.io/badge/Windows_11+-0078D6?logo=windows&logoColor=white)
![Waifu Compatible](https://img.shields.io/badge/Waifu-Compatible-ff69b4)
![Status: Active](https://img.shields.io/badge/Status-Active-success)
![Self Hosted](https://img.shields.io/badge/Self_Hosted-100%25-informational)

## Features

<table>
<tr>
<td width="30%"><a href="docs/screenshots/sapphire-ai.png"><img src="docs/screenshots/sapphire-ai.png" alt="Web UI"/></a></td>
<td><strong>Web UI with STT and TTS</strong><br/>Web or mic/speaker, works together. Mic input → TTS to speaker. Web input → web output. Use your own LLM via LM Studio, llama.cpp, Claude, or OpenAI-compatible APIs.</td>
</tr>
<tr>
<td><a href="docs/screenshots/settings-wakeword.png"><img src="docs/screenshots/settings-wakeword.png" alt="Wake Word"/></a></td>
<td><strong>Voice Assistant</strong><br/>Hands-free with wake word, STT, TTS, and VAD. Works with system mic, conference mic, or wireless lapel. Any speaker. Full <a href="docs/INSTALLATION.md">Installation Guide</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/multi-step-tool-reasoning.png"><img src="docs/screenshots/multi-step-tool-reasoning.png" alt="Multi-step Reasoning"/></a></td>
<td><strong>Multi-step Reasoning</strong><br/>Think → tool → think → tool → answer. Chains reasoning with actions until complete. See <a href="docs/TOOLS.md">Tools</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/per-chat-settings.png"><img src="docs/screenshots/per-chat-settings.png" alt="Per-chat Settings"/></a></td>
<td><strong>Per-chat Persona</strong><br/>Each chat holds its own prompt, voice, speed, pitch, spice, and toolset. Switch chat = switch persona. See <a href="docs/CONFIGURATION.md">Configuration</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/settings-manager.png"><img src="docs/screenshots/settings-manager.png" alt="Settings Manager"/></a></td>
<td><strong>Settings UI</strong><br/>Nearly every setting editable from UI, saves to JSON. No file editing needed.</td>
</tr>
<tr>
<td><a href="docs/screenshots/edit-past-messages.png"><img src="docs/screenshots/edit-past-messages.png" alt="Edit Messages"/></a></td>
<td><strong>Chat History Control</strong><br/>Edit, regenerate, continue, or delete any message-even the AI's responses.</td>
</tr>
<tr>
<td><a href="docs/screenshots/backup-manager.png"><img src="docs/screenshots/backup-manager.png" alt="Backup Manager"/></a></td>
<td><strong>Backup Manager</strong><br/>Scheduled daily, weekly, and monthly zipped backups of user data.</td>
</tr>
<tr>
<td><a href="docs/screenshots/socks-proxy.png"><img src="docs/screenshots/socks-proxy.png" alt="SOCKS Proxy"/></a></td>
<td><strong>SOCKS Proxy</strong><br/>Route web tool traffic through SOCKS for privacy. See <a href="docs/SOCKS.md">SOCKS</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/theme-switcher.png"><img src="docs/screenshots/theme-switcher.png" alt="Theme Switcher"/></a></td>
<td><strong>Themes</strong><br/>13 built-in themes with live preview. Coded by specially trained cats. Results may vary.</td>
</tr>
<tr>
<td><a href="docs/screenshots/plugin-manager.png"><img src="docs/screenshots/plugin-manager.png" alt="Plugin Manager"/></a></td>
<td><strong>Plugins</strong><br/>Extensible for both core LLM/persona and web UI. Sidebar auto-populates with plugin controls. See <a href="docs/PLUGINS.md">Plugins</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/prompt-editor-assembled.png"><img src="docs/screenshots/prompt-editor-assembled.png" alt="Assembled Prompt" width="48%"/></a> <a href="docs/screenshots/prompt-editor-monolith.png"><img src="docs/screenshots/prompt-editor-monolith.png" alt="Monolith Prompt" width="48%"/></a></td>
<td><strong>Prompt Editor</strong><br/>Assembled prompts (left) have swappable pieces the AI can edit itself. Monoliths (right) are simpler text blocks. See <a href="docs/PROMPTS.md">Prompts</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/spice-editor.png"><img src="docs/screenshots/spice-editor.png" alt="Spice Editor"/></a></td>
<td><strong>Spice Injection</strong><br/>Random snippets injected each reply. Keeps stories and conversations from going stale. See <a href="docs/SPICE.md">Spice</a>.</td>
</tr>
<tr>
<td><a href="docs/screenshots/toolset-editor.png"><img src="docs/screenshots/toolset-editor.png" alt="Toolset Editor"/></a></td>
<td><strong>Tools and Toolsets</strong><br/>Mix and match tool sets per persona. Easy to create, easy to have the AI make more for you. See <a href="docs/TOOLS.md">Tools</a> and <a href="docs/TOOLSETS.md">Toolsets</a>.</td>
</tr>
</table>

**Uses**: AI companion, work autopilot, sentient house, storytelling, research, ethics testing, or just a plain web UI.

## Quick Start

```bash
#Linux (skip for Windows)
sudo apt-get install libportaudio2

#Conda env (requires miniconda)
conda create -n sapphire python=3.11
conda activate sapphire

#Clone and run 
git clone https://github.com/ddxfish/sapphire.git
cd sapphire
pip install -r requirements.txt
python main.py

#Optional TTS, STT, Wakeword
pip install -r requirements-tts.txt
pip install -r requirements-stt.txt
pip install -r requirements-wakeword.txt
```

Web UI: https://localhost:8073 (self-signed SSL)

## Requirements

- Ubuntu 22.04+ or Windows 11+
- Python 3.10+ 
- Local LLM (LM Studio)
- 12-16GB system RAM
- (recommended) miniconda
- (recommended) Nvidia graphics card

## Documentation

| Guide | Description |
|-------|-------------|
| [Installation](https://github.com/ddxfish/sapphire/blob/main/docs/INSTALLATION.md) | Installation, systemd service |
| [Configuration](https://github.com/ddxfish/sapphire/blob/main/docs/CONFIGURATION.md) | How to make it yours |
| [Prompts](https://github.com/ddxfish/sapphire/blob/main/docs/PROMPTS.md) | Monolith vs assembled prompts |
| [Spice](https://github.com/ddxfish/sapphire/blob/main/docs/SPICE.md) | Random personality injection system |
| [Tools](https://github.com/ddxfish/sapphire/blob/main/docs/TOOLS.md) | Creating AI-callable functions (web search, memory, etc.) |
| [Toolsets](https://github.com/ddxfish/sapphire/blob/main/docs/TOOLSETS.md) | Grouping tools into switchable ability sets |
| [Plugins](https://github.com/ddxfish/sapphire/blob/main/docs/PLUGINS.md) | Keyword-triggered UI/voice extensions |
| [SOCKS Proxy](https://github.com/ddxfish/sapphire/blob/main/docs/SOCKS.md) | Privacy proxy for web scraping functions |
| [Troubleshooting](https://github.com/ddxfish/sapphire/blob/main/docs/TROUBLESHOOTING.md) | Common issues and fixes |
| [Technical](https://github.com/ddxfish/sapphire/blob/main/docs/TECHNICAL.md) | For nerds |

## Contributions

I am a solo dev with a burning passion, and Sapphire has a specific vision I am working towards. Rapid development makes it hard for contributions right now, as the architecture is changing while I settle on the plugin format. If you want you can reach me at my github username @gmail.com.

## Licenses

[AGPL-3.0](LICENSE) - Free to use, modify, and distribute. If you modify and deploy it as a service, you must share your source code changes.

## Acknowledgments

Built with:
- [openWakeWord](https://github.com/dscripka/openWakeWord) - Wake word detection
- [Faster Whisper](https://github.com/guillaumekln/faster-whisper) - Speech recognition
- [Kokoro TTS](https://github.com/hexgrad/kokoro) - Voice synthesis
