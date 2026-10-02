"""wardrobe core — Qwen-Image-2.1 through ComfyUI, the layer cut, and the file conventions. No UI here."""
import copy, hashlib, io, json, os, time
import numpy as np, cv2, requests
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
FRAME = (736, 1216)     # the portrait frame: Qwen-Image-2.1 works in multiples of 32; the card is percent-based
LAYER_ORDER = ["hair_back", "face", "underwear", "bra", "socks", "pants", "shirt", "shoes", "body", "jacket", "outer", "hair_front", "hat", "in_hand"]   # the card's stack, bottom → top
UNDER = ("hair_back",)                                         # drawn UNDER the portrait: behind her, so her body and every garment cover it
SLOTS = LAYER_ORDER + ["pack", "holster"]                      # every slot the card knows a pin for
PAIRED = ("shoes", "socks")
SLOT_WORDS = {   # item-name words → slot, so the slot box follows what you type
    "underwear": ("underwear",), "bra": ("bra",),
    "socks": ("socks", "sock", "stockings", "tights"), "pants": ("pants", "leggings", "jeans", "trousers", "slacks", "shorts", "skirt", "sweatpants", "joggers"),
    "shirt": ("shirt", "top", "tee", "tshirt", "blouse", "hoodie", "sweater", "tank", "camisole", "turtleneck"),
    "shoes": ("shoes", "boots", "heels", "sneakers", "sandals", "flats", "loafers"), "body": ("dress", "gown", "suit", "jumpsuit", "robe", "overalls", "armor", "armour", "spacesuit", "wetsuit", "onesie"),
    "jacket": ("jacket", "blazer", "cardigan", "vest", "waistcoat"), "outer": ("coat", "trenchcoat", "cloak", "cape", "parka", "poncho"),
    "hat": ("hat", "cap", "beanie", "helmet", "crown", "tiara", "headband", "hood"), "hair_front": ("hair", "ponytail", "braid", "bun", "bangs", "wig"), "hair_back": (),
    "face": ("face", "expression", "smile", "frown", "laugh", "wink", "blush"), "in_hand": ("gloves", "mug", "phone", "book", "bag_in_hand"), "pack": ("backpack", "pack", "purse", "satchel")}


def slot_for(item):
    """A slot from the item name: a slot prefix wins, else the first known word anywhere in the name. None if nothing matches."""
    words = item.lower().replace("-", "_").split("_")
    if "_".join(words[:2]) in SLOT_WORDS: return "_".join(words[:2])      # hair_back_long, in_hand_mug
    if words and words[0] in SLOT_WORDS: return words[0]
    for sl, ws in SLOT_WORDS.items():
        if any(w in ws for w in words): return sl
    return None                                    # slots whose garment is two pieces
STICKS_OUT = ("hair_front", "hair_back", "hat", "outer", "pack")   # slots that usually extend past her silhouette
HAIR = ("hair_front", "hair_back")
KEEP = " Keep everything else exactly the same, keep the transparent png background."
GARMENT_TPL = "Add {what}." + KEEP
SLOT_TPL = {"face": "Change her expression to {what}." + KEEP, "hair_front": "Give her {what} hair." + KEEP,
            "hair_back": "Give her {what} hair, falling behind her shoulders and back." + KEEP}
RGBA = "This is an RGBA image with transparency. {body} The image has alpha channel and the background is transparent."
BASE_PROMPT = RGBA.format(body="A full-body photo of a woman standing straight, facing the camera, arms relaxed at her sides, "
                               "feet slightly apart, wearing a plain grey fitted athletic top and fitted grey shorts, whole body in frame with space above the head and "
                               "below the feet, even soft studio lighting.")
REF_PROMPT = RGBA.format(body="The same person as in the reference image, same face and hair. Zoom out to a full-body photo: "
                              "standing straight, facing the camera, arms relaxed at her sides, feet slightly apart, wearing a plain grey fitted athletic top and fitted grey shorts, "
                              "whole body in frame with space above the head and below the feet, even soft studio lighting.")
EDIT_HINT = "Make her expression neutral and put her hair behind her head. Keep everything else exactly the same."
REGION_HINT = ("Refine this close-up: make the eyes sharp and detailed with natural irises, pupils and eyelashes, clean skin "
               "texture. Keep the identity, expression, colors, lighting and everything else exactly the same, keep the "
               "transparent png background.")
REGION_ADD_HINT = ("Make her hair longer, falling over her shoulders. Keep everything else exactly the same, keep the "
                   "transparent png background.")
REGION_BLANK_HINT = ("The missing part is her feet, standing flat, seen from the front, matching her skin and lighting. "
                     "Keep everything else exactly the same, keep the transparent png background.")
GARMENT_HINT = GARMENT_TPL.format(what="black leggings")



# ---------------------------------------------------------------- Comfy
class Comfy:
    """The three calls we need: upload a reference, queue a graph, fetch the PNG."""

    def __init__(self, url):
        self.url = url.rstrip("/"); self.cid = hashlib.md5(os.urandom(8)).hexdigest()

    def upload(self, img):
        buf = io.BytesIO(); img.save(buf, format="PNG"); data = buf.getvalue()
        name = f"wardrobe-{hashlib.sha1(data).hexdigest()[:10]}.png"      # same image → same file, no re-upload churn
        r = requests.post(self.url + "/upload/image", files={"image": (name, data, "image/png")},
                          data={"overwrite": "true"}, timeout=120); r.raise_for_status()
        j = r.json(); return (j.get("subfolder") + "/" if j.get("subfolder") else "") + j["name"]

    def run(self, graph, stop=None):
        r = requests.post(self.url + "/prompt", json={"prompt": graph, "client_id": self.cid}, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"comfy refused the graph: {r.text[:800]}")
        pid = r.json()["prompt_id"]
        while True:
            if stop is not None and stop.is_set():
                requests.post(self.url + "/interrupt", timeout=10); raise RuntimeError("stopped")
            h = requests.get(f"{self.url}/history/{pid}", timeout=30).json().get(pid)
            if h:
                st = h.get("status") or {}
                if st.get("status_str") == "error":
                    msg = [m[1].get("exception_message") for m in st.get("messages", []) if m[0] == "execution_error"]
                    raise RuntimeError("comfy error: " + (msg[0] if msg else "see the comfy console"))
                imgs = [im for o in h.get("outputs", {}).values() for im in o.get("images", [])]
                if imgs:
                    im = imgs[0]
                    r = requests.get(self.url + "/view", params={"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                                 "type": im.get("type", "output")}, timeout=120)
                    r.raise_for_status(); return Image.open(io.BytesIO(r.content)).convert("RGBA")
            time.sleep(0.4)


def _by_class(g, cls):
    return [k for k, v in g.items() if v.get("class_type", "").startswith(cls)]


def build_graph(tpl, prompt, seed, steps, ref_name=None, size=None, res=0, negative="", cfg=1.0):
    """Patch the exported edit workflow: literal prompt, seed, and one of three shapes —
    edit (ref, no size): the sampler starts from the encoder's latent, output = the reference's size;
    reference (ref + size): empty latent at `size`, the reference only conditions (a face → a full body);
    text (no ref): empty latent at `size`.
    ref_name may be a list: image_1 is the edit source, image_2.. are extra references ("the second image").
    Both switch nodes are bypassed by wiring around them; everything not feeding the save node is pruned."""
    g = copy.deepcopy(tpl)
    save = _by_class(g, "SaveImage")[0]; samp = _by_class(g, "KSampler")[0]
    enc = g[samp]["inputs"]["positive"][0]; e = g[enc]["inputs"]
    e["prompt"] = prompt; e["negative_prompt"] = negative or ""; e["resolution"] = int(res)   # 0 = keep the reference at its own size
    s = g[samp]["inputs"]; s["seed"] = int(seed); s["steps"] = int(steps); s["cfg"] = float(cfg)   # negative only bites when cfg > 1
    names = [ref_name] if isinstance(ref_name, str) else list(ref_name or [])
    for k in [k for k in list(e) if k.startswith("images.image_")]: e.pop(k)
    if names:
        load = _by_class(g, "LoadImage")[0]
        for i, nm in enumerate(names, 1):                       # image_1 = the edit source, image_2.. = references
            lid = load if i == 1 else f"{load}_ref{i}"
            if i > 1: g[lid] = {"class_type": "LoadImage", "inputs": {}}
            g[lid]["inputs"]["image"] = nm; e[f"images.image_{i}"] = [lid, 0]
    if names and not size:
        s["latent_image"] = [enc, 2]
    else:
        empty = _by_class(g, "EmptyLatentImage")[0]
        g[empty]["inputs"].update(width=size[0], height=size[1], batch_size=1)
        s["latent_image"] = [empty, 0]
    g[save]["inputs"]["filename_prefix"] = "wardrobe/w"
    keep, todo = set(), [save]                                   # prune: closure of the save node
    while todo:
        n = todo.pop()
        if n in keep or n not in g: continue
        keep.add(n)
        for v in g[n]["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str): todo.append(v[0])
    return {k: g[k] for k in keep}



# ---------------------------------------------------------------- cut
def head_rows(aA):
    """(top, neck) rows of the head in a standing silhouette: width grows from the crown to the cheeks, then
    narrows to the neck. Falls back to the top 20% of the frame if the profile isn't readable."""
    H = aA.shape[0]; w = (aA > 8).sum(axis=1); rows = np.where(w > 0)[0]
    if not len(rows): return 0, int(H * 0.2)
    top = int(rows[0])
    try:
        cheek = top + int(np.argmax(w[top:top + int(H * 0.15)]))
        neck = cheek + int(np.argmin(w[cheek:cheek + int(H * 0.10)]))
        if w[neck] < 0.85 * w[cheek] and neck > cheek + 8: return top, neck
    except ValueError:
        pass
    return top, top + int(H * 0.2)


def erode_frac(a, px):
    """Erode an alpha channel by a fractional number of pixels: blend the floor and ceil erosions."""
    n = int(np.floor(px)); f = px - n
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    lo = cv2.erode(a, k(n)) if n > 0 else a
    if f < 1e-6: return lo
    hi = cv2.erode(a, k(n + 1))
    return (lo.astype(np.float32) * (1 - f) + hi.astype(np.float32) * f).astype(np.uint8)


def _fill(rgb, sel, src, k):
    """rgb with the `sel` pixels recoloured as the Gaussian-weighted mean (k×k) of the `src` pixels around them;
    a `sel` pixel with no `src` within reach keeps its own colour."""
    s = src.astype(np.float32)
    num = cv2.GaussianBlur(rgb.astype(np.float32) * s[..., None], (k, k), 0); den = cv2.GaussianBlur(s, (k, k), 0)[..., None]
    return np.where(sel[..., None] & (den > 0.02), num / np.maximum(den, 0.02), rgb).astype(np.uint8)


def skin_ref(lab, aA):
    """Her skin colour in Lab: the median of a 16 px strip down the middle of her face, nose to chin. None when
    the head can't be found (no silhouette in the head rows)."""
    top, neck = head_rows(aA); h = neck - top; sil = aA > 8
    cols = np.where(sil[top:neck].any(axis=0))[0]
    if not len(cols) or h < 20: return None
    cx = (cols.min() + cols.max()) // 2
    strip = np.zeros_like(sil); strip[top + int(0.45 * h):top + int(0.9 * h), cx - 8:cx + 8] = True; strip &= sil
    return np.median(lab[strip], axis=0) if strip.sum() >= 50 else None


def cut(base, gen, thresh=24, min_blob=400, inside=True, feather=2, pieces=1, head=False, skin_guard=True, trim=0.0, reach=0, hair=False):
    """base, gen: RGBA PIL, same size. Returns (layer RGBA PIL, alpha ndarray).
    head=True (the face slot): no diff at all — the layer is the whole head down to the neck, feathered there,
    so an expression's subtle shading comes through faithfully. The head doesn't move, so a patch is exact.
    pieces: keep only the N largest blobs (1 = pants, a shirt, a hat; 2 = shoes, socks, gloves; 0 = keep all) —
    the model tends to re-shade skin in small islands elsewhere, and a garment is one or two pieces.
    skin_guard: drop changed pixels that are skin-like (her own skin colour) before AND after — the model
    re-lighting her. Turn it off for a garment that is itself skin-coloured (beige pants, a tan top).
    hair: the hair slot — hair drawn over hair is a change, so the same-hue test for re-shaded worn garments is off.
    trim: erode the garment's FREE edges by this many px (fractions allowed) — the last sliver of skin where
    the garment ends. Trim and feather never touch the edge that runs along HER outline (see below).
    reach: the garment hugs her outline — garment within this many px of her edge extends to it, the added
    pixels coloured from the garment beside them.
    inside=True: only pixels inside the base's own alpha may change — the halo the model paints around her
    edge is outside it, so it never gets in. inside=False: new opaque pixels outside her count too (coats,
    hats), and that outer edge is eroded a few px to shave the halo."""
    A = np.asarray(base, dtype=np.int16); B = np.asarray(gen, dtype=np.int16)
    aA, aB = A[..., 3], B[..., 3]
    sil = (aA > 8).astype(np.uint8)
    ell = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    if head:
        top, neck = head_rows(aA)
        a = np.zeros_like(aA, dtype=np.float32); a[top:neck + 12] = 255
        a[neck - 4:neck + 12] = np.linspace(255, 0, 16)[:, None]           # soft seam across the neck
        a = np.minimum(a, aA).astype(np.uint8)
        out = np.dstack([np.clip(B[..., :3], 0, 255).astype(np.uint8), a]); out[a == 0, :3] = 0
        return Image.fromarray(out), a
    rgb = np.abs(A[..., :3] - B[..., :3]).max(axis=2)
    m = (rgb > thresh) & (sil > 0)
    if skin_guard:
        # Re-lit skin is skin in BOTH images. Her own skin colour is the median of a strip down the middle of her
        # face (nose to chin). Skin-like = skin's chroma at any brightness (her jaw in the hair's shadow) or near
        # skin's brightness with the chroma drifted (the model re-tinting her legs beside the leggings). A changed
        # pixel that is skin-like before and after is the model re-lighting her, not a garment. Until 2026-10-02
        # the test was "same hue, modest brightness change": true of re-lit skin, but just as true of hair drawn
        # over hair, so hair thresholded out before her face did.
        L1 = cv2.cvtColor(np.clip(A[..., :3], 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
        L2 = cv2.cvtColor(np.clip(B[..., :3], 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
        ref = skin_ref(L1, aA)
        if ref is not None:
            def skin(L):
                dab, dL = np.hypot(L[..., 1] - ref[1], L[..., 2] - ref[2]), np.abs(L[..., 0] - ref[0])
                return ((dab < 12) & (dL < 120)) | ((dab < 20) & (dL < 60))
            m &= ~(skin(L1) & skin(L2))
        if not hair:
            # a garment made over a worn one: the model re-shades the one underneath too (same hue, a little
            # brighter or darker) and that must not ride into the new layer. Hair over hair looks exactly the same.
            same_hue = (np.hypot(L1[..., 1] - L2[..., 1], L1[..., 2] - L2[..., 2]) < 12) & (np.abs(L1[..., 0] - L2[..., 0]) < 60)
            m &= ~same_hue
    if not inside:
        m |= (aB > 8) & (sil == 0)
    m = m.astype(np.uint8)
    # Edge ribbons — the model's re-rendering of her outline, a thin changed strip just INSIDE her — die in the
    # open. Outside her it only chewed up wisps of hair, so there the mask stays as the result drew it.
    opened = cv2.morphologyEx(m, cv2.MORPH_OPEN, ell(3))
    m = opened if inside else np.where(sil > 0, opened, m).astype(np.uint8)
    if not inside:
        # The model's halo is a ~4 px SEMI-transparent ring hugging her outline. Remove exactly that — the ring
        # where the result isn't solid — and nothing else: a baggy flare is opaque right up to her edge, and eroding
        # every outside part from all sides was eating 20 px wide flares alive. Hair is soft there too, so a soft
        # ring pixel is halo only when nothing solid lies PAST the ring near it: a halo has nothing beyond it, hair
        # does (a notch bitten out of long hair at her old outline, 2026-10-02). Done before the pieces are picked:
        # unopened, the ring is a thread along her outline that tied patches of re-tinted skin to the garment.
        ring = (cv2.dilate(sil, ell(5)) > 0) & (sil == 0)
        beyond = ((sil == 0) & ~ring & (aB >= 100)).astype(np.uint8)
        m = np.where(ring & (aB < 200) & ~(cv2.dilate(beyond, ell(5)) > 0), 0, m).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))                              # pinholes
    if min_blob > 0 or pieces > 0:
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        ok = np.zeros(n, bool); ok[1:] = st[1:, cv2.CC_STAT_AREA] >= min_blob
        if pieces > 0 and ok.sum() > pieces:
            # Rank by GARMENT-NESS, not area: the model re-shades skin in big low-contrast patches that keep skin's
            # hue, while a garment changes the colour (chroma) and/or luminance a lot. Per pixel in Lab:
            # 2·|Δab| + max(|ΔL| − 60, 0); summed per blob. A 100k px skin tint scores below 12k px of a small garment.
            L1 = cv2.cvtColor(np.clip(A[..., :3], 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float64)
            L2 = cv2.cvtColor(np.clip(B[..., :3], 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float64)
            dab = np.hypot(L1[..., 1] - L2[..., 1], L1[..., 2] - L2[..., 2]); dL = np.abs(L1[..., 0] - L2[..., 0])
            score = np.bincount(lab.ravel(), weights=(2 * dab + np.maximum(dL - 60, 0)).ravel(), minlength=n)
            order = [i for i in np.argsort(-score) if i and ok[i]]
            ok[:] = False; ok[order[:pieces]] = True
        m = ok[lab].astype(np.uint8)
    rgb = np.clip(B[..., :3], 0, 255).astype(np.uint8); weak = aB < 230        # not solid, by the snap's own line
    if reach > 0:
        # Hug her outline: a gap of up to `reach` px between the garment and her edge is filled (the model stops a
        # px short, the 7 px open rounds off a toe) — a morphological close of garment ∪ outside-her, so the fill
        # is one piece with the garment, never a floating sliver. The added pixels are skin in the result (that is
        # why the diff missed them), so they take the garment's colour from beside them; its own edge shading stays.
        r = (reach + 2) // 2                                     # a close fills gaps up to 2r: reach px + her soft-edge px
        gap = cv2.morphologyEx((m | (sil == 0)).astype(np.uint8), cv2.MORPH_CLOSE, ell(r))
        add = (gap > 0) & (sil > 0) & (m == 0) & (cv2.dilate(m, ell(2 * r)) > 0)
        rgb = _fill(rgb, add, (m > 0) & ~weak, 4 * r + 3); m = (m | add).astype(np.uint8)
    # Trim and feather act on the garment's FREE edges only: along her outline the mask is first padded outward
    # (pixels outside her, dropped again below), so neither pulls the garment back from her edge. Without the
    # pad, a 2 px feather left a 3-4 px ramp of skin bleeding through along every leg (2026-10-01).
    pad = (cv2.dilate(m, ell(int(np.ceil(trim)) + feather + 1)) > 0) & (sil == 0) & (m == 0)
    a = np.where(pad, 255, m * 255).astype(np.uint8)
    if trim > 0: a = erode_frac(a, trim)
    if feather > 0:
        k = feather * 2 + 1; a = cv2.GaussianBlur(a, (k, k), 0)
    a[pad] = 0
    # Never more opaque than HER pixel where she is solid (keeps her soft edge, kills the halo). stick-out: outside
    # her the garment's own alpha rules; inside her, whichever is more solid — capping opaque pants to her soft leg
    # edge drew a pale line along each leg, and capping them to the model's soft edge inside her outline let skin
    # bleed through for 4 px.
    under = np.where(sil > 0, aA, 0) if inside else np.maximum(aA, aB)
    a = np.minimum(a, under).astype(np.uint8); a[under <= 8] = 0
    # The colour under a pixel the model left semi-transparent is junk — its blend with whatever backdrop it
    # invented. The model draws her shoulder a px narrower than the base, so just inside her outline its alpha
    # is low; the layer used to be nearly clear there too, which hid it. Now that the garment stays at her edge,
    # every such pixel in the layer takes its colour from the solid garment beside it (bright specks, 2026-10-02).
    weak &= a > 0
    if weak.any(): rgb = _fill(rgb, weak, (a > 0) & ~weak, 15)
    out = np.dstack([rgb, a])
    out[a == 0, :3] = 0                                              # transparent = black: small file, leaks nothing
    return Image.fromarray(out), a



def clean_alpha(img, lo=25, hi=230):
    """Snap the model's soft alpha: Qwen-Image-2.1's RGBA comes back with the background at 1–8 (not 0) and the
    body partly at 129–254 (not 255). ≤ lo → 0, ≥ hi → 255, the real edge in between stretched linearly. Without
    this, near-invisible junk counts as HER in the cut and she's slightly see-through on the card."""
    A = np.asarray(img.convert("RGBA")).copy(); a = A[..., 3].astype(np.float32)
    a = np.clip((a - lo) * 255.0 / (hi - lo), 0, 255); A[..., 3] = a.astype(np.uint8)
    A[A[..., 3] == 0, :3] = 0
    return Image.fromarray(A)


def alpha_hist(img):
    a = np.asarray(img.convert("RGBA"))[..., 3]
    return {"0": int((a == 0).sum()), "1-25": int(((a > 0) & (a <= 25)).sum()), "26-229": int(((a > 25) & (a < 230)).sum()),
            "230-254": int(((a >= 230) & (a < 255)).sum()), "255": int((a == 255).sum())}


def alpha_heat(img):
    """Alpha as colour, so ghosts become visible: 0 dark, 1–25 red, 26–229 yellow→orange, 230–254 cyan, 255 light grey."""
    a = np.asarray(img.convert("RGBA"))[..., 3]; out = np.zeros(a.shape + (3,), np.uint8); out[...] = (28, 28, 32)
    out[(a > 0) & (a <= 25)] = (230, 40, 40); out[(a >= 230) & (a < 255)] = (40, 210, 230); out[a == 255] = (190, 190, 190)
    mid = (a > 25) & (a < 230); t = (a[mid] - 25) / 205.0; out[mid] = np.stack([np.full_like(t, 240), 120 + 120 * t, np.zeros_like(t)], axis=1).astype(np.uint8)
    return Image.fromarray(np.dstack([out, np.full(a.shape, 255, np.uint8)]))


def fix_alpha(img, lo=25, hi=230, trim_px=0, defringe_px=0, min_px=0, grow_px=0):
    """The fixer, in order: clean_alpha(lo, hi) — ≤lo → empty, ≥hi → solid; TRIM the outline inward by trim_px
    (eats a baked-in white border, shrinks an overshooting garment) or GROW it outward by grow_px (a garment that
    stops a hair short of her edge; grown pixels take the nearest solid colour); DEFRINGE — edge pixels take the
    colour of the nearest solid pixels within defringe_px; drop floating alpha islands under min_px."""
    out = clean_alpha(img, lo, hi); A = np.asarray(out).copy(); a = A[..., 3]
    if trim_px > 0:
        a = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * trim_px + 1, 2 * trim_px + 1)))
    if grow_px > 0:
        a2 = cv2.dilate(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow_px + 1, 2 * grow_px + 1)))
        solid = (a > 0).astype(np.float32); rgb = A[..., :3].astype(np.float32); k = 2 * grow_px + 3
        num = cv2.GaussianBlur(rgb * solid[..., None], (k, k), 0); den = cv2.GaussianBlur(solid, (k, k), 0)[..., None]
        fill = np.where(den > 0.02, num / np.maximum(den, 0.02), rgb)
        A[..., :3] = np.where(((a == 0) & (a2 > 0))[..., None], fill, rgb).astype(np.uint8); a = a2
    if defringe_px > 0:
        solid = (a == 255).astype(np.float32); rgb = A[..., :3].astype(np.float32); k = 2 * defringe_px + 1
        num = cv2.GaussianBlur(rgb * solid[..., None], (k, k), 0); den = cv2.GaussianBlur(solid, (k, k), 0)[..., None]
        fill = np.where(den > 0.02, num / np.maximum(den, 0.02), rgb)
        A[..., :3] = np.where(((a > 0) & (a < 255))[..., None], fill, rgb).astype(np.uint8)
    if min_px > 0:
        n, lab, st, _ = cv2.connectedComponentsWithStats((a > 0).astype(np.uint8), connectivity=8)
        ok = np.zeros(n, bool); ok[1:] = st[1:, cv2.CC_STAT_AREA] >= min_px; a = np.where(ok[lab], a, 0).astype(np.uint8)
    A[..., 3] = a; A[a == 0, :3] = 0
    return Image.fromarray(A)


FLAT_BGS = {"dark": (24, 24, 28), "white": (245, 245, 245), "magenta": (255, 0, 255), "green": (0, 255, 0), "grey": (128, 128, 128)}


def over_dim(base, layer, dim=0.35):
    """The layer at full strength over a DIMMED base: gaps at the edge and overshoot past her outline stand out."""
    B = np.asarray(base.convert("RGBA")).astype(np.float32); B[..., :3] *= dim
    return Image.alpha_composite(Image.fromarray(B.astype(np.uint8)), layer.convert("RGBA"))


def on_bg(img, bg):
    """The real image over a flat colour (a white border shows on dark, a dark halo on white)."""
    return Image.alpha_composite(Image.new("RGBA", img.size, tuple(bg) + (255,)), img.convert("RGBA"))


def change_map(before, after, bg):
    """What the fixer is about to change, and nothing else: red = becomes empty, cyan = becomes solid,
    yellow = colour changes (defringe), over a dimmed copy of the image on the flat colour."""
    B = np.asarray(before.convert("RGBA")).astype(int); A = np.asarray(after.convert("RGBA")).astype(int)
    base = (np.asarray(on_bg(before, bg))[..., :3] * 0.35).astype(np.uint8)
    gone = (B[..., 3] > 0) & (A[..., 3] == 0); solid = (B[..., 3] < 255) & (A[..., 3] == 255)
    recol = (A[..., 3] > 0) & (np.abs(A[..., :3] - B[..., :3]).max(axis=2) > 12) & ~gone
    base[gone] = (235, 50, 50); base[solid] = (60, 210, 230); base[recol & ~solid] = (240, 210, 40)
    return Image.fromarray(np.dstack([base, np.full(base.shape[:2], 255, np.uint8)]))


def diff_view(before, after, gain=3.0):
    """How much each pixel moved, as heat over a dimmed `after`: faint yellow for a small shift, red for a big
    one, blue where only the alpha moved. Subtle re-shading the eye misses in a side by side shows up here."""
    B = np.asarray(before.convert("RGBA")).astype(np.float32); A = np.asarray(after.convert("RGBA")).astype(np.float32)
    d = np.abs(A[..., :3] - B[..., :3]).max(axis=2); da = np.abs(A[..., 3] - B[..., 3])
    s = np.clip(d * gain / 255, 0, 1)[..., None]; sa = np.clip(da * gain / 255, 0, 1)[..., None]
    heat = np.concatenate([np.full_like(s, 255), 230 * (1 - s), np.zeros_like(s)], axis=2)      # yellow -> red
    out = np.asarray(on_bg(after, (24, 24, 28)))[..., :3].astype(np.float32) * 0.3
    out = out * (1 - s) + heat * s
    out = out * (1 - sa * (1 - s)) + np.array([60, 120, 255], np.float32) * sa * (1 - s)
    return Image.fromarray(np.dstack([out.astype(np.uint8), np.full(d.shape, 255, np.uint8)]))


def onion(before, after):
    """Half and half, so what moved looks doubled and what stayed looks sharp."""
    return Image.blend(before.convert("RGBA"), after.convert("RGBA"), 0.5)


def alpha_is_soft(img):
    a = np.asarray(img.convert("RGBA"))[..., 3]; return ((a > 0) & (a <= 25)).mean() > 0.002 or ((a > 128) & (a < 230)).mean() > 0.01


def checker(size, cell=16):
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    v = (((x // cell) + (y // cell)) % 2 * 40 + 170).astype(np.uint8)
    return Image.fromarray(np.dstack([v, v, v, np.full_like(v, 255)]))




def stack(base, layers, slots):
    """Her with layers worn, as the card draws them: `layers` = [(item, RGBA)], `slots` = {item: slot}. An UNDER
    slot (hair_back) goes beneath her, the rest over her in LAYER_ORDER. base=None → the layers alone."""
    rank = lambda it: LAYER_ORDER.index(slots.get(it)) if slots.get(it) in LAYER_ORDER else 99
    size = base.size if base is not None else layers[0][1].size
    img = Image.new("RGBA", size, (0, 0, 0, 0)); fit = lambda im: im if im.size == size else im.resize(size)
    for it, im in sorted(layers, key=lambda x: rank(x[0])):
        if slots.get(it) in UNDER: img = Image.alpha_composite(img, fit(im))
    if base is not None: img = Image.alpha_composite(img, base)
    for it, im in sorted(layers, key=lambda x: rank(x[0])):
        if slots.get(it) not in UNDER: img = Image.alpha_composite(img, fit(im))
    return img


def preview_sheet(base, layer, slot=None):
    """[layer alone | worn] — worn as the card draws it (an UNDER slot goes beneath her). RGB ndarray."""
    ck = checker(layer.size, 24); worn = Image.alpha_composite(ck, stack(base, [("it", layer)], {"it": slot}))
    return np.hstack([np.asarray(Image.alpha_composite(ck, layer).convert("RGB")), np.asarray(worn.convert("RGB"))])


def make_icon(layer, size=160):
    """A square icon of the garment alone for the card's slot tile (icon-<item>.webp): the layer's alpha
    bbox, padded square, on transparency. None if the layer is empty."""
    bbox = layer.getbbox()
    if not bbox: return None
    crop = layer.crop(bbox); w, h = crop.size; side = max(w, h)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0)); sq.paste(crop, ((side - w) // 2, (side - h) // 2))
    return sq.resize((size, size), Image.LANCZOS)


def clamp_box(box, size):
    W, H = size; x0, y0, x1, y1 = [int(v) for v in box]
    return max(0, x0), max(0, y0), min(W, x1), min(H, y1)


def resize_premul(img, size):
    """Resize RGBA with the colour premultiplied by alpha, so transparent neighbours never bleed into edges."""
    A = np.asarray(img.convert("RGBA"), np.float32); a = A[..., 3:4] / 255
    pm = Image.fromarray(np.concatenate([A[..., :3] * a, A[..., 3:4]], axis=2).astype(np.uint8)).resize(size, Image.LANCZOS)
    P = np.asarray(pm, np.float32); a2 = P[..., 3:4] / 255
    rgb = np.where(a2 > 0.004, P[..., :3] / np.maximum(a2, 0.004), 0)
    return Image.fromarray(np.clip(np.concatenate([rgb, P[..., 3:4]], axis=2), 0, 255).astype(np.uint8))


def crop_up(src, box, up=1024):
    """Cut `box` (x0, y0, x1, y1) out of src and upscale it (uniformly, never down) so its long side is ~`up` px:
    the model spends its whole pixel budget on that region — a 100 px face drawn at 1024 px. Padded SYMMETRICALLY
    with transparency to multiples of 32 (never stretched), so geometry comes back exact. Returns (image, pad)."""
    x0, y0, x1, y1 = clamp_box(box, src.size); crop = src.crop((x0, y0, x1, y1)); w, h = crop.size
    f = max(1.0, up / max(w, h)); big = resize_premul(crop, (max(1, round(w * f)), max(1, round(h * f))))
    W, H = -(-big.width // 32) * 32, -(-big.height // 32) * 32
    px, py = (W - big.width) // 2, (H - big.height) // 2
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0)); out.paste(big, (px, py))
    return out, (px, py, big.width, big.height)


def paste_down(src, result, box, pad, feather=0.08, model_alpha=False):
    """Unpad, downscale the refined crop back to the box and blend it into src with a linear feather at the edges.
    model_alpha=False — DETAIL inside her outline: her alpha is never touched and RGB is taken only where her alpha
      is solid, so whatever the model paints around her (it invents a backdrop for a crop) never reaches an edge.
    model_alpha=True — ADD/REMOVE: alpha AND colour come from the model inside the box. Works when it honours the
      transparent background (hair grows, strands go); when it invents a backdrop instead, that colour shows at the
      edges — pick another seed, or do the change with 'edit this' at full size."""
    x0, y0, x1, y1 = clamp_box(box, src.size); w, h = x1 - x0, y1 - y0; px, py, bw, bh = pad
    part = result.convert("RGBA").crop((px, py, px + bw, py + bh))
    # detail: only the colour is used, so it is resized plainly — a premultiplied resize paints black wherever the
    # model's own alpha came back near zero, and that black then faded into the middle of the region (2026-10-01)
    res = np.asarray(resize_premul(part, (w, h)) if model_alpha else part.convert("RGB").resize((w, h), Image.LANCZOS), np.float32)
    if not model_alpha: res = np.concatenate([res, np.full((h, w, 1), 255, np.float32)], axis=2)
    S = np.asarray(src.convert("RGBA"), np.float32); R = S[y0:y1, x0:x1]
    f = max(4, int(feather * min(w, h)))
    ramp = lambda n: np.minimum(np.minimum(np.arange(n) + 1, n - np.arange(n)) / f, 1.0)
    m = np.minimum.outer(ramp(h), ramp(w))[..., None]
    if model_alpha:
        # premultiplied, so a clear pixel (black under alpha 0) never drags black into a half-blended edge
        a_r, a_s = R[..., 3:4] / 255, res[..., 3:4] / 255
        a = a_r * (1 - m) + a_s * m
        rgb = R[..., :3] * a_r * (1 - m) + res[..., :3] * a_s * m
        R[..., :3] = np.where(a > 0.002, rgb / np.maximum(a, 0.002), 0); R[..., 3:4] = a * 255
    else:
        m = m * np.clip((R[..., 3:4] - 176) / 64, 0, 1)
        R[..., :3] = R[..., :3] * (1 - m) + res[..., :3] * m
    return Image.fromarray(np.clip(S, 0, 255).astype(np.uint8))


def blank_hole(src, hole, context=0.5):
    """REPLACE a region instead of refining it: `hole` (x0, y0, x1, y1) is wiped to transparent, so the model has
    nothing wrong to copy — it rebuilds the spot from the prompt and the surroundings. The crop sent to the model
    is the hole grown by `context` of its size on every side (clamped to the frame), so it sees what the new part
    must join. Returns (crop for the model, pad, big box, the wiped source)."""
    x0, y0, x1, y1 = clamp_box(hole, src.size); w, h = x1 - x0, y1 - y0
    big = clamp_box((x0 - context * w, y0 - context * h, x1 + context * w, y1 + context * h), src.size)
    wiped = np.asarray(src.convert("RGBA")).copy(); wiped[y0:y1, x0:x1] = 0; wiped = Image.fromarray(wiped)
    ref, pad = crop_up(wiped, big)
    return ref, pad, big, wiped


def paste_hole(src, wiped, result, hole, big, pad, feather=6):
    """Paste the model's crop back over the wiped source, then keep ONLY the hole (plus a short feather) from it:
    outside the hole the original stays untouched, however the model re-rendered the surroundings."""
    filled = np.asarray(paste_down(wiped, result, big, pad, model_alpha=True), np.float32)
    S = np.asarray(src.convert("RGBA"), np.float32); x0, y0, x1, y1 = clamp_box(hole, src.size)
    m = np.zeros(S.shape[:2], np.float32); m[y0:y1, x0:x1] = 1
    k = 2 * feather + 1; m = cv2.GaussianBlur(m, (k, k), 0)[..., None]      # straddles the hole's edge
    a = S[..., 3:4] / 255 * (1 - m) + filled[..., 3:4] / 255 * m             # premultiplied, like paste_down
    rgb = S[..., :3] * S[..., 3:4] / 255 * (1 - m) + filled[..., :3] * filled[..., 3:4] / 255 * m
    out = np.concatenate([np.where(a > 0.002, rgb / np.maximum(a, 0.002), 0), a * 255], axis=2)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
