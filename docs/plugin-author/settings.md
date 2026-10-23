# Settings

Plugins can declare settings that render automatically in the web UI — no JavaScript needed. For complex interactive UIs, a custom web module is also supported.

## Manifest-Declared Settings

Declare settings in `plugin.json` and they auto-render in Settings > Plugins:

```json
"capabilities": {
  "settings": [
    {"key": "api_key", "type": "string", "label": "API Key", "default": "", "widget": "password", "help": "Your API key"},
    {"key": "units", "type": "string", "label": "Units", "default": "metric", "options": [{"label": "Metric", "value": "metric"}, {"label": "Imperial", "value": "imperial"}]},
    {"key": "cache_min", "type": "number", "label": "Cache (min)", "default": 15},
    {"key": "enabled", "type": "boolean", "label": "Enabled", "default": true}
  ]
}
```

### Field Schema

| Field | Required | Description |
|-------|----------|-------------|
| `key` | yes | Setting key (unique within plugin) |
| `type` | yes | `"string"`, `"number"`, `"boolean"`, `"list"` (array of strings) |
| `label` | yes | Display name |
| `default` | yes | Default value |
| `help` | no | Description text |
| `widget` | no | Override: `"textarea"`, `"password"`, `"select"`, `"radio"`, `"button"` (action button) |
| `options` | no | `[{label, value}]` for select/radio |
| `placeholder` | no | Input hint text |
| `confirm` | no | Danger confirm gate (see below) |
| `tab` | no | Tab name for grouping fields. Untagged fields land on a "General" tab (shown first); tagged tabs follow in first-seen schema order. The tab strip only renders when there are 2+ groups |

Widget inference when omitted: `string` -> text, `string` + `options` -> select, `number` -> number spinner, `boolean` -> toggle, `textarea` type -> textarea, `list` -> chips with a "+ Add" row.

### List Fields

`"type": "list"` renders removable chips plus an add row, and saves as a JSON array of strings. By default the add row is free text; to offer a dropdown of valid values, point it at an endpoint:

```json
{
    "key": "scopes_to_tend", "type": "list", "label": "Scopes",
    "default": ["default"],
    "options_endpoint": "/api/plugin/myplugin/scopes",
    "data_key": "scopes", "value_field": "name"
}
```

`data_key` picks the array out of the JSON response (omit if the response IS the array); `value_field` picks the string out of each row (default `name`; rows may also be plain strings). Already-added values are filtered from the dropdown.

### Danger Confirm

Any field can have a `confirm` object that shows a danger dialog when a specific value is selected:

```json
{
    "key": "validation", "type": "string", "label": "Validation", "default": "moderate",
    "confirm": {
        "values": ["trust"],
        "title": "Trust Mode",
        "warnings": ["Warning 1", "Warning 2"],
        "buttonLabel": "Enable Trust Mode"
    }
}
```

### Storage

Settings are stored at `user/webui/plugins/{name}.json` and read via `plugin_loader.get_plugin_settings(name)` (merges stored values with manifest defaults).

---

## Custom Web Settings UI

For settings that need custom JavaScript beyond what manifest settings provide, plugins can ship a `web/` subdirectory.

**Most plugins should use manifest `settings` instead** — it's simpler and requires no JavaScript. Use `web` only for complex interactive UIs.

### Manifest

```json
"capabilities": {
  "web": {
    "settingsUI": "plugin"
  }
}
```

### Structure

```
plugins/my-plugin/
  plugin.json
  web/
    index.js               # Entry point (required)
    style.css              # Optional
```

Assets served at `/plugin-web/my-plugin/index.js`.

### index.js Contract

```javascript
import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import pluginsAPI from '/static/shared/plugins-api.js';

export default {
  name: 'my-plugin',

  init(container) {
    registerPluginSettings({
      id: 'my-plugin',
      name: 'My Plugin',
      icon: '⚙️',
      helpText: 'Configure my plugin',

      render(container, settings) {
        container.innerHTML = `
          <input type="text" id="mp-url" value="${settings.url || ''}">
        `;
      },

      load: () => pluginsAPI.getSettings('my-plugin'),
      save: (settings) => pluginsAPI.saveSettings('my-plugin', settings),

      getSettings(container) {
        return { url: container.querySelector('#mp-url').value };
      }
    });
  },

  destroy() { }
};
```

### Global Save vs self-saving panels

`getSettings` + `save` are how your panel participates in the Settings view's
global **Save Changes** button: it calls `getSettings(container)` and passes the
result to `save(settings)`.

**If your panel persists through its own buttons** (account managers, per-item
CRUD editors — anything using `createAccountManager`), **omit `getSettings` and
`save` entirely.** The global Save button then hides on your tab and shows
"This page saves with its own buttons" instead.

Never register stub callbacks like `getSettings: () => ({})` with a no-op
`save` — the global button will report "settings saved" while writing nothing,
and users lose edits believing they saved (the 2026-07-04 two-save-buttons
bug, which shipped in eight plugins by copy-paste before being caught).

---

## Settings API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/webui/plugins/{name}/settings` | Read settings |
| PUT | `/api/webui/plugins/{name}/settings` | Save settings |
| DELETE | `/api/webui/plugins/{name}/settings` | Reset to defaults |

## Reference for AI

PLUGIN SETTINGS:
- Manifest: `capabilities.settings` = [{key (required, unique in plugin), type (required: string|number|boolean|list), label (required), default (required), help?, widget? (textarea|password|select|radio|button), options? ([{label, value}] for select/radio), placeholder?, confirm?, tab?}].
- Widget inference when omitted: string->text, string+options->select, number->spinner, boolean->toggle, type "textarea"->textarea, type "password"->password, list->chips with add row.
- `tab` groups fields; untagged fields land on "General" (first); tab strip renders only with 2+ groups.
- List fields save as a JSON array of strings; optional dropdown add row via options_endpoint (API URL), data_key (picks array from response; omit if response IS the array), value_field (default "name"; rows may be plain strings). Added values are filtered from the dropdown.
- `confirm` on any field: {values: [...], title, warnings: [...], buttonLabel} — danger dialog when a listed value is selected.
- Storage: user/webui/plugins/{name}.json. Python read: `plugin_loader.get_plugin_settings(name)` (stored values merged over manifest defaults). REST: GET/PUT/DELETE `/api/webui/plugins/{name}/settings` (DELETE = reset to defaults).
- Custom JS settings UI: `capabilities.web.settingsUI: "plugin"` + `web/index.js` served at /plugin-web/{name}/index.js; `export default {name, init(container), destroy()}` calling registerPluginSettings({id, name, icon, helpText, render(container, settings), load, save, getSettings(container)}).
- Global Save contract: getSettings+save wire the panel to the Settings view's global Save button. Panels that persist through their OWN buttons must OMIT both (the global button then hides for that tab). NEVER register stub getSettings/save — it reports "saved" while writing nothing.
