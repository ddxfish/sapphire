# SOCKS Proxy Configuration

Route web tool traffic through a SOCKS proxy for privacy. Only AI tools use the proxy (web_search, get_website, research_topic). LLM connections, model downloads, and TTS/STT do not use the proxy.

If the proxy is enabled but broken, tools fail-secure rather than leaking to direct connections.

## Configuration

Open Settings → Network tab.

<img src="screenshots/socks-proxy.png" alt="SOCKS proxy settings" width="100%">

1. Enable SOCKS proxy toggle
2. Set host and port (e.g., `127.0.0.1:9050` for Tor)
3. Click **Test Connection** to verify
4. Enter credentials if your proxy requires authentication

## Credentials

Sapphire checks for SOCKS credentials in this order:

1. **Credential Manager** (checked first)
   - Enter in Settings → Network
   - Stored in `~/.config/sapphire/credentials.json` (not in Sapphire's user directory)
   - Not included in backups for security
   - Use the **Clear** button to remove stored credentials

2. **Environment variables** (fallback)
   - `SAPPHIRE_SOCKS_USERNAME`
   - `SAPPHIRE_SOCKS_PASSWORD`

## Verify It Works

1. Enable web tools in a chat's toolset
2. Ask the AI to fetch your IP via https://icanhazip.com/
3. Should show your proxy's IP, not your real IP

## Reference for AI

Route web tools through SOCKS5 proxy for privacy.

WHAT IT DOES:
- Only affects tools: web_search, get_website, research_topic
- Does NOT affect: LLM connections, model downloads, TTS/STT
- Fail-secure: if proxy broken, tools error instead of leaking

SETUP:
1. Settings → Network → Enable SOCKS
2. Set host/port (e.g., 127.0.0.1:9050 for Tor)
3. Click Test Connection
4. Add credentials if needed (env vars or in Settings)

CREDENTIALS PRIORITY:
1. Credential Manager in Settings (stored in ~/.config/sapphire/)
2. Env vars: SAPPHIRE_SOCKS_USERNAME, SAPPHIRE_SOCKS_PASSWORD

VERIFY: Ask AI to fetch https://icanhazip.com/ - should show proxy IP

TROUBLESHOOTING:
- Tools failing: Check proxy is running, verify host/port
- Auth errors: Check credentials in env vars or Settings
- Tor: Use port 9050 (SOCKS) not 9051 (control)