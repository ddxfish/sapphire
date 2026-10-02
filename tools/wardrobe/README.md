# wardrobe

Base + garment **layer** maker for Game Room story packs (PySide6). Qwen-Image-2.1 through a **running
ComfyUI** (default `http://127.0.0.1:8188`) — no models on this side, it only talks to the API.

```
conda activate wardrobe            # numpy, opencv, pillow, requests, PySide6
python tools/wardrobe/wardrobe.py --out tmp/wardrobe/sapphire                                  # Base tab
python tools/wardrobe/wardrobe.py --base tmp/wardrobe/sapphire/sapphire-base.png --out tmp/wardrobe/sapphire
```
Work in a staging folder, then copy the winners into a pack's `backdrops/`. `--story <story.json>` seeds
the item dropdown from its `start_items` and points `--out` at the pack directly. `core.py` holds the
Comfy client, the graph patcher and the cut; `wardrobe.py` is the Qt app.

## Base tab — one source image
**Load source…** (her face) or Go **from text**. Candidates land in the strip; click one to see it beside
the source; **◀ Use selected as source** promotes it. Under the candidate's label, how to look at it:
**candidate** as is · **changed** (yellow = colour changed, red = became clear, cyan = became solid) ·
**difference** (heat: how far each pixel moved, blue = alpha only) · **onion** (half and half) · **alpha** (its
alpha as colour). The compare views need a source of the same size; a new canvas shows the candidate itself. Modes: **edit this** (same size, the prompt is the
change — "neutral expression, hair behind her head"), **full body from this** (736×1216 canvas, the source is the
model's reference), **from text**, **region: detail** / **add/remove** (zoom the source pane; the crop goes up at
~1024 px), **region: replace** (the zoomed part is wiped first — nothing wrong left to copy — and rebuilt from
the prompt and its surroundings; only the hole comes back). **Save source…** = a checkpoint PNG. **Keep as base** →
`<name>-base.png`. Bases are RGBA on a transparent background — keep the RGBA sentences at both ends of
the body/text prompts. The base prompts dress her in a plain grey athletic top and shorts: a base wears that
underneath everything, and garments stack over it. Frame is **736×1216** (2.1 works in multiples of 32; the card is percent-based).

**snap alpha** (on by default) snaps the soft alpha the model returns (≤25 clear, ≥230 solid) on every
result and on load/save. Switch it off when an edit comes out dark or see-through where it should not.
`region: detail` never snaps and never looks at the result's alpha: only its colour comes back, blended
in with a soft edge, so a region can no longer fade to black in the middle (2026-10-01).

Pose that makes every garment clean: a relaxed A-pose with a visible gap between hands and thighs, hair
out of the way (bald if you'll use hair layers), neutral mouth and open eyes (the floor for expressions).

## Wardrobe tab
Previews: wheel = zoom at the cursor, drag = pan, double-click = fit; the two panes move together. On every
tab the bar between the panes and the right column drags — give the column room when its rows wrap.
**Delete** removes every file of the typed item (asks first).

Name the item (free text, underscores: `black_leggings`, `face_smile`, `hair_front_long`, `hair_back_long` — a
slot prefix picks the slot), pick the **slot**, Go ×N (~12s each), click one, **Save layer** → `<out>/layer-<item>.png`,
a browsable `<out>/preview/<item>.png` (layer alone | over base) and the recipe + raw result in
`<out>/wardrobe/` (provenance only; the app never reads the raw result back). **(none)** at the top of the item list is just for looking: her with the worn layers, nothing
to generate or save.

The strip belongs to the picked item. First is what it **is now** — `saved`, its layer on disk worn as the card
wears it, or `blank` when nothing is saved yet — then whatever you generate this session (`seed N`). Click between
`saved` and a result to compare old and new; hover a thumbnail for what it is. The sliders cut a generated result;
`saved` is fixed — to change a saved item, generate again (or fix it on the Tools tab). Picking an item brings its recipe
back (slot, sliders, worn layers); a Go replaces the item's generations, Refine adds to them; results never leak
between items, and a batch that lands while you're on another item waits under its own.

Every result remembers what she was wearing when it was made and is always cut against *that* — the wear boxes
only decide what the next Go sees (the LEFT pane). Checking a shirt after the fact no longer turns the shirt's
whole area into "change" that out-scores the hat (2026-10-02).

Slots (bottom→top on the card): hair_back, face, underwear, bra, socks, pants, shirt, shoes, body, jacket,
outer, hair_front, hat, in_hand (+ pack, holster on the side). The slot sets the cut's shape:
- **face** — a head patch, no diff: the whole head down to the neck, feathered there. Expressions are
  subtle, a patch is exact because the head doesn't move.
- **hair_back / hair_front** — two slots, two generations (2026-10-02; the old `hair` slot split one layer
  by her silhouette, which put hair on the shoulders in front of the hoodie). **hair_back** is drawn **under**
  the portrait: behind her, so her body and every garment cover it — what shows is beside and below her.
  **hair_front** is over every garment, under the hat: the crown, the bangs, strands falling in front. Both
  stick out. A bald base is what makes them stack cleanly; prompt the back as "falling behind her shoulders".
- **shoes / socks** — two pieces. Everything else — one piece, inside her silhouette.

**Wear while generating**: check saved layers to stack under this generation (wear the top while making
the blazer). The new layer still holds only the new garment.

**key colour** (Cut box): a chroma key instead of the diff — the layer is every pixel of that hue that wasn't
already that hue in what the model saw, and nothing else. Layers are additive: a worn keyed layer the model
repaints stays out of the new one, but where it repaints it a little differently a sliver gets through — making
a second hair layer over a worn one, give it a different key (green front over blue back). For hair, which re-lights her whole face so the diff can't separate it: prompt **bright blue hair**, cut by
key (blue, ±35°), save, then on the Tools tab **recolour → to colour…** and pick the brown. Every strand keeps its
own lightness; only the hue and strength move. Threshold and the skin guard don't apply to a keyed cut.

**Refine picked**: the model reads the *picked result* instead of the base, and the prompt is the fix — "remove
the stray strand on the right", "make the left shoe match the right". With **zoomed region** only the LEFT
pane's rectangle is redone, at hi-res. The cut still diffs against the base, so the layer stays just the
garment; the new candidates join the strip beside the old ones. Loop: pick → zoom → prompt the fix → Refine.

**zoomed, blanked (replace)**: the LEFT pane's rectangle is wiped to transparent before the model sees it, so
wrong feet or a bad sleeve can't be copied; the prompt says what belongs there and the model rebuilds it from the
surroundings (it sees the hole plus half its size around; only the hole is pasted back). Works from the base
(Go) or the picked result (Refine). The Base tab has the same as **region: replace**. If the model leaves the
hole empty instead of filling it, say so — the fallback is wiping to a flat colour instead of transparent.

## Preview tab — a viewer
Her in the middle, every saved layer in `--out` as a checkbox grouped by slot in the card's order (hair_back
goes under her). **her (the base)** off = clothing alone; **on** none / black / white / grey;
**view** image / alpha (the composite's alpha as colour) / coverage (what the layers change against her).
**Export PNG…** writes the composite exactly as shown; 'none' keeps it transparent. **refresh** reads
`--out` again after a Save layer; the tab also refreshes when you open it.

## Tools tab — fix a source or a saved layer
Pick **source image** (the Base tab's source) or a saved layer. Four linked panes: before, after, a change map,
after over her. **trim opacity** snaps alpha and drops islands; **trim edges** erodes, grows (grown pixels take
the nearest colour) or defringes the outline. **store A/B/C** remembers an after-image + settings. **◀ Apply**
rewrites the layer on disk (the original kept once in `wardrobe/<item>.layer.bak.png`) or makes the after-image
the Base tab's source.

**recolour**: hue °, colour % and lightness of every pixel, alpha and texture untouched — **to colour…** picks the
colour the garment should be and sets the three. Blue hair → brown hair, strand for strand.

**eraser**: brush on, then on the top-left pane — LEFT erases, RIGHT restores, MIDDLE drags, wheel zooms;
hard edge, size in image pixels, the red ring is the brush. Ctrl+Z undoes a stroke, **clear** forgets them all.
Erasing is applied to the input *before* the trims, so the loop for a jagged edge is: zoom, brush the junk off,
trim 1 px, grow 1 px, Apply. The erase mask lives with the picked item until you pick another or Apply.

## The cut
A layer holds pixels that **changed** against what the model was shown **and sit inside the base's own
alpha**. The model re-renders her edge as a ~4 px semi-transparent halo — that halo is outside her alpha,
so it can never reach a layer. Thin edge ribbons die in a 7 px morphological open; islands under
*min blob* are dropped. Two sliders, both usually untouched.

**Her outline is not an edge.** Where a garment runs along her silhouette (the side of a leg, a shoulder) the
cut keeps it at exactly her alpha: trim and feather act only on the garment's *free* edges — where it ends
on skin — and in stick-out mode the model's soft rendering of her edge no longer caps the layer inside her.
Until 2026-10-01 both did, and every leg wore a 3–4 px ramp of skin bleeding through. **trim edge px** shaves
the free edges (the last sliver of re-drawn skin where the garment ends; half-px steps). **reach her edge px**
(default 2) fills a gap of up to that many px between the garment and her outline — the model stopping a px
short, the open rounding off a toe — in the garment's own colour; 0 = exactly as the model drew it.

A pixel the model left semi-transparent carries junk colour (its blend with whatever backdrop it invented), and
the model draws her shoulder a px narrower than the base, so just inside her outline its alpha dips. Every such
pixel in a layer takes its colour from the solid garment beside it — otherwise it shows as a border of bright
specks now that the garment reaches her edge (2026-10-02).

**May stick out** is for garments that really do (coats, hats, hair): outside her, anything solid in the result
counts — including a backdrop the model invented around her in a region edit (a grey band 5–10 px out). A
garment that hugs her — leggings, a tee — wants it **off**; inside mode now reaches her edge on its own.
The model's halo (a soft ring hugging her outline) is dropped only where nothing solid lies past it: hair is
soft at her old outline too, and dropping it there bit a notch out of long hair and beheaded every wisp
crossing the ring (2026-10-02). The 7 px open that kills her re-rendered edge runs inside her only.

**ignore skin re-shading** drops changed pixels that are *her* skin colour — the median of a strip down the
middle of her face — both before and after: the model re-lighting her, re-tinting her legs beside the leggings,
shading her jaw under new hair. Hair drawn over hair passes (it used to be dropped as "same hue, modest change",
so hair thresholded out before her face did — 2026-10-02). A garment the model draws a px short of her outline
with re-tinted skin in between is also caught here, then *reach* fills the gap in garment colour.

**Hair over a base that has hair** (a ponytail base, long-hair layer): threshold **4** with the guard **on**
picks up hair-over-hair. Where the new hair matches the old exactly the diff sees nothing, so the layer has holes
that show the old hair — invisible on that base, but the layer is only complete over a bald base, and old hair
the new style doesn't cover (a bob over a ponytail) will show. Bald base + the default hairstyle as its own
hair layer is the clean setup.

**May stick out** (coats, hats, bags, hair): new pixels outside her count too, and that outer edge is shaved
a few px to take the halo with it. In this mode the garment's own alpha rules (capping opaque pants to her
soft leg edge drew a pale line along each leg — fixed 2026-09-30).

**pieces** keeps only the N largest blobs: the model re-shades skin in small islands elsewhere (a hand
edge, a shin), and a garment is one or two pieces.

## Wiring the pack
- copy `<name>-base.png` and `layer-<item>.png` into the pack's `backdrops/`
- cast `image` → `<name>-base.png`; add `face` / `hair_front` / `hair_back` to the cast's `slots` if you use them
- each garment → a `start_items` entry with `"wears": "<slot>"` (the game's gear editor does this)
- optional `icon-<item>.webp` beside the layers for the slot tiles

## Workflow template
`qwen21-edit.api.json` is a ComfyUI **API-format** export of the native 2.1 edit workflow. At runtime the
prompt goes in as a literal, the sampler is wired straight to the encoder's latent (edit) or to the empty
latent (text-to-image), and everything not feeding the save node is pruned — so the prompt-enhancer branch
and any preview/compare nodes in the export are ignored. Re-export freely (`--workflow` to point elsewhere);
the patcher finds nodes by class, not id.
