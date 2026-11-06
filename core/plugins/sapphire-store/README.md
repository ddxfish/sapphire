# Sapphire Store

Lets her browse the Sapphire plugin store and install from it. Off by default.
You browse the same store yourself at **System > Store**; this plugin is her
door to it.

## Tools

| Tool | What it does |
|---|---|
| `store_browse(search?, category?, sort?)` | The store list. `search` takes a name, a keyword, or an exact slug for full detail; `sort` is `newest` (default), `votes`, `name` or `updated` |
| `store_install(slug)` | Installs and enables a plugin by its slug, at once (no confirm step). An unsigned plugin stays blocked until unsigned plugins are allowed, and she's told so |

An install downloads the plugin's source (a GitHub or GitLab repo, or a direct
`https://` .zip), copies it to `user/plugins/<name>`, enables it and rescans.
It refuses: a direct .zip on localhost or a private network, zip entries that
would escape the plugin folder or pass 10 MB, a name a shipped plugin already uses, and a plugin
that is already installed (uninstall first to update).

A store plugin with no signature loads only with unsigned plugins allowed (the
switch on the Settings > Plugins banner). Community plugins are not reviewed;
read the source if in doubt.

## Settings

| Setting | Default |
|---|---|
| `store_url` | `https://sapphireblue.dev/wp-json/sapphire-store/v1/` |
