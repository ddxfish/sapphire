// shared/notify.js - the notification center's browser half, and THE toast
// renderer (tmp/notification-center-plan.md, 2026-10-20).
//
// A toast is what you did; a notification is what happened to you.
//   notify({title, body, level, go, source, key, seconds})  a row on the server + the toast its level earns
//   flash(message, level, seconds)                            passthrough: a toast, no row
//   showToast(msg, type, duration)                            the legacy name every call site still uses:
//                                                             warning/error -> notify, success/info -> flash
// Rows come down the bus (`notification` = born or changed state, `notification_progress`
// = a tick, `notification_flash`, `notification_read`, `notifications_read`,
// `notification_gone`, `notifications_cleared`) and from GET /api/notifications at boot and
// on every reconnect. This module keeps the mirror the sidebar row and the Notifications
// page read (rows(), counts(), onChange()).
//
// Levels: info never toasts (it is the log), success toasts, warning counts yellow, error
// and critical count red, critical sticks until clicked. The threshold
// (NOTIFY_TOAST_MIN_LEVEL) applies to rows; a flash is explicit and always shows. An active
// progress row toasts when it starts and when it ends. Three toasts show at once; the rest
// wait their turn. A row-born toast is a link: click = mark read + go there.
import * as eventBus from '../core/event-bus.js';
import { fetchWithTimeout } from './fetch.js';

export const LEVELS = ['info', 'success', 'warning', 'error', 'critical'];
const rank = l => Math.max(0, LEVELS.indexOf(l));
const MAX_VISIBLE = 3;
const LEAVE_MS = 300;            // matches .toast.leaving in shared.css
const OUR_KEYS = { NOTIFY_TOAST_SECONDS: 1, NOTIFY_TOAST_MIN_LEVEL: 1 };

let _seconds = 4;
let _minLevel = 'success';
const _rows = new Map();
const _listeners = new Set();
let _booted = false;
let _connectedOnce = false;

// ── the toast lane ──────────────────────────────────────────────────────────
const _queue = [];
let _visible = 0;

function _shake() {
    const bg = document.getElementById('chatbg');
    if (!bg) return;
    bg.classList.add('shake');
    setTimeout(() => bg.classList.remove('shake'), 500);
}

function _paint({ text, level, seconds, onClick, buttons }) {
    const box = document.getElementById('toast-container');
    if (!box) return;
    const el = document.createElement('div');
    el.className = `toast ${level === 'critical' ? 'error' : level}${buttons ? ' toast-action' : ''}`;
    const msg = document.createElement('span');
    msg.className = buttons ? 'toast-message' : 'toast-text';
    msg.textContent = String(text).slice(0, 500);
    el.appendChild(msg);
    _visible++;
    let gone = false;
    const remove = () => { if (gone) return; gone = true; el.remove(); _visible--; _drain(); };
    if (buttons) {
        const row = document.createElement('div');
        row.className = 'toast-buttons';
        for (const b of buttons) {
            const btn = document.createElement('button');
            btn.className = `toast-btn ${b.dismiss ? 'toast-btn-dismiss' : 'toast-btn-action'}`;
            btn.textContent = b.label;
            if (b.title) btn.title = b.title;
            btn.addEventListener('click', e => { e.stopPropagation(); remove(); b.onClick?.(); });
            row.appendChild(btn);
        }
        el.appendChild(row);
    } else {
        const close = document.createElement('button');
        close.className = 'toast-close';
        close.textContent = '×';
        close.title = 'Dismiss';
        close.addEventListener('click', e => { e.stopPropagation(); remove(); });
        el.appendChild(close);
    }
    if (onClick) {
        el.style.cursor = 'pointer';
        el.addEventListener('click', () => { remove(); onClick(); });
    } else if (!(seconds > 0) && !buttons) {
        el.style.cursor = 'pointer';
        el.addEventListener('click', remove);
    }
    box.appendChild(el);
    if (level === 'error' || level === 'critical') _shake();
    // The ONE exit clock: inert the instant it fades, so a toast never sits
    // invisible and clickable over something else (ghost toast, 2026-08-29).
    // seconds <= 0 = sticky, click to dismiss.
    if (seconds > 0) {
        const ms = seconds * 1000;
        setTimeout(() => el.classList.add('leaving'), Math.max(0, ms - LEAVE_MS));
        setTimeout(remove, ms);
    }
    return el;
}

function _drain() {
    while (_visible < MAX_VISIBLE && _queue.length) _paint(_queue.shift());
}

function _toast(spec) {
    // A re-fire of a toast on screen shows again (that's the point of a
    // re-fire); one already WAITING in line is dropped - ten failures in a
    // row were ten toasts draining for half a minute.
    const t = String(spec.text);
    if (_queue.some(q => String(q.text) === t)) return;
    _queue.push(spec);
    _drain();
}

/** Legacy: fade an element you built yourself. duration in ms, <= 0 = sticky. */
export function dismissLater(toast, duration) {
    if (!(duration > 0)) return;
    setTimeout(() => toast.classList.add('leaving'), Math.max(0, duration - LEAVE_MS));
    setTimeout(() => toast.remove(), duration);
}

// ── settings ────────────────────────────────────────────────────────────────
function _applySettings(settings) {
    if (!settings) return;
    if (settings.NOTIFY_TOAST_SECONDS !== undefined) {
        const n = Number(settings.NOTIFY_TOAST_SECONDS);
        if (Number.isFinite(n) && n >= 1) _seconds = n;
    }
    if (LEVELS.includes(settings.NOTIFY_TOAST_MIN_LEVEL)) _minLevel = settings.NOTIFY_TOAST_MIN_LEVEL;
}

export function settings() { return { seconds: _seconds, minLevel: _minLevel }; }
/** The page's gear saved one of our keys: apply it now, the bus echo confirms. */
export const applySettings = _applySettings;

// ── rows become toasts ──────────────────────────────────────────────────────
const _rowToasts = l => rank(l) >= Math.max(1, rank(_minLevel));   // info never toasts
const _textOf = row => (row.body ? `${row.title}: ${row.body}` : row.title);

function _open(row) {
    if (row.id) markRead(row.id);
    goTo(row.go);
}

function _toastRow(row, seconds) {
    const onClick = row.go ? () => _open(row) : (row.id ? () => markRead(row.id) : null);
    if (row.state === 'active') {
        if (rank(_minLevel) <= rank('warning')) _toast({ text: row.title, level: 'info', seconds: seconds ?? _seconds, onClick });
        return;
    }
    if (!_rowToasts(row.level)) return;
    const sticky = row.level === 'critical';
    _toast({ text: _textOf(row), level: row.level, seconds: sticky ? 0 : (seconds ?? _seconds), onClick });
}

// ── public: make one ────────────────────────────────────────────────────────
export function flash(message, level = 'info', seconds) {
    if (!LEVELS.includes(level)) level = 'info';
    _toast({ text: message, level, seconds: seconds === undefined ? _seconds : seconds });
}

export async function notify({ title, body = '', level = 'info', go = null, source = 'web', key = null, seconds } = {}) {
    if (!title) return null;
    if (!LEVELS.includes(level)) level = 'info';
    const now = Date.now() / 1000;
    // Painted first, so a dead server can still toast "the server is dead".
    const local = { id: null, ts: now, updated: now, level, title: String(title).slice(0, 200),
                    body: String(body ?? '').slice(0, 1000), source, state: 'open', progress: null,
                    go, key, read: false };
    _toastRow(local, seconds);
    try {
        const res = await fetchWithTimeout('/api/notifications', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, body, level, go, source, key }),
        }, 10000);
        if (res?.row) { local.id = res.row.id; _upsert(res.row); return res.row.id; }
    } catch (e) {
        console.warn('[Notify] row not saved:', e?.message || e);
    }
    return null;
}

// A short hash of a message: the legacy shim's dedupe key, so the same error
// repeating (a dead LLM server, four sends) re-fires ONE row instead of four.
function _hash(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
    return h.toString(16);
}

/** The legacy name. duration in ms; <= 0 = sticky. */
export function showToast(msg, type = 'info', duration = 4000) {
    const seconds = duration > 0 ? duration / 1000 : 0;
    if (type === 'warning' || type === 'error') {
        notify({ title: msg, level: type, seconds, key: `msg:${_hash(String(msg))}` });
        return;
    }
    flash(msg, type === 'success' ? 'success' : 'info', seconds);
}

/** Legacy: a toast with a button. No row. */
export function showActionToast(message, actionText, actionCallback, type = 'info', duration = 0) {
    _toast({ text: message, level: LEVELS.includes(type) ? type : 'info',
             seconds: duration > 0 ? duration / 1000 : 0,
             buttons: [{ label: actionText, onClick: actionCallback },
                       { label: '✕', title: 'Dismiss', dismiss: true }] });
}

// ── the mirror ──────────────────────────────────────────────────────────────
function _emit() {
    for (const fn of _listeners) { try { fn(); } catch (e) { console.error('[Notify] listener:', e); } }
}
function _strip(d) { const { origin, ...row } = d || {}; return row; }
function _upsert(row) { if (row?.id) { _rows.set(row.id, row); _emit(); } }

/** Called on every change to the mirror. Returns the unsubscribe. */
export function onChange(fn) { _listeners.add(fn); return () => _listeners.delete(fn); }

/** Active first, then newest first. */
export function rows({ level, source, unread } = {}) {
    let out = [..._rows.values()];
    if (level) { const want = new Set(Array.isArray(level) ? level : [level]); out = out.filter(r => want.has(r.level)); }
    if (source) out = out.filter(r => r.source === source);
    if (unread !== undefined) out = out.filter(r => !r.read === !!unread);
    return out.sort((a, b) => (a.state === 'active' ? 0 : 1) - (b.state === 'active' ? 0 : 1) || b.updated - a.updated);
}

export function counts() {
    let red = 0, yellow = 0, active = 0;
    for (const r of _rows.values()) {
        if (r.state === 'active') active++;
        else if (!r.read) {
            if (r.level === 'error' || r.level === 'critical') red++;
            else if (r.level === 'warning') yellow++;
        }
    }
    return { red, yellow, active };
}

export async function refresh() {
    try {
        const res = await fetchWithTimeout('/api/notifications?limit=500', {}, 15000);
        if (!res?.rows) return;
        _rows.clear();
        for (const r of res.rows) _rows.set(r.id, r);
        _emit();
    } catch (e) {
        console.warn('[Notify] list failed:', e?.message || e);
    }
}

export async function markRead(id) {
    const r = _rows.get(id);
    if (r && !r.read) { r.read = true; _emit(); }
    try { await fetchWithTimeout(`/api/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }, 10000); } catch {}
}

export async function markAllRead() {
    let changed = false;
    for (const r of _rows.values()) if (!r.read) { r.read = true; changed = true; }
    if (changed) _emit();
    try { await fetchWithTimeout('/api/notifications/read', { method: 'POST' }, 10000); } catch {}
}

export async function dismiss(id) {
    const r = _rows.get(id);
    if (r?.state === 'active') return false;
    _rows.delete(id);
    _emit();
    try { await fetchWithTimeout(`/api/notifications/${encodeURIComponent(id)}`, { method: 'DELETE' }, 10000); } catch {}
    return true;
}

export async function clearAll() {
    for (const [id, r] of _rows) if (r.state !== 'active') _rows.delete(id);
    _emit();
    try { await fetchWithTimeout('/api/notifications', { method: 'DELETE' }, 10000); } catch {}
}

// ── where a row takes you ───────────────────────────────────────────────────
function _highlight(view, key, tries = 0) {
    const el = document.getElementById(`view-${view}`)?.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!el) {
        if (tries < 20) setTimeout(() => _highlight(view, key, tries + 1), 150);   // the tab is still painting
        return;
    }
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el.classList.add('notify-target');
    setTimeout(() => el.classList.remove('notify-target'), 2400);
}

/** go = {view, tab?, key?} or {hash}. tab rides the nav-rail's settings door;
 *  key scrolls to and lights the setting row carrying that data-key. */
export async function goTo(go) {
    if (!go) return;
    const router = await import('../core/router.js');
    if (go.hash) {
        location.hash = go.hash;
        router.switchView(go.hash.slice(1).split('/')[0]);
        return;
    }
    if (!go.view) return;
    if (go.tab) {
        if (router.getCurrentView() === go.view) {
            document.getElementById(`view-${go.view}`)
                ?.dispatchEvent(new CustomEvent('settings-navigate', { detail: { tab: go.tab }, bubbles: true }));
        } else {
            window._settingsTab = go.tab;
            router.switchView(go.view);
        }
    } else {
        router.switchView(go.view);
    }
    if (go.key) _highlight(go.view, go.key);
}

// ── boot ────────────────────────────────────────────────────────────────────
export function init(initData) {
    if (_booted) return;
    _booted = true;
    _applySettings(initData?.settings);
    eventBus.on('notification', d => {
        const row = _strip(d);
        const prev = _rows.get(row.id);
        _upsert(row);
        if (!prev || prev.state !== row.state || prev.updated !== row.updated) _toastRow(row);
    });
    eventBus.on('notification_progress', d => {
        const r = _rows.get(d?.id);
        if (!r) return;
        r.progress = d.progress;
        if (d.body !== undefined) r.body = d.body;
        r.updated = d.updated;
        _emit();
    });
    eventBus.on('notification_flash', d => { if (d?.message) flash(d.message, d.level, d.seconds ?? undefined); });
    eventBus.on('notification_read', d => { const r = _rows.get(d?.id); if (r && !r.read) { r.read = true; _emit(); } });
    eventBus.on('notifications_read', () => { for (const r of _rows.values()) r.read = true; _emit(); });
    eventBus.on('notification_gone', d => { if (_rows.delete(d?.id)) _emit(); });
    eventBus.on('notifications_cleared', () => { for (const [id, r] of _rows) if (r.state !== 'active') _rows.delete(id); _emit(); });
    eventBus.on('settings_changed', d => { if (d && d.key in OUR_KEYS) _applySettings({ [d.key]: d.value }); });
    // The list is the truth: fetched now, and again after every reconnect
    // (events missed in the gap are rows on the server).
    eventBus.on('bus_connected', () => { if (_connectedOnce) refresh(); _connectedOnce = true; });
    refresh();
}
