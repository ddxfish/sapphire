// Captioning demo — banner showing each TTS chunk as it's spoken.
//
// Subscribes to event-bus events published by hooks/captions.py:
//   captioning_start  — clear stale text, prepare banner
//   captioning_chunk  — set banner text to the current spoken chunk
//   captioning_end    — fade the banner out
//
// This plugin exists to demo the v2.7.0 streaming-TTS hook surface end-to-end:
// brain-side hook handler → event_bus.publish → SSE → frontend listener.
import { on } from '/static/core/event-bus.js';

let banner = null;
let hideTimer = null;

function ensureBanner() {
    if (banner) return banner;
    banner = document.createElement('div');
    banner.id = 'captioning-banner';
    banner.style.cssText = [
        'position: fixed',
        'bottom: 24px',
        'left: 50%',
        'transform: translateX(-50%)',
        'background: rgba(10, 14, 22, 0.85)',
        'color: #e8edf5',
        'padding: 10px 18px',
        'border-radius: 12px',
        'font-family: system-ui, -apple-system, sans-serif',
        'font-size: 17px',
        'line-height: 1.4',
        'max-width: min(80vw, 900px)',
        'text-align: center',
        'box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35)',
        'border: 1px solid rgba(120, 160, 230, 0.25)',
        'z-index: 9999',
        'opacity: 0',
        'transition: opacity 200ms ease-out',
        'pointer-events: none',
        'display: none',
    ].join(';');
    document.body.appendChild(banner);
    return banner;
}

function show(text) {
    const el = ensureBanner();
    el.textContent = text;
    el.style.display = 'block';
    // Force reflow so the transition fires
    void el.offsetHeight;
    el.style.opacity = '1';
    clearTimeout(hideTimer);
    // Safety auto-hide if `captioning_end` never arrives
    hideTimer = setTimeout(() => fade(), 12000);
}

function fade() {
    if (!banner) return;
    clearTimeout(hideTimer);
    banner.style.opacity = '0';
    hideTimer = setTimeout(() => {
        if (banner) banner.style.display = 'none';
    }, 250);
}

on('captioning_start', () => {
    // Pre-build the DOM so the first chunk doesn't pay layout cost
    ensureBanner();
});

on('captioning_chunk', (data) => {
    const t = (data && data.text) ? String(data.text).trim() : '';
    if (t) show(t);
});

on('captioning_end', () => {
    // Short delay so the last chunk gets read before it disappears
    setTimeout(fade, 800);
});

export default {
    init() {
        console.log('[Captioning] Loaded — demos tts_chunk_text hook (v2.7.0)');
    },
};
