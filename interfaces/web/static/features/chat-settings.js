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

// Global underlay: the app-wide default scene (DEFAULT_BACKGROUND setting),
// painted only when the chat has no scene of its own. Resolution happens at
// paint time, in this one choke point — the chain is
//   chat scene > global underlay > blank
// (themes-v2 P2 slots a theme-default layer between underlay and blank).
// The image URL is only referenced when actually painted, so the underlay is
// never fetched while a chat scene covers it.
let _defaultBackground = '';

export function setDefaultBackground(name) {
    _defaultBackground = (name && /^[a-z0-9_-]{1,50}$/.test(name)) ? name : '';
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
    const painted = explicit || _defaultBackground;
    if (painted) {
        bg.style.backgroundImage = `url('/api/backgrounds/${encodeURIComponent(painted)}')`;
        bg.classList.add('has-bg');
    } else {
        bg.style.backgroundImage = '';
        bg.classList.remove('has-bg');
    }
    // dataset.scene records the chat's OWN scene only ('' when riding the
    // underlay) — the per-chat Scene modal must not claim the global default.
    bg.dataset.scene = explicit;
}
