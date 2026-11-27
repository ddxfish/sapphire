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

**There is no key recovery.** A lost passphrase is a lost vault. Write it down somewhere safe. If it's gone, Settings → Privacy → **Delete vault** starts over. That deletes every private chat with it.

The padlock never changes any individual chat's privacy — see below for how a chat becomes private.

---

## You choose which chats are private

Nothing turns private on its own. When the vault opens while you're in an ordinary chat, or you send a message in one while the vault is open, a picker asks where to talk:

- **Go to a private chat** you already have
- **Create a new private chat**
- **Make this chat private** (its whole history encrypts on the spot)
- **Cancel**

None of these sends anything. Your text stays in the box and follows you to the chat you pick. While the vault is open, an ordinary chat never takes a message, and that includes voice. A spoken message there is refused aloud: *"Private mode is on, but this chat isn't private."* To talk in an ordinary chat, lock the vault.

To release a chat, open **Chat Manager** and click the **🔓** on its row. Locking the vault doesn't un-mark anything.

**The picker never asks about:** game, story, and librarian chats (they belong to plugin surfaces and stay public), chat rooms (a private room turns away its guest and door seats, so that's the key icon in Chat Manager), and anything in managed mode.

You can tell a private chat by the glow on the chat area border, the 🗝 next to its name in the chat picker, and its badge in Chat Manager.

---

## What "private" actually enforces

| Rule | What happens |
|------|--------------|
| **Local models only** | Auto mode only picks providers ticked *Local / private server*. Pinning a cloud provider refuses the turn with a message. |
| **Local tools only** | Any tool that reaches the network refuses and tells the AI why — including the ones that feel personal but hand text to a service: email, phone calls, Telegram, the plugin store. Tools that declare no locality at all (many older plugins predate the flag) are blocked too, fail-closed — if your unflagged plugins only talk to local hardware, **Settings > Privacy > "allow tools with no locality flag"** lets them run; explicitly network tools stay blocked regardless. |
| **Voice stays local** | Cloud STT and TTS refuse — you get a short notice instead of speech. The check follows the chat the speech belongs to, so a background turn (a scheduled task, a voice session) is judged on *its* chat, not on whichever chat you happen to have open. The same goes for the microphone and the wake word: they are judged on the chat your words are for (a private room on screen, the chat voice lands in), and a reply is judged on the chat that wrote it. |
| **Embeddings stay local** | A memory, a note or a document saved in a private chat is not sent to an embedder off this machine or your local network, at save time or later by any background job. It is saved without a vector, and search falls back to keywords for it. A local or network embedder works as usual, and embeds those rows once you switch to one. In Mind Palace the same goes for every memory a private model wrote (see [MEMORY.md](MEMORY.md)). |
| **Devices use ordinary chats** | A satellite, a phone or a keyboard only talks in an ordinary chat. Pointed at a private chat (or following the open chat while that one is private) it is refused, and says to pick another chat in Devices. One set to an ordinary chat keeps working while the vault is open: you can sit in a private chat and still talk to her in her main chat through the speaker. A device's setting that names a private chat is kept, and while the vault is locked the Devices page shows **🔒 locked vault** there instead of the name. |
| **Encrypted at rest** | Messages, chat settings, tool images, and plugin data are stored as AES-256-GCM values (`@enc1:` prefixed) under the vault's key. |
| **Invisible when sealed** | Lock the vault and the chat is gone from the picker, Chat Manager, chat search, and the API — it answers exactly like a chat that never existed. |

---

## Locking

The vault locks when you click the blue open padlock, press **Lock now** in Settings → Privacy, or when it goes idle. Idle timeout is `VAULT_IDLE_MINUTES` (default 30, minimum 1); using the app resets the clock, and changing the setting applies to the running timer immediately.

If you're sitting in a private chat when it locks, Sapphire moves you out first — to `default`, or the freshest ordinary chat, or a blank scratch chat if nothing else is safe. Your private chat isn't changed, just hidden until you unlock.

**A reply in progress in a private chat:**

- **You lock it** (the padlock, **Lock now**, or deleting the vault): the vault does not stay open. Every private reply in progress is stopped, the same as pressing Stop: what she had written so far is saved, the rest is cut. That includes ones you can't see, such as a task running on a private chat or a seat in a private room. The vault locks once they have stopped, usually within a second or two, and after 15 seconds regardless.
- **The idle timer locks it:** it waits for a private reply in progress to finish, then locks. If that reply runs for another whole idle period, it is stopped.

Talking to her resets the idle clock from anywhere: typing, the wake word, a satellite, a phone call. Her own activity does not: a scheduled task on a private chat won't keep the vault open, and after the idle lock that task fails with an error until you unlock.

---

## Prompts in the vault

Vault membership *is* the privacy flag for prompts. There's no separate "private only" checkbox any more.

- While the vault is unlocked, the prompt editor shows **🗝 Keep in vault**. Tick it to encrypt that prompt; new prompts you create in the editor while unlocked go straight into the vault.
- Un-ticking it writes the prompt back out as plaintext, so it asks first.
- **Unlocked is not the same as visible.** Vault prompts and pieces are read only by private chats and by you in the editor. Every other turn sees what it would see with the vault locked: a scheduled task, a phone call, a satellite, a room seat or a game seat on an ordinary chat, and anything connected over MCP.
  - An ordinary prompt that wears a vault piece (a vault mood on your everyday persona) runs in an ordinary chat **without that piece**. Nothing is refused and nothing warns; the piece is simply absent there, and present again in a private chat.
  - An ordinary chat set to a whole vault prompt is still refused, as before.
  - The AI's prompt tools follow the same rule. In an ordinary chat she can't list, view, wear, edit or delete vault pieces or prompts, and what she creates there is saved as plaintext. If she picks a name the vault already holds, she is told the name is taken.
  - **In a private chat she can't rewrite your everyday pieces.** A piece or prompt that is not in the vault is shared with every ordinary chat, so its text can't be changed from a private chat: she is told to create a new one, which is saved in the vault. She can still choose which pieces a prompt wears, and she can edit anything already in the vault. You can edit everything in the prompt editor, as always.
- Vault prompts (and any assembled prompt that uses a vault piece) refuse to export — content doesn't leave the vault.
- Lock the vault and vault prompts vanish from the selector. If one was active, the active prompt falls back to `default`.
- A chat that still points at a sealed prompt shows **🗝 vault prompt (locked)** where the name would be — chat sidebar, persona editor, trigger editors. The name is never rendered while sealed, and the chat heals back to the real prompt on unlock.
- While it's sealed, that name can't be written onto *any* chat: the save is refused with *"That prompt is asleep in the locked vault — unlock it, or pick another prompt."* This is what stops a private prompt's name from being copied onto a public chat when the vault locks under you.
- Older prompts flagged before the vault existed still show a 🔒 and still refuse cloud providers. Move them into the vault when convenient.
- **A piece's name is kept for it while the vault is locked.** Creating, renaming, importing or restoring a piece under a name the locked vault already uses is refused: *"…belongs to a piece asleep in the locked vault — unlock it or pick another name."* Otherwise the new plaintext piece would hide the vault's own after you unlock. A factory reset leaves those names alone for the same reason. (This starts working after the first unlock on this version.)
- **Moving something into the vault never replaces what is there.** If the vault already holds an item with that name, the move is refused and both copies stay as they are; rename or delete one first.

**When the vault locks under you**

- A message you were writing in a private chat is cleared from the box. If it is sent anyway, the server refuses it: words written in a private chat are not sent into an ordinary one.
- The chat sidebar repaints for the chat you were moved to, even with the cursor still in Custom Context. A change made while the sidebar still shows another chat's values is not saved.
- **New…** persona from a private chat leaves that chat's Custom Context out, and says so. Personas are one unencrypted file shown in every chat; paste the text into the persona yourself if you want it there.

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

Local backups are plain `.tar.gz` by default. Turn on `BACKUPS_ENCRYPT_LOCAL` (Settings > Backup) and each one is sealed as a `.sapphirebak` with your backup password — lose that password and those backups are gone. Backups that go to another machine (a satellite's card, an SSH host, the offsite vault) are always encrypted. A plain folder on this computer set up as a backup device (a USB stick, a second disk) can be switched to unencrypted copies; see [BACKUPS.md](BACKUPS.md).

**A gap worth knowing:** backups taken *before* a chat went private still contain that chat's earlier history in plaintext. Marking a chat private encrypts it from that moment on; nothing can rewrite copies that already left. Prune old backups if that matters to you.

See [BACKUPS.md](BACKUPS.md).

---

## Plugins

On a private-chat turn, core **withholds every hook** from plugins that haven't declared `"privacy_aware": true` in their manifest — fail-closed, so a plugin that mirrors conversation somewhere can't do it behind a locked vault. Housekeeping hooks (rename, delete) always deliver so plugin-side data never strands under a stale name.

Plugin authors: see [Private chats & `privacy_aware`](plugin-author/hooks.md#private-chats--privacy_aware).

---

## Other doors

- **MCP server** — an `ask` or `tell` into a private chat is refused: a reply would leave over the wire. With a cloud transcriber, `listen` is refused while the open chat is private. See [MCP.md](MCP.md).
- **Notifications** — rows never carry a chat name. Anything raised from a private context is a passing toast, never a stored row ([NOTIFICATIONS.md](NOTIFICATIONS.md)).
- **Model switching** — with the privacy ratchet on (Settings > Tools > AI Self-Management, `AI_MODEL_SWITCH_RATCHET`), the AI's `switch_model` only moves down the roster (top = least private), never back up. You can still change the model yourself in the sidebar.

---

## Honest limits

- **No vault means no encryption.** If you never create a vault, a chat marked private still refuses cloud models and network tools — but it isn't encrypted and it doesn't hide. Create the vault first. (Chats marked private before you had a vault are swept in automatically at the next unlock.)
- **Documents attached to a private chat are not encrypted.** The file and its extracted text sit in plaintext on disk (`user/documents/` and the knowledge database), and they stay there while the vault is locked; attaching one says so. Their text is not sent to an embedder off this machine or your network, but the files are only as private as your disk.
- **Chat names are plaintext at rest** in the database and in the log file (`user/logs/`). Content, settings, and images encrypt; the name does not. Keep names neutral. The log never holds a private chat's words: errors and tool problems there are logged by kind and size only. Short labels can still appear (a memory's label, a knowledge tab's name).
- **Old log files may hold spoken replies.** Until late November 2026 the log recorded the first 80 characters of every sentence Sapphire spoke aloud, private chats included. Logs are kept 30 days. If you used voice in a private chat, delete the files in `user/logs/` (and clear the system journal if Sapphire runs as a service) after updating.
- **Nothing decrypts while locked** — not the app, not a tool, not you. That's the point, and it's also why there's no recovery.
- **Old backups and old exports** made while a chat was public stay plaintext.
- **Managed mode** (hosted/Docker-managed installs) disables the vault and private chats entirely.
- A corrupted vault file is quarantined beside itself as `prompt_vault.enc.bad-<timestamp>` rather than deleted — restore from a backup.
- **Web image searches run server-side.** `web_view_images` queries the search engine through the app's network facade (your SOCKS settings apply) — your browser never talks to it. The tiles you see are thumbnails the server already fetched and stored with the chat; anything that still has to load from outside (a lightbox original, a tile whose thumbnail failed) goes through `external-content.duckduckgo.com` with no referrer, so your browser never hot-links the source host either.

---

## Reference for AI

One vault, one passphrase (`user/prompts/prompt_vault.enc`, scrypt + AES-256-GCM). Unlocked = private mode armed. Locked = sealed and invisible.

VAULT LIFECYCLE:
- Padlock (chat sidebar; 🔒 sealed / 🔓 open, blue while armed, amber when standing in a private chat while sealed) = vault toggle only: no vault → setup, locked → unlock, unlocked → lock
- Settings → Privacy: create / unlock / lock now / change passphrase / delete vault (+ all private chats, typed confirm); `VAULT_IDLE_MINUTES` (default 30, min 1)
- Idle timer auto-locks; the PERSON's turns reset it (typed, wake word, satellite, phone), her own lanes (tasks, agents, cadence) do not; rekey preserves lock state; no key recovery
- A private chat's turn in flight: a clicked lock or delete-vault STOPS it (rows so far saved), then seals, 15 s at most; the idle lock waits for it, one more idle period at most
- WHAT YOU SEE if the vault locked under your turn: "[Cancelled...]" rows, and a prompt or piece you try to create answers "The vault locked during this turn, so nothing was saved." Do not retry; tell the user it needs an unlock
- A task whose messages could not be saved to its chat (locked, deleted, busy) ends as an ERROR, not a success
- Disabled in managed mode

PRIVATE CHATS:
- Never automatic: with the vault UNLOCKED, a turn typed in a public chat is REFUSED; the page offers switch / create / make private
- WHAT YOU SEE in a private chat: a refused tool answers "Tool 'X' requires network access and is blocked in this private chat. Inform the user." (or "…is blocked in this private chat" for a tool with no locality flag). Tell the user which tool and why; never retry it. Cloud models and cloud STT/TTS are refused the same way
- Gate passes: mode-tagged chats (game/story/librarian/limbo/room), already-private, managed mode
- Release only via Chat Manager 🔓 (decrypts back to plaintext storage)
- Enforcement: local-only LLM providers, network tools refuse, cloud STT/TTS refuse, a remote embedder is skipped (the memory saves unembedded, search uses keywords)
- At rest: message rows, chat settings, tool images, and plugin chat data stored as `@enc1:` AES-256-GCM under a chat data key wrapped inside the vault frame
- Chat NAMES stay plaintext at rest (database, log file)
- LOG: a private turn's words are not logged. Spoken sentences log counts only (any chat); malformed tool arguments, tool exceptions and model-server error bodies log size or kind only in a private turn (`core/log_safe.py`); the opt-in tool trace file skips private turns. Names still log: chat names, memory labels, knowledge tab names. `[TTS-STREAM]` lines carried sentence text before the 2026-11-26 fix; old logs keep it for 30 days
- Chat DOCUMENTS of a private chat: plaintext at rest (`user/documents/`, knowledge db); chunks are embedded only by a local or LAN embedder
- KEPT NEAR: a row whose text a private turn held back from a far embedder is stamped `embedding_provider = 'kept-near'` (no vector). Backfills and the re-embed job skip those rows while the embedder is far; in Mind Palace they also skip private-model rows (`wr_private`, or filed under a private entity) while `private_model_filter` is on. Such rows are found by keywords, not by meaning. A plugin's cloud embedder gets the same private-turn gate as the built-in remote ones
- DEVICES: a device's turn (voice, typed, button, a phone's document) is refused when its chat is private, open vault or locked; the refusal never names the chat. A device set to a public chat is not gated by the open vault. A `local_chat` device setting naming a sealed chat is served as `🔒 locked vault` (engine `SEALED_CHAT`); sent back on a save it keeps the stored name
- Speech gates judge the chat the words belong to: `/api/transcribe` takes `chat` (the chat on screen) and also checks the landing chat; the wake word checks the landing chat; `/api/tts` file mode takes `chat` (the chat that wrote the reply). A chat that cannot be read fails closed
- NO vault = pre-vault meaning: local-only enforcement, no encryption, no hiding. Legacy private chats are encrypted by a sweep at the next unlock

WHILE SEALED (vault exists + locked):
- Private chats absent from chat list, chat picker, Chat Manager, chat search, and by-name API reads (404, same as nonexistent)
- Active private chat is evicted at lock and at boot → `default` → freshest non-private, non-mode chat → blank scratch chat
- Single-chat export 404s; task `chat_target` masks to `__locked__` (saving keeps the real target); cron at a sealed chat fails loudly, never auto-creates
- Plugin hooks withheld unless the plugin manifest declares `privacy_aware: true` (always-deliver: chat_renamed, chat_deleted, chat_cleared, plugins_ready, provider_switched, on_wake)

EXPORT / COMPRESS:
- Bulk export and export-zip skip private chats entirely
- Per-chat export works only while unlocked and is a deliberate plaintext copy
- Compress requires a local provider; the plaintext JSON backup step is forced off

PROMPTS:
- Vault membership is the privacy flag; new prompts saved in the editor or in a private chat while unlocked go into the vault
- READER RULE: unlocked vault content is read only by a private chat's turns and by the user in the editor. Any other turn (task, call, satellite, room or game seat on a public chat, MCP) gets the locked-vault view: no vault names, no vault text
- A public chat's prompt renders without its vault pieces (no refusal, no notice); a public chat set to a whole vault prompt is refused
- Prompt tools in a public chat: vault items are not listed, viewed, worn, edited or deleted; new items save as plaintext; a name the vault holds answers "taken"
- Prompt tools in a PRIVATE chat: new items go into the vault; vault items edit normally; the TEXT of a plaintext piece or monolith is not yours to change there. WHAT YOU SEE: "'emotions/x' is a shared piece: ordinary chats use it too... Create a new piece" (or "...is an ordinary prompt... Create a new prompt with prompt_create"). Do that: create under another key, then `set` it. `set` / `remove` on any preset still work
- A turn on a chat that is not the open one: `prompt_view()` and `prompt_switch()` show that chat's own prompt, never the open chat's
- Vault prompts and prompts using vault pieces refuse export; locked vault hides them and resets the active prompt to `default`
- Legacy `privacy_required` prompts still refuse cloud providers until moved into the vault
- SEALED PIECE KEYS: `user/prompts/vault_refs.json` holds salted hashes of the vault's own piece keys (no names, no text). While locked, a NEW piece under such a key is refused in every writer: your `prompt_pieces create`, rename, import, trash restore, factory reset, first-run seeding. WHAT YOU SEE: "'emotions/x' belongs to a piece asleep in the locked vault — unlock it or pick another name". Pick another key; do not retry
- Move-in refuses when the vault already holds the name (never overwrites the vault's copy)
- A typed message carries the chat it was written in (`draft_chat`); written in a private chat and sent into a public one is refused (409 `private_draft`)
- Persona from a private chat (`/api/personas/from-chat`): `custom_context` is left out, the response lists it in `left_out`
- A sealed name never renders: chat sidebar, persona editor and trigger editors show `🗝 vault prompt (locked)` while keeping the stored value, so the chat heals on unlock. Setting a chat or persona to a sealed prompt is refused (409) without naming it

BACKUPS:
- Chat DB and vault file are both backed up — restore them together (the vault holds the chat data key)
- Backups made before a chat went private still hold its earlier plaintext history
- BACKUPS_ENCRYPT_LOCAL (default off, Settings > Backup): local backups sealed as .sapphirebak with the backup password; on + no password = no backup made. Backups sent to another machine (a satellite, an SSH host, offsite) always encrypted; a folder on this computer can opt out

OTHER DOORS:
- MCP server: ask/tell into a private chat refused ("is private; it does not take messages over MCP"); listen refused while the active chat is private and STT is cloud
- Notification rows never carry a chat name; private=True callers get a flash (toast, no stored row)
- AI_MODEL_SWITCH_RATCHET (Settings > Tools > AI Self-Management): switch_model only moves DOWN AI_MODEL_SWITCH_ROSTER (top = least private); 'auto' or off-roster current counts as the top rung; the user can still change models in the sidebar
