// setup-steps/next.js - the end: confetti, then the doors out. Every door marks
// setup complete first (views/setup.js finish), so the boot gate never shows
// the wizard again. The chat is the prime door; a persona starts with a prompt.
// Plugin wizards (shared/wizards.js) get a box only when there are any.
import * as wizards from '../../shared/wizards.js';
import { burst } from '../../shared/confetti.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

// view = a nav view, tab = a Settings tab (setup.js navigate)
const DOORS = {
    chat: { view: 'chat' }, prompts: { view: 'prompts' },
    store: { view: 'store' }, 'video-guide': { view: 'video-guide' }, help: { view: 'help' },
    plugins: { tab: 'plugins' }, devices: { tab: 'devices' }, llm: { tab: 'llm' },
    scheduled: { view: 'scheduled' }, heartbeat: { view: 'heartbeat' },
};
const LISTS = [
    ['Explore', [['🛍️', 'Store', 'store'], ['🎬', 'Video guide', 'video-guide'], ['📖', 'Help docs', 'help']]],
    ['Setup', [['🧩', 'Plugins', 'plugins'], ['📟', 'Device manager', 'devices'], ['🧠', 'LLM setup', 'llm']]],
    ['Actions', [['⏰', 'Scheduled tasks', 'scheduled'], ['💓', 'Heartbeats', 'heartbeat']]],
];

const item = (emoji, label, attr) =>
    `<li><button type="button" ${attr}><span class="next-emoji">${emoji}</span>${esc(label)}</button></li>`;
const box = (title, items) => `<section class="next-box"><h4>${esc(title)}</h4><ul>${items}</ul></section>`;

export default w => ({
    id: 'next', title: 'What next', optional: true,

    render(body) {
        const others = wizards.list('setup');
        body.innerHTML = `
            <p class="setup-lead">🎉 She is ready. Where to?</p>
            <button type="button" class="next-prime" data-door="chat">
                <span class="next-prime-icon">💬</span>
                <span class="next-prime-text"><b>Chat with her</b>
                    <span>Type or speak. The chat sidebar picks the model, the prompt and the voice per chat.</span></span>
                <span class="btn btn-primary">Start chatting →</span>
            </button>
            <button type="button" class="next-card" data-door="prompts">
                <span class="next-card-icon">🎭</span>
                <span class="next-card-text"><b>Make your own persona</b>
                    <span>A persona starts with a prompt: who she is and how she talks. This opens Prompts;
                    give it a voice and a model under Personas after.</span></span>
            </button>
            <div class="next-cols">
                ${LISTS.map(([t, rows]) => box(t, rows.map(([e, l, d]) => item(e, l, `data-door="${d}"`)).join(''))).join('')}
                ${others.length ? box('More wizards', others.map(x => item('🪄', x.title, `data-wizard="${esc(x.id)}"`)).join('')) : ''}
            </div>
            ${w.needsRestart ? `<div class="setup-note">An audio device changed: that applies after a restart.
                <button type="button" class="btn-sm" id="setup-restart">Restart now</button></div>` : ''}
            <p class="setting-help">Setup stays under Settings › System if you ever want it again.</p>`;
        body.addEventListener('click', e => {
            const b = e.target.closest('[data-door], [data-wizard]');
            if (!b) return;
            if (b.dataset.door) w.finishTo(DOORS[b.dataset.door]);
            else {
                const x = wizards.list().find(z => z.id === b.dataset.wizard);
                if (x) w.finishTo({ run: () => x.open() });
            }
        });
        body.querySelector('#setup-restart')?.addEventListener('click', async () => {
            try { await fetchWithTimeout('/api/system/restart', { method: 'POST' }, 15000); showToast('Restarting…', 'info'); }
            catch (e) { showToast(e.message, 'error'); }
        });
        burst();
    },
});
