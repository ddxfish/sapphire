# sd-server — local image generation (stable-diffusion.cpp)

Fast local text-to-image for Sapphire via **sd-server** (the server mode of
[stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)). It's a
**generic provider** — it ships tuned for **Z-Image Turbo** but you can point it at
SDXL or any model sd-server can load (see *Switching models* below).

Three surfaces:
- **Tool** (`generate_image`) — Sapphire generates, sees a labeled grid, recreates by seed.
- **Image Studio** (Apps page) — hands-on generation **and** a wildcard **Slideshow** builder.
- **Reel** (chat sidebar accordion) — plays the active slideshow profile while you chat.

> Separate from the older SDXL `image-gen` plugin, which is untouched.

## The tool — `generate_image`
- **`prompt`** (required) · **`count`** (1; >1 → a numbered grid the model can see) ·
  **`seed`** (omit = random; set to reproduce) · **`width`/`height`** (1024) ·
  **`steps`** (8) · **`cfg_scale`** (1.0) · **`negative_prompt`** ("").
- Every result includes a **recipe** (prompt + params + per-image seed) for exact recreation.
- Images flow through the core tool-image path: a **vision model sees them**; a text-only
  model gets a CLIP description and the user still sees the full image.
- Configure **AI/User names → appearances** in Settings; the model writes "Sapphire"/your
  handle and the plugin swaps in the physical description (it never spells one out).

## Slideshow (Image Studio → Slideshow tab)
A **wildcards / dynamic-prompt** builder. You define **slots** (user-named textareas, one
option per line); each image picks one random line per slot, joins them, runs the same
name-swap + static-keyword expansion, and generates.

- **Profiles** — named slot-sets you save and switch between (e.g. "Weekday business",
  "Weekend casual"). Switching a profile swaps the whole vibe. Stored in plugin settings.
- **Aspect** — checkboxes (square / portrait / landscape); each image picks a random one
  among those checked (check all three = mixed).
- **Seconds between** — the GPU cooldown: the gap between a finished image and the next
  request, so it doesn't hammer the card.
- **Preview prompt** — assembles one random combo as text (no GPU) so you can tune slots.
- Ephemeral **ring buffer** of recent images with **♥ / ⬇** to save a keeper to disk.

## Reel (chat sidebar)
A compact accordion in the chat sidebar: **▶ Start / ■ Stop** + the current image. It plays
the **active** slideshow profile on its cooldown so it runs ambiently while you chat, and
**pauses when the tab is hidden**. (It stops on chat-switch; just hit Start again.)

## Requirements
- An **sd-server** instance reachable at the configured **sd-server URL**
  (default `http://127.0.0.1:7861`).
- For Sapphire to *see* her own images, run her on a **vision-capable model**.

## Settings
| Setting | Default | Notes |
|---|---|---|
| sd-server URL | `http://127.0.0.1:7861` | where sd-server listens |
| Max images per call | 6 | protects VRAM/time |
| Default steps / CFG | 8 / 1.0 | **model-specific** — see below |
| Default width/height | 1024 | base size; slideshow aspect overrides |
| Default negative prompt | (none) | Z-Image needs little; SDXL wants more |
| Per-image timeout | 180s | per single image |

## Switching models
sd-server is model-agnostic — point it at a different checkpoint and **retune three
settings**. Load the model in sd-server (its `-m model.gguf`/`--diffusion-model` flag), then
in the plugin Settings:

| Model | Steps | CFG | Negative | Size |
|---|---|---|---|---|
| **Z-Image Turbo** (default) | 8 | 1.0 | little/none | 1024² |
| **SDXL** | ~25–30 | ~6–8 | helpful (e.g. "blurry, lowres, extra fingers") | 1024² |
| **SD 1.5** | ~20–30 | ~7 | helpful | 512² |

The slideshow aspect dims (square 1024², portrait 832×1216, landscape 1216×832) suit SDXL/
Z-Image; for SD 1.5 lower the base to 512 in Settings. Everything else (slots, profiles,
the Reel, recreate-by-seed) is model-independent.

## Notes
- Seeds are assigned client-side, so the recipe is always complete and reproducible.
- Reproduction is deterministic at fixed seed + params (may differ trivially if the
  server's attention backend isn't bit-exact).
