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
