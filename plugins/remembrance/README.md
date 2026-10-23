# Remembrance — offsite encrypted backups

Ships an encrypted backup of `user/` to the zero-knowledge **Remembrance vault**.
Encryption lives in this plugin, not in core: local backups are plain `.tar.gz`
(they sit next to the live data — encrypting them protects nothing), and anything
that leaves the machine is encrypted with your **encryption password**, verified
to be ciphertext, then uploaded. The server stores ciphertext only and never sees
your data or the password. A backup that fails the ciphertext check is refused,
loudly, in the log.

## Setup
1. Get a tenant ID + API key from the vault owner (minted via the server's `mint.py`).
2. Settings → Plugins → Remembrance: enter server URL, tenant ID, API key → **Save** → **Test**.
3. Set your **encryption password** in the same panel. Nothing uploads without it.

## Three ways to back up
- **Manual:** the "Back up now (offsite)" button on the settings page.
- **AI tool:** `remembrance_backup` — no comment → vault status; with a comment → a labeled backup (kept long-term).
- **Cron:** auto-backup at a configurable hour (default just after the local backup run).

## Restore
The backups list has a ↻ per row — enter your encryption password, confirm, and Sapphire
restarts and swaps the data in (your current data is preserved as `user.old`).

## Privacy / safety
- API key and encryption password stored **scrambled** in `~/.config/sapphire`, never inside a backup.
- Symlinks are skipped (no path leak); the blob is **sha256-verified** on download before decrypt.
- Every upload is preceded by a **ciphertext check** (must not open as tar, must carry the encrypted-backup header) — a plaintext slip blocks the upload and logs CRITICAL.
- **Permadeath:** lose the encryption password and the offsite backups are unrecoverable. That's the cost of zero-knowledge.

Server contract: `INTEGRATION.md` in the Remembrance server repo.
