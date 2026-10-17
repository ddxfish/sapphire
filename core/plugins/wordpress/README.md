# WordPress

Manage a WordPress site from Sapphire through its built-in REST API. Posts, pages, users,
plugins, and site settings. Multi-site. Destructive actions are gated by a per-site,
burn-on-use PIN that the AI never sees.

## Setup

1. In WordPress: **Users → Profile → Application Passwords**. Create one for "Sapphire" and
   copy the generated password (looks like `xxxx xxxx xxxx xxxx xxxx xxxx`). This is *not*
   your login password — it's revocable and scoped to your account's capabilities.
2. In Sapphire: **Settings → WordPress → Add Site**. Enter the site URL, your username, and
   the Application Password.
3. Pick which site Sapphire may use per-chat in the **Mind → WordPress** dropdown (it's a
   scope, like Telegram/Discord). The persona carries a default; daemons each carry one.

## Tools

| Tool | What it does |
|------|--------------|
| `wp_get_blog` / `wp_get_page` | List entries (no id) or read one (with id) |
| `wp_create_blog` / `wp_create_page` | Create (no id) or update (with id). Defaults to draft. |
| `wp_delete_blog` / `wp_delete_page` | Trash by default; `force=true` permanent (PIN) |
| `wp_settings` | List settings, or change one by name (PIN; site URL / admin email protected) |
| `wp_user` | List users, or delete a user (PIN) |
| `wp_plugin` | List plugins, or enable/disable one (PIN) |

## The PIN (destructive actions)

Destructive actions — permanent delete, user delete, plugin enable/disable, settings changes —
require a 4-digit PIN **by default**. Trash (recoverable delete), reading, and creating/updating
content do **not**.

- The PIN is shown to **you** next to each site in **Settings → WordPress** and in the
  **Mind → WordPress** dropdown (`(1234) sapphireblue`). The AI cannot read it — when it needs to
  do something destructive it asks you for it.
- **Burn-on-use:** the PIN regenerates after each successful destructive action, so a PIN you
  paste into chat can't be reused by the AI on its own later.
- **Unsupervised mode:** checking *"Allow destructive operations unsupervised"* removes the PIN
  requirement — Sapphire can then perform destructive actions on her own. This is the dangerous
  opt-in; the toggle goes red. Off by default.

## Notes

- A page built with a page-builder stores its layout as data — overwriting it via `wp_create_page`
  flattens that layout (WordPress revisions are your undo). Plain pages are unaffected.
- All data (sites, Application Passwords, PINs, config) lives in
  `user/plugin_state/wordpress.json` — never committed, never sent to the AI.
