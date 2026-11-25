# Troubleshooting

Contents:
- [Where to Look First](#where-to-look-first)
- [Reset Procedures](#reset-procedures) — light reset, package reinstall, nuclear reset, password, delete all data
- [Startup Issues](#startup-issues)
- [Update Issues](#update-issues)
- [Web UI Issues](#web-ui-issues)
- [Audio Issues](#audio-issues)
- [Voice & Conversation Issues](#voice--conversation-issues)
- [Prompt issues](#prompt-issues)
- [Vault / private chat issues](#vault--private-chat-issues)
- [Chat Database Issues](#chat-database-issues)
- [LLM issues](#llm-issues)
- [Network / Proxy Issues](#network--proxy-issues)
- [Tool/Function Issues](#toolfunction-issues)
- [Plugin Issues](#plugin-issues)
- [Continuity Issues](#continuity-issues)
- [Home Assistant Issues](#home-assistant-issues)
- [Performance Issues](#performance-issues)
- [Boot crash: malloc() on multi-GPU Linux](#boot-crash-with-malloc-invalid-size-on-linux-multi-gpu-systems)

## Where to Look First

- **Main log**: `user/logs/sapphire.log` (rotates daily). Most problems leave a line here — search for `ERROR` or the feature name. Voice engines log apart: `user/logs/kokoro.log` (TTS), `user/logs/whisper.log` (STT). Also there: `hf_fetch.log` (model downloads), `gpu-install.log`, `extras-<name>.log` (a plugin's package install), and one log per plugin service.
- **System > Notifications**: errors land here as rows (a failed download or voice switch says why, and may offer **Try again**). A failed reply shows its error on the message itself in the chat, with a **Fix it** button that opens the setting to change (see [NOTIFICATIONS.md](NOTIFICATIONS.md)).
- **Browser console** (F12) for web UI problems — JS errors and failed requests show up there, not in the server log.
- **Running as a systemd service?** `journalctl --user -u sapphire -f`
- **Sapphire Launcher users**: the launcher's **Troubleshoot** tab checks whether Sapphire responds, verifies package versions and dependency health, scans voice features (STT/TTS/wake word), and detects your GPU — with fix buttons for common issues.
- **Settings > System** has a core install integrity check — it verifies every shipped file against the manifest and can restore modified ones (git installs).

## Reset Procedures

Ordered lightest to heaviest. Start at the top.

### Light Reset (settings + password only)

Resets all settings to defaults and clears the login password. **Still logged in and only want the settings reset?** Use **Settings > System > Danger Zone > Reset All Settings** (it asks twice) and skip the commands below: they are for when you can't get in. Which plugins are on, and each plugin's own settings, are kept either way. **Keeps everything else** — chats, memories, prompts, personas, knowledge, and your API keys (those live in a separate credentials file outside `settings.json`). The setup wizard runs again on next start.

Only want the wizard again? **Settings > System > Run Setup Wizard** — no reset needed.

Stop Sapphire first, then from the `sapphire/` folder:

**Linux / macOS:**
```bash
rm user/settings.json
rm ~/.config/sapphire/secret_key          # macOS: ~/Library/Application\ Support/Sapphire/secret_key
python main.py
```

**Windows (PowerShell):**
```powershell
Remove-Item user\settings.json
Remove-Item "$env:APPDATA\Sapphire\secret_key"
python main.py
```

**Docker** (from the folder holding `docker-compose.yml`):
```bash
docker compose down
rm sapphire-data/settings.json sapphire-config/secret_key
docker compose up -d
```

If only your prompts are broken, you don't need this — see [Prompt issues](#prompt-issues) for the prompt-only reset.

### Reinstall Packages (conda env only)

Nuke the conda environment and reinstall from scratch. App files and your `user/` data are untouched. Use this if your pip packages are messed up — it just reinstalls the packages, doesn't touch Sapphire. Three things were installed into that environment after setup and go with it, so redo them afterwards: **Install GPU torch and restart** (Windows with an NVIDIA card) and **Install Whisper GPU support** (Linux, Settings > STT), both described under [Voice runs on the CPU](#voice-runs-on-the-cpu-gpu-found-torch-cannot-use-it), and each plugin's **Install** button on its Missing strip in Settings > Plugins.

```bash
conda deactivate && conda remove -n sapphire --all -y && conda create -n sapphire python=3.11 -y && conda activate sapphire && pip install -r requirements.txt && python main.py
```

### Nuclear Reset (full reinstall, data preserved)

Full app reinstall: fresh clone, fresh conda environment. Your `user/` directory and your local backups (`user_backups/`, which sits beside `user/` inside the app folder) are **moved aside and restored** — never deleted. Stop Sapphire first, then run from the directory *containing* `sapphire/`:

**Linux/macOS:**
```bash
mv sapphire/user ~/sapphire-user-backup
[ -d sapphire/user_backups ] && mv sapphire/user_backups ~/sapphire-backups-kept
rm -rf sapphire
conda deactivate && conda remove -n sapphire --all -y
conda create -n sapphire python=3.11 -y && conda activate sapphire
git clone https://github.com/ddxfish/sapphire.git
mv ~/sapphire-user-backup sapphire/user
[ -d ~/sapphire-backups-kept ] && mv ~/sapphire-backups-kept sapphire/user_backups
cd sapphire && pip install -r requirements.txt
python main.py
```

**Windows (PowerShell):**
```powershell
Move-Item sapphire\user "$env:USERPROFILE\sapphire-user-backup"
if (Test-Path sapphire\user_backups) { Move-Item sapphire\user_backups "$env:USERPROFILE\sapphire-backups-kept" }
Remove-Item -Recurse -Force sapphire
conda deactivate; conda remove -n sapphire --all -y
conda create -n sapphire python=3.11 -y; conda activate sapphire
git clone https://github.com/ddxfish/sapphire.git
Move-Item "$env:USERPROFILE\sapphire-user-backup" sapphire\user
if (Test-Path "$env:USERPROFILE\sapphire-backups-kept") { Move-Item "$env:USERPROFILE\sapphire-backups-kept" sapphire\user_backups }
cd sapphire; pip install -r requirements.txt
python main.py
```

Notes:
- Never run the delete line without the two move lines above it: deleting the `sapphire/` folder deletes `user_backups/` with it, and that is every local backup you have. (A backup folder you set yourself in Settings > Backup is safe when it is outside the app folder; one given as a relative path lives inside it, so move that aside the same way.)
- Your password is **not** reset by this — it lives outside the app folder. Add the `secret_key` line from [Light Reset](#light-reset-settings--password-only) if you want that too.
- Want fresh data as well? That's [Reset Everything](#reset-everything-delete-data) below — don't just skip the `mv`/`Move-Item` lines, back up first.
- [Sapphire Launcher](https://github.com/ddxfish/sapphire-launcher) can do this without a terminal — its Uninstall tab removes the env or the whole install, then **Go** reinstalls.
- Docker installs: recreating the container is the equivalent — see [DOCKER.md](DOCKER.md). Your data lives in `sapphire-data/` and `sapphire-config/` beside `docker-compose.yml`.

### Reset Password

If you still know the current one, you don't need any of this: change it in **Settings › System › Login Password**. That tab stays logged in and every *other* device is logged out once — sign back in there with the new password.

Genuinely forgotten? Delete the password hash file `secret_key` and restart Sapphire. You'll set a new password on next visit; chats, settings, and user data are untouched.

| Install | File to delete |
|---|---|
| Linux | `~/.config/sapphire/secret_key` |
| macOS | `~/Library/Application Support/Sapphire/secret_key` |
| Windows | `%APPDATA%\Sapphire\secret_key` |
| Docker | `sapphire-config/secret_key` (beside `docker-compose.yml`), then `docker restart sapphire` |

### Reset Everything (Delete data)

Nuclear option - fresh start. Deletes your chats, memory, settings and password. Stop Sapphire first, then from the `sapphire/` folder delete `user/` and the `secret_key` from the table above:

**Linux / macOS:**
```bash
rm -rf user/
rm ~/.config/sapphire/secret_key          # macOS: ~/Library/Application\ Support/Sapphire/secret_key; with XDG_CONFIG_HOME set: $XDG_CONFIG_HOME/sapphire/secret_key
python main.py
```

**Windows (PowerShell):**
```powershell
Remove-Item -Recurse -Force user\
Remove-Item "$env:APPDATA\Sapphire\secret_key"
python main.py
```

**Docker:**
```bash
docker compose down
rm -rf sapphire-data/ sapphire-config/secret_key
docker compose up -d
```
Your API keys stay in `sapphire-config/`; delete the whole folder to drop them too.

**What this leaves behind.** Two things survive the commands above, on purpose, and you have to delete them yourself if you want nothing left (handing the computer to someone else, for example):

- **Your backups.** `user_backups/` in the `sapphire/` folder (Docker: `sapphire-backups/`), or the folder you chose in Settings > Backup. Each one is a full copy of your chats and memory, readable by anyone unless you turned on backup encryption.
- **Your saved keys.** The config folder that held `secret_key` (the table above) also holds `credentials.json` (API keys, account passwords, the backup password) and `api_tokens.json`. Delete the whole folder to drop them (Docker: `sapphire-config/`).

You'll need to re-run setup and reconfigure settings.

## Startup Issues

**"Your settings file (user/settings.json) can't be read"**
- The file is damaged (a hand edit with a missing comma, a full disk). Sapphire runs on default settings, refuses to save over the file, and holds internet requests until it is fixed, so a proxy setting it could not read is never skipped.
- Fix the JSON by hand (a copy of the broken file is beside it as `settings.json.corrupt`), or use Settings > System > Danger Zone > Reset All Settings. Then restart.

**"Your saved keys and passwords … can't be read" (all API keys seem gone)**
- `credentials.json` in the config folder is damaged. Sapphire refuses to save over it, so nothing more is lost.
- Repair the file by hand, or choose to start fresh from that notification: the old file is kept beside it as `credentials.json.corrupt.<date>` and you enter your keys again.

**"Connection refused", "No LLM that's turned on answered", or "No LLM is turned on, so Auto has nothing to use"**
- LLM server not running. Start LM Studio/llama-server first.
- Wrong port in settings. Default expects `http://127.0.0.1:1234/v1`
- Check LM Studio has "Start Server" enabled and "Allow LAN" if needed.
- "…answered, but has no model ready yet": the server is up with no model loaded. Load one, or set a model on its card in Settings > LLM.
- "No LLM is turned on": flip the switch on a provider's row in Settings > LLM.

**`[PLUGINS] <name> failed to load` or `BLOCKED <name>` in the log on startup**
- That one plugin did not load; the rest of Sapphire runs. `failed to load` is an error in the plugin (often a missing package: see its Missing strip in Settings > Plugins); `BLOCKED` is a signature problem. See [Plugin Issues](#plugin-issues).
- The line right after it in `user/logs/sapphire.log` holds the actual error.

**"Another Sapphire is already running on this user/" / "Port 8073 is already taken"**
- One Sapphire per `user/` folder: a second `python main.py` stops at once instead of booting a twin. The line names the holder; stop that one first (launcher Stop, Task Manager, `systemctl --user stop sapphire`).
- Port held by another app: Linux `ss -tlnp | grep 8073`, Windows `netstat -ano | findstr 8073`. Stop it, or change `WEB_UI_PORT` in `user/settings.json`.
- The runner then logs `Not restarting: …` and exits 43 (another instance or the port) or 44 (bad web host, port or certificate). It never retries those — fix the cause, then start again.

**Crash at boot with `malloc(): invalid size` (Linux, multi-GPU)**
- See the [deep dive at the bottom](#boot-crash-with-malloc-invalid-size-on-linux-multi-gpu-systems) — TTS and STT grabbing the same GPU during boot.

## Update Issues

**`git pull` fails with "your local changes would be overwritten"**
- You (or a tool) modified core files. `git status` shows what changed; `git stash` sets the changes aside, then pull again.

**Boot log shows `[INTEGRITY]` warnings / files don't match the manifest**
- Core files drifted from the shipped set (edits, a partial update, disk corruption). Settings > System → integrity check shows per-file status, and Repair (git installs) restores only the offending files.
- Repair refuses files with uncommitted work-in-progress changes on dev trees — deliberate. It fixes drift, it doesn't eat your edits.

**Updated but the UI looks broken or old**
- Hard-refresh the browser (Ctrl+Shift+R). Browsers cache the JS even when asked not to.

**Errors about missing packages after an update**
- Run `pip install -r requirements.txt` after every pull. Major version jumps may need a fresh env — see [Reinstall Packages](#reinstall-packages-conda-env-only).
- Launcher users: the **Update** button does the pull and the pip install together.

**Claude (or MCP) stopped working right after you upgraded packages by hand**
- `requirements.txt` caps `openai<3`, `anthropic<1` and `mcp<2` — majors Sapphire has not been ported to. `anthropic` 1.0 removed the temperature/top_p/top_k arguments, so every call through the Claude and Anthropic-compatible providers fails; `mcp` 2.0 dropped the HTTP client the MCP plugin uses.
- `pip install --upgrade openai anthropic mcp` steps over the caps. Put it back with `pip install -r requirements.txt` — it reinstalls inside the bounds.
- Installs you haven't touched are fine: neither the pip line above nor the in-app update upgrades a package that already satisfies the file.

## Web UI Issues

**403 Forbidden, blank page or "Unauthorized"**
- Try both http:// and https://
- Delete cookies for this site, or test in a private window
- Reset password (see [Reset Password](#reset-password) above) and restart Sapphire

**Certificate warning on first visit**
- Expected with self-signed certs. Accept once (Advanced → Proceed) and the browser remembers it.
- Cert is persistent (valid 10 years), stored in `user/ssl/`. Not regenerated on restart.
- Behind a reverse proxy that does HTTPS? Turn off `WEB_UI_SSL_ADHOC` (Settings > System, restart).

**UI loads but chat doesn't respond**
- Check browser console (F12) for errors
- Verify LLM server is responding: `curl http://127.0.0.1:1234/v1/models`

**My reply vanished when the phone locked / the tab slept**
- It didn't. Turns run on the server, so a browser losing its connection never cancels one — only Stop does.
- Wake the tab: it rejoins the live reply on its own, quietly, picking up where it stopped.
- If it can't reconnect (roughly a minute of tries) it says "Couldn't reconnect — her reply lands in the chat when she finishes". The reply still finishes on the server: reload the page and it's there.
- After a Regenerate or Continue the words are "Connection dropped — she's still working; the reply lands when she finishes": that one does not rejoin, the finished reply appears by itself.
- Audio isn't replayed for the part you missed; with TTS on the finished reply is spoken once when it arrives.

**My message sat waiting while she was still replying**
- By design: a send while a turn is live is **queued** in the chat's inbox, never refused. It runs when her reply ends; several typed sends fold into one turn. The × on a waiting bubble takes it back out.
- Want her to stop now instead? Press Stop.
- A send refused outright (HTTP 409) means the chat is sealed in a locked vault (or its inbox turned the turn away) — unlock and resend.

## Audio Issues

**Sample rate detection error on speakers**
- Cheap USB speakers may not support certain sample rates, so choose "auto detect" or "default"
- Auto detect will route through OS audio defaults, which can resample the audio to be compatible

**Default audio seems to not work via TTS**
- Settings > Audio - then change your device to auto-detect
- Selecting specific audio devices like default or a specific mic can work too
- Auto-detect sometimes fails test due to it being open, but it may actually work so try it

**Recording never stops after you speak (webcam or weak mic)**
- Voice activity detection hears your background noise as speech, so it keeps recording.
- Silero VAD (the default): Settings > STT > Voice Activity Detection > Silero VAD. Press **Test my voice (5s)** to see what your speech scores, then raise `STT_VAD_SPEECH_THRESHOLD` (0.6-0.7) if noise is counted as speech.
- Classic VAD (only when Silero is off or unavailable): raise Recorder Background Percentile — headsets ~10-20, webcam mics ~40.

**No TTS audio output**
- Verify TTS is enabled in Settings > TTS
- **Play speech here** (Settings > TTS) off sends her voice elsewhere: speech is made, nothing plays on this computer or in the browser
- Check the TTS server started: `user/logs/kokoro.log`
- Test system audio: `aplay /usr/share/sounds/alsa/Front_Center.wav`
- Check PulseAudio/PipeWire is running

**Boot toast says "Local audio unavailable" / Settings > Audio shows no devices (headless box, VM, server)**
- PortAudio needs a running PulseAudio/PipeWire server, and Ubuntu 26.04's PortAudio refuses to start without one
- Sapphire still boots and browser voice works. This only affects a speaker/mic plugged into the Sapphire machine itself
- To give her local audio: `sudo apt install pipewire-audio`, then `systemctl --user enable --now pipewire.socket pipewire-pulse.socket wireplumber.service`
- Add your user to the `audio` group (`sudo usermod -aG audio $USER`, then log out and in). A lingering session with no console login has no device access otherwise
- No restart needed once the server is up: open Settings > Audio and the devices re-detect
- Bluetooth speaker on a headless box: WirePlumber only enables Bluetooth for an active seat by default. Put `wireplumber.profiles = { main = { monitor.bluez.seat-monitoring = disabled } }` in a file under `~/.config/wireplumber/wireplumber.conf.d/` and restart wireplumber

**STT not transcribing**
- Check STT is enabled in Settings > STT
- For GPU: verify CUDA is working (`nvidia-smi`)
- Try CPU mode: set faster whisper device to cpu in settings
- Turn up your mic volume to 70% or 100%
- Check your OS/system default mic - it tries to read from this
- If Web UI, run **Settings > Audio > Browser Mic > Test** (Settings > STT on hosted installs). It checks permissions, shows a live level, lets you pick the mic, and **Say something** reads your words back
- A site you blocked can't ask again: allow the mic from the icon left of the address. On Windows also check Settings > Privacy & security > Microphone, including access for desktop apps

**Wake word not triggering**
- Check which wakeword you are using in Settings > Wakeword, and that wake word is enabled (model changes apply on save, no restart)
- Minimal install only: make sure you pip installed install/requirements-wakeword.txt (a full install already has it)
- Turn your mic volume up to 70-100%, and set the system mic to the one you want wakeword on
- Try Hey Mycroft instead of Hey Sapphire, or lower the sensitivity threshold to 0.5
- Test a different mic
- First time you pick a new built-in (`hey_mycroft`, `hey_jarvis`, `alexa`…), openWakeWord downloads it — give it a few seconds

**Adding or making a custom wake word**
- Upload it in **Settings > Wakeword > Library**. This computer uses openWakeWord `.onnx` models; a `.tflite` is a microWakeWord model for an ESP32 satellite and is never offered to this computer.
- Make your own from a phrase in **Apps > Wakeword Maker** (a plugin, off by default — enable it in Settings > Plugins). It builds models for this computer and for ESP32 boards. See [WAKEWORD-MAKER.md](WAKEWORD-MAKER.md).
- Built-in openWakeWord set: `alexa`, `hey_mycroft`, `hey_jarvis`, `hey_rhasspy`, `timer`, `weather`. Sapphire ships with `hey_sapphire`.

## Voice & Conversation Issues

Full voice triage lives in VOICE.md's Quick Troubleshooting. The two hottest ones:

**She hears herself / echoes in conversation mode**
- Use headphones, or let the duplex echo-cancel tier engage (see VOICE.md — echo tiers)

**Conversation mode connects but she is silent**
- With streaming off, or a voice that can't stream (ElevenLabs, gTTS), she speaks once the whole reply is written, so a long reply is a long quiet wait. For speech that starts sooner: Settings > TTS > Streaming on, with Kokoro or Piper.
- No voice at all: check that a voice is picked in Settings > TTS, then `user/logs/kokoro.log` (or the plugin voice's own error in the notification center).

**No voice, and the notification center says "Hugging Face offline mode is on"**
- `HF_HUB_OFFLINE` (or `TRANSFORMERS_OFFLINE`) is set in your environment. Hugging Face then refuses every download, so a voice or model Sapphire has not fetched yet cannot arrive. Guides for other AI tools often say to set it for a faster start; Sapphire never sets it.
- Sapphire already loads cached models with no network and never phones home (see NETWORK.md). The variable only blocks the first download: clear it, let the download land, put it back if you want it.
- Windows (PowerShell), then close Sapphire fully and start it again:
  ```
  [Environment]::SetEnvironmentVariable("HF_HUB_OFFLINE", $null, "User")
  [Environment]::SetEnvironmentVariable("TRANSFORMERS_OFFLINE", $null, "User")
  ```
- Linux / macOS: look for `export HF_HUB_OFFLINE=1` in `~/.bashrc`, `~/.profile`, `~/.zshrc` or the service unit that starts Sapphire; remove it and restart.
- The same words appear in `user/logs/kokoro.log` and under the TTS tab's Test button. The variable is read once at start, so a restart is needed either way.

**"Switching TTS to … failed" (or STT, embedding, wake word) in the notification center**
- Changing a voice, ears or embedding provider runs in the background under its own notification. When it fails the row says why and ends "Kept <the old one>.": the provider you had is back and working. "Still off." means there was no working one to go back to.
- A failed model download offers **Try again** on that row; it keeps your pick.
- Picking a second, different provider while one is still switching is refused: wait for the first to finish.

**A model download says "not moving", or fails and names your DNS**
- A download with no bytes for 20-30 seconds turns yellow and says so. For Whisper, Kokoro and the embedding model Sapphire then tries Hugging Face's other download path by itself; if both fail the row says why. The Whisper and Kokoro rows offer **Try again**; the embedding model has no button and is tried again the next time it is needed.
- "This network's DNS is giving out-of-date addresses": your router (or whatever answers DNS on your network) is handing out old addresses for the download server. Restart the router, or set this computer's DNS to a public one such as `1.1.1.1` or `9.9.9.9`, then press Try again.
- "not answering from any address": the trouble is on their side; try again later.
- "This network's DNS has no answer for …, though a public DNS server does": same cure as the out-of-date addresses above.
- "This computer does not seem to be online": nothing outside answers at all; check the cable, Wi-Fi or VPN.
- "…is not answering, and neither is a public DNS server": the connection is down or filtered (a captive portal, a firewall, security software that inspects encrypted traffic).
- The download's own log is `user/logs/hf_fetch.log`.
- "This computer's date or time looks wrong": fix the clock (a virtual machine set to the wrong time zone does this), then Try again.
- The detail of each attempt is in `user/logs/hf_fetch.log`. What this check contacts, and when it stays silent, is in NETWORK.md under "What never phones home".

**Phone call problems** — see PHONE-CALLS.md's Quick Troubleshooting (403 on register, one-way audio, hangup issues).

## Prompt issues
**If you broke your default prompts**
- Settings > System tab
- **Settings > System > Danger Zone** has two buttons: **Import App Updates** adds the shipped prompts you don't have and changes none of yours; **Reset Prompts to Defaults** replaces yours with the shipped set

## Vault / private chat issues

**A chat vanished from the dropdown**
- The vault locked (idle timeout, or someone clicked the padlock). Private chats are hidden while sealed — that's the design, nothing is lost.
- Click the 🔒 padlock at the top of the chat sidebar and unlock. Everything comes back.
- Raise the timeout in Settings > Privacy (`VAULT_IDLE_MINUTES`, default 30).

**"Private mode is on, but this chat isn't private"**
- The vault is open and you're in an ordinary chat. Nothing was sent. Pick a private chat, create one, or make this one private in the picker.
- To talk in an ordinary chat, lock the vault (the 🔓 button).

**I'm stuck in a blank "New Chat" and can't get back to default**
- Your chats (default included) are private and the vault is locked. Unlock it with the 🔒 button and they all come back.
- To release a private chat: Chat Manager > its row > 🔓. It decrypts back to normal storage.

**"<Provider> isn't marked "Local / private server", so it's blocked in this private chat"**
- The chat is private, so only providers ticked *Local / private server* are allowed.
- Tick that box on the provider in Settings > LLM if it really runs on your own machine or network, pick a local provider, or release the chat in Chat Manager.
- Same rule applies to compressing a private chat, and to cloud STT/TTS (voice goes quiet with a notice instead of speaking).

**Export or compress fails on a private chat**
- Bulk export and export-zip skip private chats on purpose. Use the row's ⬇️ to export one at a time.
- Both need the vault unlocked. If it locked while the dialog was open, unlock and retry.

**Forgot the vault passphrase**
- There is no recovery. The key is derived from the passphrase alone.
- Restore `user/prompts/prompt_vault.enc`, `user/prompts/vault_refs.json` **and** `user/history/sapphire_history.db` together from a backup taken when you still knew it. Know the cost first: that chat database holds every chat, so your public chats go back to that backup's date too.
- Or start over: Settings → Privacy → **Delete vault** (asks twice). It deletes the vault and every private chat in it; public chats are untouched. A private Default comes back as an empty public Default.

**Default (or any chat) is stuck private**
- Know the passphrase: unlock with the padlock, then Chat → Manage → 🔓 on the chat's row.
- Forgot it: see above. Delete vault is the only way out.

**"Vault file was corrupt — quarantined"**
- The bad file is renamed `prompt_vault.enc.bad-<timestamp>` beside itself, never deleted.
- Restore the vault file from a backup, paired with `vault_refs.json` and the chat database from the same backup (this rolls every chat back to that backup's date).

## Chat Database Issues

**A chat shows "⚠ degraded" and became read-only**
- Some of its messages could not be read (corruption, or a vault key mismatch). The chat latches read-only so nothing else gets damaged.
- Chat Manager → the chat's row → 🔧 Repair. Unreadable messages move to a quarantine (kept verbatim — recoverable if, say, the matching vault backup returns) and the rest of the chat becomes writable again.
- The repair preview shows how many messages it keeps vs. quarantines before you commit.

**Database acting strange / checking health**
- Sapphire backs up your data on a schedule — see [BACKUPS.md](BACKUPS.md) for what's covered and how to restore.
- Manual integrity check (needs the sqlite3 CLI, Sapphire stopped): `sqlite3 user/history/sapphire_history.db "PRAGMA quick_check;"` — should print `ok`.

## LLM issues

**A reply failed with an error**
- The failed row says what went wrong in plain words and offers **Fix it**, which opens the setting to change (Settings > LLM, the provider, Context limit, timeout). Details under it hold the raw error. See [NOTIFICATIONS.md](NOTIFICATIONS.md).

**LM Studio (simple) test failing**
- Open LM studio, click Developer in lower left to show advanced options, click green Developer tab, toggle server on, load a model
- Go back to Sapphire: Settings > LLM > LM Studio > test button

**Anthropic Claude not responding**
- conda activate sapphire && pip install -r requirements.txt (never a bare `pip install anthropic`: that pulls 1.x, which breaks every Claude call, see [Update Issues](#update-issues))
- Check API key (some are for Claude Code only)
- Put new API key in Settings > LLM > Claude

**No thinking/reasoning visible**
- Not all models support thinking. Check provider supports it.
- Claude: **Adaptive Thinking** on its card in Settings > LLM (on by default), with an Effort picker
- GPT-5.x: Uses Responses API, set reasoning_summary to "detailed"
- Gemini: Set reasoning_effort (low/medium/high) on thinking-enabled models (thinking is on by default)
- Local models via LM Studio: May need specific model that outputs `<think>` tags

**Claude prompt caching not working (always MISS)**
- Spice and datetime are NOT the cause — both ride the ghost rail and are cache-safe (see GHOST_MESSAGES.md)
- Real cache breakers: switching prompts/personas mid-conversation, editing prompt pieces, anything that rewrites the actual system prompt between turns
- Check logs for `[CACHE] Dynamic content detected - tools only, system prompt not cached`

**Claude caching enabled but costs seem high**
- First request is always a MISS (writing to cache costs 25% more)
- Cache expires after TTL (5m default, can set to 1h)
- If prompts change often, cache never gets reused

## Network / Proxy Issues

Full proxy triage lives in NETWORK.md's Quick Troubleshooting. Fail-closed reminder: with SOCKS on, a dead proxy means loud errors everywhere — nothing silently falls back to direct.

**Everything network fails right after enabling SOCKS** — proxy down or wrong host/port (see NETWORK.md)
**Every request dies "host unreachable" with SOCKS on** — your proxy has no server-side DNS; turn off "DNS via proxy" (NETWORK.md)
**LLM works, web tools fail (or vice versa)** — the LLM lane is exempt by default; check the "What rides the proxy" strip in Settings > Network

## Tool/Function Issues

**"No executor found for function"**
- Function exists in toolset but Python file missing or has errors
- Check `functions/` (core tools) or the owning plugin's directory for the module
- Look for import errors in logs

**Web search returns no results**
- Rate limited by DuckDuckGo. Wait and retry.
- If using the SOCKS proxy, verify it's working (see NETWORK.md)
- Set **Log Level** to DEBUG in Settings > System (advanced settings) for more logging (`LOG_LEVEL`)

**An MCP client gets 404 "The MCP server is off"**
- The MCP door is off by default. Turn it on in Settings > MCP Server and tick the tools to share. See [MCP.md](MCP.md).
- An `ask` into a private chat is refused on purpose: private words never leave over MCP.

## Plugin Issues

**Plugin is enabled but its tools/features are missing**
- Almost always a signature failure — an unsigned plugin is blocked unless you allow unsigned plugins, and a plugin whose `plugin.sig` matches no known key is always blocked. Sapphire still starts fine, so it looks silent.
- Check the boot log for `[PLUGINS] Enabled but blocked (intent preserved, retry next restart): [...]` — blocked plugins are listed there, and each verified plugin logs `signature verified`.
- Editing a signed plugin's files breaks its signature, so it is blocked. Delete its `plugin.sig` (it becomes unsigned) and flip the switch on the **Settings > Plugins** banner to "Unsigned plugins ALLOWED" (`ALLOW_UNSIGNED_PLUGINS`) — dev machines only. Signing with a key you made yourself does not help: that is still blocked. See [SIGNING.md](SIGNING.md).

**Plugin acting stale after an update**
- Reload it from its card's ⋮ menu in Settings > Plugins (or restart Sapphire), then hard-refresh the browser (Ctrl+Shift+R) if the plugin has UI.

**Settings > Plugins shows a "Missing: …" strip on a plugin**
- That plugin needs extra pip packages. Click **Install** — Sapphire installs them into its own conda environment or venv and reloads the plugin, no restart needed.
- Running on plain system Python (Docker, or a distro Python)? Auto-install is refused on purpose; the strip shows the exact command to run yourself. It's quoted so it pastes into PowerShell, cmd or bash unchanged.
- **Discord** needs **Git on your PATH** — its `py-cord` dependency installs from a pinned git commit. Windows: `winget install Git.Git`, then reopen the terminal. If `discord.py` is installed, `pip uninstall discord.py` first: it owns the same `discord` import name and conflicts.

## Continuity Issues

**"Scheduled tasks can't be read"**
- The tasks file is damaged, so none of your scheduled tasks run until it is fixed. The notification names the file and the error; repair it (or restore it from a backup) and restart.

**Task not running at scheduled time**
- Check its enabled toggle in Triggers > Scheduled (or Triggers > Heartbeat)
- Verify cron syntax is correct (minute hour day month weekday)
- A chance below 100% may have rolled against it — the log says `failed chance roll`

**Tasks fire at the wrong hour / her clock is off (often Windows)**
- Timezone is auto-detected at first start. Linux/macOS read `/etc/localtime`; Windows needs the `tzlocal` package (it's in `requirements.txt` and the minimal set). Without it the log warns and `USER_TIMEZONE` stays UTC — schedules and the clock in her prompt run in UTC.
- Fix: pick your timezone in the setup wizard's You step, or set `USER_TIMEZONE` in `user/settings.json`, then restart. `pip install tzlocal` makes detection work next boot.
- Docker: set `TZ` in `docker-compose.yml`.

**"Invalid cron schedule" error**
- Cron format: `minute hour day month weekday`
- Use `*` for any, `*/N` for every N, `1-5` for ranges
- Example: `0 9 * * *` = 9:00 AM daily
- Weekday: 0 or 7 = Sunday, 1-6 = Mon-Sat

**Task runs but no TTS audio**
- In the task editor, tick "Speak on server speakers" (or "Play in browser" with a tab open)
- Verify TTS is working for regular chat first
- A private chat or a privacy-required task skips browser TTS on purpose

**croniter not installed**
- Run: `pip install croniter` — scheduled tasks need it for cron parsing

## Home Assistant Issues

**Connection test failing**
- Verify URL includes port (e.g., `http://192.168.1.50:8123`)
- Check Home Assistant is running and accessible from this machine
- Try the URL in a browser first

**"401 Unauthorized" error, or a "too short" token warning**
- Token is invalid, expired, or cut off (HA tokens are ~180+ characters)
- Create a new Long-Lived Access Token in your HA profile and copy the whole string

**Entity not found**
- Check exact spelling (entity_id or friendly name)
- Entity may be blacklisted - check blacklist patterns
- Use `ha_list_lights_and_switches` to see available entities

**Notifications not sending**
- Find your service in HA: Developer Tools → Actions → search "notify"
- Enter just the service name without "notify." prefix
- Example: `mobile_app_pixel_7` not `notify.mobile_app_pixel_7`
- Make sure HA companion app is installed on your phone

**HA tools not available**
- Add Home Assistant functions to your active toolset
- Check Settings → Plugins → Home Assistant is configured
- Test connection before trying to use tools

## Performance Issues

**Slow responses**
- LLM is the bottleneck. Use a 4B or smaller model to test
- Lower the **Context limit** (`CONTEXT_LIMIT`) to send less, or set `LLM_MAX_HISTORY` to a message count (0, the default, means no message cap): a long chat gets slower over time
- Kokoro on an older laptop CPU is slow; an NVIDIA card or a faster CPU helps a lot

**High memory usage**
- Large LLM models need RAM. 4B model needs ~7GB after KV cache.
- Use quantized models in Q4_K_M to reduce memory
- STT with base Whisper models uses ~2-3GB.
- TTS (Kokoro) uses ~2-3GB. Its process restarts itself (once quiet) when it grows 2 GB past its warm size or after 500 requests — a leak guard, not a crash.

### Voice runs on the CPU (GPU found, torch cannot use it)

The Status page shows `[CPU]` with a warning mark next to TTS or STT while `cuda` is selected,
`user/logs/kokoro.log` says `torch.cuda.is_available()=False`, and the Status page or the TTS/STT
settings tab says why. The card is almost never the problem:

- **Windows:** the default `torch` from PyPI has no CUDA at all, for any card. The top of the TTS and STT
  settings tabs offers **Install GPU torch and restart**, which picks the right wheel for your card (a 2-3 GB download;
  it runs on the restart, before Sapphire boots, and the console or launcher pane shows pip's progress).
  A notification says the card is idle on every start while Kokoro or Whisper is local and set to `cuda`. Set that
  voice device to `cpu` if you want the CPU, and the reminder stops.
- **"Torch will not load, so local voice is down":** an install was interrupted or a DLL is missing. On Windows with an
  NVIDIA card the same button repairs it. A failed install never takes voice down by itself: pip puts the old torch back.
- **"Whisper is running on the CPU" (Linux):** Whisper runs in its own process and needs CUDA 12's cuBLAS, which current
  PyPI torch no longer brings. Settings > STT offers **Install Whisper GPU support** (550 MB), and Whisper moves to the card
  when it lands, no restart. By hand: `pip install -r install/requirements-whisper-gpu.txt`. Its log is `user/logs/whisper.log`.
- **Kokoro or Whisper fell back mid-session:** when the card fails while speaking or listening (out of memory, a lost
  device), that engine moves to the CPU until the next start. The Status page shows `[CPU]` with a warning mark.
  By hand, inside the sapphire env:
  ```
  pip install torch --index-url https://download.pytorch.org/whl/cu128
  ```
  GTX 10-series and 900-series cards (Pascal, Maxwell) and Volta cards like the Titan V are not in the cu128 build:
  use `cu126` in the URL instead. Cards older than Maxwell (GTX 700 and earlier) are not in any current build.
- **"is not in this torch build":** the build predates your card (RTX 50-series needs CUDA 12.8 or newer). Same command.
- **"driver may be too old":** update the NVIDIA driver. In Docker, make sure the container was given the GPU.

Restart Sapphire afterwards. On Linux, Kokoro gets CUDA from PyPI's torch already and usually only hits the driver case. The in-app installer logs to `user/logs/gpu-install.log`.

---

## Boot crash with `malloc(): invalid size` on Linux (multi-GPU systems)

**Symptom:** Sapphire crashes immediately at startup on a fresh install. Last log lines show wakeword model loading successfully, then:

```
Wakeword hot-started successfully
malloc(): invalid size (unsorted)
Fatal Python error: Aborted
```

The crash is in C-extension code (no Python traceback) and happens every boot. Toggling wake word on *after* boot works fine.

**Cause:** Kokoro (TTS, its own process) and Whisper (STT, its own worker process) both default to `cuda:0` and initialize at the same time. Their CUDA contexts collide during the boot init storm and corrupt the heap; the next allocation trips glibc's malloc check.

**Fix on multi-GPU systems:** put TTS and STT on different cards, in `user/settings.json` or the Settings UI:

```json
{
  "FASTER_WHISPER_CUDA_DEVICE": 0,
  "KOKORO_CUDA_DEVICE": "1"
}
```

`KOKORO_CUDA_DEVICE` accepts the device index as a string. It sets `CUDA_VISIBLE_DEVICES` on the Kokoro subprocess at the OS level, so Kokoro physically can't see the other GPU. Whisper uses `FASTER_WHISPER_CUDA_DEVICE` directly through CTranslate2.

Restart Sapphire after changing these. `user/logs/sapphire.log` then shows `Kokoro subprocess pinned to CUDA_VISIBLE_DEVICES=1`.

**Single-GPU systems:** leave both at default (empty `KOKORO_CUDA_DEVICE`, `FASTER_WHISPER_CUDA_DEVICE=0`). If you still hit the crash, disable wake word at boot and toggle it on after Sapphire is up — that skips the concurrent-init window.

## Reference for AI

This doc is long (34 KB): read ONE part with section='<heading>', never the whole thing.
- Headings: Where to Look First · Reset Procedures · Startup Issues · Update Issues · Web UI Issues · Audio Issues · Voice & Conversation Issues · Prompt issues · Vault / private chat issues · Chat Database Issues · LLM issues · Network / Proxy Issues · Tool/Function Issues · Plugin Issues · Continuity Issues · Home Assistant Issues · Performance Issues.
- Start: user/logs/sapphire.log (search ERROR or the feature name); voice engines log apart (kokoro.log, whisper.log; model downloads: hf_fetch.log); System > Notifications rows say why a download or a voice switch failed; a failed reply's own message carries Fix it, which opens the setting to change.
- Topic docs carry their own section='Quick Troubleshooting': voice, chats, network, backups, phone-calls, game-room, appearance.
- Plugin won't load: its card's badge, the unsigned-plugins switch, the Missing strip (PLUGINS). Tool missing: is it in this chat's toolset, is its plugin on. A refusal in a private chat is by design (PRIVACY).
- RESETS DELETE THINGS. Each is its own section: Light Reset · Reinstall Packages · Nuclear Reset · Reset Password · Reset Everything. Give one only when the user asks for a reset, and say what it removes first. Settings only: Settings > System > Danger Zone > Reset All Settings is safer than deleting files. Nuclear Reset keeps user/ and the local backups (user_backups/) ONLY if both move lines run before the delete line. Reset Everything deletes chats, memory and settings, and leaves the backups and the saved API keys behind.
- Updating: git pull AND pip install -r requirements.txt (never a bare `pip install anthropic`: 1.x breaks Claude).
- Service: journalctl --user -u sapphire -f. Exit codes: 42 = restart asked for, 43 = another Sapphire holds this user/ folder or the web port, 44 = a web host, port or certificate setting is wrong.
- Sapphire Launcher users: its Troubleshoot tab checks packages, voice and GPU with fix buttons.
