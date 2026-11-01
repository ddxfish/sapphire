// shared/chat-tools-modal.js - the trim and repair dialogs. Chat Manager opens them
// from a row, /trim and /repair from the composer (handlers/commands.js).
import * as api from '../api.js';
import { showToast } from './notify.js';
import { escapeHtml as esc } from './modal.js';
import { dispatch } from '../core/event-bus.js';
import { openOverlay, readChatRow, vaultOpenFor } from './compress-modal.js';

export async function openTrimModal(name, chat = null, onDone = null) {
    chat = chat || await readChatRow(name);
    if (!chat) return;
    const { overlay, close: closeModal } = openOverlay(`
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
        if (!await vaultOpenFor(chat, 'trim')) { closeModal(); return; }   // U4 second gate
        const p = await preview();          // fresh numbers, never stale
        if (!p) return;
        if (p.no_op) { showToast('Nothing to trim', 'warning'); return; }
        if (!confirm(`Delete ${p.deleted_messages} messages from "${name}"?`)) return;
        try {
            const r = await api.trimChat(name, { ...vals(), preview: false });
            showToast(`Trimmed ${name}: ${r.deleted_messages} messages removed`, 'success');
            closeModal();
            // Local dispatch too — the open-chat transcript must refresh even
            // if the SSE echo of this event is missed (reconnect gap etc.)
            dispatch('chat_trimmed', { chat_name: name });
            onDone?.();
        } catch (e) {
            showToast(`Trim failed: ${e.message}`, 'error');
        }
    });
}

export async function openRepairModal(name, chat = null, onDone = null) {
    chat = chat || await readChatRow(name);
    if (!chat) return;
    const { overlay, close: closeModal } = openOverlay(`
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
            showToast(r.no_op ? `${name}: nothing to repair`
                : `Repaired ${name}: ${r.quarantined} quarantined, ${r.kept} kept`, 'success');
            closeModal();
            dispatch('chat_repaired', { chat_name: name });
            onDone?.();
        } catch (e) {
            showToast(`Repair failed: ${e.message}`, 'error');
        }
    });
}
