# Sample Widgets

A reference plugin for dashboard widgets: read its source to learn how to ship
your own. Off by default. No tools, no settings, no dependencies.

Enable it, then on the Dashboard (the first tab of **Settings**) use **+ Add**:
both widgets are in the picker under this plugin.

| Widget | Shows |
|---|---|
| **Hello** (`hello`, 1x1) | The minimum: a title, info lines, a refresh action in the Actions dropdown, and cleanup on unmount |
| **Pinned Note** (`note`, 1x1 / 1x2 / 1x4) | A `settings_schema` (note text and an accent color) and the settings form the dashboard builds from it. Several may be added |

Files: `web/widgets/hello.js`, `web/widgets/note.js`, and the `widgets` block in
`plugin.json`. The contract is `docs/plugin-author/widgets.md`.
