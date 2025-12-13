# SOCKS Proxy Configuration

Sapphire supports SOCKS5 proxy for web scraping functions. This routes `web_search`, `get_website`, `get_wikipedia`, and `research_topic` through your proxy for privacy.

## Why Use a Proxy?

When using web functions, your IP is exposed to every site the AI visits. A SOCKS5 proxy masks your real IP, adding a layer of privacy for research and web scraping.

## Setup

### 1. Enable in Settings

Add to `user/settings.json`:

```json
{
  "network": {
    "SOCKS_ENABLED": true,
    "SOCKS_HOST": "your-proxy-host.com",
    "SOCKS_PORT": 1080
  }
}
```

Or use the web UI: Settings → Network → Enable SOCKS.

### 2. Configure Credentials

SOCKS credentials are stored separately from settings for security. Choose one method:

**Option A: Environment Variables**

```bash
export SAPPHIRE_SOCKS_USERNAME="your_username"
export SAPPHIRE_SOCKS_PASSWORD="your_password"
```

Add to your shell profile (`~/.bashrc`) or systemd service file.

**Option B: Config File**

Create `user/.socks_config`:

```
username=your_username
password=your_password
```

Set permissions: `chmod 600 user/.socks_config`

### 3. Restart Sapphire

The proxy applies on startup. Restart to activate.

## Verify It Works

1. Ask the AI to search for something: "Search for weather in Tokyo"
2. Check logs for proxy connection: `grep SOCKS user/logs/sapphire.log`
3. Or ask the AI: "What is my IP address?" (compare with/without proxy)

## Proxy Providers

Any SOCKS5 proxy works. Common options:

- **Self-hosted**: Run your own on a VPS with `dante-server` or `microsocks`
- **VPN providers**: Many include SOCKS5 (Mullvad, Private Internet Access, etc.)
- **Commercial**: Dedicated proxy services (Bright Data, Oxylabs, etc.)

## Troubleshooting

**"SOCKS5 is enabled but credentials are not configured"**
- Credentials not found. Check env vars or `user/.socks_config`

**"SOCKS proxy connection error"**
- Wrong host/port, or proxy is down
- Verify proxy is reachable: `curl --socks5 user:pass@host:port https://httpbin.org/ip`

**Web functions work without proxy when SOCKS_ENABLED=true**
- Sapphire falls back to direct connection if proxy fails
- Check logs for proxy errors

## What Gets Proxied

Only outbound HTTP requests from web functions use the proxy:

| Proxied | Not Proxied |
|---------|-------------|
| web_search | LLM API calls |
| get_website | TTS/STT servers |
| get_wikipedia | Internal API |
| research_topic | Memory engine |

LLM traffic stays direct to your local server.