# Installation

## Requirements

- Ubuntu 22.04+ or Windows 11+
- Python 3.11+
- 16GB+ system RAM
- (recommended) Nvidia GPU for TTS/STT

---

## Prerequisites

### Linux

```bash
sudo apt update
sudo apt install libportaudio2 python3-dev git
```

**Install Miniconda:**

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b
~/miniconda3/bin/conda init
```

Close and reopen your terminal.

### Windows

Open PowerShell as Administrator:

```powershell
winget install Anaconda.Miniconda3
winget install Git.Git
```

Or download Miniconda manually from [miniconda.io](https://docs.conda.io/en/latest/miniconda.html)

Close and reopen PowerShell.

---

## Python Environment

Sapphire requires Python 3.11 with specific package versions for GPU acceleration and audio processing. Conda pins Python exactly and handles complex dependencies automatically. Venv can work but requires manually installing Python 3.11 system-wide first, which varies by distro and often conflicts with system Python.

```bash
conda create -n sapphire python=3.11 -y
conda activate sapphire
```

---

## Install Sapphire

```bash
git clone https://github.com/ddxfish/sapphire.git
cd sapphire
pip install -r requirements.txt
```

---

## Optional: TTS, STT, Wakeword

These are separate installs to keep the base lightweight. Enable each in Settings after installing, then restart.

```bash
# TTS (Kokoro voice synthesis)
pip install -r requirements-tts.txt

# STT (Faster Whisper transcription)  
pip install -r requirements-stt.txt

# Wakeword (OpenWakeWord detection)
pip install -r requirements-wakeword.txt
```

---

## LLM Backend

Sapphire needs an LLM to talk to. The setup wizard configures this on first run.

### Option A: Local LLM (Private, Free)

Run AI models on your own hardware. No data leaves your machine.

1. Install [LM Studio](https://lmstudio.ai/)
2. Download a model (Qwen3 8B is a good start)
3. Go to Developer tab, enable "Local Server"
4. Sapphire connects to `http://127.0.0.1:1234/v1` by default

**Recommended local models:**

| Model | Strengths | Weaknesses |
|-------|-----------|------------|
| Qwen3 8B | Function calling, small | Weak stories |
| Qwen3 30B A3B | Balanced, fast | Medium RAM |
| QWQ 32B | Passionate storytelling | Bad at tools |
| Llama 3.1 | Fast output | Poor tools |

### Option B: Cloud LLM (Powerful, Not Private)

Use cloud APIs for stronger models. Your conversations go to external servers.

| Provider | Strengths | Get API Key |
|----------|-----------|-------------|
| Claude (Anthropic) | Complex tasks, conversation | [console.anthropic.com](https://console.anthropic.com/) |
| OpenAI | GPT models, well-supported | [platform.openai.com](https://platform.openai.com/) |
| Fireworks | Fast, many open models | [fireworks.ai](https://fireworks.ai/) |

Set your API key via environment variable or in the setup wizard:
- `ANTHROPIC_API_KEY` for Claude
- `OPENAI_API_KEY` for OpenAI
- `FIREWORKS_API_KEY` for Fireworks

Sapphire automatically falls back between enabled providers if one fails.

---

## First Run

```bash
python main.py
```

1. Open http://localhost:8073
2. Create a password (secures web UI and API)
3. Complete setup wizard (LLM, voice, audio)
4. Send a test message

Sapphire creates `user/` directory with your settings and data. Run once before customizing.

---

## Update Sapphire

Preserves your `user/` directory. Safe unless you modified core files.

```bash
cd sapphire
git pull
```

---

## Running as Service (Linux)

```bash
mkdir -p ~/.config/systemd/user
vim ~/.config/systemd/user/sapphire.service
```

```ini
[Unit]
Description=Sapphire User Service
After=pipewire.service
Wants=pipewire.service

[Service]
Type=simple
# Change YOURUSERNAME to your username
WorkingDirectory=/home/YOURUSERNAME/sapphire
StandardOutput=journal
StandardError=journal

# API keys for cloud LLM providers (uncomment and set if using)
#Environment="ANTHROPIC_API_KEY=sk-ant-your-key-here"
#Environment="OPENAI_API_KEY=sk-your-key-here"
#Environment="FIREWORKS_API_KEY=fw_your-key-here"

# SOCKS proxy credentials (uncomment if using)
#Environment="SAPPHIRE_SOCKS_USERNAME=abc"
#Environment="SAPPHIRE_SOCKS_PASSWORD=123"

Environment="PYTHONUNBUFFERED=1"
# Change YOURUSERNAME twice here
ExecStart=/bin/bash -c 'source /home/YOURUSERNAME/miniconda3/etc/profile.d/conda.sh && conda activate sapphire && python3 /home/YOURUSERNAME/sapphire/main.py'
Restart=on-failure
RestartSec=90

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable sapphire
systemctl --user start sapphire
journalctl --user -u sapphire -f
```

---

## Making It Yours

At this point, try Sapphire. If you want to make it yours, continue with [CONFIGURATION.md](CONFIGURATION.md)

---

## Reference for AI

Help users install Sapphire.

REQUIREMENTS:
- Ubuntu 22.04+ or Windows 11+
- Python 3.11+ (conda recommended, pins version easily)
- 16GB+ RAM
- Optional: Nvidia GPU for TTS/STT

LINUX PREREQS:
```
sudo apt install libportaudio2 git
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b && ~/miniconda3/bin/conda init
# reopen terminal
```

WINDOWS PREREQS:
```
winget install Anaconda.Miniconda3
winget install Git.Git
# reopen PowerShell
```

INSTALL (both OS):
```
conda create -n sapphire python=3.11 -y && conda activate sapphire
git clone https://github.com/ddxfish/sapphire.git && cd sapphire
pip install -r requirements.txt
python main.py
```

OPTIONAL FEATURES:
- TTS: pip install -r requirements-tts.txt
- STT: pip install -r requirements-stt.txt  
- Wakeword: pip install -r requirements-wakeword.txt
(Enable in Settings after install, then restart)

LLM OPTIONS:
- Local: LM Studio on port 1234 (private, free)
- Cloud: Claude/OpenAI/Fireworks (set API key via env var or Settings)
- Setup wizard configures on first run
- Auto-fallback between enabled providers

FIRST RUN:
1. python main.py
2. Open http://localhost:8073
3. Create password
4. Complete setup wizard
5. Send test message

TROUBLESHOOTING:
- "No module found": pip install in conda env
- "libportaudio": sudo apt install libportaudio2 (Linux)
- "Connection refused": LLM server not running
- LLM not responding: Check provider enabled in Settings

UPDATE: cd sapphire && git pull (preserves user/)