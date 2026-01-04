# Installation

## Requirements

- Ubuntu 22.04+ or Windows 11+
- Python 3.10+ 
- Local LLM (LM Studio)
- 16GB+ system RAM
- (recommended) miniconda
- (recommended) Nvidia graphics card

## Linux (skip for Windows)

```bash
sudo apt update
sudo apt install libportaudio2 python3-dev
```


## Python Environment

Install [miniconda](https://www.anaconda.com/docs/getting-started/miniconda/install) especially if you plan on using GPU. Conda is better than venv for this use case.

```bash
conda create -n sapphire python=3.11
conda activate sapphire
```

## Install Sapphire

Install [git](https://git-scm.com/install/windows) for Windows, Linux usually already has it.

```bash
git clone https://github.com/ddxfish/sapphire.git
cd sapphire
pip install -r requirements.txt
```

## Optional: TTS, STT, wakeword

Enable these in Sapphire Settings after you install, then restart app.

```bash
# TTS (Kokoro)
pip install -r requirements-tts.txt

# STT (faster_whisper transcription)  
pip install -r requirements-stt.txt

# Wakeword (openwakeword)
pip install -r requirements-wakeword.txt
```

## LLM Backend

Sapphire uses your LLM you run separately. If you are new, just install LM Studio, download Qwen3 8B, then enable the API in LM Studio

- **LM Studio** (recommended): Load your model, start API server on port 1234
- **llama-server**: `llama-server -m model.gguf --host 127.0.0.1 --port 1234 -c 8192 -t 8`
- **Ollama**, **vLLM**, **transformers**: Should work (untested, no support)
- **Claude**: This is a cloud option that would negate your privacy
- **Any OpenAI Compliant Endpoint**: uses OpenAI spec for LLM call formatting

### Choose an LLM model

You run your own model and LLM server, but these models are solid starting points:

- **Qwen3 8B** - small, great at function calling, weak at story, best starting point
- **QWQ 32B** - Passionate storytelling, bad at tools
- **Qwen3 30B A3B** - Very fast model when run properly, great mix of story/tools/speed
- **Llama 3.1** - non thinking model, faster output, decent at stories, bad at tools
- **GLM** - quite good at stories, unsure about tools, very high RAM required
- **Minimax M2** - mediocre story/companion, but good with code/tools, high RAM usage

#### Cloud LLM Option: Claude API

Sapphire also supports Anthropic's Claude API as an alternative to local models. This is **not the default** and I made it so you have to edit the JSON in Settings to even use this. This would give Sapphire great coding and tool calling, but it's no longer private if you route your data to the cloud. You have been warned. You need an API key if you want to use this. 

```json
{
  "llm": {
    "LLM_PRIMARY": {
      "provider": "claude",
      "base_url": "https://api.anthropic.com",
      "api_key": "sk-using-this-destroys-your-privacy",
      "model": "claude-sonnet-4-5-20250929",
      "enabled": true
    }
  }
}
```

## First Run

```bash
python main.py
```

1. Open `https://localhost:8073` in browser
2. Accept the self-signed certificate warning
3. Complete setup wizard (set password)
4. Send a test message

Sapphire creates `user/` directory with your settings and data. Make sure you **run once before customizing** - this bootstraps config files and directories.

## Update Sapphire (for later)

This preserves your user dir and pulls the latest Sapphire. Your files are safe unless you modified core files.

```bash
cd sapphire
git pull
```

## Running as Service (Linux, Optional)

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
# CHANGE YOURUSERNAME to your username
WorkingDirectory=/home/YOURUSERNAME/sapphire
StandardOutput=journal
StandardError=journal
# Add the environment variables (optional)
#Environment="SAPPHIRE_SOCKS_USERNAME=abc"
#Environment="SAPPHIRE_SOCKS_PASSWORD=123"
Environment="PYTHONUNBUFFERED=1"
#Change YOURUSERNAME twice here
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

## Making It Yours

At this point, try Sapphire. If you want to make it yours, continue with [CONFIGURATION.md](CONFIGURATION.md)