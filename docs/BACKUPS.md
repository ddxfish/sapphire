# Backups

Sapphire automatically backs up your data so you can recover from mistakes, corruption, or bad updates. Backups are managed through Settings > Backup, or via the API.

## What's Backed Up

Backups contain the entire `user/` directory as a `.tar.gz` archive. This includes:

| Data | Location | In Backup |
|------|----------|-----------|
| Chat history | `user/history/` | Yes |
| Memories | `user/memory.db` (in `user/`) | Yes |
| Knowledge & People | `user/knowledge.db` (in `user/`) | Yes |
| Goals | `user/goals.db` (in `user/`) | Yes |
| Prompts | `user/prompts/` | Yes |
| Personas | `user/personas/` | Yes |
| Toolsets | `user/toolsets/` | Yes |
| Spice sets | `user/spice_sets/` | Yes |
| Scheduled tasks | `user/continuity/` | Yes |
| Plugin settings | `user/webui/plugins/` | Yes |
| Plugin state | `user/plugin_state/` | Yes |
| User plugins | `user/plugins/` | Yes |
| User-created tools | `user/functions/`, `user/tools/` | Yes |
| Hand-authored story packs | `user/story_presets/` | Yes |
| Settings | `user/settings/` | Yes |
| SSL certs | `user/ssl/` | Yes |

Story journals and game saves live *inside* the chat database, so they ride along with `user/history/` — there's no separate saves folder any more. If an older `user/story_saves/` folder is still on disk it's archived with everything else under `user/`, but nothing reads it.

### What's NOT Backed Up

| Data | Location | Why |
|------|----------|-----|
| API keys & credentials | `~/.config/sapphire/credentials.json` | Security — credentials are stored outside `user/` deliberately. They're encrypted with a machine-identity key and excluded from backups. |
| MCP inbound auth keys | `user/plugin_state/*_mcp_key.json` | Plaintext bearer keys for plugins exposing MCP servers. Re-issued from the plugin UI on restore. |
| MCP outbound bearer keys | `user/webui/plugins/mcp_client.json` | Plaintext `Authorization` headers for external MCP servers Sapphire connects to. Re-enter from the plugin's settings on restore. |
| Backup files themselves | `user_backups/` | Backups don't back up other backups. |
| Logs | `user/logs/` | Included in the archive but not critical for restore. |
| Downloaded models | System-dependent | STT/TTS models are re-downloaded if missing. |
| Per-file `.tmp` rename intermediaries + `.bad-{ts}` quarantine files | various | Excluded by filter — they're either in-flight writes or corrupted-state forensics, not data. |

### Private Chats in Backups

Backups include both the chat database and the vault file (`user/prompts/prompt_vault.enc`), so a restore keeps working — but **restore them together**. The vault holds the key those encrypted chat rows were written with; pairing an old vault file with a newer chat database leaves them unreadable, and Sapphire refuses to mint a replacement key rather than orphan them silently.

Private chats ride the archive as ciphertext, but backups taken **before** a chat went private still hold its earlier history in plaintext. Marking a chat private encrypts it from that moment on; nothing can rewrite copies that already left. See [PRIVACY.md](PRIVACY.md).

## Automatic Backups

Sapphire runs a scheduled backup check daily at 3 AM. Three tiers rotate automatically:

| Tier | Frequency | Default Retention |
|------|-----------|-------------------|
| **Daily** | Every day | 7 backups |
| **Weekly** | Every Sunday | 4 backups |
| **Monthly** | 1st of each month | 3 backups |

Older backups are automatically deleted when the retention limit is reached. Manual backups have a separate limit (default: 5).

### Settings

These can be changed in Settings > System:

| Setting | Default | Description |
|---------|---------|-------------|
| `BACKUPS_ENABLED` | `true` | Enable/disable automatic backups |
| `BACKUPS_KEEP_DAILY` | `7` | Number of daily backups to keep |
| `BACKUPS_KEEP_WEEKLY` | `4` | Number of weekly backups to keep |
| `BACKUPS_KEEP_MONTHLY` | `3` | Number of monthly backups to keep |
| `BACKUPS_KEEP_MANUAL` | `5` | Number of manual backups to keep |

Set any retention value to `0` to disable that tier.

## Manual Backups

Create a backup anytime from Settings > Backup, or via API:

```bash
curl -X POST https://localhost:8073/api/backup/create \
  -H "Content-Type: application/json" \
  -d '{"type": "manual"}'
```

## Where Backups Are Stored

Backups are saved to the `user_backups/` directory in your Sapphire root:

```
sapphire/
  user/                  <-- your data (what gets backed up)
  user_backups/          <-- backup archives live here
    sapphire_2026-03-19_030000_daily.tar.gz
    sapphire_2026-03-16_120000_manual.tar.gz
    ...
```

For Docker installs, this maps to your `sapphire-backups/` volume.

## Downloading Backups

You can download any backup from the UI (Settings > Backup) or via API:

```
GET /api/backup/download/{filename}
```

Keep a copy off-machine for safety.

## Restoring from a Backup

If something goes wrong, here's how to restore:

### 1. Stop Sapphire

```bash
# If running directly
Ctrl+C

# If running as a service
sudo systemctl stop sapphire
```

### 2. Move the current user directory aside

```bash
cd /path/to/sapphire
mv user user_broken
```

### 3. Extract the backup

```bash
tar -xzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz
```

This creates a `user/` directory with everything from the backup.

### 4. Re-enter credentials

Since credentials are stored outside `user/` (in `~/.config/sapphire/`), they're not affected by a restore. Your API keys and passwords survive. If you're restoring to a different machine, you'll need to re-enter credentials in Settings.

### 5. Start Sapphire

```bash
python main.py
```

That's it. Sapphire will load the restored data — chat history, memories, prompts, plugins, everything.

### Partial Restore

If you only need to restore specific data (e.g., just chat history), you can extract individual files:

```bash
# List contents without extracting
tar -tzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz

# Extract just chat history
tar -xzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz user/history/

# Extract just memories
tar -xzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz user/memory.db
```

### Docker Restore

For Docker installs, the backup is in your `sapphire-backups/` directory on the host:

```bash
# Stop the container
docker compose down

# Move current data aside
mv sapphire-data sapphire-data-broken

# Extract backup into the data directory
mkdir sapphire-data
tar -xzf sapphire-backups/sapphire_2026-03-19_030000_daily.tar.gz -C sapphire-data --strip-components=1

# Start again
docker compose up -d
```

## How Backups Stay Durable

A few things make Sapphire's backup behavior robust beyond just "tar the directory":

- **Atomic writes** — backups are written to `<filename>.gz.partial` first and atomically renamed on success. A power loss mid-backup leaves a partial file that's invisible to the rotation system; prior good backups are untouched.
- **WAL checkpoint before tar** — chat / memory / knowledge / goals SQLite databases are checkpointed first so the tar captures a consistent snapshot. If a database is mid-stream and the checkpoint can't acquire a lock, that DB is **skipped from the archive** rather than tar'd in a torn main+WAL state. Watch the logs for `WAL checkpoint BUSY for chat.db — backup will skip this DB` if you ever see a smaller-than-expected backup.
- **0600 permissions** — the tarball is chmod'd to user-only-readable before rename, since it contains plaintext data (chat history, memories, story state, etc.).
- **Health-check sentinel** — corrupt-DB integrity checks halt rotation so a bad copy can't waterfall through daily → weekly → monthly retention and rotate out your last good backup.
- **Auto-vacuum after big deletes** — deleting a chat or pruning tool images now incrementally shrinks the chat DB file (since 2.6.4), so backup sizes track actual content rather than high-water-marks.

## Pre-Update Backups

The Dashboard's Update button automatically creates a backup before pulling new code. If an update breaks something, your most recent pre-update backup is in `user_backups/`.

## Backup File Format

Backups are standard `.tar.gz` archives. The filename format is:

```
sapphire_{date}_{time}_{type}.tar.gz
```

- **date**: `YYYY-MM-DD`
- **time**: `HHMMSS`
- **type**: `daily`, `weekly`, `monthly`, or `manual`

Inside the archive, everything is rooted at `user/`:

```
user/
  history/sapphire_history.db
  memory.db
  knowledge.db
  goals.db
  settings/
  prompts/
  personas/
  continuity/tasks.json
  ...
```
