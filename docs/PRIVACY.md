# Privacy: the Vault and Private Chats

Sapphire has one vault, protected by one passphrase. While it's **unlocked**, private mode is armed. While it's **locked**, everything inside is sealed — encrypted on disk and invisible to the app.

Two kinds of things live in the vault: **prompts** you don't want sitting in plaintext, and **private chats** — whole conversations, encrypted message by message.

This page covers privacy at rest; for the network-egress side (proxies, what leaves the machine), see [NETWORK.md](NETWORK.md).

---

## Start here: the padlock

The 🔒 button sits in the chat sidebar, right-aligned in the row with **+** and **🗑**. It does exactly one job: it toggles the vault.

| Padlock | Meaning | Click does |
|---------|---------|-----------|
| 🔒 plain | No vault yet | Asks you to pick a passphrase, then creates and unlocks it |
| 🔒 plain | Vault exists, locked | Asks for your passphrase |
| 🔓 blue | Vault unlocked — private mode armed | Locks the vault now |
| 🔒 amber | You're standing in a private chat while the vault is sealed (rare) | Unlocks so you can see it |

**There is no key recovery.** A lost passphrase is a lost vault. Write it down somewhere safe.

The padlock never changes any individual chat's privacy — see below for how a chat becomes private.

---

## Talking makes a chat private

With the vault open, the moment you send a message in an ordinary chat, that chat is marked **private**. No dialog, no extra click.

It's one-way from the padlock's side: locking the vault doesn't un-mark anything. To release a chat, open **Chat Manager** and click the **🔓** on its row.

**Never auto-marked:** game, story, and librarian chats (they belong to plugin surfaces), chats that are already private, and anything running in managed mode.

You can tell a private chat by the glow on the chat area border, the 🗝 next to its name in the chat picker, and its badge in Chat Manager.

---

## What "private" actually enforces

| Rule | What happens |
|------|--------------|
| **Local models only** | Auto mode only picks providers ticked *Local / private server*. Pinning a cloud provider refuses the turn with a message. |
| **Local tools only** | Any tool that reaches the network refuses and tells the AI why. Tools that declare no locality at all (many older plugins predate the flag) are blocked too, fail-closed — if your unflagged plugins only talk to local hardware, **Settings > Privacy > "allow tools with no locality flag"** lets them run; explicitly network tools stay blocked regardless. |
| **Voice stays local** | Cloud STT and TTS refuse — you get a short notice instead of speech. |
| **Encrypted at rest** | Messages, chat settings, tool images, and plugin data are stored as AES-256-GCM values (`@enc1:` prefixed) under the vault's key. |
| **Invisible when sealed** | Lock the vault and the chat is gone from the picker, Chat Manager, chat search, and the API — it answers exactly like a chat that never existed. |

---

## Locking

The vault locks when you click the blue open padlock, press **Lock now** in Settings → Privacy, or when it goes idle. Idle timeout is `VAULT_IDLE_MINUTES` (default 30, minimum 1); using the app resets the clock, and changing the setting applies to the running timer immediately.

If you're sitting in a private chat when it locks, Sapphire moves you out first — to `default`, or the freshest ordinary chat, or a blank scratch chat if nothing else is safe. Your private chat isn't changed, just hidden until you unlock.

---

## Prompts in the vault

Vault membership *is* the privacy flag for prompts. There's no separate "private only" checkbox any more.

- While the vault is unlocked, the prompt editor shows **🗝 Keep in vault**. Tick it to encrypt that prompt; new prompts you create while unlocked go straight into the vault.
- Un-ticking it writes the prompt back out as plaintext, so it asks first.
- Vault prompts (and any assembled prompt that uses a vault piece) refuse to export — content doesn't leave the vault.
- Lock the vault and vault prompts vanish from the selector. If one was active, the active prompt falls back to `default`.
- Older prompts flagged before the vault existed still show a 🔒 and still refuse cloud providers. Move them into the vault when convenient.

See [PROMPTS.md](PROMPTS.md) for prompt authoring itself.

---

## Chat Manager

Chat Manager gains a **🗝 Private** tab while the vault is unlocked (it hides when there's nothing in it, and while the vault is sealed there is nothing to show). Private chats can also be archived — Archive wins the tab, and the row keeps its 🗝 badge.

Per-row actions while unlocked:

- **🗝** — mark a chat private. Its whole history encrypts on the spot, so you can retro-protect an old conversation.
- **🔓** — release it: everything decrypts back to normal storage.
- **⬇️** — export one chat. This writes a plaintext file on purpose; it's the deliberate escape hatch, and it only works while the vault is unlocked.

Compressing a private chat needs a provider marked local, and the "back up full JSON first" step is forced off — nothing readable leaves the database. If the vault locks while a compress or trim dialog is open, the dialog says so instead of failing with a confusing error.

---

## Games and stories

Story journals, game saves, and rendered story prompts live in a chat-scoped store inside the chat database rather than in loose files. They travel with their chat: renamed with it, encrypted with it, and **deleted with it**. Deleting a chat deletes that playthrough for good.

---

## Scheduled tasks and daemons

A task can target a chat by name. While the vault is sealed, a task pointing at a private chat shows **🔒 locked vault** instead of the name; saving that task keeps the real target intact. A task that fires at a sealed chat fails loudly rather than quietly creating a plaintext chat with the same name.

---

## Backups

Backups include the chat database and the vault file (`user/prompts/prompt_vault.enc`), so a restored backup keeps working — but restore **both together**. The vault file holds the key that the encrypted chat rows were written with; pairing an old vault file with a newer chat database makes those rows unreadable, and Sapphire refuses to mint a replacement key rather than orphan them silently.

**A gap worth knowing:** backups taken *before* a chat went private still contain that chat's earlier history in plaintext. Marking a chat private encrypts it from that moment on; nothing can rewrite copies that already left. Prune old backups if that matters to you.

See [BACKUPS.md](BACKUPS.md).

---

## Plugins

On a private-chat turn, core **withholds every hook** from plugins that haven't declared `"privacy_aware": true` in their manifest — fail-closed, so a plugin that mirrors conversation somewhere can't do it behind a locked vault. Housekeeping hooks (rename, delete) always deliver so plugin-side data never strands under a stale name.

Plugin authors: see [Private chats & `privacy_aware`](plugin-author/hooks.md#private-chats--privacy_aware).

---

## Honest limits

- **No vault means no encryption.** If you never create a vault, a chat marked private still refuses cloud models and network tools — but it isn't encrypted and it doesn't hide. Create the vault first. (Chats marked private before you had a vault are swept in automatically at the next unlock.)
- **Chat names are plaintext at rest** in the database. Content, settings, and images encrypt; the name does not. Keep names neutral.
- **Nothing decrypts while locked** — not the app, not a tool, not you. That's the point, and it's also why there's no recovery.
- **Old backups and old exports** made while a chat was public stay plaintext.
- **Managed mode** (hosted/Docker-managed installs) disables the vault and private chats entirely.
- A corrupted vault file is quarantined beside itself as `prompt_vault.enc.bad-<timestamp>` rather than deleted — restore from a backup.

---

## Reference for AI

One vault, one passphrase (`user/prompts/prompt_vault.enc`, scrypt + AES-256-GCM). Unlocked = private mode armed. Locked = sealed and invisible.

VAULT LIFECYCLE:
- Padlock (chat sidebar; 🔒 sealed / 🔓 open, blue while armed, amber when standing in a private chat while sealed) = vault toggle only: no vault → setup, locked → unlock, unlocked → lock
- Settings → Privacy: create / unlock / lock now / change passphrase; `VAULT_IDLE_MINUTES` (default 30, min 1)
- Idle timer auto-locks; user activity resets it; rekey preserves lock state; no key recovery
- Endpoints: POST /api/vault/setup | /unlock | /lock | /rekey | /move; state rides `vault` on GET /api/status
- Disabled in managed mode

PRIVATE CHATS:
- Talking in a chat while the vault is UNLOCKED marks it `private_chat` automatically (one-way)
- Skipped: mode-tagged chats (game/story/librarian/limbo), already-private, managed mode
- Release only via Chat Manager 🔓 (decrypts back to plaintext storage)
- Enforcement: local-only LLM providers, network tools refuse, cloud STT/TTS refuse
- At rest: message rows, chat settings, tool images, and plugin chat data stored as `@enc1:` AES-256-GCM under a chat data key wrapped inside the vault frame
- Chat NAMES stay plaintext at rest
- NO vault = pre-vault meaning: local-only enforcement, no encryption, no hiding. Legacy private chats are encrypted by a sweep at the next unlock

WHILE SEALED (vault exists + locked):
- Private chats absent from chat list, chat picker, Chat Manager, chat search, and by-name API reads (404, same as nonexistent)
- Active private chat is evicted at lock and at boot → `default` → freshest non-private, non-mode chat → blank scratch chat
- Single-chat export 404s; task `chat_target` masks to `__locked__` (saving keeps the real target); cron at a sealed chat fails loudly, never auto-creates
- Plugin hooks withheld unless the plugin manifest declares `privacy_aware: true` (always-deliver: chat_renamed, chat_deleted, plugins_ready, provider_switched, on_wake)

EXPORT / COMPRESS:
- Bulk export and export-zip skip private chats entirely
- Per-chat export works only while unlocked and is a deliberate plaintext copy
- Compress requires a local provider; the plaintext JSON backup step is forced off

PROMPTS:
- Vault membership is the privacy flag; new prompts saved while unlocked go into the vault
- Vault prompts and prompts using vault pieces refuse export; locked vault hides them and resets the active prompt to `default`
- Legacy `privacy_required` prompts still refuse cloud providers until moved into the vault

BACKUPS:
- Chat DB and vault file are both backed up — restore them together (the vault holds the chat data key)
- Backups made before a chat went private still hold its earlier plaintext history
