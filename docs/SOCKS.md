# SOCKS Proxy Configuration

A proxy is not a VPN. This just routes functions like the AI doing a web_search, get_website or research_topic through the proxy. If you add functions, you must code them to use the proxy. Only functions use the proxy, things like model downloads for STT does not use the proxy.

If the proxy is enabled but broken, functions that use the proxy will fail for security. 

## Enable in Settings

Use the web UI: Settings → Network → Enable SOCKS.
<img src="screenshots/socks-proxy.png" alt="socks proxy in Sapphire settings" width="100%">


### Configure Credentials

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

### Restart Sapphire

The proxy applies on startup. Restart to activate.

## Verify It Works

1. Enable web tools in a chat
2. Ask the AI to get your IP via https://icanhazip.com/
3. Check logs for proxy connection: `grep SOCKS user/logs/sapphire.log`
