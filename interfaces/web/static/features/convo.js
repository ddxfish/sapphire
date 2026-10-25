// features/convo.js — the ONE owner of Conversation-mode transitions.
//
// Two engines, one switch: 'local' = true speech mode on the server's mic
// (PUT /api/runtime/true-speech); 'browser' = this browser's mic over a
// WebSocket, where the connection itself IS the switch (conversation.js).
// Mutually exclusive server-side. Truth lives in core/state.js and is written
// by the conversation_mode_changed SSE event + the boot GET; the segment and
// the mic button (features/mic.js) only READ it. A phone call is source
// 'phone' — it lights nothing here (not a mic session on this machine).
// The switch survives restarts: the server keeps its last choice (and resumes
// Local itself at boot); a device that chose Browser resumes it on page load.
import * as state from '../core/state.js';
import * as eventBus from '../core/event-bus.js';
import * as conversation from './conversation.js';
import { showToast } from '../shared/toast.js';
import { wordsOf } from '../shared/fetch.js';

const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

export const getConvo = () => state.getConvo();

const isOurs = (c) => c.enabled && (c.source === 'local' || c.source === 'browser');

async function setLocal(enabled) {
    const res = await fetch('/api/runtime/true-speech', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
        body: JSON.stringify({ enabled }),
    });
    const data = await res.json().catch(() => ({}));
    const on = data.active === true;
    if (!res.ok) throw new Error(wordsOf(data, res.status));
    if (on !== enabled) throw new Error(data.note || "Sapphire couldn't switch Conversation mode. Try again.");
    state.setConvo({ enabled: on, source: on ? 'local' : null });   // SSE confirms
}

// mode: 'off' | 'local' | 'browser'
export async function setConvo(mode) {
    const cur = getConvo();
    try {
        if (mode === 'off') {
            // this tab's own intent first: an Off clicked before the SSE echo
            // landed (or mid-retry) used to fall through and leave it on
            if (cur.source === 'browser' || conversation.isWanted()) {
                // WS close IS the switch; state stays 'browser' until the SSE
                // confirms. Painting from state here bounced the segment
                // off->browser->off on EVERY click (S2 #2) -- let the
                // optimistic 'off' stand.
                conversation.stop();
                return;
            }
            if (cur.source === 'local' && cur.enabled) await setLocal(false);
        } else if (mode === 'local') {
            if (cur.source === 'browser' || conversation.isWanted()) conversation.stop();
            await setLocal(true);
        } else if (mode === 'browser') {
            if (cur.source === 'local' && cur.enabled) await setLocal(false);
            if (!(await conversation.start())) { paintSegment(); return; }   // start() said why
            // Browser truth arrives via the SSE; state is still stale here --
            // painting now flashed 'off' after the (slow) mic-permission
            // start (S2 #2b).
            return;
        }
    } catch (e) {
        showToast(e.message || 'Conversation mode failed', 'error');
    }
    paintSegment();
}

// Segment reflects state; `optimistic` pre-lights a choice while its request
// is in flight (local/off only — a browser start waits on the mic-permission
// prompt, so it lights from the event).
export function paintSegment(optimistic) {
    const seg = document.getElementById('conv-seg');
    if (!seg) return;
    const c = getConvo();
    const cur = optimistic ?? (isOurs(c) ? c.source : 'off');
    seg.querySelectorAll('[data-conv]').forEach(b => b.classList.toggle('active', b.dataset.conv === cur));
}

// Page load: Browser was the last choice and THIS device made it. Never a
// permission prompt nobody clicked for -- without a standing grant, just say so.
async function resumeBrowser() {
    let perm = 'granted';
    try { perm = (await navigator.permissions.query({ name: 'microphone' })).state; } catch {}
    if (perm !== 'granted' || !(await conversation.start({ auto: true })))
        showToast('Conversation mode was on — pick Browser to resume it', 'info');
}

export function initConvo() {
    eventBus.on('conversation_mode_changed', (d) => {
        state.setConvo({ enabled: d?.enabled === true, source: d?.source || null });
        paintSegment();
    });
    fetch('/api/runtime/true-speech')
        .then(r => r.ok ? r.json() : null)
        .then(d => {
            if (!d) return;
            state.setConvo({ enabled: d.enabled === true, source: d.source || null });
            paintSegment();
            if (d.last_mode !== 'browser') conversation.forget();   // another device or Local chose since
            // a live session at load is another tab's (a reload's 1001 ends ours) -- never steal it
            else if (conversation.remembered() && !d.enabled) resumeBrowser();
        })
        .catch(() => {});
    document.getElementById('conv-seg')?.addEventListener('click', (e) => {
        const b = e.target.closest('[data-conv]');
        if (!b) return;
        if (b.dataset.conv !== 'browser') paintSegment(b.dataset.conv);
        setConvo(b.dataset.conv);
    });
}
