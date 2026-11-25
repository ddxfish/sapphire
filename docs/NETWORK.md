# Network — SOCKS proxy routing, the LAN/WAN split, and what phones home

Sapphire routes its outbound (WAN) traffic through a SOCKS5 proxy when you enable one, keeps LAN traffic direct by design, and ships a set of egress invariants so a privacy-focused install makes no surprise calls home. Everything lives in Settings > Network.

## What it is

When the SOCKS proxy is on, Sapphire stamps proxy environment variables process-wide, so essentially *all* internet-bound HTTP traffic rides the proxy — web tools, search, downloads, cloud TTS/STT, model and plugin-key downloads, and plugin daemons. Traffic to your local network never rides the proxy: a remote proxy can't dial back into your LAN, so LAN requests always go direct. The design is **fail-closed**: if the proxy is enabled but broken, requests fail loudly — nothing ever silently falls back to a direct connection.

## Using it

### Turning it on

1. Open Settings > Network.
2. Enable the SOCKS proxy toggle and set host and port (e.g. `127.0.0.1` / `9050` for Tor).
3. In **Proxy Credentials**, enter a username and password and click **Save**. Web and network tools require both — for an auth-less proxy like Tor, enter any placeholder values (Tor ignores them).
4. Click **Test** — Sapphire fetches its public IP through the proxy and shows the exit IP. If you see your proxy's IP and not your real one, you're routed.

Changes apply live: every SOCKS settings save re-derives the proxy environment, drops pooled connections, and re-probes the proxy. Plugin daemons are the one exception — they inherit the environment when they spawn, so a running daemon picks up proxy changes on its next restart.

### What rides the proxy — the trust strip

Settings > Network has a **What rides the proxy** panel (backed by `GET /api/socks/status`). It's the honest lane list — read it after any change:

| Lane | With SOCKS on |
|------|---------------|
| Web tools, search & downloads | Routed |
| LLM providers | Exempted by default; routed if you flip "Route LLM traffic" on |
| Cloud TTS / STT | Routed (standard HTTP lanes) |
| Model & plugin-key downloads | Routed |
| Your LAN (Home Assistant, local LLM servers, printers...) | Always direct — never proxied |
| Discord & Telegram | Direct (own connection libraries) |
| Voice calls | Direct (UDP media — SOCKS can't carry it) |
| Email | Direct (IMAP/SMTP sockets) |
| Web UI in your browser | Unaffected (inbound traffic; the proxy only touches outbound requests) |

The strip also lists bypassed hosts, live warnings, and ends with the ground rule: nothing falls back to direct — a dead proxy fails loudly.

### The LAN/WAN split

Sapphire decides direct-vs-proxy per request (the `core/net.py` facade, `classify()`), plus a `NO_PROXY` belt in the environment for anything using plain HTTP libraries. A host counts as LAN when it is:

- Loopback, a private (RFC1918) IP, or a link-local IP
- `localhost`, or ends in `.local`, `.lan`, or `.home.arpa`
- A single-label hostname with no dot (e.g. `sapphire-pi`) — home-LAN convention
- A host a plugin registered as direct (LAN gear the plugin owns)
- Listed in your **Socks No Proxy Extra** setting

Everything else — every real domain name — is WAN and rides the proxy. Classification is purely syntactic: it **never resolves DNS** (resolving would leak hostnames to your local resolver and defeat the remote-DNS promise). Disguised IPs are decoded first (dotless decimal/hex/octal forms, zero-padded quads, unicode dot lookalikes), so a public IP in costume still rides the proxy. Anything ambiguous fails toward the proxy lane — that's the safe direction.

Two safety details worth knowing:

- **LAN redirects are refused by default.** If a LAN device answers with a redirect pointing at an internet URL, Sapphire hands the redirect back to the caller instead of silently following it off-proxy.
- **LAN sessions ignore environment trust entirely** — no proxy vars, no `.netrc`, no CA overrides. LAN gear is often plain-HTTP or self-signed; that stays contained to the LAN lane.

### LLM traffic — Route LLM traffic (`SOCKS_ROUTE_LLM`)

By default, LLM API traffic (Claude, OpenAI, Gemini, and custom providers) is **exempt** from the proxy even when SOCKS is on. Two reasons:

1. Many providers block or flag datacenter proxy IPs (403s, "unusual activity").
2. The LLM lane (httpx with SOCKS support) *always* asks the proxy to resolve hostnames, so a proxy without server-side DNS can never carry it.

Flip `SOCKS_ROUTE_LLM` on for full-tunnel privacy — LLM traffic rides too. This requires a proxy with working remote DNS. Local/LAN LLM servers are always exempt either way.

If an LLM request fails with a 403/blocked-style error while routed, Sapphire shows a one-time hint toast per provider pointing you at this setting.

### DNS — Resolve DNS through the proxy (`SOCKS_REMOTE_DNS`)

- **Off (default):** `socks5` — hostnames resolve on your machine, then the traffic rides the tunnel. Compatible with every SOCKS proxy, but your local DNS resolver sees the names Sapphire visits.
- **On:** `socks5h` — hostnames are sent to the proxy and resolved server-side. No local DNS leak, but it requires a proxy that can resolve names. Some VPN vendors' standalone SOCKS5 services (e.g. PIA's) can't: with this on, every request dies with "host unreachable". The boot probe detects this and warns you to turn it off.

Note the asymmetry: this switch governs the requests/urllib lanes. The httpx lane (LLMs, some cloud calls) always does remote DNS regardless — which is why `SOCKS_ROUTE_LLM` is the knob to reach for on a no-DNS proxy.

### Bypassing hosts (`SOCKS_NO_PROXY_EXTRA`)

Comma-separated hostnames or IPs that must never ride the proxy — LAN gear with a real domain name that the automatic rules can't catch, e.g. `homeassistant.example.com, 192.168.1.50`. Loopback, private IP ranges, LAN suffixes, and detected LAN LLM endpoints are exempted automatically; you only need this for the stragglers.

### Fail-closed philosophy

- A dead or unreachable proxy means requests **fail with errors** — traffic never quietly leaks to a direct connection.
- Missing credentials: the environment is stamped without auth and the proxy refuses loudly (and the UI warns).
- A broken proxy at boot triggers a background reachability probe that pushes a warning toast in the web UI and shows on the trust strip — you find out immediately, not tool by tool. The probe re-runs on every proxy settings change. It dials `1.1.1.1:80` (and `api.anthropic.com:443` when DNS via proxy is on), both through the proxy: nothing goes direct.
- An empty host or invalid port can't be stamped at all — Sapphire warns loudly in the UI and logs; fix the field.

### Credentials

Both username and password are required for the web/network tool lane whenever SOCKS is on (fail-secure — tools error rather than leak). Resolution order:

1. **Credential manager** — set via Settings > Network (Save/Test/Clear buttons). Stored in `~/.config/sapphire/credentials.json`, deliberately *outside* the `user/` directory and excluded from backups.
2. **Environment variables** — `SAPPHIRE_SOCKS_USERNAME` and `SAPPHIRE_SOCKS_PASSWORD`.

For auth-less proxies (Tor), any placeholder pair works. Saving or clearing credentials immediately re-derives the proxy environment.

### Windows and system proxies

Sapphire does not use the operating system's proxy settings. With SOCKS **off** it goes direct: a proxy set in Windows (the IE/WinINET setting, often left behind by a VPN, Fiddler or a company policy) is ignored, and `HTTP_PROXY`/`HTTPS_PROXY` set in the shell that started Sapphire are removed from her own environment at boot. This is on purpose: the Windows setting carries no exceptions for your local network, so honoring it sent LM Studio, Ollama and Home Assistant traffic to the company proxy. If your network only reaches the internet through a proxy, turn on Sapphire's SOCKS setting; it is the one way to proxy her.

### Trust strip says SOCKS support is missing

The httpx lane needs `httpx[socks]` (the `socksio` package) to carry LLM and other cloud calls through the proxy. It's in both `requirements.txt` and `install/requirements-minimal.txt`, so a current install has it — older minimal installs predate that and an environment built by hand may not. Without it, those requests fail with SOCKS on and the trust strip warns. Fix: `pip install 'httpx[socks]'`.

### What never phones home — egress invariants

Independent of the proxy, Sapphire holds a set of quiet-egress guarantees:

| Surface | Guarantee |
|---------|-----------|
| STT (Whisper), image vibes (CLIP), embeddings | Cached models load with `local_files_only` — offline. Network is only touched once, to download a missing model. |
| Kokoro TTS | Resolves model and voice files from the local Hugging Face cache (no network when cached); a file not yet cached is downloaded once, on first use. `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` in the environment are honored, never set or cleared: a download they block is named as such (notification center, kokoro.log, the Test button), not as the hub's "check your Internet connection". |
| tiktoken | BPE file cached persistently under `user/cache/tiktoken` — no re-download per reboot. |
| Hugging Face telemetry | Off at boot (`HF_HUB_DISABLE_TELEMETRY=1`), unless your own environment already sets that variable: your value wins. |
| Hugging Face download path | A missing model comes down over Hugging Face's classic path (plain HTTPS from its CDN), set at boot with `HF_HUB_DISABLE_XET=1`; your own value in the environment wins. The newer transfer talks to a second server and can hang for minutes when that server is unreachable. The models you wait on (Whisper, Kokoro, embeddings) try the newer path second when the first stops moving for 20 seconds; with SOCKS on they use the classic path only. |
| Stalled-download check | Runs only when a model download has stopped moving or failed, never on a healthy one. Sapphire looks the download host up with this computer's DNS, tries each address, and asks one public DNS server (Cloudflare `1.1.1.1`, over HTTPS) about the stalled download's own hosts and nothing else (for a model: `huggingface.co` and the file server it was sent to; for the wake word and voice-detection downloads: `github.com` and its file host), to tell out-of-date DNS from an outage or a dead connection. Nothing else is sent. With SOCKS on the check does not run at all: no lookup, no connection. Sapphire does not use the operating system's proxy settings, so with SOCKS off this check goes direct, as the download itself does. |
| Silero VAD | Model pinned to an exact upstream commit URL — no floating "latest" fetch. |
| Plugin signing keys | Disk-cached allowlist is authoritative for a day — a boot with a warm cache makes no GitHub call. |
| Update check | A once-daily GitHub check for new releases — one of the **two recurring unprompted outbound calls the core app makes** (the other is the Store row below). Turn off `UPDATE_CHECK_ENABLED` to stop it (takes effect within a day; restart applies immediately). |
| Dashboard font | One fetch from Google Fonts on first boot (the Dancing Script name font, saved to `user/fonts/`); never again once the file exists. Set `DASHBOARD_FONTS_AUTOFETCH` to `false` in `user/settings.json` to skip it — the dashboard falls back to a system font. |
| Store | Contacted when you open System > Store (answers cached in memory, `STORE_CACHE_TTL_SECONDS`, default 1 h), and **about once a day in the background** for the dashboard's Plugin Spotlight: one request for the featured list, which the store answers with no body when nothing changed. Every store request says `Sapphire/<version>` as its user agent and carries no install id; the store sees your IP address, as with any request. Turn off `STORE_ENABLED` and the Store page and the spotlight stop: the core app no longer contacts the store. One exception: the separate Store plugin (off by default), whose AI tools browse and install from the store on their own setting while that plugin is on. |
| Video guide | System > Videos fetches public YouTube channel pages and feeds only when opened with a stale cache (12 h, `VIDEO_GUIDE_CACHE_TTL_SECONDS`); hide channels with `VIDEO_GUIDE_CHANNELS_DISABLED`. |
| Browser libraries for plugins | A plugin that draws in 3D loads its library through Sapphire's own server, which fetches it once from a public CDN (`esm.sh`, `cdn.jsdelivr.net` or `unpkg.com` only) and keeps it in `user/cdn_cache/` for good. Today that is the 3D avatar's three.js, fetched the first time the avatar is shown. It rides the proxy like any download. |
| Cookies | API traffic accepts no cookies at all; only the web-tools browser session keeps a bounded cookie jar. |
| Images in chat / store / video guide | Rendered with `no-referrer` policy — remote image hosts don't learn your Sapphire URL. |

## Settings

All in Settings > Network:

| Key | Default | What it does |
|-----|---------|--------------|
| `SOCKS_ENABLED` | `false` | Master switch — routes outbound traffic through the SOCKS5 proxy. |
| `SOCKS_HOST` | `""` | Proxy hostname or IP (Tor: `127.0.0.1`). |
| `SOCKS_PORT` | `1080` | Proxy port (Tor: `9050`). |
| `SOCKS_TIMEOUT` | `10.0` | Seconds to wait on the proxy during auth tests and probes. |
| `SOCKS_ROUTE_LLM` | `false` | Off: LLM APIs go direct, everything else rides the proxy. On: full tunnel — needs a remote-DNS-capable proxy. |
| `SOCKS_REMOTE_DNS` | `false` | Off: local DNS (`socks5`), works on every proxy. On: DNS via proxy (`socks5h`), no local name leak — fails on proxies without server-side DNS. |
| `SOCKS_NO_PROXY_EXTRA` | `""` | Comma-separated hosts that always bypass the proxy (LAN gear with real names). |
| `UPDATE_CHECK_ENABLED` | `true` | Daily GitHub check for a newer release — one of the core app's two recurring unprompted outbound calls (the other is the Store's daily Plugin Spotlight request). |
| `DASHBOARD_FONTS_AUTOFETCH` | `true` | First-boot download of the dashboard name font from Google Fonts. JSON only (`user/settings.json`), no UI. |

Proxy credentials (username/password) are set in the same tab but stored in the credential manager, not in settings.

## Quick Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| Every web tool fails right after enabling SOCKS | Trust strip warnings; click **Test** | Proxy down or wrong host/port. Nothing falls back to direct — fix the proxy or disable SOCKS. |
| Tools error "credentials not found" | Proxy Credentials section shows "Not set" | Set both username and password. Auth-less proxy (Tor)? Any placeholders work. |
| Every request fails "host unreachable" | Is **DNS via proxy** (`SOCKS_REMOTE_DNS`) on? | Your proxy has no server-side DNS — turn it off. The boot probe warns about exactly this. |
| LLM requests fail with 403/blocked while proxied | Did you turn `SOCKS_ROUTE_LLM` on? | The provider blocks proxy IPs — turn it back off (the default). A learn-once toast points here too. |
| LLM/cloud calls fail, web tools fine | Trust strip: `httpx[socks]` warning? | `pip install 'httpx[socks]'` — it ships in both requirements files now, so this means an older or hand-built environment. |
| LAN device unreachable with SOCKS on | Does its hostname have dots and a public-looking TLD? | Add it to `SOCKS_NO_PROXY_EXTRA`, or address it by IP / `.local` name. |
| Plugin daemon ignoring new proxy settings | Was it running when you changed them? | Daemons inherit the environment at spawn — restart the plugin. |
| Nothing reaches the internet on a network that needs a proxy | Is Sapphire's own SOCKS on? | She ignores the operating system's proxy and a shell `HTTP_PROXY`. Set the proxy in Settings > Network. |
| Tor connects but nothing works | Port | Use `9050` (SOCKS), not `9051` (control port). |
| Don't want the daily update ping | Settings > Network | Turn off `UPDATE_CHECK_ENABLED`. |

## See also

- [PRIVACY.md](PRIVACY.md) — private chats, the vault, and what "private" enforces
- [INSTALLATION.md](INSTALLATION.md) — full vs minimal requirements
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general diagnostics
- [TOOLS.md](TOOLS.md) — the web and network tools that ride these lanes

## Reference for AI

SOCKS5 proxy routing, the LAN/WAN split, and what leaves the machine. Settings tab: Network.

YOUR TOOLS: get_external_ip, check_internet, website_status(url). All ride the proxy when SOCKS is on, and all are refused in a private chat ("requires network access and is blocked in this private chat").

SETTINGS KEYS (defaults): SOCKS_ENABLED=false, SOCKS_HOST="", SOCKS_PORT=1080, SOCKS_TIMEOUT=10.0, SOCKS_ROUTE_LLM=false, SOCKS_REMOTE_DNS=false, SOCKS_NO_PROXY_EXTRA="", UPDATE_CHECK_ENABLED=true. Proxy login: Settings > Network (Save / Test / Clear; Test returns the exit IP), stored in the config folder outside user/, never backed up; env fallback SAPPHIRE_SOCKS_USERNAME / SAPPHIRE_SOCKS_PASSWORD. Both are needed for the web tools; placeholders are fine for a proxy with no login (Tor).

WHAT RIDES THE PROXY (SOCKS on):
- All WAN HTTP: web tools, search, downloads, cloud TTS/STT, model and plugin-key downloads, plugin daemons (a daemon reads the proxy at start: restart the plugin after a change).
- NEVER proxied: the LAN (loopback, private ranges, localhost, *.local / *.lan / *.home.arpa, single-label names, SOCKS_NO_PROXY_EXTRA entries).
- Not carried at all: Discord and Telegram (their own libraries), voice calls (UDP), email (IMAP/SMTP).
- LLM providers are EXEMPT by default (SOCKS_ROUTE_LLM off). On = full tunnel, and the proxy must do DNS itself.
- SOCKS_REMOTE_DNS off (default): this computer looks names up, so the local resolver sees them. On: the proxy does; a proxy that can't fails every request ("host unreachable").
- FAIL-CLOSED: a dead proxy means loud errors, never a quiet direct connection.
- The operating system's proxy is never used: with SOCKS off she goes direct, and a shell HTTP_PROXY / HTTPS_PROXY is dropped at boot.

MODEL DOWNLOADS: Whisper, Kokoro and the embedding model download in a child process, the classic Hugging Face path first and the xet path second (classic only with SOCKS on); a path silent for 20 s is ended and the other tried. Log: user/logs/hf_fetch.log. When a download stalls or fails, a check (never with SOCKS on) looks its hosts up locally, tries each address, and asks Cloudflare 1.1.1.1 over HTTPS about those hosts only. The row then names a verdict: stale-dns / no-dns (router DNS: restart it or use 1.1.1.1), offline, cut-off (connection down or filtered), host-down (their side), clock (this computer's date is wrong).

WHAT ELSE GOES OUT, unasked:
- Two recurring calls: the daily GitHub release check (UPDATE_CHECK_ENABLED) and, about once a day, the Store's Plugin Spotlight request (STORE_ENABLED=false stops the Store page and the spotlight; the Store PLUGIN's own tools still reach the store while that plugin is on).
- Once: the dashboard font from Google Fonts at first boot (DASHBOARD_FONTS_AUTOFETCH=false skips it).
- Plugin-key allowlist: a disk copy under 24 h old is used with no GitHub call.
- On demand only: Store pages (cache STORE_CACHE_TTL_SECONDS 3600), the Video guide (YouTube pages when System > Videos opens with a 12 h cache gone stale).
- Cached models load offline; a voice or model never used before is fetched once. Hugging Face telemetry is off at boot (a value already in the environment wins). HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE are honored and never set by Sapphire; a download they block says so.
- Once per library: a plugin's browser library (the 3D avatar's three.js) is fetched through /cdn-cache/ from esm.sh, cdn.jsdelivr.net or unpkg.com and kept in user/cdn_cache/.
- API sessions refuse all cookies; chat, store and video images send no referrer.

FIXES: every web tool fails after turning SOCKS on = the proxy is down, nothing falls back (Settings > Network > Test) · "credentials not found" = set a username and password (placeholders for Tor) · "host unreachable" on everything = turn off DNS via proxy · LLM 403 while proxied = turn Route LLM off · a LAN device is unreachable = add it to SOCKS_NO_PROXY_EXTRA or use its IP / .local name · Tor = port 9050, not 9051 · LLM calls fail with SOCKS on in a hand-built env = pip install 'httpx[socks]' · more: section='Quick Troubleshooting'.
