# Remembrance — offsite encrypted backups

Ships an encrypted backup of `user/` to the zero-knowledge **Remembrance vault**.
Anything that leaves the machine is encrypted with your **backup password** (the
one in **Settings > Backup > Encryption**, shared with device backups and, if you
turn that on, local backups), verified to be ciphertext, then uploaded. Local
backups stay plain `.tar.gz` by default. The server stores ciphertext only and never sees
your data or the password. A backup that fails the ciphertext check is refused,
loudly, in the log.

## Setup
1. Get a tenant ID + API key from the vault owner (minted via the server's `mint.py`).
2. Settings → Plugins → Remembrance: enter server URL, tenant ID, API key → **Save** → **Test**.
3. Set the **backup password** in **Settings > Backup > Encryption**. Nothing uploads without it.

## Three ways to back up
- **Manual:** the "Back up now (offsite)" button on the settings page.
- **AI tool:** `remembrance_backup` — no comment → vault status; with a comment → a labeled backup (kept long-term).
- **Schedule:** one daily run at the hour you set (default 04:00, an hour after the local backup's default); off until **Automatic daily backup** is on. One scheduler entry, no hourly polling.

## Restore
The backups list has a ↻ per row — enter your backup password, confirm, and Sapphire
restarts and swaps the data in (your current data is preserved as `user.old`).

## Privacy / safety
- API key and backup password stored **scrambled** in Sapphire's credentials (`~/.config/sapphire` on Linux), never inside a backup.
- Symlinks are skipped (no path leak); the blob is **sha256-verified** on download before decrypt.
- Every upload is preceded by a **ciphertext check** (must not open as tar, must carry the encrypted-backup header) — a plaintext slip blocks the upload and logs CRITICAL.
- **Permadeath:** lose the backup password and the offsite backups are unrecoverable. That's the cost of zero-knowledge.

Server contract: `INTEGRATION.md` in the Remembrance server repo.
