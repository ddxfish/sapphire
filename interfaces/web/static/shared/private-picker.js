// shared/private-picker.js — THE private picker (2026-11-07, Teri's lockout).
// One modal, two triggers: the vault opening while you stand in a public chat
// (views/chat.js eyeball), and a send/regen in a public chat while the vault
// is open (handlers/*, plus the server's private_gate event for a stale tab).
// Nothing is decided for the person and nothing auto-sends: every choice
// leaves the composer's text where it is, and a switch carries it along (the
// composer isn't per-chat). The server gate (chat_streaming.private_gate)
// refuses the same turns, so this is the explanation, not the enforcement.
//
// openPrivatePicker({ reason }) resolves 'switched' | 'created' | 'made' | null.
//   reason: 'unlock' | 'send'
import * as api from '../api.js';
import { escapeHtml as esc } from './modal.js';

let _open = null;   // one picker at a time — a second trigger rides the first

// THE client rule (server twin: chat_streaming.private_gate): vault open and
// the chat on screen is public and not a plugin surface. One function, read
// by the status paint (scene.js) and by the eyeball off a FRESH status — a
// cached flag lost the race to updateScene's newest-call-wins guard.
export const gateFrom = (status) => !!status?.vault?.exists && !!status?.vault?.unlocked
    && !status?.chat_settings?.private_chat && !status?.chat_settings?.mode && !window.__managed;

export function openPrivatePicker({ reason = 'send' } = {}) {
    if (_open) return _open;
    _open = new Promise(async (resolve) => {
        const chatSelect = document.getElementById('chat-select');
        const active = chatSelect?.value || '';
        const activeName = chatSelect?.options[chatSelect.selectedIndex]?.text || active;
        let privates = [];
        try {
            const data = await api.fetchChatList();
            privates = (data?.chats || []).filter(c => c.private_chat && !c.archived && !c.mode);
        } catch { /* list unavailable: create / make-private still work */ }

        const lead = reason === 'unlock'
            ? 'The vault is open. Pick where to talk privately.'
            : 'Private mode is on, so nothing was sent. Your text is still in the box.';
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        overlay.innerHTML = `
            <div class="modal-base" style="max-width:440px;width:92vw;padding:18px">
                <div style="font-weight:600;margin-bottom:8px">You're in a public chat</div>
                <div style="font-size:var(--font-sm);margin-bottom:12px;line-height:1.5">${esc(lead)}</div>
                ${privates.length ? `
                <div style="font-size:var(--font-xs);color:var(--text-dim);margin-bottom:6px">Go to a private chat</div>
                <div style="display:flex;flex-direction:column;gap:6px;max-height:180px;overflow:auto;margin-bottom:12px">
                    ${privates.map(c => `<button type="button" class="btn-sm pp-go" data-name="${esc(c.name)}"
                        style="text-align:left">🔒 ${esc(c.display_name || c.name)}</button>`).join('')}
                </div>` : ''}
                <form id="pp-new" action="#" style="display:flex;gap:6px;margin-bottom:8px">
                    <input type="text" id="pp-name" placeholder="New private chat name" maxlength="80"
                        style="flex:1;padding:6px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:6px;color:var(--text-bright)">
                    <button type="submit" class="btn-sm">Create</button>
                </form>
                <button type="button" class="btn-sm" id="pp-make" style="width:100%;margin-bottom:8px">Make “${esc(activeName)}” private</button>
                <div id="pp-error" style="color:#ef4444;font-size:var(--font-xs);min-height:16px;margin-bottom:8px"></div>
                <div class="modal-actions" style="display:flex;justify-content:flex-end">
                    <button type="button" class="btn-sm" id="pp-cancel">Cancel</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const errEl = overlay.querySelector('#pp-error');
        const done = (val) => {
            document.removeEventListener('keydown', onKey);
            overlay.remove();
            _open = null;
            document.getElementById('prompt-input')?.focus();
            resolve(val);
        };
        const onKey = (e) => { if (e.key === 'Escape') done(null); };
        document.addEventListener('keydown', onKey);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) done(null); });
        overlay.querySelector('#pp-cancel').onclick = () => done(null);

        const { populateChatDropdown, handleChatChange } = await import('../features/chat-manager.js');
        const { updateScene } = await import('../features/scene.js');
        const switchTo = async (name) => {
            await populateChatDropdown();
            if (chatSelect) chatSelect.value = name;
            await handleChatChange();
        };
        const busy = (on) => overlay.querySelectorAll('button,input').forEach(el => { el.disabled = on; });
        const run = async (fn, result) => {
            errEl.textContent = '';
            busy(true);
            try { await fn(); done(result); }
            catch (e) { errEl.textContent = e?.message || 'That didn\'t work'; busy(false); }
        };

        overlay.querySelectorAll('.pp-go').forEach(btn => {
            btn.onclick = () => run(() => switchTo(btn.dataset.name), 'switched');
        });
        overlay.querySelector('#pp-new').onsubmit = (e) => {
            e.preventDefault();
            const name = overlay.querySelector('#pp-name').value.trim();
            if (!name) { errEl.textContent = 'Name the new chat first.'; return; }
            run(async () => {
                // Born public (the create route refuses private_chat), flipped
                // private BY NAME before it's ever active, then switched to.
                const created = await api.createChat(name);
                const n = created?.name || name;
                try {
                    await api.updateChatSettings(n, { private_chat: true });
                } catch (err) {
                    try { await api.deleteChat(n); } catch {}
                    throw err;
                }
                await switchTo(n);
            }, 'created');
        };
        overlay.querySelector('#pp-make').onclick = () => run(async () => {
            await api.updateChatSettings(active, { private_chat: true });
            await populateChatDropdown();
            await updateScene();
        }, 'made');
        overlay.querySelector(privates.length ? '.pp-go' : '#pp-name')?.focus();
    });
    return _open;
}
