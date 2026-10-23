# Appearance — themes, fonts, scenes, and ambient motion

Everything that controls how Sapphire looks: color themes, fonts, text size and spacing, background scenes, and ambient motion — plus per-chat overrides and the tools that let Sapphire restyle the view herself.

## What it is

**Settings > Visual** is the global appearance control room: pick a theme, a font, text sizing, a background scene, and an ambient motion. Each chat can then override the accent color, scene, and motion for itself from the 🎨 **Appearance** button in the chat sidebar — and Sapphire has tools (`set_scene`, `set_motion`) to change the current chat's look mid-conversation.

## Using it

### Themes (Colors)

The theme grid at the top of Settings > Visual shows every installed theme as a miniature live preview — each card renders the theme's real colors (nav rail, chat bubbles, composer), not an approximate swatch. Click a card to switch instantly; there is no save button.

- **Core themes** ship with Sapphire — the canonical Dark, a clean Light, and moodier picks like Sapphire, Synthwave, Noir, Terminal, and Abyss. Some bundle extras: Abyss starts a fireflies motion by default; Paper switches to a handwriting font over a paper background.
- **Plugin themes** appear in the same grid once their plugin is installed and enabled.
- A card marked **animated** carries legacy theme scripts (older plugin themes).
- If the active theme declares its own settings (common with plugin themes), a settings panel appears under the grid. Those knobs belong to the theme and apply live.

Your theme choice is saved **in the browser**, per device — your phone and desktop can wear different themes. Open tabs in the same browser follow along automatically. If a theme's plugin is later disabled, Sapphire falls back to the default theme and tells you with a toast.

**Bundles vs. your picks:** a theme may bundle a default font, background, or motion, but an explicit pick you make always outranks the bundle — including explicitly picking the System font or the None motion.

### Type (fonts)

The font cards switch the app-wide font. System uses your device's native font; the others (Rounded, Serif, Monospace, Handwriting) are open-licensed webfonts. A card showing a **⤓** badge downloads its font on first use — a few hundred KB, verified against a pinned hash on the server — then applies without a reload and loses the badge. A **⚠** badge means the download failed; click again to retry.

Your font pick is per-browser and beats any theme's bundled font.

### Spacing and text size

Under **Options**: **Spacing** (Compact / Default / Comfortable) adjusts UI density, and **Font Size** (Small through Extra Large) scales all text — it stacks with the chosen font's own sizing. Both apply immediately and are per-browser.

### Background scenes (the global underlay)

The scene library under **Background & Motion** sets the **global underlay** — the background shown behind any chat that has no scene of its own. Click a tile to apply it (saves immediately), **None** to clear, or **+ Upload** to add your own: any common image format is accepted, named on upload, and re-encoded server-side with a thumbnail. Delete a scene with the **×** on its tile; a search box appears once the library grows past a dozen or so.

The scene library is **server-side** — every device shares the same scenes and underlay. The same picker appears in three places: here (global), in the per-chat 🎨 modal (that chat only), and on the persona page.

Background layers resolve top-down: **chat scene → global underlay → theme background → plain theme color.**

### Ambient motion

Motion cards run an animation behind the chat — above the background image, below the readability scrim (a snowfall over a winter scene is the point). Core motions: Snow, Stars, Fireflies, Nibblers, Triangles, Coder; plugin motions join the row automatically. **Speed** and **Intensity** apply to whichever motion runs.

The pick here is your **global default**; **None** switches motion off even for themes that bundle one. Motion pauses automatically while the tab is hidden or the chat surface is off screen, so it never burns cycles invisibly.

**Reduced motion:** when your OS asks for reduced motion, theme-bundled motions never auto-start — but an explicit pick (here or per-chat) still runs, because a pick is consent. A note under the row explains the current state.

### Per-chat Appearance (the 🎨 modal)

The 🎨 button in the chat sidebar opens the per-chat **Appearance** modal — accent color, scene, and motion for this chat only. Everything in it saves directly to the chat as you change it; the Done button just closes the modal.

- **Accent color** — previews live while you drag, saves when you release. It tints the trim across the UI whenever this chat is open. **Reset** clears the chat's own accent, falling back to the theme's trim.
- **Scene** — the same scene picker; a pick here overrides the global underlay for this chat.
- **Motion** — **Default** follows your global pick; anything else overrides it for this chat, and a per-chat pick runs even under OS reduced-motion.

Per-chat values live in the chat's settings, so they follow the chat to any device — unlike the browser-side global picks. Personas can carry an accent of their own, applied the same per-chat way.

**Resolution:** per-chat beats global everywhere it exists — chat scene > global underlay; chat motion > global pick > theme bundle; chat accent > theme accent.

### Sapphire's own tools

Two tools let Sapphire restyle the current chat live (when her active toolset includes them):

- **set_scene** — sets the chat's background scene ("let's talk on the boat"). The tool's description carries a live menu of the scene library, so she always knows what's available; `none` clears back to your underlay.
- **set_motion** — sets the chat's ambient motion; called with no name it lists what's installed; `none` clears back to your default.

Both write the same per-chat settings the 🎨 modal writes, and the page updates live as she calls them.

### Other options

- **Send Button** — color the send button with the active trim instead of the provider indicator.
- **Icon Color** — tints the gem logo and favicon for this Sapphire instance (handy when you run more than one). An active chat/persona trim overrides it. Saved with the Settings **Save** button.
- **Avatars In Chat** — show or hide avatars next to messages. Saved with the Settings **Save** button.

## Settings

| Setting | Where | Scope | Notes |
|---|---|---|---|
| Theme | Visual > Colors | per browser | instant; open tabs sync |
| Theme's own settings | panel under the theme grid | per browser | only when the theme declares any |
| Font | Visual > Type | per browser | webfonts download on first use |
| Spacing / Font Size | Visual > Options | per browser | instant |
| Send Button trim | Visual > Options | per browser | instant |
| Global scene underlay | Visual > Background & Motion | server (all devices) | saves immediately |
| Motion + Speed / Intensity | Visual > Background & Motion | per browser | the global default |
| Icon Color | Visual > Options | server | Save button; trim overrides it |
| Avatars In Chat | Visual > Options | server | Save button |
| Accent / scene / motion per chat | chat sidebar 🎨 | per chat | overrides globals; travels with the chat |

## Quick Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| Theme or layout looks broken after an update | The browser is serving cached files | Hard refresh (Ctrl+Shift+R) |
| A motion won't play | OS "reduce motion" is on — theme-bundled motions never auto-start under it | Pick the motion explicitly in Visual or the chat's 🎨 modal (a pick is consent) |
| One chat ignores the global scene | That chat has a scene of its own | Open the chat's 🎨 modal and pick **None** |
| Motion row highlights one thing, screen shows another | The open chat overrides the global pick — a note under the row says so | Change it from the chat's 🎨 modal |
| Theme reverted to default with a warning toast | The theme's plugin was disabled or removed | Re-enable the plugin, then re-pick the theme |
| Font card shows ⚠ | The webfont download failed | Check connectivity and click the card again |
| Another device looks different | Theme, font, and motion picks are per browser | Set them on each device — scenes and per-chat looks are shared |

## See also

- [PERSONAS.md](PERSONAS.md) — personas and the look they carry
- [TOOLS.md](TOOLS.md) — the tool system behind `set_scene` / `set_motion`
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general UI troubleshooting
- [plugin-author/THEMES.md](plugin-author/THEMES.md) — shipping your own themes and motions in a plugin

## Reference for AI

Visual surface = Settings > Visual (global) + chat sidebar 🎨 Appearance modal (per chat) + AI tools.

BROWSER-LOCAL PICKS (localStorage, per device):
- theme: `sapphire-theme` — applied by core/theme.js applyTheme(); tabs sync via storage events; dead id falls back to default + toast
- font: `sapphire-font` (system|rounded|serif|mono|handwriting) — explicit pick > theme bundle font
- density: `sapphire-density` (compact|comfortable; unset=default); text scale: `sapphire-fontsize` (small|large|xlarge)
- motion: `sapphire-motion` ('none' = explicitly off); multipliers: `sapphire-motion-speed` / `sapphire-motion-intensity` (0.5|1|1.75)
- send button trim: `sapphire-send-btn-trim`

SERVER SETTINGS: DEFAULT_BACKGROUND (global scene underlay, saves immediately from the Visual picker), ICON_COLOR (gem+favicon tint; active trim overrides), AVATARS_IN_CHAT.

PER-CHAT (chat settings rows, direct save from the 🎨 modal): trim_color, background (scene name), motion (motion id).

RESOLUTION ORDERS:
- background: chat scene > DEFAULT_BACKGROUND > theme bg > blank
- motion: chat motion > explicit user pick > theme bundle > none; OS prefers-reduced-motion gates THEME-DEFAULT motions only (an explicit or per-chat pick is consent)
- font: explicit pick > theme font > system
- accent: chat/persona trim_color > theme accent; icon tint: active trim > ICON_COLOR > sapphire blue

SCENES: library stored server-side as webp + thumbnail (uploads re-encoded, common image formats); picker mounts in Visual, the chat 🎨 modal, and the persona page; registry via /api/backgrounds.

MOTIONS: core set snow, stars, fireflies, nibblers, triangles, coder; plugin motions minted as plugin:{plugin}:{id}; registry via /api/motions; auto-paused when tab hidden, surface off-screen, or a view owns the chat surface (story rooms); paints above bg image, under readability scrim.

THEMES: core registry static/themes/themes.json (default 'dark'); Abyss bundles the fireflies motion, Paper bundles handwriting font + paper bg; plugin themes minted as plugin:{plugin}:{id}; registry via /api/themes; theme cards are live [data-theme]-scoped previews.

AI TOOLS: set_scene(name) in functions/scene.py — per-chat scene, live scene menu in the tool description, 'none' clears; set_motion(name) in functions/meta.py — per-chat motion, no-arg lists motions, 'none' clears. Both update chat settings and publish a live repaint event.
