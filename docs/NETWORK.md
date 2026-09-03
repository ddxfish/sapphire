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
- A broken proxy at boot triggers a background reachability probe that pushes a warning toast in the web UI and shows on the trust strip — you find out immediately, not tool by tool. The probe re-runs on every proxy settings change.
- An empty host or invalid port can't be stamped at all — Sapphire warns loudly in the UI and logs; fix the field.

### Credentials

Both username and password are required for the web/network tool lane whenever SOCKS is on (fail-secure — tools error rather than leak). Resolution order:

1. **Credential manager** — set via Settings > Network (Save/Test/Clear buttons). Stored in `~/.config/sapphire/credentials.json`, deliberately *outside* the `user/` directory and excluded from backups.
2. **Environment variables** — `SAPPHIRE_SOCKS_USERNAME` and `SAPPHIRE_SOCKS_PASSWORD`.

For auth-less proxies (Tor), any placeholder pair works. Saving or clearing credentials immediately re-derives the proxy environment.

### Windows and system proxies

When SOCKS is **off**, Sapphire checks whether your OS has a system proxy configured (on Windows, the IE/WinINET registry proxy — often a leftover VPN, Fiddler, or corporate setting; on any OS, shell-set `HTTP_PROXY`/`HTTPS_PROXY`). If one exists, the trust strip shows an amber warning: your traffic is *not* direct even though Sapphire's proxy is off. Clear it in your OS network settings, or turn Sapphire's SOCKS on to override it.

### Installed from the minimal requirements?

The SOCKS support for the httpx lane (`httpx[socks]`, i.e. the `socksio` package) is in the root `requirements.txt` but **not** in `install/requirements-minimal.txt`. On a minimal install with SOCKS enabled, LLM and other httpx-based cloud requests will fail; the trust strip warns about it. Fix: `pip install 'httpx[socks]'`.

### What never phones home — egress invariants

Independent of the proxy, Sapphire holds a set of quiet-egress guarantees:

| Surface | Guarantee |
|---------|-----------|
| STT (Whisper), image vibes (CLIP), embeddings | Cached models load with `local_files_only` — offline. Network is only touched once, to download a missing model. |
| Kokoro TTS | Boots with Hugging Face offline mode armed when the model is already cached; lifts it once per process only if files are missing. |
| tiktoken | BPE file cached persistently under `user/cache/tiktoken` — no re-download per reboot. |
| Hugging Face telemetry | Disabled unconditionally at boot. |
| Silero VAD | Model pinned to an exact upstream commit URL — no floating "latest" fetch. |
| Plugin signing keys | Disk-cached allowlist is authoritative for a day — a boot with a warm cache makes no GitHub call. |
| Update check | A once-daily GitHub check for new releases — the **only unprompted outbound call the core app makes**. Turn off `UPDATE_CHECK_ENABLED` to stop it (takes effect within a day; restart applies immediately). |
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
| `UPDATE_CHECK_ENABLED` | `true` | Daily GitHub check for a newer release — the core app's only unprompted outbound call. |

Proxy credentials (username/password) are set in the same tab but stored in the credential manager, not in settings.

## Quick Troubleshooting

| Symptom | Check | Fix |
|---------|-------|-----|
| Every web tool fails right after enabling SOCKS | Trust strip warnings; click **Test** | Proxy down or wrong host/port. Nothing falls back to direct — fix the proxy or disable SOCKS. |
| Tools error "credentials not found" | Proxy Credentials section shows "Not set" | Set both username and password. Auth-less proxy (Tor)? Any placeholders work. |
| Every request fails "host unreachable" | Is **DNS via proxy** (`SOCKS_REMOTE_DNS`) on? | Your proxy has no server-side DNS — turn it off. The boot probe warns about exactly this. |
| LLM requests fail with 403/blocked while proxied | Did you turn `SOCKS_ROUTE_LLM` on? | The provider blocks proxy IPs — turn it back off (the default). A learn-once toast points here too. |
| LLM/cloud calls fail, web tools fine | Trust strip: `httpx[socks]` warning? | `pip install 'httpx[socks]'` — missing on minimal installs. |
| LAN device unreachable with SOCKS on | Does its hostname have dots and a public-looking TLD? | Add it to `SOCKS_NO_PROXY_EXTRA`, or address it by IP / `.local` name. |
| Plugin daemon ignoring new proxy settings | Was it running when you changed them? | Daemons inherit the environment at spawn — restart the plugin. |
| Trust strip warns about a system proxy with SOCKS off | OS proxy settings (Windows: WinINET/IE proxy) | Clear the leftover OS proxy, or enable Sapphire's SOCKS to override it. |
| Tor connects but nothing works | Port | Use `9050` (SOCKS), not `9051` (control port). |
| Don't want the daily update ping | Settings > Network | Turn off `UPDATE_CHECK_ENABLED`. |

## See also

- [PRIVACY.md](PRIVACY.md) — private chats, the vault, and what "private" enforces
- [INSTALLATION.md](INSTALLATION.md) — full vs minimal requirements
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general diagnostics
- [TOOLS.md](TOOLS.md) — the web and network tools that ride these lanes

## Reference for AI

SOCKS5 proxy routing + LAN/WAN split + egress invariants. Settings tab: Network.

SETTINGS KEYS (defaults): SOCKS_ENABLED=false, SOCKS_HOST="", SOCKS_PORT=1080, SOCKS_TIMEOUT=10.0, SOCKS_ROUTE_LLM=false, SOCKS_REMOTE_DNS=false, SOCKS_NO_PROXY_EXTRA="", UPDATE_CHECK_ENABLED=true.

MECHANISM:
- SOCKS on -> process env stamped: ALL_PROXY/HTTPS_PROXY/HTTP_PROXY = socks5[h]://user:pass@host:port, plus NO_PROXY belt (stamped first). Applied at boot, post plugin scan, and on every SOCKS settings change (clear_session_cache in core/socks_proxy.py).
- ALL WAN HTTP egress rides the proxy: web tools, search, downloads, cloud TTS/STT, model + plugin-key downloads, plugin daemons (env inherited at spawn; restart daemon to pick up changes).
- LAN NEVER proxied: loopback, RFC1918, link-local, localhost, *.local/*.lan/*.home.arpa, single-label hostnames, registered direct hosts, SOCKS_NO_PROXY_EXTRA entries. core/net.py classify() decides per request; never resolves DNS; unknown -> 'wan' (safe default); decodes dotless/hex/octal/zero-padded IP disguises.
- LAN lane: trust_env=False, redirects refused by default (3xx returned, not followed). WAN lane honors env.
- NOT carried at all: Discord/Telegram (own libs), voice calls (UDP), email (IMAP/SMTP). Browser->server web UI is inbound, unaffected.
- FAIL-CLOSED: dead proxy = loud errors, never silent direct fallback.

KNOBS:
- SOCKS_ROUTE_LLM off (default): LLM provider hosts (core: api.anthropic.com, api.openai.com, generativelanguage.googleapis.com; plus custom base_urls) added to NO_PROXY = exempt. On: full tunnel; requires proxy with remote DNS (httpx/socksio lane ALWAYS sends hostnames to proxy regardless of SOCKS_REMOTE_DNS). LAN LLM hosts always exempt.
- SOCKS_REMOTE_DNS off (default): socks5 scheme, local DNS, works on any proxy, local resolver sees hostnames. On: socks5h, DNS via proxy, no local leak; proxies without server-side DNS fail every hostname request (0x04 host unreachable) — boot probe detects and warns.

UI/ROUTES:
- Trust strip "What rides the proxy" in Settings > Network <- GET /api/socks/status (enabled, route_llm, remote_dns, dns_via_proxy, env_applied, httpx_socks, no_proxy[], system_proxy, warnings[]).
- Credentials: GET/PUT/DELETE /api/credentials/socks, POST /api/credentials/socks/test (fetches icanhazip.com via proxy, returns exit IP). Stored ~/.config/sapphire/credentials.json (outside user/, excluded from backups); env fallback SAPPHIRE_SOCKS_USERNAME/SAPPHIRE_SOCKS_PASSWORD. Both required for web-tools lane when SOCKS on; placeholders OK for auth-less proxies (Tor).
- Boot: apply_proxy_env early + post-scan; start_boot_probe late (background thread, auth test + hostname-connect test when remote DNS on; failures -> load_errors toast lane + trust strip warnings). Re-probe on every proxy config change.
- Learn-once toast: LLM 403/blocked/proxy-ish failure while SOCKS_ENABLED and SOCKS_ROUTE_LLM both on -> one PLUGIN_NOTICE per provider per boot suggesting the Route LLM exemption.
- Windows honesty: SOCKS off -> proxy_status reports urllib.getproxies() (WinINET registry / shell env) as system_proxy; strip shows amber not-direct warning.

DEPENDENCY: httpx[socks] (socksio) in root requirements.txt only — NOT install/requirements-minimal.txt. Minimal install + SOCKS on = LLM/httpx lane fails; strip warns; fix: pip install 'httpx[socks]'.

EGRESS INVARIANTS (independent of proxy):
- Cached models load offline: whisper/CLIP/embeddings local_files_only (retry online only if missing); kokoro arms HF_HUB_OFFLINE when cached, lifts once if files missing; silero VAD pinned to exact commit URL; tiktoken cache persistent at user/cache/tiktoken.
- HF_HUB_DISABLE_TELEMETRY=1 unconditionally.
- Plugin-key allowlist: disk cache authoritative 24h — warm-cache boot makes no GitHub call.
- UPDATE_CHECK_ENABLED: daily GitHub release check = only unprompted core outbound call; toggle checked per-iteration (effective within a day, restart = immediate).
- Cookies: API sessions block all Set-Cookie; browser-profile web-tools session keeps a capped jar.
- Chat/store/video images: referrerpolicy=no-referrer.
