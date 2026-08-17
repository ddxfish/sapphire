// core/motions.js — ambient motion layer host (themes-v2 P3, plan: tmp/themes-v2-plan.md)
//
// Motions replace the legacy theme scripts[] lane (which had NO teardown —
// removing a <script> tag never stopped its rAF loop). A motion is an ES
// module default-exporting { id, name, mount(host, settings), unmount() };
// this loader dynamic-import()s it, holds the module ref, and calls unmount()
// before any switch — the teardown is a real function call, not a prayer.
//
// The host div #motion-layer is a CHILD of #chatbg (before #chatbg-overlay):
// #chatbg is a MOVING node — organs.js re-parents it into story rooms — so a
// child rides the transplant while a sibling would be orphaned invisible.
//
// Resolution: per-chat motion (chat settings, set in the scene modal or by
// the set_motion tool) > explicit user pick (localStorage 'sapphire-motion',
// 'none' = explicitly off) > theme bundle default > none. The global pick is
// the master default; a chat value overrides it, unset falls through — the
// exact shape of chat background > underlay (Krem's rulings 2026-08-17).
//
// The motion only runs when ALL of these hold (each wired below):
//   - prefers-reduced-motion: gates THEME-DEFAULT motions only. An explicit
//     in-app pick is user consent and always wins — the OS flag sets the
//     default, it must never lock the picker (Krem's box: GNOME
//     enable-animations=false made the row unclickable, 2026-08-16).
//   - no view OWNS the chat surface (data-bg-owner on #chatbg — story rooms
//     control their own presentation). Scene/theme bg images do NOT suppress
//     (Krem's ruling 2026-08-16: snow over a winter scene is the point) —
//     motion paints above the bg image, under the readability scrim.
//   - the tab is visible (sd-server visibility-pause pattern)
//   - #motion-layer is actually on screen (view switches away from chat)
//
// settings.speed / settings.intensity (localStorage 'sapphire-motion-speed'
// / '-intensity', set in Visual) are merged into the settings passed to
// mount() — plugin motions get both for free.

let _registry = [];
let _themeMotion = '';
let _chatMotion = '';      // per-chat override ('' = unset, falls through)
let _mounted = null;       // { id, mod }
let _gen = 0;              // supersedes in-flight imports on rapid switches
let _suppressed = false;   // a view owns the chat surface (story room)
let _hidden = document.hidden;
let _offscreen = false;    // host not in viewport (other view active)
let _reduced = false;
let _wired = false;
let _fetchRetries = 0;
let _retryTimer = 0;

export function getMotions() { return _registry; }

function _explicitPick() {
    try { return localStorage.getItem('sapphire-motion') || ''; } catch { return ''; }
}

// Effective desired motion id ('' = none), ignoring suppression state.
export function currentMotionId() {
    if (_chatMotion && _registry.some(m => m.id === _chatMotion)) return _chatMotion;
    const pick = _explicitPick();
    if (pick === 'none') return '';
    // An orphan pick (its plugin uninstalled/disabled) must not block the
    // chain — fall through to the theme default. The key is kept untouched:
    // the pick comes back if its plugin returns.
    const valid = pick && _registry.some(m => m.id === pick);
    const want = valid ? pick : _themeMotion;
    return _registry.some(m => m.id === want) ? want : '';
}

async function _sync() {
    const host = document.getElementById('motion-layer');
    if (!host) return;
    // reduced-motion blocks theme defaults only — an explicit pick is
    // consent, and a per-chat pick is consent too (someone chose it for
    // this chat deliberately).
    const chatPick = _chatMotion && _registry.some(m => m.id === _chatMotion);
    // Consent = a VALID explicit pick (an orphan pick now falls through to
    // the theme default in currentMotionId, and that fallback must stay
    // gated under reduced motion; 'none' resolves to off either way).
    const p = _explicitPick();
    const pickConsent = p && _registry.some(m => m.id === p);
    const gated = _reduced && !pickConsent && !chatPick;
    const want = (!gated && !_suppressed && !_hidden && !_offscreen) ? currentMotionId() : '';
    // Supersede ANY in-flight import BEFORE the no-op check — an import
    // launched under an older desired state must not land in a state that
    // said 'off' (it would paint over a story room / loop offscreen).
    const gen = ++_gen;
    if ((_mounted ? _mounted.id : '') === want) return;
    if (_mounted) {
        try { _mounted.mod.unmount(); }
        catch (e) { console.warn('[Motion] unmount failed:', e); }
        host.innerHTML = '';   // backstop: a leaky unmount never strands nodes
        _mounted = null;
    }
    if (!want) return;
    const entry = _registry.find(m => m.id === want);
    const src = entry && entry.script || '';
    // Same-app module URLs only — /api/motions builds exactly these prefixes.
    if (!src.startsWith('/static/motions/') && !src.startsWith('/plugin-web/')) return;
    let mod;
    try { mod = (await import(src)).default; }
    catch (e) { console.warn(`[Motion] failed to load '${want}':`, e); return; }
    if (gen !== _gen) return;   // superseded while importing
    if (!mod || typeof mod.mount !== 'function' || typeof mod.unmount !== 'function') {
        console.warn(`[Motion] '${want}' does not export {mount, unmount} — skipped`);
        return;
    }
    let speed = 1, intensity = 1;
    try {
        speed = parseFloat(localStorage.getItem('sapphire-motion-speed')) || 1;
        intensity = parseFloat(localStorage.getItem('sapphire-motion-intensity')) || 1;
    } catch {}
    try {
        mod.mount(host, { ...(entry.settings || {}), speed, intensity });
        _mounted = { id: want, mod };
    } catch (e) {
        console.warn(`[Motion] mount of '${want}' failed:`, e);
        host.innerHTML = '';
    }
}

// Force a remount (theme switch): motions sample theme colors at mount.
function _restart() {
    if (_mounted) {
        try { _mounted.mod.unmount(); } catch {}
        const host = document.getElementById('motion-layer');
        if (host) host.innerHTML = '';
        _mounted = null;
    }
    _sync();
}

// Per-chat override — set by chat.js on chat switch (settings.motion), the
// scene modal, and the set_motion tool via CHAT_SETTINGS_CHANGED. '' clears
// back to the global pick / theme default. Not persisted here: the chat's
// settings row is the store, this is just the live value.
export function setChatMotion(id) {
    // Uppercase allowed: plugin names are [a-zA-Z0-9_-] and mint into the id.
    const v = (typeof id === 'string' && /^[a-zA-Z0-9:_-]{1,120}$/.test(id)) ? id : '';
    if (v === _chatMotion) return;
    _chatMotion = v;
    _sync();
}

export function chatMotionId() { return _chatMotion; }
export function themeMotionId() { return _themeMotion; }

// Explicit user pick. '' or 'none' = explicitly off — stored either way, so
// a user's None survives switching to a theme that bundles a motion.
export function applyMotion(id) {
    try { localStorage.setItem('sapphire-motion', id || 'none'); } catch {}
    _sync();
}

// Global speed / intensity multipliers — remount so the running motion picks them up.
export function setMotionSpeed(mult) {
    try { localStorage.setItem('sapphire-motion-speed', String(mult || 1)); } catch {}
    _restart();
}

export function setMotionIntensity(mult) {
    try { localStorage.setItem('sapphire-motion-intensity', String(mult || 1)); } catch {}
    _restart();
}

// Theme bundle default (set by core/theme.js _applyBundle, like setThemeBackground).
export function setThemeMotion(id) {
    _themeMotion = (typeof id === 'string' && /^[a-zA-Z0-9:_-]{1,120}$/.test(id)) ? id : '';
    _sync();
}

// Public remount hook — theme.js calls it when a theme stylesheet finishes
// LOADING (the data-theme observer fires before the CSS lands, so mount-time
// color sampling would read the old palette).
export function restartMotion() { _restart(); }

export async function initMotions() {
    if (!_wired) {
        _wired = true;

        const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
        _reduced = mq.matches;
        if (mq.addEventListener) mq.addEventListener('change', e => { _reduced = e.matches; _sync(); });

        document.addEventListener('visibilitychange', () => { _hidden = document.hidden; _sync(); });

        // Multi-tab: follow motion picks made in another tab (theme.js pattern).
        window.addEventListener('storage', e => { if (e.key === 'sapphire-motion') _sync(); });

        const bg = document.getElementById('chatbg');
        if (bg) {
            // Only a view that OWNS the surface suppresses (story rooms —
            // they control their own presentation). Bg images don't: motion
            // paints above the image, under the readability scrim.
            const owned = () => bg.hasAttribute('data-bg-owner');
            _suppressed = owned();
            new MutationObserver(() => {
                const s = owned();
                if (s !== _suppressed) { _suppressed = s; _sync(); }
            }).observe(bg, { attributes: true, attributeFilter: ['data-bg-owner'] });
        }

        const host = document.getElementById('motion-layer');
        if (host && 'IntersectionObserver' in window) {
            new IntersectionObserver(entries => {
                const off = !entries.some(x => x.isIntersecting);
                if (off !== _offscreen) { _offscreen = off; _sync(); }
            }).observe(host);
        }

        // Remount on theme switch so mount-time color sampling stays current.
        new MutationObserver(() => _restart())
            .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

        // Plugin enable/disable changes the registry — refetch, and the
        // _sync below unmounts a motion whose plugin just went away
        // (already-imported modules kept animating after disable).
        document.addEventListener('sapphire:plugin_toggled', () => initMotions());
    }
    let ok = false;
    try {
        const res = await fetch('/api/motions');
        if (res.ok) { _registry = (await res.json()).motions || []; ok = true; }
    } catch {}
    if (ok) {
        _fetchRetries = 0;
    } else if (_fetchRetries < 5 && !_retryTimer) {
        // A boot-time blip (server mid-restart) otherwise kills motions for
        // the whole session — the only other refetch is opening Visual.
        _fetchRetries++;
        console.warn(`[Motion] /api/motions unavailable — retry ${_fetchRetries}/5 in 10s`);
        _retryTimer = setTimeout(() => { _retryTimer = 0; initMotions(); }, 10000);
    }
    _sync();
}
