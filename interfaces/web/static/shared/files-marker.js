// shared/files-marker.js — the ONE attachments renderer (2026-09-26). A tool, or a
// plugin's own turn, hands the user files by appending ONE marker line:
//   <!--FILES:{"title":"…","items":[{"url":"/api/plugin/<name>/…","name":"song.mp3"}]}-->
// Audio items get a player; every item gets a download button. Built in Python by
// core/attachments.py. Sibling of shared/gallery-marker.js, and like it the marker
// is UI-only: core strips it from every copy the model reads.
// Urls are this app's own plugin routes and nothing else — a tool result can carry
// text from the open web, and a marker must never point the browser at a third party.
// Three renderers parse + build through here: tool results in history (ui-parsing
// renderToolResult), tool results live (ui-streaming doEndTool), and message text
// (ui-parsing parseContent — a take the user played lands in their own bubble).

export const FILES_RE = /<!--FILES:(\{[^\n]*\})-->[ \t]*\n?/;
const FILES_RE_ALL = new RegExp(FILES_RE.source, 'g');
const SAFE_URL = /^\/api\/plugin\/[A-Za-z0-9_-]+\/[A-Za-z0-9_.\/-]+(\?[A-Za-z0-9_=&-]*)?$/;
const AUDIO = /\.(mp3|wav|ogg|oga|m4a|flac)$/i;
const MAX_ITEMS = 12;

export function safeUrl(url) {
    return typeof url === 'string' && SAFE_URL.test(url) && !url.includes('..') && !url.includes('//');
}

export function normalizeFiles(raw) {
    const list = (raw && typeof raw === 'object' && Array.isArray(raw.items)) ? raw.items : [];
    return list.slice(0, MAX_ITEMS).map(e => {
        if (!e || typeof e !== 'object' || !safeUrl(e.url)) return null;
        const path = e.url.split('?')[0];
        const name = (typeof e.name === 'string' && e.name.trim()) || path.split('/').pop();
        return { url: e.url, name, audio: AUDIO.test(path) };
    }).filter(Boolean);
}

// → { groups: [{ title, files }], text } — text has every marker removed.
export function parseFilesMarker(text) {
    const src = text || '';
    // a non-string (a legacy list-shaped content) passes through untouched
    if (typeof src !== 'string' || !src.includes('<!--FILES:')) return { groups: [], text: src };
    const groups = [];
    for (const m of src.matchAll(FILES_RE_ALL)) {
        try {
            const parsed = JSON.parse(m[1]);
            const files = normalizeFiles(parsed);
            if (files.length) groups.push({ title: typeof parsed.title === 'string' ? parsed.title : '', files });
        } catch (e) { console.warn('[Files] bad marker JSON:', e); }
    }
    return { groups, text: src.replace(FILES_RE_ALL, '').trimEnd() };
}

function chip(file) {
    const a = document.createElement('a');
    a.className = 'files-chip';
    a.href = file.url;
    a.download = file.name;
    a.title = 'Download ' + file.name;
    a.textContent = '↓ ' + file.name;
    a.addEventListener('click', ev => ev.stopPropagation());
    return a;
}

// groups → [.files-row elements] (empty when there is nothing to show).
export function buildFilesRows(groups) {
    return (groups || []).map(({ title, files }) => {
        if (!files || !files.length) return null;
        const row = document.createElement('div');
        row.className = 'files-row';
        if (title) {
            const head = document.createElement('div');
            head.className = 'files-title';
            head.textContent = title;
            row.appendChild(head);
        }
        files.filter(f => f.audio).forEach(f => {
            const player = document.createElement('audio');
            player.className = 'files-audio';
            player.controls = true;
            player.preload = 'none';          // nothing is fetched until play is pressed
            player.src = f.url;
            row.appendChild(player);
        });
        const chips = document.createElement('div');
        chips.className = 'files-chips';
        files.forEach(f => chips.appendChild(chip(f)));
        row.appendChild(chips);
        return row;
    }).filter(Boolean);
}
