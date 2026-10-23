# Plugin Apps

Plugins can ship a full-page app that appears in the **Apps** nav item. Apps get the full content area and can build any UI — dashboards, control panels, games, custom tools.

## Quick Start

1. Create an `app/` directory in your plugin
2. Add `index.js` with `render()` and optional `cleanup()` exports
3. Declare the app in your `plugin.json` manifest

### File Structure

```
plugins/my-plugin/
  plugin.json
  app/
    index.js      # Required — exports render() and cleanup()
    app.css       # Optional — imported by your index.js
```

### plugin.json

```json
{
  "name": "my-plugin",
  "capabilities": {
    "app": {
      "label": "My Dashboard",
      "icon": "📊",
      "description": "Real-time system dashboard"
    }
  }
}
```

The `capabilities.app` fields:
- `label` — display name on the app tile (falls back to plugin display_name)
- `icon` — emoji shown on the tile (falls back to plugin emoji)
- `description` — short description shown below the tile
- `nav` — promotes the app out of the Apps grid: `true` gives it its own navrail icon (max 3), or a nav group name like `"chat"` makes it an entry in that group's flyout (no rail slot, uncapped)

### app/index.js

```js
// Your app gets a container element to render into
export function render(container) {
    container.innerHTML = `
        <h1>My Dashboard</h1>
        <div id="my-stats"></div>
    `;

    // Use Sapphire's APIs
    fetch('/api/status').then(r => r.json()).then(data => {
        container.querySelector('#my-stats').textContent = JSON.stringify(data);
    });

    // Start any timers, intervals, SSE connections
    _interval = setInterval(() => updateStats(container), 5000);
}

let _interval = null;

// Called when user navigates away — clean up timers, connections
export function cleanup() {
    if (_interval) clearInterval(_interval);
    _interval = null;
}
```

## What Your App Can Do

### Use Sapphire APIs
All `/api/*` endpoints work with the same session auth. No extra setup.

```js
// Read settings
const res = await fetch('/api/status');

// Use the shared fetch wrapper (adds CSRF + timeout)
import { fetchWithTimeout } from '/static/shared/fetch.js';  // absolute — apps load from /plugin-web/{name}/app/, so relative ../../ paths miss
const data = await fetchWithTimeout('/api/init');
```

### Use the Event Bus (SSE)
Get real-time events from Sapphire:

```js
import * as eventBus from '/static/core/event-bus.js';

eventBus.on('message_added', (data) => {
    console.log('New message in chat:', data);
});
```

### Use Plugin State
Store persistent data via your plugin's settings:

```js
// Read your plugin's settings
const res = await fetch('/api/webui/plugins/my-plugin/settings');
const settings = await res.json();

// Save settings
await fetch('/api/webui/plugins/my-plugin/settings', {
    method: 'PUT',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content
    },
    body: JSON.stringify({ my_key: 'my_value' })
});
```

### Show Toasts
```js
// Import Sapphire's UI module
import * as ui from '/static/ui.js';
ui.showToast('Operation complete', 'success');
```

## Nav Promotion

By default, apps appear in the Apps grid. If your app is a primary feature users access frequently, you can promote it to the navrail:

```json
{
  "capabilities": {
    "app": {
      "label": "Mission Control",
      "icon": "🎯",
      "description": "Visual command dashboard",
      "nav": true
    }
  }
}
```

With `"nav": true`:
- Your app gets its own icon in the navrail (between Settings and Apps)
- Clicking it loads your `app/index.js` directly — no Apps grid in between
- The URL hash is `#app-{plugin-name}` (bookmarkable)
- `cleanup()` is called when navigating away, `render()` on return

**Limits:** Maximum 3 nav-promoted apps. Beyond that, extras appear in the Apps grid only. Use this sparingly — it's for primary workflows like dashboards, not utilities.

### Flyout Promotion

Instead of `true`, name a nav group and your app becomes an entry inside that group's flyout menu — no rail slot spent, no cap:

```json
{
  "capabilities": {
    "app": {
      "label": "Game Room",
      "icon": "🎲",
      "nav": "chat"
    }
  }
}
```

Groups: `chat`, `personas`, `triggers`, `mind`, `settings`. An unrecognized group falls back to a rail slot. Flyout apps behave exactly like rail apps otherwise — same view container, same `#app-{plugin-name}` hash, same `render()`/`cleanup()` contract. Clicking the entry while you're already deep in one of the app's sub-routes (`#app-{name}/…`) returns you to the app's home view. The bundled **Game Room** rides in the Chat flyout this way.

**Important:** Do NOT inject nav items via DOM manipulation (`document.createElement`, `insertBefore`, etc.). Use `"nav": true` in your manifest. Sapphire handles nav item creation, view container setup, and router registration automatically.

## How It Works

- The Apps nav item only appears if at least one non-nav plugin has an app
- Nav-promoted apps get their own navrail icon automatically
- Clicking an app tile or nav icon loads your `app/index.js` via dynamic import
- Your `render(container)` function receives a DOM element to fill
- When the user navigates away, `cleanup()` is called
- `render()` may instead **return** a teardown function — that closure wins over the module-level `cleanup()` export. Prefer it if your app claims shared DOM (the Game Room borrows the chat rail and composer): the module is memoized across renders, so a module-level `cleanup()` can otherwise tear down state a newer render already owns
- Your app runs inline (not iframe) — full access to Sapphire's JS modules
- Static assets in your `app/` dir are served via `/plugin-web/{name}/app/`

## Tips

- Use `cleanup()` to stop intervals, close WebSockets, remove event listeners
- Use CSS variables from Sapphire's theme (`var(--bg)`, `var(--text)`, `var(--accent)`, etc.)
- For CSRF-aware API calls: import `fetchWithTimeout` from `/static/shared/fetch.js` (absolute path — relative `../../` won't reach Sapphire's modules), or just use plain `fetch()` with an `X-CSRF-Token` header from `meta[name="csrf-token"]` (the dependency-free pattern the bundled `status` app uses)
- Your app inherits Sapphire's dark theme automatically
- Keep your app self-contained — don't modify the nav rail or other views
- Prefer `"nav": true` over DOM manipulation — it's cleaner and survives Sapphire updates

## Reference for AI

PLUGIN APPS:
- Manifest: `capabilities.app` = {label (falls back to display_name), icon (falls back to plugin emoji), description, nav?}. Files: `app/index.js` (required) exporting `render(container)` and optional `cleanup()`; assets served at `/plugin-web/{name}/app/`.
- `render(container)` receives a DOM element; may RETURN a teardown function — that closure wins over the module-level `cleanup()` export (prefer it when claiming shared DOM; the module is memoized across renders). `cleanup()` is called on navigate-away.
- nav: absent = Apps grid tile; `true` = own navrail icon (max 3 promoted — overflow falls back to the grid); a group name string = entry in that group's flyout (no rail slot, uncapped). Valid groups: chat, personas, triggers, mind, settings — unknown/malformed falls back to a rail slot. URL hash: `#app-{plugin-name}` (bookmarkable); clicking the nav entry while inside a sub-route (#app-{name}/...) returns to the app home.
- Apps run inline (not iframed) with full access to Sapphire modules: `/api/*` with session auth; `import { fetchWithTimeout } from '/static/shared/fetch.js'` (ABSOLUTE path — apps load from /plugin-web/, relative ../../ misses); `import * as eventBus from '/static/core/event-bus.js'` for SSE events; `import * as ui from '/static/ui.js'` for toasts; plugin settings via GET/PUT `/api/webui/plugins/{name}/settings` (PUT needs X-CSRF-Token from meta[name="csrf-token"]).
- Rules: never inject nav items via DOM manipulation (use `nav` in the manifest); clean up intervals/sockets/listeners; use theme CSS variables; keep the app self-contained.
