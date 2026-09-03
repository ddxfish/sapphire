# Dashboard Widgets

Plugins can ship widgets — small action-panel cards that live in the
Sapphire dashboard's command center. Each widget is a single render
function plus a manifest entry. Same shape as the built-in System,
Updates, Backups, Maintenance, and Spotlight panels — those are
literally widgets too, registered through the same API.

> **Canonical example:** [`plugins/sample-widgets/`](../../plugins/sample-widgets/) ships with two
> reference widgets (`hello`, `note`). Read them alongside this doc — the
> source IS the spec.

---

## Quickstart

A working widget in five minutes:

```bash
# 1. Copy the reference plugin to your own
cp -r plugins/sample-widgets plugins/my-widget
```

Edit `plugins/my-widget/plugin.json` — change at minimum the `name`,
keep the `widgets` array shape, point `render` at your file under
`web/widgets/`. The `name` field MUST match the directory name.

```json
{
  "name": "my-widget",
  "version": "0.1.0",
  "description": "My first widget",
  "author": "you",
  "icon": "✨",
  "priority": 110,
  "capabilities": {
    "widgets": [{
      "id": "hello",
      "name": "Hello",
      "render": "widgets/hello.js",
      "sizes": ["1x1"],
      "default_size": "1x1"
    }]
  }
}
```

Edit `plugins/my-widget/web/widgets/hello.js` — change the contents
of `paint()` to whatever you want.

Sign + restart:

```bash
python tools/sign_plugin.py plugins/my-widget
systemctl --user restart sapphire   # or your usual restart command
```

Enable the plugin in **Settings > Plugins** (toggle on). Open
**Settings > Dashboard**, click `+ Add` in the controls row above the
panels, click **Add** next to your widget. It appears.

---

## Anatomy of a widget

A widget is a JS module that exports a `render` function. The host
calls it, the function fills in the panel body and returns metadata.
This is the entire contract:

```js
// plugins/my-widget/web/widgets/hello.js

export async function render(container, ctx) {
    // 1) container — the panel's info-line wrapper. Mutate its innerHTML.
    //    The host owns the title bar and Actions dropdown chrome around it.
    // 2) ctx — { plugin, widget_id, instance_id, size, settings, api, ... }

    let aborted = false;

    function paint() {
        if (aborted) return;
        const t = new Date().toLocaleTimeString();
        container.innerHTML = `
            <div class="dash-action-panel-info-line">
                <strong>Hello!</strong> The time is ${t}
            </div>
        `;
    }

    paint();
    const tick = setInterval(paint, 1000);

    return {
        title: '👋 My widget',
        actions: [
            { icon: '↻', label: 'Wave', onClick: () => ctx.api.toast('hi', 'success') },
        ],
        cleanup: () => {
            aborted = true;
            clearInterval(tick);   // CRITICAL: cancel any timers/listeners
        },
    };
}
```

What the host does for you:

- Wraps your output in panel chrome (title bar, Actions dropdown, drag handle, delete button).
- Renders your `actions` array as buttons in the dropdown.
- Auto-appends a `⚙ Settings...` action if your manifest declares `settings_schema`.
- Calls your `cleanup()` when the widget is removed, the dashboard tab leaves, or the plugin reloads.

What you must do:

- Mutate `container` to render your body.
- Return `{ title, actions, cleanup }`.
- **Cancel everything you started in cleanup.** setIntervals, EventSources, fetches in flight, anything.

---

## Manifest reference

Widgets are declared in `capabilities.widgets[]`:

```json
"capabilities": {
  "widgets": [
    {
      "id": "current",
      "name": "Current Weather",
      "description": "Now + today's high/low.",
      "icon": "🌤",
      "render": "widgets/current.js",
      "sizes": ["1x1", "1x2"],
      "default_size": "1x1",
      "multi_instance": true,
      "settings_schema": [ ... ],
      "api_version": 1
    }
  ]
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | **required** | Unique within your plugin. `[a-z0-9_-]{1,64}` |
| `name` | string | `id` | Display name in the picker |
| `description` | string | `""` | One-line tagline shown in the picker |
| `icon` | string | `""` | Emoji or single char shown next to name in the picker |
| `render` | string | `widgets/{id}.js` | Path to render module, relative to `web/`. **Files live under `plugin/web/`.** |
| `sizes` | array | `["1x1"]` | Subset of `["1x1", "1x2", "1x3", "1x4"]`. Sizes the user can choose. |
| `default_size` | string | `"1x1"` | Initial size when added |
| `multi_instance` | bool | `false` | Allow multiple copies on the dashboard |
| `settings_schema` | array | `[]` | Per-instance settings — see [Settings schema](#settings-schema) |
| `api_version` | int | `1` | Widget API version targeted (currently 1) |

> **Path note:** `render: "widgets/foo.js"` means the file is at
> `plugin/web/widgets/foo.js`. The `/web/` prefix is automatic — you
> only specify the path under it.

---

## Render contract & `ctx`

```js
export async function render(container, ctx) { ... return { title, actions, cleanup }; }
```

### What you receive

`container` is a `<div class="dash-action-panel-info">` element.
You own its content. Don't touch its parent — that's the host's chrome.

`ctx` shape:

| Field | Type | Description |
|-------|------|-------------|
| `ctx.plugin` | string | Your plugin's name |
| `ctx.widget_id` | string | The widget's id from your manifest |
| `ctx.instance_id` | string | UUID for this specific placement (different for each `+ Add`) |
| `ctx.size` | string | Current size: `"1x1"` / `"1x2"` / `"1x3"` / `"1x4"` |
| `ctx.settings` | object | Merged user settings + your schema defaults |
| `ctx.pluginWebPath` | string | `/plugin-web/{plugin}/` — base path for your other web assets |
| `ctx.api` | object | See below |

`ctx.api`:

| Method | Description |
|--------|-------------|
| `api.fetch(url, init?)` | Wrapper around `window.fetch`. Same-origin requests carry the user's session cookie. |
| `api.toast(msg, kind?)` | Show a transient notification. `kind` ∈ `'success' \| 'error' \| 'info'`. |
| `api.listStorePlugins(opts?)` | Convenience wrapper around the Store catalog API. |
| `api.pollForRestart()` | Watches `/api/health` and reloads when Sapphire is back. Use after restart-triggering actions. |
| `api.navigateSettingsTab(name)` | Programmatically switch Settings tabs (e.g. `'backup'`). |
| `api.openWidgetSettings(instance_id)` | Open the auto-rendered settings modal for a widget. Useful if you want a custom action like `✏ Edit` that opens settings. |
| `api.registerCleanup(fn)` | Register a cleanup function up-front, before scheduling work. Safer than only relying on the returned `cleanup` — if your render throws *after* starting an interval but *before* returning, the registered cleanup still runs and the timer doesn't leak. Recommended for any render that schedules side effects before its first `await`. |

### What you must return

```js
return {
    title: 'Display name',          // string shown in panel header
    actions: [                       // optional; array of dropdown items
        { icon: '↻', label: 'Refresh', onClick: () => ... },
        { icon: '⚙', label: 'Action', kind: 'danger', onClick: () => ... },
    ],
    cleanup: () => { /* tear down */ },  // REQUIRED
};
```

`actions[].onClick` runs in your render's closure — it can read your
local state, call your refresh function, etc.

`cleanup` is called when:
- User removes the widget via the delete `×` in edit mode
- User resizes the widget (re-renders with new size)
- User changes settings (re-renders with new settings)
- Dashboard tab leaves
- Plugin is uninstalled

If you return without a cleanup function, the host warns in the
console. Always return one, even if it's empty.

---

## Settings schema

Declare per-instance settings in `settings_schema`. The host renders a
form automatically and saves to the panel's `settings` object on save.
You access it via `ctx.settings`.

```json
"settings_schema": [
  {
    "key": "text",
    "type": "textarea",
    "label": "Note text",
    "default": "Type your note...",
    "rows": 3
  },
  {
    "key": "color",
    "type": "select",
    "label": "Accent color",
    "default": "blue",
    "options": [
      { "value": "blue", "label": "Blue" },
      { "value": "green", "label": "Green" }
    ]
  }
]
```

When the widget is added, `ctx.settings` is `{ text: "Type your note...", color: "blue" }`
(defaults filled in). After the user opens `⚙ Settings...` and saves,
the host re-renders with the new values.

### Field types

| `type` | Renders as | Extra fields |
|--------|------------|---------------|
| `text` | single-line input | — |
| `textarea` | multi-line textarea | `rows` (default 3) |
| `number` | number input | `min`, `max`, `step` |
| `select` | dropdown | `options: [{value, label}]` (required) |
| `boolean` | checkbox | — |
| `color` | native color picker | — |

All field types support: `key` (required), `label`, `default`, `help`
(small dim text below the input).

> The `⚙ Settings...` action is appended automatically when
> `settings_schema` is non-empty. You don't wire it yourself — but you
> can also call `ctx.api.openWidgetSettings(ctx.instance_id)` from any
> custom action to open the same modal.

---

## Sizes, multi-instance, namespacing

### Sizes

Four horizontal spans: `1x1`, `1x2`, `1x3`, `1x4`. Vertical is
content-driven — your widget's height is whatever its body needs.

Declare which sizes your widget supports:

- **`1x1`** — narrow, ~200-300px wide. Fits 5 panels per row at desktop.
- **`1x2`** — medium, ~400-600px. Good for a list of 3-5 short items.
- **`1x3`** — wide, ~600-800px. Stretches one panel across most of the row.
- **`1x4`** — full-width on desktop. Wraps to its own line.

Read `ctx.size` and adapt your render — show more content at larger
sizes, smaller font at smaller sizes. Sample `note.js` does this:

```js
const fontSize = ctx.size === '1x4' ? '20px'
               : ctx.size === '1x2' ? '15px'
               : '13px';
```

On mobile (≤768px), all panels collapse to single column regardless
of declared size.

### Multi-instance

Set `multi_instance: true` to allow users to add your widget more
than once. Useful when each instance has different settings (Weather
for two cities, Notes for two topics).

When multi-instance is on, `ctx.instance_id` is your way to
differentiate state between copies. **Don't hardcode HTML IDs in your
render output** — two instances will collide and break.

```js
// BAD — same id rendered twice if user adds two of these
container.innerHTML = `<div id="my-temp">${temp}</div>`;

// GOOD — scoped to instance, or no id at all (use querySelector within container)
container.innerHTML = `<div class="my-plugin-temp">${temp}</div>`;
```

### CSS namespacing

Widgets share the dashboard's stylesheet. Plugin authors should
prefix class names with the plugin name to avoid collision:

```css
.weather-temp { font-size: 24px; }    /* OK — namespaced */
.temp { font-size: 24px; }            /* BAD — could collide with anything */
```

You can use the dashboard's existing classes (`.dash-action-panel-info-line`,
`.dim`, etc.) freely — those are stable host-provided utilities.

---

## Common mistakes

A list of pitfalls observed during the widget API's design. Most
correspond to a quick fix.

### Forgot `cleanup()` → leaked timers

If you start a `setInterval` and don't clear it on unmount, every
re-render leaves another running. Symptom: dashboard slowly slows
down, network tab fills with duplicate fetches.

```js
// WRONG
const tick = setInterval(refresh, 1000);
return { title, actions };  // no cleanup

// RIGHT
const tick = setInterval(refresh, 1000);
return { title, actions, cleanup: () => clearInterval(tick) };
```

### Risky pattern: scheduling before an `await` that might throw

If your render starts a timer/listener BEFORE an `await` that fails,
the returned cleanup never reaches the host — your timer leaks.
Use `ctx.api.registerCleanup` to bind the cleanup up-front:

```js
// SAFER — cleanup is bound BEFORE the risky await, so it still runs if fetch throws.
export async function render(container, ctx) {
    const tick = setInterval(refresh, 1000);
    ctx.api.registerCleanup(() => clearInterval(tick));

    const data = await ctx.api.fetch('/api/something-that-might-404');  // could throw
    if (!data.ok) throw new Error('boom');
    // ...
    return { title: '...', actions: [...] };  // cleanup already registered
}
```

You can use both patterns together — anything you `registerCleanup` AND
anything you return as `cleanup` will all run on unmount.

### Used inline `onclick="..."` → may break with strict CSP

Sapphire's CSP isn't strict today, but if it tightens, inline event
handlers stop working. Always use `addEventListener` or react to the
host's `actions` array.

```js
// WRONG
container.innerHTML = `<button onclick="alert('hi')">Hi</button>`;

// RIGHT
container.innerHTML = `<button class="my-plugin-btn">Hi</button>`;
container.querySelector('.my-plugin-btn').addEventListener('click', () => alert('hi'));
```

### Hardcoded element IDs → multi-instance collisions

If your widget's render uses fixed IDs and `multi_instance` is true,
two copies break each other. Use classes scoped to your container,
or `querySelector` within `container` (not `document`).

### Modified `ctx.settings` directly

`ctx.settings` is a snapshot from the host. Mutating it does nothing
to persisted state. To save changes, call
`ctx.api.openWidgetSettings(ctx.instance_id)` and let the user save
through the modal. (V2 may add a programmatic save API; for now,
modal-only.)

### Forgot to re-sign after editing → blocked at boot

Sapphire verifies plugin signatures on load. If you edit anything
under `plugins/{name}/`, you must re-sign before restart:

```bash
python tools/sign_plugin.py plugins/{name}
```

Skipping this means the plugin loads as unsigned and is blocked
unless `ALLOW_UNSIGNED_PLUGINS=true` is set (default false).

### Widget renders but doesn't appear in picker

Your plugin is signed but not enabled. Check **Settings > Plugins**
and toggle your plugin on. Then re-open the dashboard picker.

### `render()` threw on first call

The host shows a "render failed: {message}" placeholder in the panel
body. Check the browser console for the stack trace. Common causes:
referenced global before it's defined, JSON parsing on an empty fetch
response, returning before setting required fields on the result.

---

## Lifecycle and versioning

### Lifecycle hooks (when `cleanup()` fires)

- Widget removed via the delete `×`
- Widget resized (re-render with new `ctx.size`)
- Widget settings saved (re-render with new `ctx.settings`)
- Dashboard tab leaves and is later re-entered
- Plugin disabled, uninstalled, or reloaded

The host wraps your `cleanup()` in a try/catch — a throwing cleanup
won't break the dashboard, but it logs a warning with your plugin name.

### Hot reload

Today, when a plugin is reloaded via `POST /api/plugins/{name}/reload`
or the Plugins UI button, the existing rendered widgets continue to
run their *old* code until the user reloads the dashboard tab. This
is a limitation we'll address in a future API version. For now, after
a reload, manually refresh the dashboard or click `Reload static
assets` in the Maintenance widget.

### API version

Set `api_version: 1` in your manifest entry. This is the V1 contract
described above. We promise:

- The shape of `ctx` will only grow (new fields), never shrink
- Existing `ctx.api.*` methods will keep their signatures
- The render contract (`render(container, ctx) → { title, actions, cleanup }`) is stable

If we ever need to break this, we'll bump to `api_version: 2` and
keep V1 widgets running unchanged.

---

## Publishing

Once your widget works locally, publishing follows the standard
Sapphire plugin distribution flow. See
**[plugin-author/publishing.md](publishing.md)**.

Tl;dr: push your `plugins/{name}/` directory to a GitHub repo. Users
install via the in-app Store or by pasting your repo URL into
**Settings > Plugins > Install from URL**. Featured plugins (curated by
the project maintainer) appear in the Plugin Spotlight widget on every
dashboard.

---

## Reference: the sample plugin

The truest documentation is [`plugins/sample-widgets/`](../../plugins/sample-widgets/):

- `plugin.json` — full manifest with two widgets, multi-instance,
  settings_schema. Read this alongside the [Manifest reference](#manifest-reference) above.
- `web/widgets/hello.js` — minimal example. ~50 lines. Shows the bare
  contract: render, actions, cleanup.
- `web/widgets/note.js` — full example. Demonstrates `ctx.size` adaptation,
  `ctx.settings`, the auto-rendered settings modal, multi-instance
  considerations, namespaced inline styles.

Copy either as your starting point. Have fun.

## Reference for AI

DASHBOARD WIDGETS:
- Manifest: `capabilities.widgets` = [{id (required, [a-z0-9_-]{1,64}, unique in plugin), name?, description?, icon?, render? (default widgets/{id}.js, path relative to plugin `web/`), sizes? (subset of 1x1|1x2|1x3|1x4, default ["1x1"]), default_size?, multi_instance? (default false), settings_schema? ([]), api_version? (1)}].
- Render contract: `export async function render(container, ctx)` -> `{ title, actions?, cleanup }`. container = the panel body div (mutate innerHTML; host owns surrounding chrome). cleanup is REQUIRED and must cancel every timer/listener/EventSource started.
- ctx: {plugin, widget_id, instance_id (UUID per placement), size, settings (merged defaults+user), pluginWebPath (/plugin-web/{plugin}/), api}.
- ctx.api: fetch(url, init?) (session cookie on same-origin), toast(msg, kind: success|error|info), listStorePlugins(opts?), pollForRestart(), navigateSettingsTab(name), openWidgetSettings(instance_id), registerCleanup(fn) — register cleanups BEFORE any await that might throw; registered + returned cleanups all run.
- actions: [{icon, label, onClick, kind?: 'danger'}] rendered in the Actions dropdown; a ⚙ Settings action is auto-appended when settings_schema is non-empty.
- settings_schema field types: text, textarea (rows), number (min/max/step), select (options [{value,label}] required), boolean, color; all take key (required), label, default, help. ctx.settings is a snapshot — mutating it persists nothing; saving goes through the settings modal only (V1).
- cleanup fires on: widget removed, resized (re-render new size), settings saved (re-render), dashboard tab left, plugin disabled/uninstalled/reloaded. Host try/catches a throwing cleanup.
- Rules: no hardcoded element ids when multi_instance (scope by class/container), no inline onclick=, prefix CSS classes with the plugin name; host utility classes (.dash-action-panel-info-line, .dim) are stable.
- Sizes are horizontal spans only (height is content-driven); mobile <=768px collapses to one column. api_version 1 promises: ctx only grows, api method signatures stable, render contract stable.
- Hot-reload limitation: after POST /api/plugins/{name}/reload, already-rendered widgets keep running OLD code until the dashboard tab is reloaded.
- Reference plugin: `plugins/sample-widgets/` (hello.js minimal, note.js full). Re-sign after edits: `python tools/sign_plugin.py plugins/{name}`.
