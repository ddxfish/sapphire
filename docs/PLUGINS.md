# Plugins

<img width="66%" alt="image" src="https://github.com/user-attachments/assets/a4e4c033-08a4-499e-8453-183969039900" />

Most of what Sapphire can do beyond chatting comes from plugins: memory, voices, Discord, the phone line, devices, games, the store. Many ship with her and are on by default; others you turn on when you want them, or install.

---

## Turning plugins on and off

**Settings → Plugins** lists every plugin with All / Enabled / Disabled filters. Flip a plugin's switch to turn it on or off. It's live, with no restart. Some plugins pop a short note when you enable them (what they need, what they'll do).

- **Missing: …** under a plugin means it needs Python packages that aren't installed. **Install** installs them (conda or venv installs; Docker shows the command to run instead).
- Some plugins build their own environment the first time (a heavy model, a separate Python); the enable dialog offers to build it, skip, or point at one that already exists.
- **🔄 Rescan** picks up plugins you dropped into the folder by hand. **🔍 Check Updates** looks for newer versions of installed ones.
- A plugin's settings appear in its own section of Settings once it's on. Its README is under **Help → Plugins**.
- Plugins with a page of their own show in the nav rail or the **Apps** menu; pin the ones you use with 📌 ([plugin-author/APPS.md](plugin-author/APPS.md)).

Two plugins are mutually exclusive by design and refuse to run together: the memory engines (Mind Palace and Memory (legacy)), because they share tool names.

## Installing a plugin

- **The Store** (**System → 🛍️ Store**): browse and install with one click. Sapphire can also browse and install from the store herself (`store_browse`, `store_install`).
- **+ Install Plugin** on Settings → Plugins takes a **GitHub URL** (a branch or a `/tree/` subfolder works), a GitLab URL, or a **.zip** upload.

Installed plugins live in `user/plugins/` and are yours. The ones that ship with Sapphire live in `core/plugins/`.

## Signed and unsigned

Every plugin carries a badge:

| Badge | Meaning | Loads? |
|---|---|---|
| Official | Signed by Sapphire | Yes |
| Verified author | Signed by an author whose key is on the trusted list | Yes |
| Unsigned | No signature | Only when unsigned plugins are allowed |
| Tampered | A signature that doesn't match (edited files, or a key nobody trusts) | Never |

Unsigned plugins are **blocked by default**. The banner at the top of Settings → Plugins says "Unsigned plugins blocked"; its switch allows them (`ALLOW_UNSIGNED_PLUGINS`). Most community plugins, and anything Sapphire builds with the Toolmaker, are unsigned. See [SIGNING.md](SIGNING.md).

## Writing your own

The **[Plugin Author Guide](plugin-author/README.md)** covers everything. For a single tool, the [Toolmaker](TOOLMAKER.md) is quicker.

| Guide | What's inside |
|---|---|
| [Overview & Quick Start](plugin-author/README.md) | What plugins are, quick start, complete example |
| [Manifest](plugin-author/manifest.md) | `plugin.json` reference |
| [Hooks](plugin-author/hooks.md) | Every hook point, HookEvent fields, system access, privacy gate |
| [Tools](plugin-author/tools.md) | Tool files, schema flags, decay, settings, state |
| [Commands](plugin-author/commands.md) | Your own slash commands |
| [Settings](plugin-author/settings.md) | Manifest settings, custom UI, seeding |
| [Routes](plugin-author/routes.md) | HTTP endpoints and WebSockets |
| [Web UI](plugin-author/web-ui.md) | Shared JS modules, CSS variables, modals |
| [Apps](plugin-author/APPS.md) | Full-page plugin UIs in the nav rail |
| [Widgets](plugin-author/widgets.md) | Dashboard panels |
| [Themes](plugin-author/THEMES.md) | Themes and motions |
| [Daemons](plugin-author/daemons.md) | Event sources and realtime gates |
| [Schedule](plugin-author/schedule.md) | Cron tasks |
| [Subprocesses](plugin-author/subprocesses.md) | ProcessManager and declared services |
| [Providers](plugin-author/providers.md) | Custom TTS, STT, embedding, LLM backends; `core.notify` |
| [Voice Commands](plugin-author/voice-commands.md) | Spoken triggers that skip the LLM |
| [Voice Routing](plugin-author/voice-routing.md) | Send her voice to an avatar, robot or bot |
| [Devices](plugin-author/devices.md) | Device drivers |
| [Agents](plugin-author/agents.md) | New agent kinds |
| [Memory Layers](plugin-author/memory-layers.md) | Layers in the Mind Palace |
| [Prompt Packs](plugin-author/prompts.md) | Shipping prompts with a plugin |
| [Games](plugin-author/games.md) | Game Room games |
| [Stories](plugin-author/stories.md) | Story packs |
| [Social](plugin-author/social.md) | Social media providers |
| [Thinking](plugin-author/thinking.md) | Side LLM calls from a plugin |
| [Examples](plugin-author/examples.md) | Copy-paste samples |
| [Lifecycle](plugin-author/lifecycle.md) | Startup, toggle, reload, rescan, API reference |
| [Signing](plugin-author/signing.md) | Verification states and sideloading |
| [Publishing](plugin-author/publishing.md) | Repo layout and the Sapphire Store |
| [Design Principles](plugin-author/design-principles.md) | How a plugin should feel |
| [AI Reference](plugin-author/ai-reference.md) | Compact reference for Sapphire's own use |

---

## Reference for AI

PLUGINS: Settings → Plugins (toggle live, no restart; filters; 🔄 Rescan; 🔍 Check Updates; "Missing: …" + Install for pip deps; some build their own env on enable). Shipped plugins core/plugins/, installed/yours user/plugins/. Plugin READMEs: search_help_docs(doc_name='plugin/<name>').

INSTALL: Store (System → Store) or your store_browse/store_install (installs + enables at once, no confirm step); + Install Plugin = GitHub URL (branch / tree ok), GitLab, or .zip.

TRUST: official / verified author load; unsigned loads only with ALLOW_UNSIGNED_PLUGINS (Settings → Plugins banner switch, default off); tampered (edited signed files or an unknown key) never loads. Toolmaker-made plugins are unsigned. If a plugin "won't load", check its badge and the banner switch first, then Missing deps.

MUTUALLY EXCLUSIVE: mindpalace vs memory (legacy). Writing plugins: plugin-author/ai-reference (your compact reference), plugin-author/README.
