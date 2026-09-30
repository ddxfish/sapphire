# wardrobe

Base + garment **layer** maker for Game Room story packs. Qwen-Image-2.1 through a **running ComfyUI**
(default `http://127.0.0.1:8188`) — no models on this side, it only talks to the API.

```
conda activate wardrobe
python tools/wardrobe/wardrobe.py --story user/plugins/<pack>/stories/<slug>/story.json
python tools/wardrobe/wardrobe.py --base backdrops/sapphire-base.png --out backdrops/   # straight to Wardrobe
```
`--story` fills the item dropdown from `start_items` (those with a `wears` slot) and points `--out` at the
pack's `backdrops/`. `--scale 1` on a 1080p screen (default 2 = 4K).

## Base tab
Go ×N from the prompt → click the best → **refine selected** ("put her hair behind her back") → Go ×N →
click → **Keep as base** → `<name>-base.png`. Bases are RGBA on a transparent background: keep the RGBA
sentences at both ends of the prompt. Frame is **736×1216** (2.1 works in multiples of 32; the card is
percent-based so 720×1200 vs 736×1216 makes no difference). Refining an older base keeps its size.

## Wardrobe tab
Pick the item (story key, underscores: `black_leggings`), "Add black leggings. Keep everything else exactly
the same, keep the transparent background.", Go ×N (~12s each), click one, **Save layer** →
`backdrops/layer-<item>.png` + recipe and raw result in `backdrops/wardrobe/`. Picking an item that has a
saved raw result reloads it, so you can re-cut without regenerating.

**Wear while generating**: check saved layers to stack under this generation (wear the top while making
the blazer). The new layer still holds only the new garment.

## The cut
A layer holds pixels that **changed** against what the model was shown **and sit inside the base's own
alpha**. The model re-renders her edge as a ~4 px semi-transparent halo — that halo is outside her alpha,
so it can never reach a layer. Thin edge ribbons die in a 7 px morphological open; islands under
*min blob* are dropped. Two sliders, both usually untouched.

**May stick out** (coats, hats, bags): new pixels outside her count too, and that outer edge is shaved a
few px to take the halo with it.

## Wiring the pack
- cast `image` → `<name>-base.png`
- each garment → a `start_items` entry with `"wears": "<slot>"` (slot order: underwear, bra, socks, pants,
  shirt, shoes, body, jacket, outer, hat, in_hand)
- optional `icon-<item>.webp` beside the layers for the slot tiles

## Workflow template
`qwen21-edit.api.json` is a ComfyUI **API-format** export of the native 2.1 edit workflow. At runtime the
prompt goes in as a literal, the sampler is wired straight to the encoder's latent (edit) or to the empty
latent (text-to-image), and everything not feeding the save node is pruned — so the prompt-enhancer branch
and any preview/compare nodes in the export are ignored. Re-export freely (`--workflow` to point elsewhere);
the patcher finds nodes by class, not id.
