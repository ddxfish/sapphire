/**
 * Chat Manager — bulk chat operations (tmp/chat-manager.md v1a).
 *
 * Table of every chat with size-on-disk, message counts, and dates.
 * Checkbox selection + bulk delete / clear / export, per-row rename /
 * export / delete. Backend refusals (live phone call, agents attached)
 * come back per-chat and surface as toasts — one refused chat never
 * blocks the rest.
 */
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as eventBus from '../core/event-bus.js';
import { handleChatChange, populateChatDropdown } from '../features/chat-manager.js';
import { getIsProc } from '../core/state.js';
import { switchView } from '../core/router.js';
import { vaultStatus } from '../shared/vault-api.js';

let container = null;
let chats = [];
let selected = new Set();
let sortKey = 'modified';
let sortDir = -1; // -1 desc, 1 asc
let tab = 'chat';        // 'chat' | 'private' | 'game' | 'librarian' | 'archive'
let vaultState = { exists: false, unlocked: false };
let query = '';          // live name filter (as-you-type)
let deepHits = null;     // Map(name → matching msg count) after Enter, else null
let deepQuery = '';      // the query deepHits answers
let visible = false;     // vault hunt U2: only refresh-on-lock when on screen

/* ── helpers ─────────────────────────────────────────────────────────── */

const esc = s => String(s ?? '').replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* Tab classification. Game Room = mode-tagged chats (game sessions AND story
   playthroughs — room.js stamps both). Librarian = the mint_session_chat
   naming convention: 'librarian' or 'librarian-YYYYMMDD-HHMM[-scope]' — the
   date shape keeps a user chat like "librarian-notes" out of that tab. */
const LIB_RE = /^librarian(-\d{8}-\d{4}(-.*)?)?$/;
// One chat, one tab — and archived trumps everything (Krem's ruling
// 2026-08-05: a real archive tucks away, it doesn't badge). The living
// tabs never show archived chats; the Archive tab shows only them.
// Private ranks under archived (Krem's ruling 2026-08-14: Archive wins,
// the 🗝 badge marks private rows there) — private chats NEED archivability
// or the Private tab recreates the dropdown-clog archive exists to solve.
// While the vault is sealed, private chats aren't in the payload at all
// (server filter), so the Private tab shows only when there's something in it.
const chatTab = c => {
    if (c.archived) return 'archive';
    if (c.private_chat) return 'private';
    const m = c.mode || c.settings?.mode;
    if (m === 'game') return 'game';
    // mode stamp = new plumbing (librarian stamps at claim since 2026-08-05);
    // the name regex stays for the pre-stamp generations.
    if (m === 'librarian' || LIB_RE.test(c.name)) return 'librarian';
    return 'chat';
};
const isStory = c => ((c.settings?.game_id) || '').startsWith('story:');
const TAB_LABELS = { chat: '\u{1F4AC} Chats', private: '\u{1F5DD} Private',
                     game: '\u{1F3B2} Game Room',
                     librarian: '\u{1F4DA} Librarian', archive: '\u{1F4E6} Archive' };

function matchesSearch(c) {
    if (deepHits) return deepHits.has(c.name);
    if (!query) return true;
    const q = query.toLowerCase();
    return c.name.toLowerCase().includes(q)
        || (c.display_name || '').toLowerCase().includes(q);
}

const visibleChats = () => chats.filter(c => chatTab(c) === tab && matchesSearch(c));

function humanSize(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return '—';
    return d.toLocaleDateString(undefined, { year: '2-digit', month: 'short', day: 'numeric' })
        + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function daysOld(iso) {
    if (!iso) return 0;
    const d = new Date(iso);
    return isNaN(d) ? 0 : (Date.now() - d.getTime()) / 86400000;
}

function downloadJSON(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function confirmList(verb, names) {
    const shown = names.slice(0, 15).join('\n  ');
    const more = names.length > 15 ? `\n  ...and ${names.length - 15} more` : '';
    return confirm(`${verb} ${names.length} chat(s)?\n\n  ${shown}${more}`);
}

function reportResults(results, okVerb) {
    const refused = Object.entries(results).filter(([, r]) => !r.ok);
    const okCount = Object.keys(results).length - refused.length;
    if (okCount) ui.showToast(`${okVerb} ${okCount} chat(s)`, 'success');
    for (const [name, r] of refused) {
        ui.showToast(`${name}: ${r.message}`, 'warning', 8000);
    }
}

/* ── data + render ───────────────────────────────────────────────────── */

async function refresh() {
    try {
        // Vault state rides along: the 🗝 toggle is offered only while the
        // vault is open (or absent — the pre-vault v1 flag). vaultStatus()
        // never throws; null = server unreachable → treat as closed-looking
        // (exists+locked) so the toggle is WITHHELD, not offered blind.
        const [data, v] = await Promise.all([api.fetchChatListStats(), vaultStatus()]);
        vaultState = v || { exists: true, unlocked: false };
        // mode 'limbo' = the backrooms (the vault's hidden eviction landing).
        // Invisible even here, by ruling — the one chat the admin surface
        // doesn't list (Krem 2026-08-15: "lame AF for people to see").
        chats = (data.chats || []).filter(c => (c.mode || c.settings?.mode) !== 'limbo');
        // Drop selections for chats that no longer exist
        const names = new Set(chats.map(c => c.name));
        for (const s of [...selected]) if (!names.has(s)) selected.delete(s);
    } catch (e) {
        console.error('[chat-manage] list failed:', e);
        ui.showToast('Failed to load chat list', 'error');
        chats = [];
    }
    render();
}

function sortedChats() {
    const key = sortKey;
    return visibleChats().sort((a, b) => {
        let va = a[key], vb = b[key];
        if (key === 'display_name') { va = (va || '').toLowerCase(); vb = (vb || '').toLowerCase(); }
        if (va == null) va = 0;
        if (vb == null) vb = 0;
        return (va < vb ? -1 : va > vb ? 1 : 0) * sortDir;
    });
}

function render() {
    const list = container.querySelector('#cm-body');
    const arrow = k => sortKey === k ? (sortDir < 0 ? ' ▾' : ' ▴') : '';
    container.querySelectorAll('#cm-table th[data-sort]').forEach(th => {
        th.textContent = th.dataset.label + arrow(th.dataset.sort);
    });

    // Per-tab totals (and match counts while a search is narrowing things)
    const counts = { chat: 0, private: 0, game: 0, librarian: 0, archive: 0 };
    const matched = { chat: 0, private: 0, game: 0, librarian: 0, archive: 0 };
    for (const c of chats) {
        const t = chatTab(c);
        counts[t]++;
        if (matchesSearch(c)) matched[t]++;
    }
    // Sealed vault (or simply no private chats): the Private tab has nothing
    // to say — hide it entirely (ruling C: show NOTHING about locked chats).
    // If it was selected when the vault locked, land back on Chats.
    if (tab === 'private' && !counts.private) tab = 'chat';
    const searching = !!(query || deepHits);
    container.querySelectorAll('.cm-tab').forEach(b => {
        const t = b.dataset.tab;
        b.textContent = `${TAB_LABELS[t]} (${searching ? `${matched[t]}/${counts[t]}` : counts[t]})`;
        b.classList.toggle('active', t === tab);
        if (t === 'private') b.style.display = counts.private ? '' : 'none';
    });

    const vis = sortedChats();
    // Selection is always a subset of what's on screen — one rule that makes
    // tab switches and search narrowing safe (no invisible chats in a bulk op).
    const visNames = new Set(vis.map(c => c.name));
    for (const s of [...selected]) if (!visNames.has(s)) selected.delete(s);

    list.innerHTML = vis.map(c => {
        const checked = selected.has(c.name) ? 'checked' : '';
        // Game/story chats: the transcript is the tale and journal msg_index
        // anchors point into it — trim/compress/clear stay off the menu.
        // Keyed off MODE, not tab: an archived story chat is still a story.
        const gated = (c.mode ?? c.settings?.mode) === 'game';
        const hits = deepHits?.get(c.name);
        // 🗝 toggle: only while the vault is open, or absent entirely (the
        // pre-vault v1 flag). Sealed vault: the server 403s membership
        // changes anyway — no button, no tease. Game/story rows toggle too
        // since v1.3 (journals/saves live on the chat's rows and seal with
        // it — the old F1 refusal is lifted server-side).
        const canVault = !vaultState.exists || vaultState.unlocked;
        return `<tr data-name="${esc(c.name)}" class="${checked ? 'cm-sel' : ''}">
            <td><input type="checkbox" class="cm-check" ${checked}></td>
            <td class="cm-name"><span class="cm-open" title="Open this chat">${esc(c.display_name)}</span>${(c.mode ?? c.settings?.mode) === 'game' ? (isStory(c) ? ' <span class="cm-badge">\u{1F4D6} story</span>' : ' <span class="cm-badge">\u{1F3B2} game</span>') : ''}${c.private_chat ? ` <span class="cm-badge">\u{1F5DD} ${c.vaulted ? 'encrypted' : 'private'}</span>` : ''}${c.degraded ? ' <span class="cm-badge" title="Some messages could not be read — chat is read-only until repaired">⚠ degraded</span>' : ''}${c.is_active ? ' <span class="cm-badge">active</span>' : ''}${hits ? ` <span class="cm-hits">${hits} hit${hits === 1 ? '' : 's'}</span>` : ''}</td>
            <td class="cm-num">${c.message_count}</td>
            <td class="cm-num">${c.turn_count ?? '—'}</td>
            <td class="cm-num">${humanSize(c.size_bytes)}</td>
            <td>${fmtDate(c.modified)}</td>
            <td>${fmtDate(c.created)}</td>
            <td class="cm-actions">
                ${canVault ? `<button class="cm-act" data-act="private" title="${c.private_chat ? 'Make public (local-only + vault rules stop applying)' : 'Make private (local models only; hides when the vault locks)'}">${c.private_chat ? '\u{1F513}' : '\u{1F5DD}'}</button>` : ''}
                <button class="cm-act" data-act="archive" title="${c.archived ? 'Unarchive (back to its tab + sidebar)' : 'Archive (tuck away in the Archive tab)'}">${c.archived ? '\u{1F4C2}' : '\u{1F4E6}'}</button>
                <button class="cm-act" data-act="rename" title="Rename">✏️</button>
                ${c.degraded ? '<button class="cm-act" data-act="repair" title="Repair — quarantine unreadable messages so the chat can save again">🔧</button>' : ''}
                <button class="cm-act" data-act="export" title="Export JSON">⬇️</button>
                ${gated ? '' : `<button class="cm-act" data-act="trim" title="Trim (keep first/last turns)">✂️</button>
                <button class="cm-act" data-act="compress" title="Compress (summarize history)">\u{1F5DC}️</button>`}
                <button class="cm-act" data-act="delete" title="Delete">\u{1F5D1}️</button>
            </td>
        </tr>`;
    }).join('');
    if (!vis.length) {
        list.innerHTML = `<tr><td colspan="8" class="cm-hint" style="text-align:center; padding:24px">${searching ? 'No chats match' : 'Nothing here yet'}</td></tr>`;
    }

    const bar = container.querySelector('#cm-bulkbar');
    const n = selected.size;
    bar.style.display = n ? '' : 'none';
    if (n) bar.querySelector('#cm-selcount').textContent = `${n} selected`;
    // Clear on a game/story chat leaves a zombie (journal at turn N, empty
    // transcript), and clearing something you shelved to KEEP is nonsense —
    // both tabs lose the button.
    bar.querySelector('#cm-bulk-clear').style.display =
        (tab === 'game' || tab === 'archive') ? 'none' : '';
    container.querySelector('#cm-summary').textContent =
        (deepHits ? `"${deepQuery}" in messages · ` : '')
        + `${vis.length} chat${vis.length === 1 ? '' : 's'} · ${humanSize(vis.reduce((s, c) => s + (c.size_bytes || 0), 0))}`;
}

/* ── actions ─────────────────────────────────────────────────────────── */

async function doBulkDelete() {
    const names = [...selected];
    if (!names.length || !confirmList('Delete', names)) return;
    try {
        const res = await api.bulkDeleteChats(names);
        reportResults(res.results, 'Deleted');
        // One local dispatch repaints this tab's dropdown for the whole
        // batch — the SSE echoes are self-filtered (see rename above).
        eventBus.dispatch('chat_deleted', { names });
    } catch (e) {
        ui.showToast(`Bulk delete failed: ${e.message}`, 'error');
    }
    selected.clear();
    await refresh();
}

async function doBulkClear() {
    const names = [...selected];
    if (!names.length) return;
    if (!confirm(`Clear ALL MESSAGES in ${names.length} chat(s)?\nThe chats survive; their histories are wiped.`)) return;
    try {
        const res = await api.bulkClearChats(names);
        reportResults(res.results, 'Cleared');
    } catch (e) {
        ui.showToast(`Bulk clear failed: ${e.message}`, 'error');
    }
    await refresh();
}

async function doBulkExport() {
    const names = [...selected];
    if (!names.length) return;
    // Vault hunt U3: the zip endpoint silently skips private chats (they
    // export one at a time, per-row) — filter here and SAY so, instead of
    // toasting a count that includes chats that never landed in the zip.
    const privCount = names.filter(n => chats.find(c => c.name === n)?.private_chat).length;
    const pubNames = names.filter(n => !chats.find(c => c.name === n)?.private_chat);
    if (!pubNames.length) {
        ui.showToast('Private chats export one at a time — use each row\'s ⬇️ button', 'warning');
        return;
    }
    try {
        // Zip download — raw fetch (fetchWithTimeout JSON-parses responses).
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        const resp = await fetch('/api/chats/bulk-export-zip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify({ names: pubNames })
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `sapphire_chats_${new Date().toISOString().slice(0, 10)}.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        const skipped = privCount
            ? ` — ${privCount} private skipped (export those per-row)` : '';
        ui.showToast(`Exported ${pubNames.length} chat(s) as zip${skipped}`,
                     privCount ? 'warning' : 'success');
    } catch (e) {
        ui.showToast(`Export failed: ${e.message}`, 'error');
    }
}

// Name click → activate the chat and land in the Chat view. Rides the one
// true switch path (handleChatChange: pending-save flush, activate, repaint,
// chat-activated event) — all of it targets persistent DOM, so it runs fine
// from this view; then we navigate to the freshly painted transcript.
async function openChat(name) {
    if (getIsProc()) {
        ui.showToast('Cannot switch chats while generating', 'error');
        return;
    }
    const sel = document.getElementById('chat-select');
    if (!sel) return;
    if (![...sel.options].some(o => o.value === name)) {
        // Archived chats aren't in the picker's hidden select — seed a
        // temporary option; the post-activate repopulate makes it permanent
        // (the active chat is exempt from the archived filter).
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = name;
        sel.appendChild(opt);
    }
    sel.value = name;
    await handleChatChange();
    await populateChatDropdown();
    switchView('chat');
}

async function doRowAction(act, name) {
    if (act === 'rename') {
        const chat = chats.find(c => c.name === name);
        const newName = prompt(`Rename "${chat?.display_name || name}" to:`, name);
        if (!newName || newName === name) return;
        try {
            const res = await api.renameChat(name, newName);
            ui.showToast(`Renamed to ${res.new}`, 'success');
            // Local dispatch, same reason as archive below: the SSE echo is
            // self-filtered (origin match), so THIS tab's dropdown never
            // hears the rename — other tabs repaint, the initiating tab
            // kept an orphaned select on the old name (Krem, 2026-08-13).
            eventBus.dispatch('chat_renamed', { old: name, new: res.new });
        } catch (e) {
            ui.showToast(`Rename failed: ${e.message}`, 'error');
        }
        await refresh();
    } else if (act === 'export') {
        try {
            const res = await api.exportChatByName(name);
            downloadJSON(res, `${name}_export.json`);
            ui.showToast('Chat exported', 'success');
        } catch (e) {
            ui.showToast(`Export failed: ${e.message}`, 'error');
        }
    } else if (act === 'private') {
        const chat = chats.find(c => c.name === name);
        const to = !chat?.private_chat;
        try {
            await api.updateChatSettings(name, { private_chat: to });
            ui.showToast(to
                ? `${chat?.display_name || name} is now private \u{1F5DD} — local models only, hides on vault lock`
                : `${chat?.display_name || name} is now public`, 'success');
        } catch (e) {
            // Likely 409 (privacy_required prompt — switch prompts first) or
            // 403 (vault sealed). Server message says which; show it whole.
            ui.showToast(e.message, 'error', 8000);
        }
        await refresh();
        await populateChatDropdown();   // private section membership changed
    } else if (act === 'archive') {
        const chat = chats.find(c => c.name === name);
        const toArchived = !chat?.archived;
        try {
            await api.setChatArchived(name, toArchived);
            ui.showToast(toArchived
                ? `Archived ${name} — tucked away in the Archive tab`
                : `Unarchived ${name} — back in its tab`, 'success');
            // Local dispatch: the dropdown refreshes without the SSE echo
            eventBus.dispatch('chat_archived', { chat_name: name, archived: toArchived });
        } catch (e) {
            ui.showToast(`Archive failed: ${e.message}`, 'error');
        }
        await refresh();
    } else if (act === 'trim') {
        openTrimModal(name);
    } else if (act === 'repair') {
        openRepairModal(name);
    } else if (act === 'compress') {
        openCompressModal(name);
    } else if (act === 'delete') {
        if (!confirmList('Delete', [name])) return;
        try {
            await api.deleteChat(name);
            ui.showToast(`Deleted ${name}`, 'success');
            // Local dispatch — SSE echo is self-filtered (see rename above).
            eventBus.dispatch('chat_deleted', { name });
        } catch (e) {
            ui.showToast(`Delete failed: ${e.message}`, 'error');
        }
        selected.delete(name);
        await refresh();
    }
}

/* ── trim + compress modals (v1b) ────────────────────────────────────── */

function openModal(html) {
    const overlay = container.querySelector('#cm-modal');
    overlay.querySelector('#cm-modal-box').innerHTML = html;
    overlay.style.display = '';
    return overlay;
}

function closeModal() {
    const overlay = container.querySelector('#cm-modal');
    overlay.style.display = 'none';
    overlay.querySelector('#cm-modal-box').innerHTML = '';
}

// Vault hunt U4 (the P4 second gate, now shared): the vault can LOCK while
// a dialog sits open (idle-lock, another tab). The server refuses anyway
// (the chat is hidden = nonexistent), but "Chat not found" is a lying error
// for a chat the user is looking at — re-check and say what happened.
// Returns true when it's safe to proceed; closes the modal otherwise.
async function ensureVaultOpenFor(chat, verb) {
    if (!chat?.private_chat) return true;
    try {
        const v = await vaultStatus();   // null (unreachable) → server is the gate
        if (v && v.exists && !v.unlocked) {
            ui.showToast(`The vault locked while this dialog was open — unlock it to ${verb} this chat`, 'error');
            closeModal();
            return false;
        }
    } catch (e) { /* status unknown — let the server be the gate */ }
    return true;
}

function openTrimModal(name) {
    const chat = chats.find(c => c.name === name);
    const overlay = openModal(`
        <h3>✂️ Trim "${esc(chat?.display_name || name)}"</h3>
        <p class="cm-hint">A turn = your message + everything until your next one.
        Tool chains never split. The middle gets deleted.</p>
        <div id="cm-trim-shape" class="cm-hint" style="font-weight:600"></div>
        <div class="cm-form-row">
            Keep first <input type="number" id="cm-trim-first" value="0" min="0" style="width:4.5em">
            and last <input type="number" id="cm-trim-last" value="40" min="1" style="width:4.5em"> turns
        </div>
        <div id="cm-trim-preview" class="cm-hint"></div>
        <div class="cm-modal-btns">
            <button class="cm-btn" id="cm-trim-cancel">Cancel</button>
            <button class="cm-btn cm-danger" id="cm-trim-go">Trim</button>
        </div>`);
    const vals = () => ({
        keep_first_turns: parseInt(overlay.querySelector('#cm-trim-first').value, 10) || 0,
        keep_last_turns: parseInt(overlay.querySelector('#cm-trim-last').value, 10) || 1,
    });
    const preview = async () => {
        const out = overlay.querySelector('#cm-trim-preview');
        if (!out) return null;   // modal closed while a debounced preview was pending
        try {
            const r = await api.trimChat(name, { ...vals(), preview: true });
            overlay.querySelector('#cm-trim-shape').textContent =
                `This chat: ${r.messages_total} messages · ${r.turns_total} turns`;
            const asked = r.kept_first_turns + r.kept_last_turns;
            out.textContent = r.no_op
                ? `Keep ${r.kept_first_turns} + ${r.kept_last_turns} = ${asked} turns, `
                  + `but this chat only has ${r.turns_total} — nothing would be deleted.`
                : `Deletes ${r.deleted_messages} of ${r.messages_total} messages `
                  + `(~${(r.deleted_tokens_est / 1000).toFixed(1)}k tokens). `
                  + `${r.messages_after} messages remain.`;
            return r;
        } catch (e) {
            out.textContent = `Preview failed: ${e.message}`;
            return null;
        }
    };
    // Live preview: numbers on open, fresh numbers as you type
    let previewTimer = null;
    const debouncedPreview = () => {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(preview, 350);
    };
    overlay.querySelector('#cm-trim-first').addEventListener('input', debouncedPreview);
    overlay.querySelector('#cm-trim-last').addEventListener('input', debouncedPreview);
    preview();
    overlay.querySelector('#cm-trim-cancel').addEventListener('click', closeModal);
    overlay.querySelector('#cm-trim-go').addEventListener('click', async () => {
        if (!await ensureVaultOpenFor(chat, 'trim')) return;   // U4 second gate
        const p = await preview();          // fresh numbers, never stale
        if (!p) return;
        if (p.no_op) { ui.showToast('Nothing to trim', 'warning'); return; }
        if (!confirm(`Delete ${p.deleted_messages} messages from "${name}"?`)) return;
        try {
            const r = await api.trimChat(name, { ...vals(), preview: false });
            ui.showToast(`Trimmed ${name}: ${r.deleted_messages} messages removed`, 'success');
            closeModal();
            // Local dispatch too — the open-chat transcript must refresh even
            // if the SSE echo of this event is missed (reconnect gap etc.)
            eventBus.dispatch('chat_trimmed', { chat_name: name });
            await refresh();
        } catch (e) {
            ui.showToast(`Trim failed: ${e.message}`, 'error');
        }
    });
}

function openRepairModal(name) {
    const chat = chats.find(c => c.name === name);
    const overlay = openModal(`
        <h3>🔧 Repair "${esc(chat?.display_name || name)}"</h3>
        <p class="cm-hint">Unreadable messages move to a quarantine (kept verbatim
        — recoverable if a matching vault backup returns), the rest are kept and
        re-numbered, and the chat can save again.</p>
        <div id="cm-repair-preview" class="cm-hint">Diagnosing…</div>
        <div class="cm-modal-btns">
            <button class="cm-btn" id="cm-repair-cancel">Cancel</button>
            <button class="cm-btn cm-danger" id="cm-repair-go" disabled>Repair</button>
        </div>`);
    let plan = null;
    const goBtn = overlay.querySelector('#cm-repair-go');
    const out = overlay.querySelector('#cm-repair-preview');
    const preview = async () => {
        try {
            plan = await api.repairChat(name, { preview: true });
            if (plan.vault_mismatch) {
                out.innerHTML = `⚠ <b>Most of this chat can't be decrypted `
                    + `(${plan.bad.length} of ${plan.total} rows).</b> That usually means `
                    + `the vault changed — <b>restoring the matching vault backup recovers `
                    + `everything</b>; repair would quarantine most of the chat. Only `
                    + `proceed if you're sure there's no matching backup.`;
            } else if (!plan.bad.length && !plan.gap_count) {
                out.textContent = 'Nothing to repair — this chat reads clean now.';
            } else {
                const dec = plan.bad.filter(b => b.reason === 'decrypt').length;
                const par = plan.bad.length - dec;
                out.textContent = `${plan.total} rows: ${plan.ok} healthy · `
                    + `${par} corrupt · ${dec} undecryptable`
                    + (plan.gap_count ? ` · ${plan.gap_count} sequence gap(s)` : '')
                    + `. Repair keeps ${plan.ok} messages and quarantines ${plan.bad.length}.`;
            }
            goBtn.disabled = false;
        } catch (e) {
            out.textContent = `Diagnosis failed: ${e.message}`;
        }
    };
    preview();
    overlay.querySelector('#cm-repair-cancel').addEventListener('click', closeModal);
    goBtn.addEventListener('click', async () => {
        if (!plan) return;
        const force = !!plan.vault_mismatch;
        if (force && !confirm(`Really quarantine ${plan.bad.length} of ${plan.total} `
                + `messages in "${name}"? A matching vault backup would recover them all.`)) return;
        if (!force && plan.bad.length
                && !confirm(`Quarantine ${plan.bad.length} unreadable message(s) from "${name}"?`)) return;
        try {
            const r = await api.repairChat(name, { preview: false, force });
            ui.showToast(r.no_op ? `${name}: nothing to repair`
                : `Repaired ${name}: ${r.quarantined} quarantined, ${r.kept} kept`, 'success');
            closeModal();
            eventBus.dispatch('chat_repaired', { chat_name: name });
            await refresh();
        } catch (e) {
            ui.showToast(`Repair failed: ${e.message}`, 'error');
        }
    });
}

let _llmCache = null;   // {providers, metadata} from /api/llm/providers

async function openCompressModal(name) {
    const chat = chats.find(c => c.name === name);
    let chatLLM = { primary: '', model: '' };
    try {
        if (!_llmCache) {
            _llmCache = await fetch('/api/llm/providers').then(r => r.ok ? r.json() : null);
        }
        const s = await api.getChatSettings(name);
        chatLLM = { primary: s?.settings?.llm_primary || '', model: s?.settings?.llm_model || '' };
    } catch (e) { /* pickers fall back to first enabled provider */ }
    // Private chats only compress through local providers (backend enforces too)
    const provs = (_llmCache?.providers || []).filter(p => p.enabled && (!chat?.private_chat || p.is_local));
    if (!provs.length) {
        ui.showToast(chat?.private_chat
            ? 'This chat is private — enable a local provider to compress it'
            : 'No enabled LLM providers — configure one in Settings first', 'error');
        return;
    }
    const defaultProv = provs.some(p => p.key === chatLLM.primary) ? chatLLM.primary : provs[0].key;

    const overlay = openModal(`
        <h3>\u{1F5DC}️ Compress "${esc(chat?.display_name || name)}"</h3>
        <p class="cm-hint">Older history becomes AI-written summaries; the recent
        tail stays verbatim. Runs in the background — minutes on a local model.</p>
        <div class="cm-hint" style="font-weight:600">This chat: ${chat?.message_count ?? '?'} messages · ${chat?.turn_count ?? '?'} turns</div>
        <div class="cm-form-row">
            <label><input type="radio" name="cm-cmode" value="whole" checked> One summary</label>
            <label><input type="radio" name="cm-cmode" value="chunked"> Timeline chunks (one summary pair per ~6k tokens)</label>
        </div>
        <div class="cm-form-row">
            Model: <select id="cm-c-prov">${provs.map(p =>
                `<option value="${esc(p.key)}"${p.key === defaultProv ? ' selected' : ''}>${esc(p.display_name)}${p.is_local ? ' 🏠' : ' ☁️'}</option>`).join('')}
            </select>
            <select id="cm-c-model"></select>
        </div>
        <div class="cm-form-row">
            Target size: <select id="cm-c-target">
                <option value="2000">~2k tokens</option>
                <option value="5000" selected>~5k tokens</option>
                <option value="10000">~10k tokens</option>
            </select>
            · keep last <input type="number" id="cm-c-keep" value="10" min="1" style="width:4.5em"> turns verbatim
        </div>
        <div class="cm-form-row">
            <label><input type="checkbox" id="cm-c-backup" checked> Back up full JSON to user/history/exports/ first (skipped for private chats)</label>
        </div>
        <div class="cm-modal-btns">
            <button class="cm-btn" id="cm-c-cancel">Cancel</button>
            <button class="cm-btn cm-danger" id="cm-c-go">Compress</button>
        </div>`);

    // Buttons bind BEFORE any data-dependent population — a populate failure
    // must never leave the modal with dead buttons (learned 2026-07-10:
    // model_options is a DICT {id: label}; the old list-shaped .map() threw
    // in fillModels and killed the cancel/go bindings queued after it).
    const modelSel = overlay.querySelector('#cm-c-model');
    overlay.querySelector('#cm-c-cancel').addEventListener('click', closeModal);
    overlay.querySelector('#cm-c-go').addEventListener('click', async () => {
        const opts = {
            mode: overlay.querySelector('input[name="cm-cmode"]:checked').value,
            provider: overlay.querySelector('#cm-c-prov').value,
            model: modelSel.value,
            target_tokens: parseInt(overlay.querySelector('#cm-c-target').value, 10),
            keep_last_turns: parseInt(overlay.querySelector('#cm-c-keep').value, 10) || 10,
            backup: overlay.querySelector('#cm-c-backup').checked,
        };
        // Second gate (P4) — shared helper, see ensureVaultOpenFor.
        if (!await ensureVaultOpenFor(chat, 'compress')) return;
        try {
            await api.compressChat(name, opts);
            ui.showToast(`Compress started on ${name} — running in background`, 'success');
            closeModal();
            startCompressPoll();
        } catch (e) {
            ui.showToast(`Compress failed to start: ${e.message}`, 'error');
        }
    });

    const fillModels = (provKey) => {
        // model_options is a dict {model_id: label} for core providers,
        // null for custom ones (same consumer shape as chat.js sidebar)
        const opts = Object.entries(_llmCache?.metadata?.[provKey]?.model_options || {});
        const prov = provs.find(p => p.key === provKey);
        const def = prov?.model ? ` (${prov.model.split('/').pop()})` : '';
        modelSel.innerHTML = `<option value="">Provider default${def}</option>`
            + opts.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('');
        if (provKey === chatLLM.primary && chatLLM.model
            && opts.some(([id]) => id === chatLLM.model)) modelSel.value = chatLLM.model;
    };
    fillModels(defaultProv);
    overlay.querySelector('#cm-c-prov').addEventListener('change', e => fillModels(e.target.value));
}

let _compressPoll = null;

function startCompressPoll() {
    if (_compressPoll) return;
    _compressPoll = setInterval(async () => {
        let s;
        try { s = await api.compressStatus(); } catch (e) { return; }
        const summary = container?.querySelector('#cm-summary');
        if (s.running) {
            if (summary) summary.textContent =
                `\u{1F5DC}️ compressing ${s.chat}${s.progress ? ` — ${s.progress}` : ''}…`;
            return;
        }
        clearInterval(_compressPoll);
        _compressPoll = null;
        if (!s.done) {
            // Not running AND not done while we were watching a live job:
            // the job state reset — almost always a server restart mid-job.
            // Write-last means the chat is untouched; say so instead of
            // silently never delivering the toast the user is waiting on.
            ui.showToast('Compress job vanished — the server likely restarted mid-job. '
                + 'The chat is untouched; re-run the compress.', 'error', 10000);
            return;
        }
        if (s.ok) {
            const r = s.result || {};
            ui.showToast(`Compressed ${r.chat}: ${r.messages_before} → ${r.messages_after} messages `
                + `(${r.summary_pairs} summary pair${r.summary_pairs === 1 ? '' : 's'})`, 'success', 8000);
            // Local dispatch — refresh the open-chat transcript without
            // depending on the SSE echo arriving
            eventBus.dispatch('chat_compressed', { chat_name: s.chat });
        } else {
            ui.showToast(`Compress failed: ${s.error}`, 'error', 10000);
        }
        refresh();
    }, 2500);
}

async function resumeCompressPollIfRunning() {
    try {
        const s = await api.compressStatus();
        if (s.running) startCompressPoll();
    } catch (e) { /* status is best-effort */ }
}

/* ── view module ─────────────────────────────────────────────────────── */

export default {
    init(el) {
        container = el;
        el.innerHTML = `
        <style>
            /* .view is a flex column with min-height:0 — the wrap must claim
               flex:1 + overflow-y:auto to become the scroll region, or long
               lists fall below the fold unreachably (Krem, 2026-07-10). */
            #cm-wrap { padding: 20px; max-width: 1100px; margin: 0 auto;
                       flex: 1; min-height: 0; overflow-y: auto; width: 100%;
                       box-sizing: border-box; }
            #cm-tabs { display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
                       margin-bottom: 14px; border-bottom: 1px solid var(--border-color, #333);
                       padding-bottom: 0; }
            .cm-tab { padding: 7px 14px; border: 1px solid transparent; border-bottom: none;
                      border-radius: 8px 8px 0 0; background: transparent; cursor: pointer;
                      color: var(--text-muted, #999); font-size: var(--font-sm); }
            .cm-tab:hover { color: var(--text-primary, #ddd); }
            .cm-tab.active { color: var(--accent, #4a9eff); border-color: var(--border-color, #333);
                             background: rgba(74,158,255,0.08); }
            #cm-searchrow { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
            #cm-search { background: var(--bg-secondary, #26262e); color: var(--text-primary, #ddd);
                         border: 1px solid var(--border-color, #444); border-radius: 6px;
                         padding: 6px 10px; flex: 1; min-width: 0; }
            .cm-hits { font-size: 0.78em; color: var(--accent, #4a9eff); font-weight: 600; }
            .cm-open { cursor: pointer; }
            .cm-open:hover { color: var(--accent, #4a9eff); text-decoration: underline; }
            #cm-toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
            #cm-toolbar .cm-spacer { flex: 1; }
            #cm-summary { color: var(--text-muted); font-size: var(--font-sm); }
            #cm-bulkbar { display: flex; gap: 8px; align-items: center; padding: 8px 12px;
                          background: var(--bg-secondary, rgba(255,255,255,0.05));
                          border-radius: 8px; margin-bottom: 10px; }
            #cm-table { width: 100%; border-collapse: collapse; font-size: var(--font-sm); }
            #cm-table th { text-align: left; padding: 8px 10px; cursor: pointer; user-select: none;
                           color: var(--text-muted); border-bottom: 1px solid var(--border-color, #333); }
            #cm-table td { padding: 7px 10px; border-bottom: 1px solid var(--border-color, #222); }
            #cm-table tr.cm-sel { background: rgba(74,158,255,0.08); }
            .cm-num { text-align: right; font-variant-numeric: tabular-nums; }
            .cm-badge { font-size: 0.72em; padding: 1px 7px; border-radius: 9px;
                        background: var(--accent, #4a9eff); color: #fff; vertical-align: 1px; }
            .cm-act { background: none; border: none; cursor: pointer; font-size: 1em;
                      opacity: 0.55; padding: 2px 4px; }
            .cm-act:hover { opacity: 1; }
            .cm-actions { white-space: nowrap; text-align: right; }
            .cm-btn { padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border-color, #444);
                      background: transparent; color: var(--text-primary, #ddd); cursor: pointer; }
            .cm-btn:hover { border-color: var(--accent, #4a9eff); }
            .cm-btn.cm-danger:hover { border-color: #e5534b; color: #e5534b; }
            #cm-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.55);
                        display: flex; align-items: center; justify-content: center; z-index: 1000; }
            #cm-modal-box { background: var(--bg-primary, #1c1c22); border: 1px solid var(--border-color, #444);
                            border-radius: 10px; padding: 18px 22px; max-width: 520px; width: 92%;
                            max-height: 85vh; overflow-y: auto; }
            #cm-modal-box h3 { margin: 0 0 8px; }
            .cm-hint { color: var(--text-muted); font-size: var(--font-sm); margin: 6px 0; min-height: 1em; }
            .cm-form-row { margin: 10px 0; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
            .cm-form-row input[type="number"], .cm-form-row select {
                background: var(--bg-secondary, #26262e); color: var(--text-primary, #ddd);
                border: 1px solid var(--border-color, #444); border-radius: 5px; padding: 3px 6px; }
            .cm-modal-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
        </style>
        <div id="cm-wrap">
            <div id="cm-tabs">
                <button class="cm-tab" data-tab="chat"></button>
                <button class="cm-tab" data-tab="private"></button>
                <button class="cm-tab" data-tab="game"></button>
                <button class="cm-tab" data-tab="librarian"></button>
                <button class="cm-tab" data-tab="archive"></button>
            </div>
            <div id="cm-searchrow">
                <input id="cm-search" type="search" placeholder="Filter by name…">
                <button class="cm-btn" id="cm-deep">\u{1F50D} Search all messages</button>
            </div>
            <div id="cm-toolbar">
                <button class="cm-btn" id="cm-sel-all">All</button>
                <button class="cm-btn" id="cm-sel-none">None</button>
                <select class="cm-btn" id="cm-sel-older">
                    <option value="">Older than…</option>
                    <option value="30">30 days</option>
                    <option value="60">60 days</option>
                    <option value="90">90 days</option>
                </select>
                <span class="cm-spacer"></span>
                <span id="cm-summary"></span>
            </div>
            <div id="cm-bulkbar" style="display:none">
                <span id="cm-selcount"></span>
                <span class="cm-spacer" style="flex:1"></span>
                <button class="cm-btn" id="cm-bulk-export">⬇️ Export</button>
                <button class="cm-btn cm-danger" id="cm-bulk-clear">\u{1F9F9} Clear</button>
                <button class="cm-btn cm-danger" id="cm-bulk-delete">\u{1F5D1}️ Delete</button>
            </div>
            <table id="cm-table">
                <thead><tr>
                    <th style="width:28px"></th>
                    <th data-sort="display_name" data-label="Name">Name</th>
                    <th data-sort="message_count" data-label="Msgs" class="cm-num" title="Stored messages — yours, hers, and every tool result. What Size tracks.">Msgs</th>
                    <th data-sort="turn_count" data-label="Turns" class="cm-num" title="Your message + her full reply (tool work included). The unit Trim and Compress use.">Turns</th>
                    <th data-sort="size_bytes" data-label="Size" class="cm-num">Size</th>
                    <th data-sort="modified" data-label="Last active">Last active</th>
                    <th data-sort="created" data-label="Created">Created</th>
                    <th></th>
                </tr></thead>
                <tbody id="cm-body"></tbody>
            </table>
        </div>
        <div id="cm-modal" style="display:none"><div id="cm-modal-box"></div></div>`;

        // Backdrop click closes the modal (box clicks don't bubble past it)
        el.querySelector('#cm-modal').addEventListener('click', e => {
            if (e.target.id === 'cm-modal') closeModal();
        });

        // Vault hunt U2: lock/unlock changes WHICH CHATS EXIST — without
        // ears here, private names lingered on this table indefinitely
        // after an idle-lock (the walk-up exposure the vault exists to
        // prevent). Same PROMPT_CHANGED/action ride main.js uses.
        eventBus.on(eventBus.Events.PROMPT_CHANGED, (data) => {
            if (data?.action === 'vault_changed' && visible) refresh();
        });

        // ALL handlers bound ONCE here via delegation (never per-render —
        // the stacked-handler class bug).
        el.querySelector('#cm-tabs').addEventListener('click', e => {
            const b = e.target.closest('.cm-tab');
            if (!b || b.dataset.tab === tab) return;
            tab = b.dataset.tab;
            render();   // the subset-of-visible rule empties the selection
        });
        const searchBox = el.querySelector('#cm-search');
        const runDeepSearch = async () => {
            const q = searchBox.value.trim();
            if (!q) { ui.showToast('Type something to search for first', 'warning'); return; }
            try {
                const res = await api.searchChats(q);
                deepHits = new Map(Object.entries(res.hits || {}));
                deepQuery = q;
                ui.showToast(`"${q}": ${res.chats} chat(s), ${res.messages} message(s)`,
                    res.chats ? 'success' : 'warning');
            } catch (err) {
                ui.showToast(`Search failed: ${err.message}`, 'error');
                return;
            }
            render();
        };
        searchBox.addEventListener('input', () => {
            query = searchBox.value;
            deepHits = null; deepQuery = '';   // typing reverts to name mode
            render();
        });
        searchBox.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                searchBox.value = ''; query = ''; deepHits = null; deepQuery = '';
                render();
            } else if (e.key === 'Enter') {
                runDeepSearch();
            }
        });
        el.querySelector('#cm-deep').addEventListener('click', runDeepSearch);
        el.querySelector('#cm-sel-all').addEventListener('click', () => {
            visibleChats().forEach(c => selected.add(c.name));
            render();
        });
        el.querySelector('#cm-sel-none').addEventListener('click', () => {
            selected.clear();
            render();
        });
        el.querySelector('#cm-sel-older').addEventListener('change', e => {
            const days = parseInt(e.target.value, 10);
            if (days) {
                visibleChats().forEach(c => { if (daysOld(c.modified) > days) selected.add(c.name); });
                render();
            }
            e.target.value = '';
        });
        el.querySelector('#cm-bulk-delete').addEventListener('click', doBulkDelete);
        el.querySelector('#cm-bulk-clear').addEventListener('click', doBulkClear);
        el.querySelector('#cm-bulk-export').addEventListener('click', doBulkExport);

        el.querySelector('#cm-table thead').addEventListener('click', e => {
            const th = e.target.closest('th[data-sort]');
            if (!th) return;
            const key = th.dataset.sort;
            if (sortKey === key) sortDir = -sortDir;
            else { sortKey = key; sortDir = key === 'display_name' ? 1 : -1; }
            render();
        });

        el.querySelector('#cm-body').addEventListener('click', e => {
            const row = e.target.closest('tr[data-name]');
            if (!row) return;
            const name = row.dataset.name;
            const actBtn = e.target.closest('.cm-act');
            if (actBtn) {
                doRowAction(actBtn.dataset.act, name);
                return;
            }
            if (e.target.closest('.cm-open')) {
                openChat(name);
                return;
            }
            // Row / checkbox click toggles selection
            if (selected.has(name)) selected.delete(name);
            else selected.add(name);
            render();
        });
    },

    show() {
        visible = true;
        refresh();
        // A compress started last visit may still be running — reattach
        resumeCompressPollIfRunning();
    },

    hide() { visible = false; }
};
