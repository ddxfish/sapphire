// shared/compress-modal.js - THE compress dialog and its job watcher. Chat Manager
// opens it from a row, /compact from the composer (handlers/commands.js), so it
// owns its overlay and reads its own data: the chat page has no Chat Manager loaded.
import * as api from '../api.js';
import { showToast } from './notify.js';
import { setupModalClose, escapeHtml as esc } from './modal.js';
import { vaultStatus } from './vault-api.js';
import { listPersonas } from './persona-api.js';
import { dispatch } from '../core/event-bus.js';

// Vault hunt U4 (the P4 second gate, shared): the vault can LOCK while a dialog
// sits open (idle-lock, another tab). The server refuses anyway (the chat is
// hidden = nonexistent), but "Chat not found" is a lying error for a chat the
// user is looking at - re-check and say what happened. False = don't proceed.
export async function vaultOpenFor(chat, verb) {
    if (!chat?.private_chat) return true;
    try {
        const v = await vaultStatus();   // null (unreachable) → server is the gate
        if (v && v.exists && !v.unlocked) {
            showToast(`The vault locked while this dialog was open — unlock it to ${verb} this chat`, 'error');
            return false;
        }
    } catch (e) { /* status unknown — let the server be the gate */ }
    return true;
}

// chat: the caller's row from /api/chats?stats=1, else read here. No row means no
// dialog: the private-chat provider filter below can't run blind.
export async function openCompressModal(name, chat = null) {
    if (!chat) {
        try {
            chat = (await api.fetchChatListStats()).chats?.find(c => c.name === name);
        } catch (e) { /* the refusal below says it */ }
        if (!chat) {
            showToast(`Couldn't read chat "${name}"`, 'error');
            return;
        }
    }
    let llm = null;     // {providers, metadata} from /api/llm/providers
    let chatLLM = { primary: '', model: '' };
    let chatPersona = '';
    try {
        llm = await fetch('/api/llm/providers').then(r => r.ok ? r.json() : null);
        const s = await api.getChatSettings(name);
        chatLLM = { primary: s?.settings?.llm_primary || '', model: s?.settings?.llm_model || '' };
        chatPersona = s?.settings?.persona || '';
    } catch (e) { /* pickers fall back to first enabled provider */ }
    // A room has no one voice: the server writes its summaries in third person
    // from fixed instructions, so neither control applies. Either fetch failing
    // only hides its control; the server falls back to the chat's persona and
    // the saved instructions.
    const isRoom = (chat.mode ?? chat.settings?.mode) === 'room';
    const personas = isRoom ? null : (await listPersonas().catch(() => null))?.personas || null;
    const instr = isRoom ? null : await api.getCompressInstructions().catch(() => null);
    const defaultPersona = personas?.some(p => p.name === chatPersona) ? chatPersona : '';
    // Private chats only compress through local providers (backend enforces too)
    const provs = (llm?.providers || []).filter(p => p.enabled && (!chat.private_chat || p.is_local));
    if (!provs.length) {
        showToast(chat.private_chat
            ? 'This chat is private — enable a local provider to compress it'
            : 'No enabled LLM providers — configure one in Settings first', 'error');
        return;
    }
    const defaultProv = provs.some(p => p.key === chatLLM.primary) ? chatLLM.primary : provs[0].key;

    document.querySelector('.cm-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'cm-overlay';
    overlay.innerHTML = `<div class="cm-overlay-box">
        <h3>\u{1F5DC}️ Compress "${esc(chat.display_name || name)}"</h3>
        <p class="cm-hint">Older history becomes AI-written summaries; the recent
        tail stays verbatim. Runs in the background — minutes on a local model.</p>
        <div class="cm-hint" style="font-weight:600">This chat: ${chat.message_count ?? '?'} messages · ${chat.turn_count ?? '?'} turns</div>
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
        ${personas ? `<div class="cm-form-row">
            Speaks as: <select id="cm-c-persona">
                <option value="">No persona</option>
                ${personas.map(p => `<option value="${esc(p.name)}"${p.name === defaultPersona ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}
            </select>
        </div>` : ''}
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
        ${instr ? `<details class="cm-advanced">
            <summary>Advanced</summary>
            <p class="cm-hint">What the summary keeps and how it's written. Saved for every
            compress when you press Compress; Cancel discards your edit. The size limit and
            the chat text are added automatically.</p>
            <textarea id="cm-c-instructions" rows="8" maxlength="8000">${esc(instr.instructions)}</textarea>
            <button class="cm-btn" id="cm-c-reset" type="button">Reset to default</button>
        </details>` : ''}
        <div class="cm-modal-btns">
            <button class="cm-btn" id="cm-c-cancel">Cancel</button>
            <button class="cm-btn cm-danger" id="cm-c-go">Compress</button>
        </div></div>`;
    document.body.appendChild(overlay);
    let dropEsc = () => {};
    const close = () => { dropEsc(); overlay.remove(); };
    dropEsc = setupModalClose(overlay, close);

    // Buttons bind BEFORE any data-dependent population — a populate failure
    // must never leave the modal with dead buttons (learned 2026-07-10:
    // model_options is a DICT {id: label}; the old list-shaped .map() threw
    // in fillModels and killed the cancel/go bindings queued after it).
    const modelSel = overlay.querySelector('#cm-c-model');
    const instrBox = overlay.querySelector('#cm-c-instructions');
    overlay.querySelector('#cm-c-reset')?.addEventListener('click', () => { instrBox.value = instr.default; });
    overlay.querySelector('#cm-c-cancel').addEventListener('click', close);
    overlay.querySelector('#cm-c-go').addEventListener('click', async () => {
        const personaSel = overlay.querySelector('#cm-c-persona');
        const opts = {
            ...(personaSel ? { persona: personaSel.value } : {}),
            mode: overlay.querySelector('input[name="cm-cmode"]:checked').value,
            provider: overlay.querySelector('#cm-c-prov').value,
            model: modelSel.value,
            target_tokens: parseInt(overlay.querySelector('#cm-c-target').value, 10),
            keep_last_turns: parseInt(overlay.querySelector('#cm-c-keep').value, 10) || 10,
            backup: overlay.querySelector('#cm-c-backup').checked,
        };
        if (!await vaultOpenFor(chat, 'compress')) { close(); return; }
        try {
            if (instrBox && instrBox.value.trim() !== instr.instructions) {
                await api.saveCompressInstructions(instrBox.value);
            }
            await api.compressChat(name, opts);
            showToast(`Compress started on ${name} — running in background`, 'success');
            close();
            startCompressPoll();
        } catch (e) {
            showToast(`Compress failed to start: ${e.message}`, 'error');
        }
    });

    const fillModels = (provKey) => {
        // model_options is a dict {model_id: label} for core providers,
        // null for custom ones (same consumer shape as chat.js sidebar)
        const opts = Object.entries(llm?.metadata?.[provKey]?.model_options || {});
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
let _watcher = null;    // Chat Manager's row summary and list refresh, once it's loaded

// fn(status): every poll while running, once more when the job ends.
export const watchCompress = (fn) => { _watcher = fn; };

function startCompressPoll() {
    if (_compressPoll) return;
    _compressPoll = setInterval(async () => {
        let s;
        try { s = await api.compressStatus(); } catch (e) { return; }
        if (s.running) {
            _watcher?.(s);
            return;
        }
        clearInterval(_compressPoll);
        _compressPoll = null;
        if (!s.done) {
            // Not running AND not done while we were watching a live job:
            // the job state reset — almost always a server restart mid-job.
            // Write-last means the chat is untouched; say so instead of
            // silently never delivering the toast the user is waiting on.
            showToast('Compress job vanished — the server likely restarted mid-job. '
                + 'The chat is untouched; re-run the compress.', 'error', 10000);
            return;
        }
        if (s.ok) {
            const r = s.result || {};
            showToast(`Compressed ${r.chat}: ${r.messages_before} → ${r.messages_after} messages `
                + `(${r.summary_pairs} summary pair${r.summary_pairs === 1 ? '' : 's'})`, 'success', 8000);
            // Local dispatch — refresh the open-chat transcript without
            // depending on the SSE echo arriving
            dispatch('chat_compressed', { chat_name: s.chat });
        } else {
            showToast(`Compress failed: ${s.error}`, 'error', 10000);
        }
        _watcher?.(s);
    }, 2500);
}

export async function resumeCompressPollIfRunning() {
    try {
        const s = await api.compressStatus();
        if (s.running) startCompressPoll();
    } catch (e) { /* status is best-effort */ }
}
