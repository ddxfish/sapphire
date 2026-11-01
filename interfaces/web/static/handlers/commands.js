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

const CONTEXT_LABELS = { system: 'System prompt', schemas: 'Tool definitions', you: 'Your messages', her: 'Her replies',
    results: 'Tool results', calls: 'Tool calls', images: 'Images', injected: 'Ghost and documents', thinking: 'Thinking', framing: 'Message framing' };

// One row of /context: label, tokens, and a bar sized against the whole.
const contextBar = (label, n, total, note = '') => `<div style="margin:6px 0">
    <div style="display:flex;justify-content:space-between;gap:12px"><span>${escapeHtml(label)}</span><span>${n.toLocaleString()}${note ? ` <small>${escapeHtml(note)}</small>` : ''}</span></div>
    <div style="height:6px;border-radius:3px;background:var(--bg-secondary,#333)"><div style="height:100%;border-radius:3px;background:var(--accent,#4a9eff);width:${total ? Math.min(100, n * 100 / total) : 0}%"></div></div></div>`;

function contextDialog({ chat, meter, parts, image_count, tools, thinking_stored, pictures_stored }) {
    const total = Object.values(parts).reduce((a, b) => a + b, 0);
    const rows = Object.entries(parts).filter(([, n]) => n).sort((a, b) => b[1] - a[1]).map(([k, n]) =>
        contextBar(CONTEXT_LABELS[k] || k, n, total, k === 'images' ? `${image_count} sent` : ''));
    const toolRows = tools.slice(0, 12).map(t => contextBar(t.name, t.schema + t.calls + t.results, total,
        t.runs ? `${t.runs} run${t.runs === 1 ? '' : 's'}, ${t.results.toLocaleString()} in results` : 'definition only'));
    const notes = [
        meter.measured ? 'From her last send.' : 'No send yet since the app started, so this is the stored chat, without the prompt, tool definitions or images. Send a message to measure it.',
        meter.out_of_view ? `${meter.out_of_view} oldest turn(s) are out of her view.` : '',
        meter.stubbed ? `${meter.stubbed} old tool result(s) were cut to one line.` : '',
        `Thinking stored in this chat: ${thinking_stored.toLocaleString()} tokens. Only what is listed above gets sent.`,
        pictures_stored ? `Pictures saved in tool results: ${pictures_stored}.` : '',
    ].filter(Boolean).map(n => `<p class="cm-hint" style="margin:4px 0">${escapeHtml(n)}</p>`).join('');
    showModal(`Context: ${chat}`, [{ type: 'html', value:
        `<div style="font-weight:600">${meter.used.toLocaleString()} of ${meter.limit.toLocaleString()} tokens (${meter.percent}%)${meter.server ? `, model holds ${meter.server.toLocaleString()}` : ''}</div>`
        + rows.join('') + (toolRows.length ? `<h4 style="margin:14px 0 4px">Biggest tools</h4>${toolRows.join('')}` : '')
        + (tools.length > 12 ? `<p class="cm-hint">and ${tools.length - 12} more</p>` : '') + notes }], null);
}

// The dialogs a command can ask for ({open: name}).
const dialogs = {
    remove: ({ turns }) => showModal('Remove turns', [
        { type: 'html', value: `<p style="margin:0">This chat has ${escapeHtml(String(turns))} turn(s). A turn is one of your messages and every reply to it. Removing them can't be undone.</p>` },
        { id: 'cmd-remove-count', label: 'Newest turns to remove', type: 'number', value: '1' },
    ], (data) => run(`/remove ${String(data['cmd-remove-count'] || '').trim()}`), { saveLabel: 'Remove' }),
    compact: ({ chat }) => openCompressModal(chat),
    context: contextDialog,
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
