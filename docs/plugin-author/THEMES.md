# Plugin Themes

Plugins can ship custom themes with CSS styling, animated JS backgrounds, and user-configurable settings. Themes appear in **Settings > Visual** as clickable cards with color previews.

## Quick Start

Add themes to your plugin manifest and provide CSS + optional JS files.

### plugin.json

```json
{
  "name": "my-theme-pack",
  "capabilities": {
    "themes": [
      {
        "id": "cyberpunk",
        "name": "Cyberpunk",
        "icon": "⚡",
        "description": "Neon city vibes with rain effects",
        "css": "themes/cyberpunk/cyberpunk.css",
        "scripts": ["themes/cyberpunk/rain.js"],
        "preview": {
          "bg": "#0a0a1a",
          "accent": "#ff00ff",
          "text": "#e0e0ff"
        },
        "settings": [
          {
            "key": "cyberpunk-rain-mode",
            "type": "select",
            "label": "Rain Effect",
            "default": "ambient",
            "options": [
              {"value": "off", "label": "Off"},
              {"value": "ambient", "label": "Ambient"},
              {"value": "reactive", "label": "Reactive"}
            ]
          }
        ]
      }
    ]
  }
}
```

### File Structure

```
plugins/my-theme-pack/
  plugin.json
  web/
    themes/
      cyberpunk/
        cyberpunk.css     # CSS variables + custom styles
        rain.js           # Optional animated background
        texture.png       # Optional assets
```

## Theme CSS

Theme CSS files override Sapphire's CSS variables. **Two runtime behaviors you must account for:**

1. **Paths are relative to your plugin's `web/` directory.** Manifest `css`/`scripts` values are served from `/plugin-web/{plugin}/`, which already points at `web/` — so write `themes/cyberpunk/cyberpunk.css`, NOT `web/themes/...` (the latter resolves to `web/web/...` and 404s).
2. **Your theme id is namespaced.** The runtime sets `data-theme="plugin:{plugin-name}:{theme-id}"` (colon-separated — colons can't appear in either part, so names with dashes never collide), not your bare `id`. Wrap your CSS in that prefixed selector — a bare `[data-theme="cyberpunk"]` will never match. For id `cyberpunk` in a plugin named `neon-pack`:

```css
[data-theme="plugin:neon-pack:cyberpunk"] {
    --bg: #0a0a1a;
    --bg-secondary: #12122a;
    --text: #e0e0ff;
    --text-muted: #8888aa;
    --trim: #ff00ff;
    --accent: #ff00ff;
    --border: #333366;
    /* ... full variable list in any core theme for reference */
}
```

See `static/themes/dark.css` for the complete list of CSS variables.

## Preview Colors

The `preview` object controls the color swatch shown in the theme picker card:

```json
{
  "bg": "#0a0a1a",      // Background color (leftmost bar)
  "bg2": "#12122a",     // Secondary background (optional, auto-derived if missing)
  "text": "#e0e0ff",    // Text color
  "accent": "#ff00ff",  // Accent/trim color
  "border": "#333366"   // Border color (optional)
}
```

## Bundle Defaults (font / bg / motion)

A theme can ship look-and-feel defaults beyond CSS. Each is a *default*: an explicit user pick in Settings > Visual always wins.

```json
{
  "id": "cyberpunk",
  "font": "mono",
  "bg": "themes/cyberpunk/city.webp",
  "motion": "rain",
  ...
}
```

- `font` — a font preset id (`system`, `rounded`, `serif`, `mono`, `handwriting`). Webfont presets download on first use.
- `bg` — a background image, path relative to your `web/` dir. It sits at the BOTTOM of the background chain: a chat's own scene and the global underlay both cover it.
- `motion` — a motion id to run by default (see Motions below). Use your own motion's **bare** id here — the runtime resolves it against your plugin's namespace first, or name a core motion (`stars`, `drift`, `fireflies`).

## Motions (`capabilities.motions`)

Motions are the supported way to ship animated backgrounds. A motion is an ES module with a mount/unmount contract — the runtime imports it, mounts it into a dedicated layer behind the chat transcript, and **calls `unmount()` before any switch**, so teardown is guaranteed (the old scripts[] lane had none).

### plugin.json

```json
{
  "capabilities": {
    "motions": [
      { "id": "rain", "name": "Neon Rain", "description": "Falling neon streaks",
        "script": "motions/rain.js" }
    ]
  }
}
```

`script` is relative to your `web/` dir. Your motion id is namespaced at runtime to `plugin:{plugin-name}:{id}`, same as themes.

### motion module contract

```js
let canvas, raf = 0, ro;

export default {
    id: 'rain', name: 'Neon Rain',
    mount(host, settings) {
        // host = the #motion-layer div (pointer-events:none, behind chat).
        // Create your canvas here; size it to the host (use a ResizeObserver —
        // the layer lives inside a flexing, sometimes re-parented container).
        canvas = document.createElement('canvas');
        host.appendChild(canvas);
        // ... start your requestAnimationFrame loop, keep the id in `raf`
    },
    unmount() {
        // MUST cancel rAF, disconnect observers, remove your nodes.
        cancelAnimationFrame(raf);
        ro?.disconnect();
        canvas?.remove();
    },
};
```

The runtime handles the hard parts for you — your motion is automatically unmounted while another view owns the chat surface (e.g. game-room story backdrops), while the tab is hidden, and while the chat surface is off-screen. Background images do NOT suppress motion — your canvas paints above the bg image and below the readability scrim. When the OS requests `prefers-reduced-motion`, theme-default motions never auto-start — but an explicit user pick in the picker still runs (a pick is consent).

`settings` passed to `mount()` includes two global user multipliers: `speed` (0.5 slow / 1 normal / 1.75 fast — multiply your velocities and cadences by it) and `intensity` (0.5 low / 1 normal / 1.75 high — multiply your particle/element counts by it). Any other keys you declare in your motion's `settings` object in the manifest are passed through alongside them. It is **remounted on theme switch**, so sample theme colors (e.g. `--trim`) at mount time and they will always be current. Don't add your own visibility handling.

Users pick motions in Settings > Visual > Background & Motion; the choice persists in localStorage (`sapphire-motion`).

## Animated Backgrounds (JS) — DEPRECATED

**Deprecated in favor of Motions (above).** The `scripts` array still works — scripts are loaded as `<script>` tags when the theme is activated and removed when switching away — but script-tag removal cannot stop a running animation loop; you must self-terminate. New themes should ship a motion instead. Use an IIFE to avoid polluting the global scope:

```js
(function() {
    'use strict';

    const canvas = document.createElement('canvas');
    canvas.id = 'cyberpunk-bg';
    canvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:-1;pointer-events:none;';
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    let animId;

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }

    function draw() {
        // Your animation logic here
        animId = requestAnimationFrame(draw);
    }

    resize();
    window.addEventListener('resize', resize);
    draw();

    // Cleanup when theme changes — check periodically.
    // IMPORTANT: the runtime stamps data-theme-script with the NAMESPACED id
    // `plugin:<your-plugin-name>:<theme-id>` (same namespacing as the CSS
    // note above), NOT the bare theme id. Using the bare id here makes this
    // check fire on its first tick and kill your animation ~1s after it
    // starts. Match your own <script> tag instead — it is always correct:
    const selfScript = document.currentScript;
    const cleanup = setInterval(() => {
        if (!selfScript || !selfScript.isConnected) {
            cancelAnimationFrame(animId);
            window.removeEventListener('resize', resize);
            canvas.remove();
            clearInterval(cleanup);
        }
    }, 1000);
})();
```

### Reacting to theme-setting changes

Theme JS can react when the user changes one of your theme's settings live:

```js
// Fired when a theme setting changes in the appearance panel
window.addEventListener('sapphire-theme-setting', e => {
    const { key, value } = e.detail;
    // re-apply your effect with the new value
});
```

(There is no AI-activity event for themes — `thinking`/`speaking`/`idle`/`tool_call` hooks are not exposed to theme JS.)

## Theme Settings

Declare settings that users can configure per-theme. Settings appear in a panel below the theme picker when the theme is active.

### Setting Types

**Select (dropdown)**
```json
{
  "key": "cyberpunk-rain-mode",
  "type": "select",
  "label": "Rain Effect",
  "help": "Controls the animated rain overlay",
  "default": "ambient",
  "options": [
    {"value": "off", "label": "Off"},
    {"value": "ambient", "label": "Ambient"},
    {"value": "reactive", "label": "Reactive"}
  ]
}
```

Options can also be simple strings: `"options": ["low", "medium", "high"]`

**Boolean (checkbox)**
```json
{
  "key": "cyberpunk-scanlines",
  "type": "boolean",
  "label": "Scanlines",
  "help": "CRT scanline overlay effect",
  "default": "true"
}
```

**Range (slider)**
```json
{
  "key": "cyberpunk-intensity",
  "type": "range",
  "label": "Rain Intensity",
  "min": 1,
  "max": 10,
  "step": 1,
  "default": "5"
}
```

**Text (freeform)**
```json
{
  "key": "cyberpunk-custom-color",
  "type": "text",
  "label": "Custom Accent",
  "help": "Hex color code",
  "default": "#ff00ff"
}
```

### How Settings Work

- All settings are stored in **localStorage** with the key you specify
- Your theme JS reads them via `localStorage.getItem('cyberpunk-rain-mode')`
- The Sapphire settings panel reads/writes the same keys — zero bridging needed
- Changes fire a custom event for instant reactivity:

```js
window.addEventListener('sapphire-theme-setting', e => {
    const { key, value } = e.detail;
    if (key === 'cyberpunk-rain-mode') {
        setRainMode(value);  // Update live without reload
    }
});
```

### Chat Style Settings

A common pattern is "frosted glass" chat messages. If your setting key matches `*-chat-style`, Sapphire automatically sets a `data-{theme}-chat` attribute on the document element:

```json
{"key": "cyberpunk-chat-style", "type": "select", "label": "Chat Style", "default": "transparent",
 "options": [{"value": "transparent", "label": "Transparent"}, {"value": "glass", "label": "Frosted Glass"}]}
```

Then in your CSS:
```css
[data-cyberpunk-chat="glass"] .message-content {
    background: rgba(10, 10, 26, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 0, 255, 0.2);
}
```

## Legacy Theme Plugins

If your theme plugin exposes themes via `window.sapphireThemes.getAll()` instead of the manifest format, Sapphire will still discover and display them. Settings are supported if each theme object includes a `settings` array.

This is the backwards-compatible path — new themes should use the manifest format.

## Tips

- Use Sapphire's CSS variables (`var(--bg)`, `var(--text)`, etc.) in your theme CSS so elements inherit properly
- Core themes in `static/themes/` are good references for the full variable list
- Animated backgrounds should use `z-index: -1` and `pointer-events: none`
- Always provide a cleanup mechanism in your JS (canvas removal, interval clearing)
- Keep performance in mind — offer a performance tier setting so users on slow machines can dial it down
- Test with both light and dark base themes to ensure your variables cover everything
