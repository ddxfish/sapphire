"""CLIP-based "vibes" describer for images that can't reach a vision LLM.

Use case: a tool returns an image, but the active model is text-only (GLM-5p1,
DeepSeek, smaller local models). Instead of just "image saved, can't analyze",
this module produces a 50-100 word atmospheric description of the image so
the model can respond to what it would have seen, in feel if not in detail.

Approach: CLIP image embedding → cosine similarity to a curated vocabulary
of vibe phrases (lighting, mood, atmosphere, texture, place-feel). Top
phrase per category + dominant color extraction → composed prose.

Cost: CLIP ViT-B/32 (~600MB torch weights, downloaded on first use; sits
in HF cache afterward). Inference ~100-300ms per image on CPU. One model
load per Sapphire process.
"""
import logging
import threading
from io import BytesIO
from typing import List, Tuple

logger = logging.getLogger(__name__)

CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"

# Vibe vocabulary — phrases CLIP scores the image against. Curated to cover
# atmosphere, not object identity. One pick per category fed into composer.
VIBE_VOCAB = {
    "people": [
        "a person facing the camera",
        "a person in profile",
        "a close-up of a person's face",
        "a person at a distance in the room",
        "a person looking down at something they're holding",
        "a person looking away from the camera",
        "the back of a person's head or body",
        "a hand or arm reaching into the frame",
        "multiple people in the frame",
        "an empty room with no people present",
        "a person seated at a desk or workbench",
        "a person standing",
    ],
    "person_doing": [
        "someone working with their hands",
        "someone holding an object up to the camera",
        "someone working at a computer or screen",
        "someone simply sitting or resting",
        "someone in motion",
        "someone gesturing toward the camera",
        "someone reading or looking at something close",
        "no person engaged in any activity",
    ],
    "light": [
        "warm overhead lamp light",
        "soft natural daylight from a window",
        "harsh fluorescent overhead light",
        "golden hour sunlight",
        "blue hour twilight",
        "candlelight or low warm glow",
        "dim ambient evening lighting",
        "bright direct sunlight",
        "indirect diffused window light",
        "late evening lamp light",
        "early morning natural light",
        "shadowy low light",
    ],
    "mood": [
        "calm and quiet",
        "intimate and cozy",
        "tense or unsettled",
        "energetic and busy",
        "lonely or empty",
        "relaxed and unhurried",
        "focused and attentive",
        "playful",
        "melancholy",
        "contemplative",
        "warm and inviting",
        "stark and clinical",
        "homey and lived-in",
    ],
    "atmosphere": [
        "warm-toned palette",
        "cool-toned palette",
        "neutral palette",
        "muted desaturated colors",
        "vivid saturated colors",
        "earthy tones",
        "near-monochrome",
        "high-contrast lighting",
        "soft hazy quality",
        "crisp and clear quality",
    ],
    "texture": [
        "cluttered and dense with objects",
        "tidy and organized",
        "minimalist and sparse",
        "rich in detail",
        "spacious and empty",
        "rustic and worn",
        "modern and polished",
        "weathered and used",
        "pristine and untouched",
    ],
    "place_feel": [
        "feels like a workshop or garage",
        "feels like a kitchen",
        "feels like a bedroom",
        "feels like a home office",
        "feels like a living room",
        "feels outdoors",
        "feels industrial",
        "feels like a domestic interior",
        "feels public or institutional",
        "feels late at night",
        "feels mid-day",
        "feels like a morning scene",
    ],
}

# Named colors for quantized-palette lookup. Chosen for atmospheric description,
# not perfect color science — these are vibe labels, not Pantone refs.
_NAMED_COLORS = [
    ("black", (10, 10, 10)),
    ("near-black", (40, 40, 40)),
    ("dark grey", (70, 70, 70)),
    ("grey", (130, 130, 130)),
    ("light grey", (190, 190, 190)),
    ("white", (240, 240, 240)),
    ("dark red", (120, 30, 30)),
    ("red", (210, 50, 50)),
    ("deep brown", (70, 45, 25)),
    ("warm brown", (140, 90, 50)),
    ("light brown", (190, 150, 110)),
    ("warm yellow", (230, 190, 80)),
    ("amber", (220, 160, 60)),
    ("soft amber", (240, 200, 130)),
    ("orange", (240, 130, 50)),
    ("dark green", (30, 70, 30)),
    ("green", (70, 160, 80)),
    ("muted green", (130, 170, 110)),
    ("dark blue", (25, 45, 95)),
    ("navy", (40, 50, 90)),
    ("blue", (70, 130, 220)),
    ("soft blue", (140, 180, 220)),
    ("teal", (60, 150, 160)),
    ("purple", (130, 70, 180)),
    ("deep purple", (80, 40, 110)),
    ("pink", (240, 170, 180)),
    ("warm beige", (220, 200, 170)),
    ("cool beige", (200, 200, 200)),
]

_model = None
_processor = None
_vocab_embeds = None
_vocab_cat_ranges = None  # category → (start_idx, end_idx) in flat embed tensor
_load_lock = threading.Lock()
_load_failed = False


def _ensure_loaded():
    """Lazy-load CLIP and embed the vocab once. Safe to call repeatedly."""
    global _model, _processor, _vocab_embeds, _vocab_cat_ranges, _load_failed
    if _model is not None:
        return True
    if _load_failed:
        return False
    with _load_lock:
        if _model is not None:
            return True
        if _load_failed:
            return False
        try:
            logger.info(f"[VIBES] Loading CLIP ({CLIP_MODEL_NAME}) — first load may download ~600MB...")
            from transformers import CLIPModel, CLIPProcessor
            import torch
            # Local-first (negspace E5): a warm cache loads with zero
            # network — transformers otherwise pings huggingface.co on
            # every load. Missing files -> one retry online to download.
            try:
                _model = CLIPModel.from_pretrained(CLIP_MODEL_NAME, local_files_only=True)
                _processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME, local_files_only=True)
            except (OSError, EnvironmentError):
                logger.info("[VIBES] CLIP not in local cache — downloading (~600MB)")
                _model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
                _processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
            _model.eval()

            # Flatten vocab and remember category boundaries
            flat_phrases = []
            cat_ranges = {}
            offset = 0
            for cat, phrases in VIBE_VOCAB.items():
                cat_ranges[cat] = (offset, offset + len(phrases))
                flat_phrases.extend(phrases)
                offset += len(phrases)

            with torch.no_grad():
                inputs = _processor(text=flat_phrases, return_tensors="pt", padding=True)
                tf = _model.get_text_features(**inputs)
                # transformers ≥ 4.x may wrap the return in a model output
                # rather than returning a bare tensor — extract if so.
                if hasattr(tf, 'text_embeds'):
                    tf = tf.text_embeds
                elif hasattr(tf, 'pooler_output'):
                    tf = tf.pooler_output
                tf = tf / tf.norm(dim=-1, keepdim=True)

            _vocab_embeds = tf
            _vocab_cat_ranges = cat_ranges
            logger.info(f"[VIBES] Loaded CLIP, embedded {len(flat_phrases)} vibe phrases")
            return True
        except Exception as e:
            logger.exception(f"[VIBES] CLIP load failed: {e}")
            _load_failed = True
            return False


def warmup_async():
    """Kick off CLIP load in a background thread. Call early to avoid a
    long first-tool-call latency."""
    def _warm():
        _ensure_loaded()
    threading.Thread(target=_warm, daemon=True, name="vibes-warmup").start()


def _dominant_colors(image_bytes: bytes, n_colors: int = 5) -> List[Tuple[str, int, Tuple[int,int,int]]]:
    """Quantize to N colors and return [(name, percent, rgb), ...] sorted by frequency."""
    from PIL import Image
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((150, 150))
    quant = img.quantize(colors=n_colors, method=Image.Quantize.FASTOCTREE)
    palette = quant.getpalette()
    colors_with_counts = sorted(quant.getcolors() or [], reverse=True)
    total = max(1, sum(c for c, _ in colors_with_counts))
    results = []
    for count, idx in colors_with_counts[:n_colors]:
        rgb = (palette[idx*3], palette[idx*3+1], palette[idx*3+2])
        results.append((_name_color(rgb), int(100 * count / total), rgb))
    return results


def _name_color(rgb):
    """Nearest-neighbor lookup against the curated palette."""
    best = None
    best_d = float("inf")
    for name, ref in _NAMED_COLORS:
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if d < best_d:
            best_d = d
            best = name
    return best


def describe(image_bytes: bytes) -> str:
    """Generate a 50-100 word atmospheric description of an image.

    Returns a single string ready to drop into a tool result. If CLIP can't
    be loaded, returns a short fallback that still includes dominant colors
    (computed without CLIP)."""
    # Always try color extraction even if CLIP fails — colors are pure Pillow.
    try:
        colors = _dominant_colors(image_bytes, n_colors=5)
    except Exception as e:
        logger.warning(f"[VIBES] color extraction failed: {e}")
        colors = []

    color_str = ""
    if colors:
        top = ", ".join(f"{name} ({pct}%)" for name, pct, _ in colors[:3])
        color_str = f"Dominant colors: {top}."

    if not _ensure_loaded():
        # CLIP unavailable — fall back to colors-only description
        if color_str:
            return f"Vibes (CLIP unavailable, colors only): {color_str}"
        return "Vibes: image captured but CLIP-based description is unavailable on this brain."

    try:
        import torch
        from PIL import Image

        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        with torch.no_grad():
            inputs = _processor(images=img, return_tensors="pt")
            img_feat = _model.get_image_features(**inputs)
            # Defensive unwrap for newer transformers that returns a model
            # output object instead of a tensor.
            if hasattr(img_feat, 'image_embeds'):
                img_feat = img_feat.image_embeds
            elif hasattr(img_feat, 'pooler_output'):
                img_feat = img_feat.pooler_output
            img_feat = img_feat / img_feat.norm(dim=-1, keepdim=True)
            sims = (img_feat @ _vocab_embeds.T).squeeze(0)

        picks = {}
        for cat, (start, end) in _vocab_cat_ranges.items():
            cat_sims = sims[start:end]
            best_idx = int(cat_sims.argmax())
            picks[cat] = VIBE_VOCAB[cat][best_idx]

        # Person detection — lead with the human if one is in frame. "empty
        # room with no people" / "no person engaged" → suppress this section.
        person_phrase = picks.get("people", "")
        person_doing = picks.get("person_doing", "")
        person_present = (
            person_phrase
            and "empty room" not in person_phrase.lower()
            and "no people" not in person_phrase.lower()
        )

        # Compose prose — person first if present, then scene/atmosphere
        light = picks["light"]
        mood = picks["mood"]
        atmosphere = picks["atmosphere"]
        texture = picks["texture"]
        place = picks["place_feel"]

        parts = ["Vibes (no vision available — CLIP atmospheric reading):"]
        if person_present:
            person_line = person_phrase.capitalize() + "."
            if person_doing and "no person" not in person_doing.lower():
                person_line += f" Looks like {person_doing}."
            parts.append(person_line)
        parts.append(f"{light.capitalize()}.")
        parts.append(f"{place.capitalize()}, {mood}.")
        parts.append(f"The atmosphere is {atmosphere}, feeling {texture}.")
        if color_str:
            parts.append(color_str)

        return " ".join(parts)
    except Exception as e:
        logger.exception("[VIBES] describe failed")
        if color_str:
            return f"Vibes (CLIP failed — colors only): {color_str}"
        return f"[Vibes unavailable: {e}]"
