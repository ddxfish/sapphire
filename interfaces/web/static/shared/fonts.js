// shared/fonts.js — Font presets + downloadable webfonts (themes-v2 P2)
//
// Presets are what the user (or a theme bundle) picks; families are the
// downloadable OFL webfonts behind them (core/routes/fonts.py, pinned+hashed,
// never in git). Downloaded fonts register through the FontFace API — no
// static @font-face, so never-downloaded fonts produce zero 404 noise and a
// just-downloaded font becomes usable without a reload.

export const FONT_PRESETS = [
    { id: 'system',      label: 'System',      family: null,
      stack: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` },
    { id: 'rounded',     label: 'Rounded',     family: 'nunito',
      stack: `'Nunito', 'Varela Round', -apple-system, sans-serif` },
    { id: 'serif',       label: 'Serif',       family: 'lora',
      stack: `'Lora', 'Georgia', 'Cambria', 'Times New Roman', serif` },
    { id: 'mono',        label: 'Monospace',   family: 'jetbrainsmono',
      stack: `'JetBrains Mono', 'Monaco', 'Menlo', 'Consolas', 'Ubuntu Mono', monospace` },
    { id: 'handwriting', label: 'Handwriting', family: 'caveat',
      stack: `'Caveat', 'Segoe Script', cursive` },
];

let _status = null;          // family -> {downloaded, css_family, label}
const _registered = new Set();

export async function fontStatus(force = false) {
    if (_status && !force) return _status;
    _status = {};
    try {
        const res = await fetch('/api/fonts');
        if (res.ok) {
            const d = await res.json();
            for (const f of (d.fonts || [])) _status[f.family] = f;
        }
    } catch {}
    return _status;
}

async function _register(family) {
    const meta = _status?.[family];
    if (!meta || _registered.has(family)) return;
    try {
        const ff = new FontFace(meta.css_family, `url(/api/fonts/file/${family})`);
        await ff.load();
        document.fonts.add(ff);
        _registered.add(family);
    } catch (e) {
        console.warn(`[Fonts] register failed for ${family}:`, e);
    }
}

// Boot: register everything already downloaded so the [data-font] stacks
// resolve to real webfonts immediately.
export async function initFonts() {
    const status = await fontStatus();
    for (const [family, meta] of Object.entries(status)) {
        if (meta.downloaded) _register(family);
    }
}

// Make a family usable: download if missing (sha-verified server-side),
// then register. Resolves when the font is live; rejects on failure.
export async function ensureFont(family) {
    const status = await fontStatus();
    const meta = status[family];
    if (!meta) throw new Error(`unknown font ${family}`);
    if (!meta.downloaded) {
        const res = await fetch('/api/fonts/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ family }),
        });
        if (!res.ok) throw new Error(`download failed (${res.status})`);
        meta.downloaded = true;
    }
    await _register(family);
}

// Theme-bundle entry point: best-effort fetch of a preset's webfont.
export function ensurePresetFont(presetId) {
    const preset = FONT_PRESETS.find(p => p.id === presetId);
    if (preset?.family) ensureFont(preset.family).catch(() => {});
}
