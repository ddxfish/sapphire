// handlers/commands.js - slash commands typed in the chat composer (core/commands.py)
import * as api from '../api.js';
import * as ui from '../ui.js';
import { showModal, escapeHtml } from '../shared/modal.js';
import { openCompressModal } from '../shared/compress-modal.js';

// `/remove 2` is a command; `/home/me/x` has a second slash and is a message.
export const isCommand = (text) => /^\/[a-z][\w-]*(\s|$)/i.test(text);

// The dialogs a command can ask for ({open: name}).
const dialogs = {
    remove: ({ turns }) => showModal('Remove turns', [
        { type: 'html', value: `<p style="margin:0">This chat has ${escapeHtml(String(turns))} turn(s). A turn is one of your messages and every reply to it. Removing them can't be undone.</p>` },
        { id: 'cmd-remove-count', label: 'Newest turns to remove', type: 'number', value: '1' },
    ], (data) => run(`/remove ${String(data['cmd-remove-count'] || '').trim()}`), { saveLabel: 'Remove' }),
    compact: ({ chat }) => openCompressModal(chat),
};

let _running = false;   // a double Enter on /remove 2 must not remove 4

// Run one line. True when it ran, so the composer can let go of the text.
export async function run(line) {
    if (_running) return false;
    _running = true;
    try {
        const out = await api.runCommand(line);
        if (out.open && dialogs[out.open]) dialogs[out.open](out);
        else if (out.say) ui.showToast(out.say, out.ok ? 'success' : 'error');
        return !!out.ok;
    } catch (e) {
        ui.showToast(e.message || 'That command failed', 'error');
        return false;
    } finally {
        _running = false;
    }
}
