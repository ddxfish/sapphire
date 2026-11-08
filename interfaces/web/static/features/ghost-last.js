// features/ghost-last.js — the System Prompt accordion's "Last Ghost Sent":
// the ghost envelope the viewed chat's last turn actually carried (plugin
// lines like voice tone included). Server keeps it in memory only; a private
// chat is never kept. Refreshes on chat switch and after each turn.
import * as eventBus from '../core/event-bus.js';
import * as api from '../api.js';
import { getViewedChat } from '../core/bound-chat.js';

const HEADER = '[System context';
let initialized = false;
let seq = 0;

function paint(text, at) {
    const pre = document.getElementById('sb-ghost-last');
    const when = document.getElementById('sb-ghost-last-at');
    if (!pre) return;
    pre.textContent = text;
    pre.classList.toggle('empty', !at);
    if (when) when.textContent = at ? new Date(at * 1000).toLocaleTimeString() : '';
}

export async function refreshGhostLast() {
    const chat = getViewedChat();
    const mine = ++seq;
    if (!chat) return paint('(no chat)', null);
    try {
        const r = await api.chatGhostLast(chat);
        if (mine !== seq) return;                       // a newer refresh owns the box
        if (r.private) return paint('(private chat: not kept)', null);
        if (r.ghost === null) return paint('(no turn since Sapphire started)', null);
        if (!r.ghost) return paint('(the last turn carried no ghost)', r.at);
        const body = r.ghost.split('\n').filter(l => !l.startsWith(HEADER)).join('\n');
        paint(body, r.at);
    } catch {
        if (mine === seq) paint('(could not load)', null);
    }
}

export function initGhostLast() {
    if (initialized) return;
    initialized = true;
    const select = document.getElementById('chat-select');
    select?.addEventListener('chat-activated', refreshGhostLast);
    select?.addEventListener('chat-list-ready', refreshGhostLast);
    const E = eventBus.Events;
    const ifViewed = (data) => { if (!data?.chat || data.chat === getViewedChat()) refreshGhostLast(); };
    for (const ev of [E.MESSAGE_ADDED, E.AI_TYPING_END, E.CHAT_CLEARED])
        eventBus.on(ev, ifViewed);
    for (const ev of [E.CHAT_SWITCHED, E.CHAT_RENAMED])
        eventBus.on(ev, refreshGhostLast);
    refreshGhostLast();
}
