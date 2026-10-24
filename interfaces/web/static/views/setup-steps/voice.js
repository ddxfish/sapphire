// setup-steps/voice.js - her voice, her ears and her wake word: three sections.
// Each is the REAL Settings tab (tts, stt, wakeword) mounted through the host;
// under ctx.wizard a tab paints its compact shape: the cards, then one Advanced
// fold with everything else (shared/provider-selector.js renderProviderCompact).
// Silero rides inside the ears' fold. A voice package set that is not installed
// gets an Install strip (core/extras.py, under its own row).
import ttsTab from '../settings-tabs/tts.js';
import sttTab from '../settings-tabs/stt.js';
import wakewordTab from '../settings-tabs/wakeword.js';
import { ensureExtra } from '../../shared/extras.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

const PARTS = [
    ['tts', ttsTab, 'Her voice', 'How she speaks.', "Kokoro's packages are not installed."],
    ['stt', sttTab, 'Her ears', 'How she hears you.', "Whisper's packages are not installed."],
    ['wakeword', wakewordTab, 'Wake word', 'Say it to talk hands-free.', 'The wake word packages are not installed.'],
];

export default w => ({
    id: 'voice', title: 'Voice', tabs: PARTS.map(p => p[1]),

    render(body) {
        const pk = w.state?.packages || {};
        body.innerHTML = PARTS.map(([key, , head, sub, missing]) => `
            <section class="setup-sub" data-tab="${key}">
                <h3>${esc(head)} <span class="setup-sub-note">${esc(sub)}</span></h3>
                ${pk[key] === false ? `<div class="setup-strip">${esc(missing)}
                    <button class="btn-sm" data-extra="${key}">Install</button></div>` : ''}
                <div class="setup-sub-body"></div>
            </section>`).join('');
        for (const [key, tab] of PARTS) w.host.mount(tab, body.querySelector(`[data-tab="${key}"] .setup-sub-body`));
        body.querySelectorAll('[data-extra]').forEach(b => b.addEventListener('click', async () => {
            b.disabled = true;
            try {
                if (await ensureExtra(b.dataset.extra)) { await w.reloadState(); await w.repaint(); }
            } catch (e) { showToast(e.message, 'error'); }
            finally { b.disabled = false; }
        }));
    },

    leave: () => w.save(),

    unmount(body) {
        for (const [key, tab] of PARTS) {
            const el = body.querySelector(`[data-tab="${key}"] .setup-sub-body`);
            if (el) w.host.unmount(tab, el);
        }
    },
});
