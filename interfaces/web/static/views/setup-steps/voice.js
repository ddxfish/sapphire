// setup-steps/voice.js - her voice, her ears and her wake word, in one column.
// The TTS and STT tabs are mounted as they are (cards, fields, test buttons);
// the wake word part is the two fields that matter. A voice package set that
// is not installed gets an Install strip (core/extras.py, under its own row).
import ttsTab from '../settings-tabs/tts.js';
import sttTab from '../settings-tabs/stt.js';
import { ensureExtra } from '../../shared/extras.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

// The wake word fields only; the library (uploads, who listens) stays in Settings.
const wakeMini = {
    id: 'wakeword',
    render(ctx) { return ctx.renderFields(['WAKE_WORD_ENABLED', 'WAKEWORD_MODEL']); },
};

const PARTS = [
    ['tts', ttsTab, 'Her voice', 'How she speaks. Kokoro runs here and downloads its model on first use.', "Kokoro's packages are not installed."],
    ['stt', sttTab, 'Her ears', 'How she hears you. Whisper runs here and downloads its model on first use.', "Whisper's packages are not installed."],
    ['wakeword', wakeMini, 'Wake word', 'Say her name to talk hands-free.', 'The wake word packages are not installed.'],
];

export default w => ({
    id: 'voice', title: 'Voice', tabs: PARTS.map(p => p[1]),

    render(body) {
        const pk = w.state?.packages || {};
        const emb = w.host.createCtx().settings.EMBEDDING_PROVIDER || 'local';
        body.innerHTML = PARTS.map(([key, , head, sub, missing]) => `
            <section class="setup-sub" data-tab="${key}">
                <h3>${esc(head)} <span class="setup-sub-note">${esc(sub)}</span></h3>
                ${pk[key] === false ? `<div class="setup-strip">${esc(missing)}
                    <button class="btn-sm" data-extra="${key}">Install</button></div>` : ''}
                <div class="setup-sub-body"></div>
            </section>`).join('') + `
            <div class="setup-note">Memory model: <b>${esc(emb)}</b>. A local one downloads on first use; the row
            shows below. Change it under Settings › Embedding.</div>`;
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
