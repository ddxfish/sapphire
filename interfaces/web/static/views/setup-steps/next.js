// setup-steps/next.js - where to go from here, and the other wizards (shared/wizards.js).
import * as wizards from '../../shared/wizards.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

const PLACES = [
    ['#chat', '💬', 'Chat', 'Talk to her. The sidebar picks the model, the prompt and the voice per chat.'],
    ['#prompts', '📝', 'Prompts', 'What she is told about herself, and about you.'],
    ['#toolsets', '🧰', 'Toolsets', 'What she is allowed to do.'],
    ['#store', '🛍️', 'Store', 'More plugins.'],
    ['#help', '📖', 'Help', 'The guide, and the API.'],
];

const card = (icon, title, blurb) => `
    <span class="setup-link-icon">${icon}</span>
    <span class="setup-link-text"><b>${esc(title)}</b><span>${esc(blurb)}</span></span>`;

export default w => ({
    id: 'next', title: 'What next', optional: true,

    render(body) {
        const others = wizards.list('setup');
        body.innerHTML = `
            <p class="setup-lead">She is ready. A few places worth knowing:</p>
            <div class="setup-links">${PLACES.map(([hash, icon, title, blurb]) =>
                `<a class="setup-link" href="${hash}">${card(icon, title, blurb)}</a>`).join('')}</div>
            <h3>More wizards</h3>
            ${others.length
                ? `<div class="setup-links">${others.map(x =>
                    `<button type="button" class="setup-link" data-wizard="${esc(x.id)}">${card('🪄', x.title, x.blurb)}</button>`).join('')}</div>`
                : '<p class="setting-help">None yet. A wizard a plugin adds appears here.</p>'}
            ${w.needsRestart ? `<div class="setup-note">An audio device changed: that applies after a restart.
                <button type="button" class="btn-sm" id="setup-restart">Restart now</button></div>` : ''}
            <p class="setting-help">Finish takes you to the chat. Setup stays under Settings › System if you ever want it again.</p>`;
        body.querySelectorAll('[data-wizard]').forEach(b => b.addEventListener('click', () =>
            wizards.list().find(x => x.id === b.dataset.wizard)?.open()));
        body.querySelector('#setup-restart')?.addEventListener('click', async () => {
            try { await fetchWithTimeout('/api/system/restart', { method: 'POST' }, 15000); showToast('Restarting…', 'info'); }
            catch (e) { showToast(e.message, 'error'); }
        });
    },
});
