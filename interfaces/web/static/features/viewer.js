// features/viewer.js — a tab that lost the feed of the turn it started.
// Server-owned turns, 2026-09-15 (record tmp/server-owned-turns-plan.md).
//
// Krem's phone: Brave drops the SSE socket on screen lock. The server keeps
// her turn running now; this tab stops being anything special — there are
// only viewers, one of them happened to press Send — and reattaches `since`
// the last seq it saw. Stop still works through the mirror's /api/cancel-by-
// chat path, Send stays busy until the turn really ends, the bubble keeps
// filling in place. Audio is never replayed: the reattached feed is text-only
// and the caller speaks the finished reply once if TTS is on.
import * as api from '../api.js';
import * as ui from '../ui.js';
import { getBoundChat } from '../core/bound-chat.js';
import { releaseOwnership, refresh } from '../core/state.js';

const BACKOFF_MS = [1000, 2000, 4000, 8000, 15000, 15000];

export async function becomeViewer({ since, handlers, onGiveUp }) {
    releaseOwnership();   // controller → null: main.js's typing mirror holds the button now
    ui.showToast("Connection dropped — she's still working, reconnecting…", 'warning');
    const chat = getBoundChat() || document.getElementById('chat-select')?.value || null;
    let lastSeq = typeof since === 'number' ? since : null;

    const giveUp = async (reason) => {
        if (document.getElementById('streaming-message')) ui.detachStreaming();
        try { await onGiveUp?.(reason); } catch { /* button release is best-effort */ }
        await refresh(false);
        if (reason === 'unreachable') ui.showToast("Couldn't reconnect — her reply lands in the chat when she finishes", 'error');
    };

    for (let i = 0; i < BACKOFF_MS.length; i++) {
        await new Promise(r => setTimeout(r, BACKOFF_MS[i]));
        let outcome = null;
        const h = {
            ...handlers,
            onError: (e, code, seq) => {
                if (typeof seq === 'number') lastSeq = seq;
                outcome = e?.feedLost ? 'lost' : 'error';
                if (outcome === 'error') handlers.onError?.(e, code);
            },
            onResync: async () => {
                // The ring no longer reaches back to our seq: repaint from
                // history, then keep streaming the live remainder into a
                // fresh bubble.
                ui.detachStreaming();
                await refresh(false);
                ui.startStreaming();
                handlers.onResync?.();
            },
        };
        const r = await api.attachTurn(chat, lastSeq, h);
        if (typeof r.lastSeq === 'number') lastSeq = r.lastSeq;
        if (r.live === true && outcome !== 'lost') return;     // ended through the handlers
        if (r.live === false) { await giveUp('finished'); return; }
        // unreachable, or the feed died again → back off and retry
    }
    await giveUp('unreachable');
}
