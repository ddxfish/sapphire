# Simple Screenshot

Lets the AI see your screen. Best-effort, one tool, no fuss.

## Tool

`get_screenshot(source="local")`

- **`source="local"`** (default) — silently captures this machine's full screen (all monitors as one image) and hands it to the model.
- **`source="user"`** — asks the user to paste/upload a screenshot themselves (works everywhere).

The AI just calls it when you ask it to look at your screen, a window, or an on-screen error.

## What works where

Capture happens on the machine **Sapphire runs on** (the "local" source). For most people that's the same machine they're sitting at; if Sapphire is remote/headless, use `source="user"` and paste instead.

| Environment | `source="local"` | Backend |
|---|---|---|
| Windows | ✅ silent | `mss` (`pip install mss`) |
| Linux — X11 | ✅ silent | `mss` |
| Linux — Wayland (wlroots: Sway/Hyprland) | ✅ silent | `grim` |
| Linux — Wayland (GNOME) | ✅ works² | `xdg-desktop-portal` (via `gdbus`, no install) |
| Linux — Wayland (KDE) | ✅ silent | `spectacle` |
| macOS | ✅ silent¹ | `screencapture` |
| anything else / nothing installed | falls back | tells the AI to ask you to paste a screenshot |

¹ macOS asks for Screen-Recording permission once.
² GNOME blocks silent third-party capture by design. The portal captures via GNOME's own shutter path (brief flash + sound), saving to your Pictures dir and reading it straight back. First use shows a one-time "Allow" dialog; silent after that. No package to install — `gdbus` ships with GNOME.

**If `local` can't capture** (no backend installed, headless, locked-down Wayland), the tool returns a message telling the AI to ask you to paste a screenshot — which always works, since Sapphire accepts pasted/uploaded images.

## Dependencies

- **Windows / Linux-X11 / macOS-via-mss:** `pip install mss` (imported lazily — the plugin still loads without it; you'll just get a "install mss" message on those systems).
- **Linux Wayland:** `grim` (wlroots) / `spectacle` (KDE) on PATH, or — on GNOME — nothing extra; the `xdg-desktop-portal` fallback drives `gdbus` (ships with glib).
- Pillow (for downscaling) — already shipped with Sapphire.

## Notes

- Captures the **full canvas** (every monitor in one image). It's downscaled to a long-edge cap (default 1568px, configurable in settings) before going to the model — vision models downscale anyway, so this just saves tokens.
- **Privacy:** ships disabled by default. Enabling it in a toolset is the consent — once on, the AI can capture the screen silently via `source="local"`. Don't add it to a default/shared toolset if that's not what you want.
- This is **server-side** capture (the Sapphire host's screen). A browser-based "share screen" button (`getDisplayMedia`) is a possible future addition; it needs a user click, so it isn't silent.
