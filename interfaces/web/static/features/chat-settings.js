// features/chat-settings.js - Trim color utility

export function applyTrimColor(color) {
    const root = document.documentElement;

    if (!color || !color.match(/^#[0-9a-f]{6}$/i)) {
        color = '';
    }

    if (color && color.match(/^#[0-9a-f]{6}$/i)) {
        root.style.setProperty('--trim', color);

        const r = parseInt(color.slice(1, 3), 16);
        const g = parseInt(color.slice(3, 5), 16);
        const b = parseInt(color.slice(5, 7), 16);
        root.style.setProperty('--trim-glow', `rgba(${r}, ${g}, ${b}, 0.35)`);
        root.style.setProperty('--trim-light', `rgba(${r}, ${g}, ${b}, 0.15)`);
        root.style.setProperty('--trim-border', `rgba(${r}, ${g}, ${b}, 0.4)`);
        root.style.setProperty('--trim-50', `rgba(${r}, ${g}, ${b}, 0.5)`);
        root.style.setProperty('--accordion-header-bg', `rgba(${r}, ${g}, ${b}, 0.08)`);
        root.style.setProperty('--accordion-header-hover', `rgba(${r}, ${g}, ${b}, 0.12)`);
    } else {
        root.style.removeProperty('--trim');
        root.style.removeProperty('--trim-glow');
        root.style.removeProperty('--trim-light');
        root.style.removeProperty('--trim-border');
        root.style.removeProperty('--trim-50');
        root.style.removeProperty('--accordion-header-bg');
        root.style.removeProperty('--accordion-header-hover');
    }

    import('./volume.js').then(vol => vol.updateSliderFill()).catch(() => {});
    import('./logo.js').then(m => m.applyLogoTint(color)).catch(() => {});
}

// Scene background: set #chatbg's image from a scene name (or clear to default CSS).
// `name` is a sanitized library stem; anything not matching is treated as "none".
//
// Ownership: a view that has borrowed the chat surface (the Game Room's story
// rooms paint per-room backdrops onto this same element) marks it via
// claimBackground(). While claimed, chat-settings paints are RECORDED, not
// applied — otherwise loadSidebar overpainted the story's scene about half a
// second after every room entry and only the 12s poll healed it (finding 4.3).
export function claimBackground(owner) {
    const bg = document.getElementById('chatbg');
    if (bg) bg.dataset.bgOwner = owner;
}

export function releaseBackground(owner) {
    const bg = document.getElementById('chatbg');
    if (!bg || bg.dataset.bgOwner !== owner) return;
    delete bg.dataset.bgOwner;
    const pending = bg.dataset.bgPending;
    if (pending !== undefined) {
        delete bg.dataset.bgPending;
        applyBackground(pending);
    }
}

// Background resolution happens at paint time, in this one choke point:
//   chat scene > global underlay (DEFAULT_BACKGROUND) > theme bg > blank
// Image URLs are only referenced when actually painted, so lower layers are
// never fetched while a higher one covers them.
let _defaultBackground = '';   // scene NAME from the background library
let _themeBackground = '';     // URL from the active theme's bundle (core/theme.js)

export function setDefaultBackground(name) {
    _defaultBackground = (name && /^[a-z0-9_-]{1,50}$/.test(name)) ? name : '';
}

export function setThemeBackground(url) {
    // Same-app asset URLs only — theme bg comes from /api/themes which builds
    // /static/... or /plugin-web/... paths; refuse anything else.
    _themeBackground = (typeof url === 'string' &&
        (url.startsWith('/static/') || url.startsWith('/plugin-web/'))) ? url : '';
}

export function applyBackground(name) {
    const bg = document.getElementById('chatbg');
    if (!bg) return;
    const explicit = (name && /^[a-z0-9_-]{1,50}$/.test(name)) ? name : '';
    if (bg.dataset.bgOwner) {
        bg.dataset.bgPending = name || '';
        // Keep the "current scene" record honest even while deferred — the
        // Scene modal seeds its selection from dataset.scene and was showing
        // the pre-story scene as current during stories (P0 hunt 2026-08-16).
        bg.dataset.scene = explicit;
        return;
    }
    const paintedName = explicit || _defaultBackground;
    if (paintedName) {
        bg.style.backgroundImage = `url('/api/backgrounds/${encodeURIComponent(paintedName)}')`;
        bg.classList.add('has-bg');
        bg.classList.remove('has-theme-bg');
    } else if (_themeBackground) {
        // has-theme-bg marks paints from the THEME layer only — theme CSS can
        // key on it to tile a texture (background-size:auto) while scenes and
        // the underlay keep has-bg's default cover.
        bg.style.backgroundImage = `url('${_themeBackground}')`;
        bg.classList.add('has-bg', 'has-theme-bg');
    } else {
        bg.style.backgroundImage = '';
        bg.classList.remove('has-bg', 'has-theme-bg');
    }
    // dataset.scene records the chat's OWN scene only ('' when riding the
    // underlay) — the per-chat Scene modal must not claim the global default.
    bg.dataset.scene = explicit;
}
