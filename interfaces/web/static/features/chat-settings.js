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

export function applyBackground(name) {
    const bg = document.getElementById('chatbg');
    if (!bg) return;
    if (bg.dataset.bgOwner) {
        bg.dataset.bgPending = name || '';
        return;
    }
    if (name && /^[a-z0-9_-]{1,50}$/.test(name)) {
        bg.style.backgroundImage = `url('/api/backgrounds/${encodeURIComponent(name)}')`;
        bg.classList.add('has-bg');
        bg.dataset.scene = name;
    } else {
        bg.style.backgroundImage = '';
        bg.classList.remove('has-bg');
        bg.dataset.scene = '';
    }
}
