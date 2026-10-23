// core/theme.js — THE theme apply path (themes-v2 B1, plan: tmp/themes-v2-plan.md)
//
// Before this module, theme state had three independent apply/restore sites
// (index.html boot guard, main.js initAppearance, appearance.js _applyTheme)
// that disagreed in both directions — chat-style settings reverted on live
// switch but ghost-restored at boot, scripts double-injected, multi-tab never
// synced. Boot, live switch, and the storage listener now all run through
// applyTheme(). The inline boot guard remains ONLY as a pre-module FOUC
// shield; initTheme() re-asserts through this path as soon as modules load.
//
// Theme setting values persist under namespaced keys `theme:{id}:{key}` —
// bare manifest keys could clobber sapphire-* app state (lifecycle #9 /
// chaos T7). The server refuses sapphire-* setting keys as a second wall.

import { applyBackground, setThemeBackground } from '../features/chat-settings.js';
import { ensurePresetFont } from '../shared/fonts.js';
import { setThemeMotion, restartMotion } from './motions.js';

let _themes = [];
let _defaultId = 'dark';
let _current = null;

export function getThemes() { return _themes; }
export function getDefaultId() { return _defaultId; }

export function currentThemeId() {
    try { return localStorage.getItem('sapphire-theme') || _defaultId; }
    catch { return _defaultId; }
}

export async function fetchThemes() {
    try {
        const res = await fetch('/api/themes');
        if (res.ok) {
            const d = await res.json();
            _themes = d.themes || [];
            _defaultId = d.default || 'dark';
        }
    } catch {}
    return _themes;
}

export function settingKey(themeId, key) { return `theme:${themeId}:${key}`; }

// Re-apply the ACTIVE theme's stored setting side effects (chat-style attrs).
// One implementation for boot re-assert and live switch — the old split made
// settings silently revert on switch (lifecycle #2) while boot restored keys
// for every theme ever used (lifecycle #3).
export function applyThemeSettings(theme) {
    for (const s of (theme.settings || [])) {
        let val = null;
        try { val = localStorage.getItem(settingKey(theme.id, s.key)); } catch {}
        if (val === null || val === undefined) val = s.default ?? '';
        const m = String(s.key).match(/^(.+)-chat-style$/);
        if (m && val) document.documentElement.setAttribute(`data-${m[1]}-chat`, String(val));
    }
}

function _applyBundle(theme) {
    // Font: an explicit user pick always wins; the theme's font is the
    // default for users who never chose one. 'system' explicit = no attr.
    let explicit = null;
    try { explicit = localStorage.getItem('sapphire-font'); } catch {}
    const font = explicit || theme.font || '';
    if (font && font !== 'system') {
        document.documentElement.setAttribute('data-font', font);
        ensurePresetFont(font);   // best-effort webfont download+register
    } else {
        document.documentElement.removeAttribute('data-font');
    }

    // Background chain: chat scene > global underlay > theme bg > blank.
    // setThemeBackground stores the theme layer; repaint resolves the chain.
    setThemeBackground(theme.bg || '');
    const chatScene = document.getElementById('chatbg')?.dataset.scene || '';
    applyBackground(chatScene);

    // Motion: theme bundle default; an explicit pick (incl. 'none') wins
    // inside the motions module. Recolor-on-switch is handled there too
    // (it watches data-theme and remounts).
    setThemeMotion(theme.motion || '');
}

export function applyTheme(theme, opts = {}) {
    if (!theme || !theme.id) return;
    const persist = opts.persist !== false;
    const root = document.documentElement;
    const oldId = root.getAttribute('data-theme');

    // Teardown: signal first (scripts get a chance to cancel rAF/canvases —
    // node removal alone never stopped them, lifecycle #4/#5), then remove.
    if (oldId && oldId !== theme.id) {
        window.dispatchEvent(new CustomEvent('sapphire-theme-unload', { detail: { id: oldId } }));
    }
    document.querySelectorAll('script[data-theme-script]').forEach(s => s.remove());

    // Clear chat-style attrs; the new theme's stored values re-apply below.
    Array.from(root.attributes)
        .filter(a => a.name.startsWith('data-') && a.name.endsWith('-chat'))
        .forEach(a => root.removeAttribute(a.name));

    // Apply DOM/CSS first, persist LAST — a storage failure must never strand
    // a half-switched UI (lifecycle #7).
    root.setAttribute('data-theme', theme.id);
    if (theme.source === 'core') {
        let link = document.getElementById('theme-stylesheet');
        if (!link) {
            link = document.createElement('link');
            link.id = 'theme-stylesheet';
            link.rel = 'stylesheet';
            document.head.appendChild(link);
        }
        if (link.href !== new URL(theme.css, location.origin).href) {
            link.href = theme.css;
            // Remount the motion once the sheet actually LANDS — the
            // data-theme observer fires before the CSS loads, so mount-time
            // color sampling read the outgoing palette (hunt 2026-08-17).
            link.addEventListener('load', () => restartMotion(), { once: true });
        }
        link.disabled = false;
        document.getElementById('plugin-theme-css')?.remove();
    } else {
        let pcss = document.getElementById('plugin-theme-css');
        if (!pcss) {
            pcss = document.createElement('link');
            pcss.id = 'plugin-theme-css';
            pcss.rel = 'stylesheet';
            document.head.appendChild(pcss);
        }
        pcss.href = theme.css;
        // Plugin sheets are never preloaded by the Visual grid — without
        // this the motion keeps the outgoing palette until a later remount.
        pcss.addEventListener('load', () => restartMotion(), { once: true });
        const coreLink = document.getElementById('theme-stylesheet');
        if (coreLink) coreLink.disabled = true;
    }

    applyThemeSettings(theme);

    // Scripts (legacy plugin-theme lane; P3's motion contract supersedes)
    for (const src of (theme.scripts || [])) {
        const s = document.createElement('script');
        s.src = src;
        s.dataset.themeScript = theme.id;
        document.body.appendChild(s);
    }

    _applyBundle(theme);

    // Browser/PWA chrome color follows the theme (lifecycle #16)
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && theme.preview?.bg) meta.setAttribute('content', theme.preview.bg);

    if (persist) {
        try {
            localStorage.setItem('sapphire-theme', theme.id);
            if (theme.source === 'core') {
                localStorage.removeItem('sapphire-theme-data');
            } else {
                localStorage.setItem('sapphire-theme-data', JSON.stringify({
                    id: theme.id, source: theme.source, css: theme.css, scripts: theme.scripts || []
                }));
            }
        } catch (e) { console.warn('[Theme] persist failed:', e); }
    }
    _current = theme;
}

// Boot: fetch the authoritative list, re-assert the saved theme through the
// one apply path (bundle font/bg and settings the inline guard can't know),
// self-heal a dead saved id (lifecycle #8), and wire multi-tab sync (#10).
export async function initTheme() {
    await fetchThemes();
    if (!_themes.length) return;
    const savedId = currentThemeId();
    const theme = _themes.find(t => t.id === savedId);
    if (theme) {
        applyTheme(theme, { persist: false });
    } else {
        const fallback = _themes.find(t => t.id === _defaultId) || _themes[0];
        console.warn(`[Theme] saved theme '${savedId}' not available — falling back to '${fallback.id}'`);
        applyTheme(fallback);
        import('../ui.js')
            .then(ui => ui.showToast?.(`Theme "${savedId}" is no longer available — switched to ${fallback.name}.`, 'warning'))
            .catch(() => {});
    }

    window.addEventListener('storage', e => {
        if (e.key === 'sapphire-theme' && e.newValue && e.newValue !== _current?.id) {
            const t = _themes.find(x => x.id === e.newValue);
            if (t) applyTheme(t, { persist: false });
        }
    });
}
