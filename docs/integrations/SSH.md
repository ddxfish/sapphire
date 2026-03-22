# SSH

Run commands on remote servers through Sapphire. Uses SSH key authentication — no passwords stored.

## Setup

1. **Ensure SSH Key Auth Works:**
   - You should be able to `ssh user@server` without a password prompt
   - If not: `ssh-keygen -t ed25519` then `ssh-copy-id user@server`

2. **Add a Server in Sapphire:**
   - Settings → Plugins → SSH (via gear icon)
   - Add server with: friendly name, hostname, port, username, key path
   - Key path defaults to `~/.ssh/id_ed25519`

3. **Test it** — Ask the AI to run a simple command like `uptime`

## Available Tools

| Tool | What it does |
|------|--------------|
| `ssh_get_servers` | List configured servers or get details for one |
| `ssh_run_command` | Execute a command on a remote server |

### Running Commands

The AI calls `ssh_run_command` with the server name and command:
- "Check disk space on the web server"
- "Run `docker ps` on production"
- "What's the uptime on the database server?"

Commands have a configurable timeout (default 30 seconds, max 120).

## Safety

SSH has a **command blacklist** to prevent dangerous operations. These are blocked by default:

- `rm -rf /` and variants
- `mkfs` (filesystem formatting)
- Fork bombs
- Other destructive patterns

You can customize the blacklist in Settings → Plugins → SSH. It uses regex patterns, one per line.

### Output Limits

Command output is truncated at 6000 characters by default (configurable). This prevents massive outputs from flooding the AI's context.

## Multiple Servers

Add as many servers as you need. Each gets a friendly name the AI uses to target commands:

```
Name: web-prod     Host: 10.0.1.5    User: deploy
Name: db-primary   Host: 10.0.1.10   User: admin
Name: monitoring   Host: 10.0.1.20   User: ops
```

The AI can then say "I'll check the web-prod server" and route the command correctly.

## Local Shell

For running commands on the **local** machine (where Sapphire runs), use the **Command Line** plugin instead. It has the same safety blacklist system but runs locally via subprocess.

## Example Commands

- "List my SSH servers"
- "Check memory usage on web-prod"
- "Run `tail -50 /var/log/nginx/error.log` on the web server"
- "How much disk space is left on db-primary?"
- "Restart nginx on web-prod"

## Troubleshooting

- **Connection refused** — Check hostname, port, and that SSH is running on the server
- **Permission denied** — Check key path, verify key is in the server's `authorized_keys`
- **Command blocked** — It hit the safety blacklist. Edit the blacklist if it's a false positive
- **Output truncated** — Increase `output_limit` in settings if you need more
- **Timeout** — Increase timeout or check if the command hangs

## Reference for AI

SSH integration for remote server command execution.

SETUP:
- Settings → Plugins → SSH
- Add servers: name, host, port, user, key_path
- Uses system ssh via subprocess (BatchMode=yes, StrictHostKeyChecking=accept-new)

AVAILABLE TOOLS:
- ssh_get_servers(name?) - list all servers or get one by name
- ssh_run_command(server, command, timeout?) - execute remote command (default 30s, max 120s)

SAFETY:
- Regex blacklist blocks dangerous commands (rm -rf /, mkfs, fork bombs)
- Blacklist customizable in settings (one regex per line)
- Output truncated at output_limit chars (default 6000)

LOCAL SHELL:
- Command Line plugin for local commands (same blacklist system)
- run_command(command, timeout?) - local subprocess execution

TROUBLESHOOTING:
- Permission denied: verify key is in remote authorized_keys
- Timeout: increase timeout param or check for hanging command
- Blocked: check blacklist patterns in settings
