// handlers/commands.js - slash commands typed in the chat composer (core/commands.py)
import * as api from '../api.js';
import * as ui from '../ui.js';
import { showModal, showHelpModal, escapeHtml } from '../shared/modal.js';
import { openCompressModal } from '../shared/compress-modal.js';
import { openTrimModal, openRepairModal } from '../shared/chat-tools-modal.js';
import { getIsProc } from '../core/state.js';
import { handleRegen, handleContinue, handleReplay } from './message-handlers.js';

// `/remove 2` is a command; `/home/me/x` has a second slash and is a message.
export const isCommand = (text) => /^\/[a-z][\w-]*(\s|$)/i.test(text);

// The dialogs a command can ask for ({open: name}).
const dialogs = {
    remove: ({ turns }) => showModal('Remove turns', [
        { type: 'html', value: `<p style="margin:0">This chat has ${escapeHtml(String(turns))} turn(s). A turn is one of your messages and every reply to it. Removing them can't be undone.</p>` },
        { id: 'cmd-remove-count', label: 'Newest turns to remove', type: 'number', value: '1' },
    ], (data) => run(`/remove ${String(data['cmd-remove-count'] || '').trim()}`), { saveLabel: 'Remove' }),
    compact: ({ chat }) => openCompressModal(chat),
    trim: ({ chat }) => openTrimModal(chat),
    repair: ({ chat }) => openRepairModal(chat),
    help: ({ commands }) => showHelpModal('Slash commands', commands.map(c =>
        `- /${c.name}${c.args ? ' ' + c.args : ''}: ${c.description}`).join('\n')),
    status: (s) => showHelpModal(`Status: ${s.chat}`, [
        `- Persona: ${s.persona || 'none'}`,
        `- Prompt: ${s.prompt || 'none'}`,
        `- Provider: ${s.provider}${s.model ? ' / ' + s.model : ''}`,
        `- Toolset: ${s.toolset || 'none'}`,
        `- History: ${s.turns} turns, ${s.messages} messages`,
        `- Context: ${s.context.used.toLocaleString()} of ${s.context.limit.toLocaleString()} tokens (${s.context.percent}%)${s.context.measured ? '' : ', stored messages only so far'}`,
        `- Private: ${s.private ? 'yes' : 'no'}`,
        `- Replying now: ${s.streaming ? 'yes' : 'no'}`,
    ].join('\n')),
    prompt: async ({ chat }) => {
        let p;
        try {
            p = await api.chatPromptPreview(chat);
        } catch (e) {
            ui.showToast(e.message || "Couldn't read the prompt", 'error');
            return;
        }
        const block = (title, text) => `<h4 style="margin:10px 0 4px">${title}</h4><pre style="white-space:pre-wrap;margin:0;max-height:40vh;overflow:auto">${escapeHtml(text || '(empty)')}</pre>`;
        showModal(`Prompt: ${chat}`, [{ type: 'html', value:
            block('System prompt', p.system_prompt) + block('Ghost message', p.ghost) + block('Tools', (p.tools || []).join(', ')) }], null);
    },
};

// The newest message's place in the transcript, as the toolbar counts it.
async function lastIndex(role = null) {
    const hist = await api.fetchHistory();
    for (let i = hist.length - 1; i >= 0; i--) {
        if (!role || hist[i].role === role) return i;
    }
    return -1;
}

// What only this page can do: the stream lives here. Each is the toolbar button's own path.
const actions = {
    retry: async () => {
        const i = await lastIndex();
        if (i < 0) return ui.showToast('Nothing to retry yet', 'error');
        handleRegen(i, false);
    },
    continue: async () => {
        const i = await lastIndex();
        if (i < 0) return ui.showToast('Nothing to continue yet', 'error');
        handleContinue(i, false);
    },
    replay: async () => {
        const i = await lastIndex('assistant');
        if (i < 0) return ui.showToast('Nothing to replay yet', 'error');
        handleReplay(i);
    },
};

let _running = false;   // a double Enter on /remove 2 must not remove 4

// Run one line. True when it ran, so the composer can let go of the text.
export async function run(line) {
    if (_running) return false;
    _running = true;
    try {
        const out = await api.runCommand(line);
        if (out.do && actions[out.do]) {
            if (getIsProc()) {
                ui.showToast(`/${out.do} has to wait for this reply to finish`, 'error');
                return false;
            }
            await actions[out.do]();
        } else if (out.open && dialogs[out.open]) dialogs[out.open](out);
        else if (out.say) ui.showToast(out.say, out.ok ? 'success' : 'error');
        return !!out.ok;
    } catch (e) {
        ui.showToast(e.message || 'That command failed', 'error');
        return false;
    } finally {
        _running = false;
    }
}
