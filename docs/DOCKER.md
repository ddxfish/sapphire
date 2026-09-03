# Docker Installation

## What is Docker?

This is for beginners, by request. Advanced users just skip this.

Docker runs Sapphire in an isolated container — think of it as a lightweight virtual computer inside your computer. The container has its own operating system, Python, and all of Sapphire's dependencies pre-installed. Your data (chats, memories, settings) is stored on your real filesystem, not inside the container. When Sapphire updates, you delete the old container and create a new one — your data stays exactly where it is, untouched. Nothing critical lives inside the container. It's disposable by design.

Docker works in **layers** — the base system, Python packages, AI models, and Sapphire's code are stacked on top of each other. When you update, only the layers that changed get re-downloaded (usually just the code layer, which is tiny). The 700MB of AI models don't re-download every time.

## Why Docker?

- **Can't break your system** — Sapphire runs in its own sandbox. Bad plugins, shell commands gone wrong, experiments — they only affect the container, not your machine
- **No dependency hell** — No conda, no pip conflicts, no "works on my machine." Everything is pre-configured
- **One command install** — Pull and run. That's it
- **Clean uninstall** — Remove the container and your system is exactly how it was before
- **Built-in command line** — The container includes a full Linux environment with standard tools (curl, grep, git, etc.) that Sapphire's shell plugin can use safely

---

## Quick Start (CPU)

### 1. Install Docker

- **Windows/Mac**: Install [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- **Linux**: Install [Docker Engine](https://docs.docker.com/engine/install/)

### 2. Create a directory and download the compose file

#### Linux / Mac (Terminal)

```bash
mkdir ~/sapphire && cd ~/sapphire
curl -fsSL https://raw.githubusercontent.com/ddxfish/sapphire/main/docker-compose.yml -o docker-compose.yml
mkdir -p sapphire-data sapphire-backups sapphire-config
```

#### Windows (PowerShell)

```powershell
mkdir $HOME\sapphire; cd $HOME\sapphire
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/ddxfish/sapphire/main/docker-compose.yml" -OutFile "docker-compose.yml"
mkdir sapphire-data, sapphire-backups, sapphire-config
```

> **Windows note**: Use PowerShell (not cmd.exe). Right-click Start → "Windows PowerShell" or search for "PowerShell."

### 3. Edit your settings (optional)

Open `docker-compose.yml` in any text editor and set:
- **Timezone** — Change `America/New_York` to yours ([timezone list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones))
- **API keys** — Uncomment and fill in whichever LLM provider you use (Claude, OpenAI, etc.)

### 4. Start Sapphire

```bash
docker compose up -d
```

### 5. Open the web UI

Go to **https://localhost:8073** in your browser and complete the setup wizard.

> **Note**: You'll see a browser security warning about the self-signed certificate. This is normal — click "Advanced" → "Proceed" to continue.

> **Permissions note (Linux only)**: If you skipped step 2's `mkdir` commands, Docker creates the data directories as root. Fix with: `sudo chown -R $USER:$USER ~/sapphire`

---

## GPU Support (NVIDIA only)

If you have an NVIDIA GPU and want faster voice processing:

### Prerequisites
- NVIDIA GPU drivers installed
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed

### Use the GPU image

Edit `docker-compose.yml` and change the image line:

```yaml
image: ghcr.io/ddxfish/sapphire:gpu
```

Add GPU access under the service:

```yaml
services:
  sapphire:
    image: ghcr.io/ddxfish/sapphire:gpu
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

Then restart:

```bash
docker compose down
docker compose up -d
```

Kokoro TTS and Faster Whisper STT will automatically use the GPU. No settings changes needed.

---

## Common Commands

| What you want | Command |
|---|---|
| Start Sapphire | `docker compose up -d` |
| Stop Sapphire | `docker compose down` |
| View logs | `docker compose logs -f` |
| Restart | `docker compose restart` |

---

## Updating

When a new version is available:

```bash
docker compose down
docker compose pull
docker compose up -d
```

That's it. Your data in `sapphire-data/`, `sapphire-backups/`, and `sapphire-config/` is untouched. Only the container (code + models) gets replaced.

---

## Uninstalling

### Keep your data (can reinstall later)

```bash
docker compose down
docker rmi ghcr.io/ddxfish/sapphire:latest
```

Your data stays in `sapphire-data/`, `sapphire-backups/`, and `sapphire-config/`.

### Remove everything

```bash
docker compose down
docker rmi ghcr.io/ddxfish/sapphire:latest
```

Then delete the data directories:

**Linux / Mac:**
```bash
rm -rf sapphire-data sapphire-backups sapphire-config
```

**Windows (PowerShell):**
```powershell
Remove-Item -Recurse -Force sapphire-data, sapphire-backups, sapphire-config
```

---

## Where is my data?

Everything is stored in plain folders next to your `docker-compose.yml`:

```
sapphire/                         ← ~/sapphire (Linux/Mac) or %USERPROFILE%\sapphire (Windows)
├── docker-compose.yml            ← Configuration
├── sapphire-data/                ← Chats, memories, settings, plugins
│   ├── settings.json
│   ├── history/
│   ├── memory.db
│   ├── knowledge.db
│   └── ...
├── sapphire-backups/             ← Automatic backups
└── sapphire-config/              ← Credentials and secrets
```

These are normal folders on your machine. You can browse them, back them up, or move them to another computer.

To restore a backup from `sapphire-backups/`, use the one-click restore in **Settings > Backup** — it works in Docker too (see [BACKUPS.md](BACKUPS.md)).

---

## Using LM Studio (Local LLM)

If you run [LM Studio](https://lmstudio.ai/) on your host machine, Sapphire in Docker can connect to it.

Edit `docker-compose.yml` and uncomment the appropriate line:

```yaml
# Docker Desktop (Mac/Windows):
LMSTUDIO_BASE_URL: "http://host.docker.internal:1234/v1"

# Linux Docker:
LMSTUDIO_BASE_URL: "http://172.17.0.1:1234/v1"
```

Make sure LM Studio's server is running and set to allow network connections.

---

## Troubleshooting

**"Permission denied" on data directories (Linux)**
```bash
mkdir -p sapphire-data sapphire-backups sapphire-config
```
Create the directories before first run so they're owned by your user, not root. If they already exist as root: `sudo chown -R $USER:$USER sapphire-data sapphire-backups sapphire-config`. This doesn't apply to Windows/Mac — Docker Desktop handles permissions automatically.

**Browser shows "connection refused"**
Sapphire takes 30-60 seconds to start on first run (loading AI models). Check progress with `docker compose logs -f`.

**TTS occasionally fails**
The TTS engine has automatic retry with backoff. If a single message fails, regenerate it — subsequent attempts usually succeed immediately. This is more common on CPU than GPU.

**Container keeps restarting**
Check the logs for errors: `docker compose logs --tail 50`. Common causes:
- Missing API keys (configure in docker-compose.yml or via the web UI)
- Port 8073 already in use (change the port mapping in docker-compose.yml: `"8074:8073"`)

## Reference for AI

Docker install: image built from repo Dockerfile, CMD = main.py supervisor. docker-compose mounts three volumes (user data, backups, models cache); TZ + API-key + LMSTUDIO env vars pass through; extra_hosts maps host LLM servers. In-app Settings > Backup restore works in Docker (supervisor applies staged swap on restart). Data path answers: user/ rides the mounted volume — container replacement never loses it.
