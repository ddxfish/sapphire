// features/doc-bar.js - the row above the composer: what she has of this chat.
// Documents stay until their X (core/documents.py). Pictures count down the turns she
// can still see them (IMAGE_MEMORY_TURNS, the server's own count) and leave by
// themselves. Pictures staged for the next message sit in #image-preview-area, in
// this same row (handlers/send-handlers.js). Empty row, no row.
import * as eventBus from '../core/event-bus.js';
import * as ui from '../ui.js';
import * as api from '../api.js';
import { organHolder } from '../surface/organs.js';

const MAX_BYTES = 25 * 1024 * 1024;
const REPAINT_MS = 150;      // a turn's events arrive in bursts
const LEVELS = [['off', 'Off'], ['light', 'Light, ~2K/turn'], ['normal', 'Normal, ~8K/turn'], ['heavy', 'Heavy, ~24K/turn']];
const STATUS_TIPS = {
    searchable: '',
    keyword: 'Found by exact words only for now: nothing has embedded it yet.',
    pictures: 'A scan: no text to search. She looks at its pages as pictures, on a model that can see.',
};

const PRIVATE_WARNING = 'Not protected: sealing this private chat does not encrypt its documents. Anyone with access to this computer\'s disk can read them.';

let initialized = false;
const uploading = new Map(); // chat -> [names on their way up]
let paintId = 0;             // only the newest paint draws: a slow answer can't overwrite a chat switch
let paintedChat = '';
let repaintTimer = null;

const chatName = () => document.getElementById('chat-select')?.value || '';
const pills = () => document.getElementById('doc-pills');
const url = (chat, rest = '') => `/api/chats/${encodeURIComponent(chat)}/${rest}`;

function pill(cls, text, title, onX) {
    const el = document.createElement('span');
    el.className = `agent-pill ${cls}`;
    el.title = title;
    const name = document.createElement('span');
    name.className = 'agent-name';
    name.textContent = text;
    el.appendChild(name);
    if (onX) {
        const x = document.createElement('span');
        x.className = 'agent-x';
        x.title = 'Remove';
        x.textContent = '×';
        x.addEventListener('click', e => { e.stopPropagation(); onX(); });
        el.appendChild(x);
    }
    return el;
}

function docPill(chat, d, isPrivate) {
    const icon = isPrivate ? '⚠️' : d.status === 'pictures' ? '\u{1F5BC}' : '\u{1F4C4}';
    const size = d.pages ? `${d.pages} pages` : `${d.chunks} parts`;
    const tip = [`${d.filename}, ${size}`, STATUS_TIPS[d.status], isPrivate && PRIVATE_WARNING].filter(Boolean).join('\n');
    return pill(`doc-pill doc-${d.status}${isPrivate ? ' doc-exposed' : ''}`, `${icon} ${d.filename}`, tip, async () => {
        try {
            const r = await fetch(url(chat, `documents/${encodeURIComponent(d.filename)}`), { method: 'DELETE' });
            if (!r.ok) ui.showToast(`Couldn't remove ${d.filename}`, 'error');
        } catch { ui.showToast(`Couldn't remove ${d.filename}`, 'error'); }
        refreshDocBar();
    });
}

const turnsWord = n => `${n} turn${n === 1 ? '' : 's'}`;

async function hide(chat, ids) {
    await Promise.all(ids.map(id => fetch(url(chat, `images-in-view/${encodeURIComponent(id)}/hide`), { method: 'POST' }).catch(() => {})));
    refreshDocBar();
}

function imagePill(chat, img) {
    const el = pill('doc-pill doc-image', turnsWord(img.turns), `She can see this picture for ${turnsWord(img.turns)} more. It stays in the chat afterwards.`,
                    () => hide(chat, [img.id]));
    const thumb = document.createElement('img');
    thumb.src = `/api/tool-image/${encodeURIComponent(img.id)}`;
    thumb.alt = '';
    el.prepend(thumb);
    return el;
}

function levelChip(chat, level) {
    const el = document.createElement('label');
    el.className = 'agent-pill doc-level';
    el.title = 'How much of these documents she is handed each turn. She can always read more herself.';
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Document budget');
    for (const [value, words] of LEVELS) select.add(new Option(`Docs: ${words}`, value));
    select.value = level;
    select.addEventListener('change', async () => {
        try { await api.updateChatSettings(chat, { rag_context: select.value }); }
        catch (e) {
            ui.showToast(`Couldn't save that: ${e.message}`, 'error');
            refreshDocBar();    // back to what is really saved
        }
    });
    el.appendChild(select);
    return el;
}

/** Repaint from the server, soon: events arrive in bursts. */
export function refreshDocBar() {
    clearTimeout(repaintTimer);
    repaintTimer = setTimeout(paint, REPAINT_MS);
}

/** Rooms and games borrow the composer and have no documents. */
async function paint() {
    const box = pills();
    if (!box) return;
    // an open dropdown would die under the cursor: look again once it's closed
    if (box.querySelector('select') === document.activeElement && chatName() === paintedChat) return;
    const id = ++paintId;
    const chat = chatName();
    if (!chat || organHolder()) { paintedChat = ''; box.replaceChildren(); return; }
    let docs = [], images = [], level = 'normal', isPrivate = false;
    try {
        const [d, i] = await Promise.all([
            fetch(url(chat, 'documents')).then(r => r.ok ? r.json() : {}),
            fetch(url(chat, 'images-in-view')).then(r => r.ok ? r.json() : {}),
        ]);
        docs = d.documents || [];
        images = i.images || [];
        if (docs.length) {
            const settings = (await api.getChatSettings(chat))?.settings;
            level = settings?.rag_context || 'normal';
            isPrivate = !!settings?.private_chat;
        }
    } catch (e) {
        console.warn('[DocBar] refresh failed:', e);
        // never leave another chat's pills, with their live X, under this one
        if (id === paintId && paintedChat !== chat) { paintedChat = ''; box.replaceChildren(); }
        return;
    }
    if (id !== paintId) return;
    paintedChat = chat;
    const row = docs.map(d => docPill(chat, d, isPrivate));
    for (const name of uploading.get(chat) || []) row.push(pill('doc-pill doc-uploading', `\u{1F4C4} ${name}`, 'Reading this file...'));
    const mine = images.filter(i => i.source === 'you'), tools = images.filter(i => i.source === 'tool');
    row.push(...mine.map(i => imagePill(chat, i)));
    if (tools.length) {
        const most = Math.max(...tools.map(t => t.turns));
        row.push(pill('doc-pill doc-image', `\u{1F5BC} ${tools.length} from tools, ${turnsWord(most)}`,
                      'Pictures her tools showed her. She can still see them.', () => hide(chat, tools.map(t => t.id))));
    }
    if (docs.length) row.unshift(levelChip(chat, level));
    box.replaceChildren(...row);
}

/** Any non-image file, however it got here (+, drop, paste). */
export async function attachDocument(file) {
    if (organHolder()) { ui.showToast('Documents work in normal chats, not here.', 'info'); return; }
    const chat = chatName();
    if (!chat) return;
    if (file.size > MAX_BYTES) { ui.showToast(`${file.name} is too large (max 25MB).`, 'error'); return; }
    const form = new FormData();
    form.append('file', file);
    uploading.set(chat, [...(uploading.get(chat) || []), file.name]);
    refreshDocBar();
    try {
        const r = await fetch(url(chat, 'documents'), { method: 'POST', body: form });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            ui.showToast(typeof err.detail === 'string' ? err.detail : `Couldn't attach ${file.name}`, 'error');
        } else if ((await api.getChatSettings(chat).catch(() => null))?.settings?.private_chat) {
            ui.showToast("Attached, but not protected: sealing this private chat won't encrypt it.", 'error', 9000);
        }
    } catch {
        ui.showToast(`Couldn't attach ${file.name}`, 'error');
    } finally {
        const left = uploading.get(chat) || [];
        left.splice(left.indexOf(file.name), 1);
        if (!left.length) uploading.delete(chat);
        refreshDocBar();
    }
}

export function initDocBar() {
    if (initialized) return;
    initialized = true;
    const select = document.getElementById('chat-select');
    select?.addEventListener('chat-activated', refreshDocBar);
    select?.addEventListener('chat-list-ready', refreshDocBar);   // a rename lands in the picker after its event
    window.addEventListener('organs-moved', refreshDocBar);       // a room took the composer, or gave it back
    const E = eventBus.Events;
    for (const ev of [E.CHAT_SWITCHED, E.MESSAGE_ADDED, E.MESSAGE_REMOVED, E.CHAT_CLEARED, E.AI_TYPING_END,
                      E.CHAT_RENAMED, E.CHAT_TRIMMED, E.CHAT_SETTINGS_CHANGED, 'documents_changed'])
        eventBus.on(ev, refreshDocBar);
    refreshDocBar();
}
