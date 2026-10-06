# Backups — automatic local backups, one-click restore, and optional encrypted offsite copies

Sapphire automatically backs up your data so you can recover from mistakes, corruption, or bad updates. Everything lives in **Settings > Backup**: create backups, watch their health, exclude what you don't need, and restore with one click.

## What it is

Backups are plain `.tar.gz` archives of the entire `user/` directory, written to `user_backups/` next to it (or any folder you pick — see [Where backups are stored](#where-backups-are-stored)). A scheduler creates them daily and rotates old ones out by tier; the Update button takes one automatically before pulling new code; and any backup — scheduled, manual, or a file you carried in from another machine — restores through the UI without touching a terminal.

One design line worth knowing: **local backups are plaintext by default.** They sit on the same disk as the live data, so encrypting them at rest protects little and costs you everything if you lose the password. Anything that *leaves* the machine — a device's SD card, a folder on another box, the offsite vault — is always encrypted first. You can turn encryption on for local backups too; see [Encryption](#encryption) below.

## Using it

### What's backed up

| Data | Location | In Backup |
|------|----------|-----------|
| Chat history | `user/history/` | Yes |
| Memories | `user/memory.db` | Yes |
| Knowledge & People | `user/knowledge.db` | Yes |
| Goals | `user/goals.db` | Yes |
| Prompts | `user/prompts/` | Yes |
| Personas | `user/personas/` | Yes |
| Toolsets | `user/toolsets/` | Yes |
| Spice sets | `user/spice_sets/` | Yes |
| Scheduled tasks | `user/continuity/` | Yes |
| Plugin settings | `user/webui/` | Yes |
| Plugin state | `user/plugin_state/` | Yes |
| User plugins | `user/plugins/` | Yes |
| User-created tools | `user/functions/`, `user/tools/` | Yes |
| Hand-authored story packs | `user/story_presets/` | Yes |
| Settings | `user/settings.json` | Yes |
| SSL certs | `user/ssl/` | Yes |

Story journals and game saves live *inside* the chat database, so they ride along with `user/history/` — there's no separate saves folder any more.

### What's NOT backed up

| Data | Location | Why |
|------|----------|-----|
| API keys & credentials | `~/.config/sapphire/credentials.json` | Security — credentials are stored outside `user/` deliberately. They're encrypted with a machine-identity key and excluded from backups. |
| MCP inbound auth keys | `user/plugin_state/*_mcp_key.json` | Plaintext bearer keys for plugins exposing MCP servers. Re-issued from the plugin UI on restore. |
| MCP outbound bearer keys | `user/webui/plugins/mcp_client.json` | Plaintext `Authorization` headers for external MCP servers Sapphire connects to. Re-enter from the plugin's settings on restore. |
| Health-check API token | `user/sapphire-health.token` | Live credential — re-minted by the health script. |
| Downloaded models | `user/models/` | Rebuildable cache by contract — everything under it is re-downloaded on first use. Keeps backups from carrying hundreds of MB of model blobs. |
| Backup files themselves | `user_backups/` | Backups don't back up other backups. |
| In-flight `.tmp` files + `.bad-*` quarantine files | various | Either half-written or corrupted-state forensics, not data. |
| Symlinks & hardlinks | various | Tar would store the target *path* (a private-location leak once backups go offsite) and they don't restore cleanly cross-platform. |

### Private chats in backups

Backups include both the chat database and the vault file (`user/prompts/prompt_vault.enc`), so a restore keeps working — but **restore them together**. The vault holds the key those encrypted chat rows were written with; pairing an old vault file with a newer chat database leaves them unreadable, and Sapphire refuses to mint a replacement key rather than orphan them silently.

Private (🔒) chats ride the archive as ciphertext, but backups taken **before** a chat went private still hold its earlier history in plaintext. Marking a chat private encrypts it from that moment on; nothing can rewrite copies that already left. See [PRIVACY.md](PRIVACY.md).

### Automatic backups

A scheduler runs daily at the hour you pick (`BACKUPS_HOUR`, default 3 AM local). Three tiers rotate automatically, plus two more that fill on their own:

| Tier | When created | Default retention |
|------|--------------|-------------------|
| **Daily** | Every day | 7 backups |
| **Weekly** | Every Sunday | 4 backups |
| **Monthly** | 1st of each month | 3 backups |
| **Manual** | When you click Backup Now (or via API) | 5 backups |
| **Update** | Automatically before every code update | 3 backups |

Older backups are deleted when a tier passes its retention limit. Setting a retention value to `0` **pauses** that tier — no new backups of that kind, and the existing ones are kept, never purged.

The nightly run also does database housekeeping first: an integrity check daily and a VACUUM weekly. If a database fails its integrity check, Sapphire writes a **corruption sentinel** to `user/health/` and *halts all backup creation and rotation* — a corrupt copy must never waterfall through the daily → weekly → monthly tiers and rotate out your last good backup. Fix or restore the database, then delete the `CORRUPT_*.flag` file to resume.

### Manual backups

Click **Backup Now** in Settings > Backup, or via API:

```bash
curl -k -X POST https://localhost:8073/api/backup/create \
  -H "Authorization: Bearer $SAPPHIRE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type": "manual"}'
```

### Backup health

The top of Settings > Backup shows a health strip (backed by `GET /api/backup/health`). Green means healthy, with the newest backup's age and scheduler status. It warns loudly when something needs you:

- **Backups HALTED** — a corruption sentinel is active (with the flag filenames and how to clear them)
- The scheduler thread isn't running (scheduled backups won't fire until restart)
- The last scheduled run failed
- The newest backup is more than two days old
- No backups exist yet

### Excluding files & checking size

The **Exclude from backups** box takes one pattern per line and saves automatically. A bare folder name skips the whole subtree (`piper-voices`, `rag`); a `*` means "anything" (`*.log`, `history/*`). Your passwords and keys are never backed up either way — the privacy floor above applies regardless of patterns.

Click **Check size** for a live estimate of the uncompressed backup size with your patterns applied, plus a per-folder breakdown so you can see what's heavy. If the estimate crosses `BACKUPS_MAX_SIZE_WARN_MB` (default 2048), the page flags it.

If a pattern set would exclude *every* file, the backup refuses rather than writing a useless empty archive.

### Where backups are stored

```
sapphire/
  user/                  <-- your data (what gets backed up)
  user_backups/          <-- backup archives live here
    sapphire_2026-03-19_030000_daily.tar.gz
    sapphire_2026-03-16_120000_manual.tar.gz
    ...
```

For Docker installs, this maps to your `sapphire-backups/` volume.

**Pick your own folder.** `BACKUPS_DIR` (Settings > Backup) moves the whole folder: an absolute path (a second disk, a NAS mount, a USB stick) or one relative to the Sapphire folder; `~` expands. A folder inside `user/` is refused (backups would back up backups), and one that can't be written falls back to the default — the health strip at the top of the page always names the folder really in use.

**Where devices come from.** A satellite with a card, this computer with a folder (a USB stick, or a mounted NAS share, which is always sealed), or an SSH machine with a backup folder on its Backup tab (a Raspberry Pi, a server: `scp` carries the file, its `sha256sum` is checked). All of them appear in [Settings > Devices](DEVICES.md).

**Send to devices**, beside Backup Now, sends the newest backup to every device that holds backups right now, sealed first, and the line under it says who those devices are and how the last send went. The nightly run does the same on its own for every device that is online at the time. For a different rhythm per device — weekly to the card, nightly to a USB stick — make a **Device** task in Triggers > Scheduled ([Continuity](CONTINUITY.md)): pick the device, `storage`, `backup`, and a cron. No AI is involved.

**Open a backup without Sapphire.** Four small files sit beside the archives: `open-backup.sh` (Linux/macOS), `open-backup.bat` (Windows, double-click), `decrypt_backup.py` and a `README.txt`. Run the script, pick a backup, and it unpacks into a folder next to it — plain `.tar.gz` needs only `tar`; an encrypted `.sapphirebak` asks for your password and needs Python with `pip install cryptography`. Sapphire writes the same files onto every device and folder it backs up to, so a card pulled from a dead box restores on any PC.

You can download any backup from the list in Settings > Backup (or `GET /api/backup/download/{filename}`). Keep a copy off-machine — or let the [Remembrance plugin](#offsite-backups-remembrance-plugin) do it for you.

### Encryption

One **backup password** covers everything Sapphire seals: backups sent to devices and folders ([Device backups](DEVICES.md)), the offsite vault, and — if you turn it on — local backups. Set it in **Settings > Backup > Encryption**. It is stored scrambled in `~/.config/sapphire/` (machine-bound), never inside a backup. **Write it down.** A sealed backup opens with this password and nothing else — not even us. On a new machine, restore-from-file asks for it.

**Encrypt local backups too** (`BACKUPS_ENCRYPT_LOCAL`, off by default) seals every new local backup as a `.sapphirebak` and keeps no plain copy. Turning it on goes through an I-UNDERSTAND confirmation. The one rule it enforces: *if encryption is on and no password is set, no backup is made* — never a plain one in its place. The health strip says so loudly, and the scheduled run publishes a health alert. Backups already in the folder stay as they are.

Everything that leaves the machine is encrypted whatever this switch says. There is no setting that sends plaintext to a device or the vault; only a folder on *this* machine (a USB stick) can opt out.

### Restoring — the one-click way

Restore happens in **Settings > Backup**, no terminal needed:

1. **From the backup list** — every backup row has a restore (↻) button. Or use **Restore from a file** at the bottom to upload a `.tar.gz` or `.sapphirebak` you carried in from another machine or downloaded from the offsite vault.
2. If the file is encrypted (`.sapphirebak`), Sapphire prompts for the backup password.
3. Confirm. Sapphire validates the archive (it must be a real backup rooted at `user/`), stages it, and restarts.
4. The actual swap happens **offline at boot**, before anything opens your data: the current `user/` is set aside as `user.old` and the backup takes its place. If anything goes wrong mid-swap, the restore rolls back and your existing data is left untouched.
5. When the page reconnects, a banner reports the outcome — restore complete (with the source named) or restore failed (with the reason, and your data untouched).

Your previous data stays in `user.old` until you delete it, and restoring twice in a row keeps the earlier rollback point as `user.old.prev` — a second restore never destroys your first escape hatch.

Credentials live outside `user/`, so a restore never touches your API keys and passwords. If you're restoring onto a *different* machine, re-enter credentials in Settings afterward.

This works the same in Docker — the container keeps running while the supervisor applies the swap.

### Restoring by hand (advanced / fallback)

If Sapphire won't start at all, the archives are standard tarballs and you can do the swap yourself:

```bash
# 1. Stop Sapphire (Ctrl+C, or: systemctl stop sapphire)
cd /path/to/sapphire

# 2. Move the current user directory aside
mv user user_broken

# 3. Extract the backup (creates a fresh user/)
tar -xzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz

# 4. Start Sapphire
python main.py
```

For a **partial restore** — just one database or folder — extract individual paths:

```bash
tar -tzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz   # list contents
tar -xzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz user/history/
tar -xzf user_backups/sapphire_2026-03-19_030000_daily.tar.gz user/memory.db
```

Docker, by hand (the in-app restore above is usually the easier path):

```bash
docker compose down
mv sapphire-data sapphire-data-broken
mkdir sapphire-data
tar -xzf sapphire-backups/sapphire_2026-03-19_030000_daily.tar.gz -C sapphire-data --strip-components=1
docker compose up -d
```

### How backups stay durable

A few things make the behavior robust beyond "tar the directory":

- **Live databases are never tar'd directly.** Every SQLite database under `user/` is snapshotted through SQLite's own backup API — a consistent point-in-time copy that's safe even while chats are being written — and the snapshot rides into the archive at the live file's path, so restores stay transparent. `-wal`/`-shm` journal files are excluded; the snapshots are already complete. If a database can't be snapshotted, it's **omitted from that backup** rather than captured torn (watch the logs for a `DB snapshot failed ... DB will be OMITTED from this backup` line); the next run retries.
- **Atomic writes** — backups are written to a `.partial` file first and atomically renamed on success. A power loss mid-backup leaves a partial that's invisible to rotation; prior good backups are untouched.
- **0600 permissions** — the tarball is chmod'd user-only-readable before rename, since it contains plaintext data.
- **Health-check sentinel** — corrupt-DB integrity checks halt creation *and* rotation so a bad copy can't waterfall through the tiers.
- **Unreadable files are skipped, not fatal** — one locked file (Windows AV, an editor's exclusive lock) skips that file with a warning instead of aborting the whole backup.
- **Auto-vacuum after big deletes** — deleting a chat or pruning tool images incrementally shrinks the chat DB file, so backup sizes track actual content.

### Pre-update backups

The Dashboard's Update button automatically creates a backup before pulling new code — it lands in the **Update** tier. This one is held to a stricter standard: if any database is busy and can't be snapshotted, the backup is *refused* and the update stops, because an insurance snapshot missing your chat or knowledge database isn't insurance.

### Backup file format

```
sapphire_{date}_{time}_{type}.tar.gz
```

- **date**: `YYYY-MM-DD` · **time**: `HHMMSS` · **type**: `daily`, `weekly`, `monthly`, `manual`, or `pre_update` (listed under the Update tier)

Inside, everything is rooted at `user/`. Files ending in `.sapphirebak` are encrypted backups (shown with a 🔒 in the list) — Sapphire decrypts them on restore after asking for the password.

## Offsite backups (Remembrance plugin)

The **Remembrance** plugin (disabled by default) ships encrypted backups of `user/` to an offsite, zero-knowledge vault. This is where the encryption lives: everything that leaves the machine is encrypted *first*, on your machine, with a password only you know. The server stores ciphertext and can never read your data — and by the same token, **losing the password means the offsite backups are gone for good.** Write it down.

### Setup

1. Get a server URL, tenant ID, and API key from the vault owner.
2. Enable the plugin, then open **Settings > Plugins > Remembrance**: enter the server details → **Save** → **Test**.
3. Set your **backup password** in **Settings > Backup > Encryption** (one password for the vault, devices and local backups). Nothing uploads without it.

### Three ways to back up

- **Manual** — the "Back up now (offsite)" button on the plugin panel.
- **AI tool** — `remembrance_backup`: called with no comment it reports vault status; with a comment it creates and uploads a labeled backup (kept long-term).
- **Automatic** — with auto-backup on, an hourly check uploads once a day at your configured hour (default: one hour after the local backup run). Cadence mirrors the local rhythm: monthly on the 1st, weekly on Sunday, daily otherwise.

### How it protects you

- Encryption is password-derived **AES-256-GCM** (scrypt key derivation) in a framed format — a multi-GB backup encrypts in flat memory, and any truncation or tampering fails loudly on decrypt. Encrypted files use the `.sapphirebak` extension.
- Every upload is preceded by a **ciphertext check** — the file must *not* open as a tar and must carry the encrypted-backup header. A plaintext slip blocks the upload and logs CRITICAL.
- Downloads are **sha256-verified** before decrypting.
- The API key and encryption password are stored scrambled in `~/.config/sapphire/` — never inside a backup.
- The plugin panel has its own **Offsite excludes** and size cap, layered on top of your local exclude patterns.

### Restoring from offsite

The plugin panel lists your vault backups, each with a restore (↻) button — enter the encryption password and Sapphire downloads, verifies, decrypts, and goes through the same restart-and-swap flow as a local restore (`user.old` rollback included).

**Disaster recovery without Sapphire:** `tools/decrypt_backup.py` is a standalone decryptor. Copy that one file anywhere with Python and the `cryptography` package (`pip install cryptography`), run it against your `.sapphirebak` with your password, and you get a normal `.tar.gz` back — then `tar -xzf` gives you `user/`. No running Sapphire required.

## Settings

| Key | Default | What it does | Where in UI |
|-----|---------|--------------|-------------|
| `BACKUPS_ENABLED` | `true` | Enable/disable automatic backups | Settings > Backup |
| `BACKUPS_HOUR` | `3` | Local hour (0–23) the daily scheduled run fires | Settings > Backup |
| `BACKUPS_KEEP_DAILY` | `7` | Daily backups to keep | Settings > Backup |
| `BACKUPS_KEEP_WEEKLY` | `4` | Weekly backups to keep | Settings > Backup |
| `BACKUPS_KEEP_MONTHLY` | `3` | Monthly backups to keep | Settings > Backup |
| `BACKUPS_KEEP_MANUAL` | `5` | Manual backups to keep | Settings > Backup |
| `BACKUPS_KEEP_UPDATE` | `3` | Pre-update backups to keep | settings.json / API only |
| `BACKUPS_EXCLUDE_PATTERNS` | (empty) | Glob patterns excluded from backups, one per line | Settings > Backup — "Exclude from backups" box |
| `BACKUPS_MAX_SIZE_WARN_MB` | `2048` | Size estimate above this shows a warning | Settings > Backup |
| `BACKUPS_DIR` | (empty) | Folder for local backups; empty = `user_backups/` | Settings > Backup |
| `BACKUPS_ENCRYPT_LOCAL` | `false` | Seal local backups with the backup password | Settings > Backup — Encryption box (I-UNDERSTAND gate) |

Any `KEEP` value set to `0` pauses that tier: nothing new is created, existing backups are kept.

## Quick Troubleshooting

**"Backups HALTED" in the health strip** — a database failed its integrity check and a corruption sentinel is protecting your last good backups. Fix or restore the affected DB, then delete the `CORRUPT_*.flag` file(s) in `user/health/` to resume.

**A backup is smaller than expected** — check your exclude patterns and the size breakdown (**Check size**), and remember `user/models/` is always excluded as rebuildable cache. Also scan the logs for `DB snapshot failed` — a busy database gets omitted from that backup and picked up next run.

**Restore failed banner after restart** — nothing was changed; your data was left untouched (or rolled back from `user.old`). The banner shows the reason. On Windows, a `PermissionError` usually means something held a file open under `user/` — close Explorer/editors and retry.

**"Wrong password or corrupted backup"** — the `.sapphirebak` password didn't match (or the file is damaged). There is no recovery path around the password — that's the zero-knowledge deal.

**Newest backup is days old / scheduler not running** — the health strip flags both. A dead scheduler thread needs a restart of Sapphire; a failed run shows the failing tier in the strip.

**Restored on a new machine and the AI has amnesia about API keys** — expected: credentials live outside `user/` and never ride the backup. Re-enter them in Settings.

## See also

- [PRIVACY.md](PRIVACY.md) — what's plaintext where, private chats, the vault
- [DOCKER.md](DOCKER.md) — volumes and where backup files land in Docker
- [IMPORT-EXPORT.md](IMPORT-EXPORT.md) — moving individual data between machines without a full restore
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general recovery

## Reference for AI

Local tar backups of user/ (plain by default, optionally sealed) with tiered rotation + offline-at-boot restore; one core gate seals everything that leaves (devices, folders, remembrance). Settings tab: Backup.

SETTINGS KEYS (defaults): BACKUPS_ENABLED=true, BACKUPS_HOUR=3, BACKUPS_KEEP_DAILY=7, BACKUPS_KEEP_WEEKLY=4, BACKUPS_KEEP_MONTHLY=3, BACKUPS_KEEP_MANUAL=5, BACKUPS_KEEP_UPDATE=3 (no UI field), BACKUPS_EXCLUDE_PATTERNS=[] (UI textarea, auto-saves), BACKUPS_MAX_SIZE_WARN_MB=2048, BACKUPS_DIR="" (empty=user_backups/; inside user/ refused → default + backup_dir_error), BACKUPS_ENCRYPT_LOCAL=false. KEEP<=0 = pause tier (no create, no purge).

ENCRYPTION (core, 2026-10-06): one backup password (credentials.backup_password, machine-bound scramble; PUT /api/backup/password; status in GET /api/backup/health.password_status). backup_crypto.seal = encrypt + verify (not-a-tar + magic + non-empty) or raise. create_backup(encrypt=None→BACKUPS_ENCRYPT_LOCAL): seals the tar, unlinks plaintext, returns .sapphirebak; encrypt on + no password = NO backup + last_backup_error + health alert backup_password_missing (never plaintext). export_encrypted(type, dest_dir, extra_patterns, cap_mb) = the shipper gate (password → sentinel → cap → create(encrypt=True)); remembrance calls it. sealed(filename) = ship-ready copy of an existing local backup. backup_targets.ship(filename) seals once, one thread per target, Target.check refuses .tar.gz to any remote target and any .sapphirebak lacking the magic; rotation on targets = select_doomed(names, keep). Openers (README.txt, open-backup.sh/.bat, decrypt_backup.py) written beside local backups and onto every target.

MECHANISM:
- Archive = user/ → {backup_dir}/sapphire_{YYYY-MM-DD}_{HHMMSS}_{type}.tar.gz (or .sapphirebak when BACKUPS_ENCRYPT_LOCAL); types daily/weekly/monthly/manual/pre_update (pre_update lists under "update" tier). Local backups plaintext BY DEFAULT; egress (devices, folders, remembrance) always sealed. Encrypted local .sapphirebak files list (🔒) and restore.
- SQLite: live *.db NEVER tar'd — sqlite3 backup API snapshots each to staging, added at live arcnames; live db/-wal/-shm excluded from walk. Snapshot failure → DB OMITTED from that backup (log: "DB snapshot failed for X ... OMITTED"); retried next run. WAL checkpoint beforehand is best-effort trim only, no longer gates inclusion. Pre-update backup passes require_complete=True → refuses instead of shipping without a busy DB.
- Always-excluded privacy floor (regardless of patterns): *_mcp_key.json, mcp_client.json, sapphire-health.token, *.tmp/.tmp.*, .bad-*, symlinks/hardlinks; user/models/ excluded as rebuildable cache. Credentials live outside user/ (~/.config/sapphire/) — never in archive.
- Durability: write .partial → chmod 0600 → atomic rename; 0-files-after-exclusions refused; unreadable files skipped w/ warning; create+rotate serialized under one lock.
- Scheduler: daily at BACKUPS_HOUR (bad value → falls back to 3, loop never dies). Housekeeping first: integrity_check daily, VACUUM Sundays. Integrity failure → sentinel user/health/CORRUPT_{db}_{ts}.flag + sapphire_health_alert event → create+rotate HALT until user deletes flag(s).
- Estimate: POST /api/backup/estimate {patterns[],extra_patterns[]} → uncompressed total, excluded bytes, per-top-level breakdown, over_warn vs BACKUPS_MAX_SIZE_WARN_MB. UI "Check size" previews unsaved patterns.

RESTORE FLOW:
- UI: Settings > Backup — per-row ↻ (POST /api/backup/restore {filename,password}) or upload (POST /api/backup/restore-upload multipart file+password; accepts .tar.gz/.sapphirebak). Server decrypts if needed, validates (gzip tar rooted at user/, path-traversal + Windows-reserved-name checks), stages user_restore/pending.tar.gz + pending_restore.json, schedules restart.
- At boot, main.py applies pending restore BEFORE app spawn: extract → user/ becomes user.old (prior user.old → user.old.prev) → swap. Failure = rollback, existing user/ kept, marker cleared (never loop-retries). Own backups extract trusted/faithful; uploads use tarfile 'data' filter; symlinks/hardlinks/devices never extracted.
- Outcome: user_restore/last_restore_result.json → GET /api/backup/restore-result → UI banner; DELETE same route dismisses. Works in Docker (container CMD is the main.py supervisor).

ROUTES: GET /api/backup/list, GET /api/backup/health (enabled, sentinels, halted, scheduler_alive, newest{filename,size,age_hours}, last_scheduled_result, last_backup_error, devices{targets,shipping,last_ship} → health strip), POST /api/backup/send {filename?} (newest to every online storage device, async; 400 no device/no backup, 409 already sending), POST /api/backup/create {type: daily|weekly|monthly|manual}, POST /api/backup/estimate, DELETE /api/backup/delete/{filename}, GET /api/backup/download/{filename}, POST /api/backup/restore, POST /api/backup/restore-upload, GET/DELETE /api/backup/restore-result. All require auth.

OFFSITE (plugins/remembrance, default_enabled=false):
- Zero-knowledge: scrypt-derived AES-256-GCM, framed format (magic SAPPHIREBAK, per-frame nonce + frame-index AAD, EOF marker → truncation/reorder/tamper all fail loudly), ext .sapphirebak. Password loss = unrecoverable.
- Flow: core creates plain tar (plugin extra excludes + size cap offsite_max_mb=2048) → encrypt → verify-ciphertext-or-refuse (CRITICAL log) → upload. Download sha256-verified before decrypt.
- Cron: hourly check, uploads at offsite_cron_hour (default BACKUPS_HOUR+1) when auto_enabled; cadence monthly (1st) / weekly (Sun) / daily. Failure surfaces to user; success silent.
- Tool: remembrance_backup — no comment → vault status; comment → labeled manual backup (kept long-term).
- Creds (server URL/tenant/API key) scrambled in ~/.config/sapphire/, never in backups; the backup password is core's (Settings > Backup). Gate = core export_encrypted; plugin owns account + cadence + upload + restore download. Restore: panel ↻ → same staged-restart flow.
- Standalone recovery: tools/decrypt_backup.py (needs only python + cryptography pkg + password) → .tar.gz → tar -xzf.
