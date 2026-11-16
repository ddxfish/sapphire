// setup-steps/mode.js - what kind of setup (tmp/easy-mode-plan.md, 2026-11-15).
// A card is the choice AND the way on: the click saves the kind (SETUP_MODE),
// and the wizard's steps follow it (core/routes/setup.py MODES). Offered on
// every run. A later kind is one more card here and one more row there.
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

const KINDS = [
    ['easy', '✨', 'Easy', 'One page',
     'Sapphire looks at this computer and picks her voice, her ears and her wake word. You see what she picked and can change any of it.'],
    ['advanced', '🛠️', 'Advanced', 'Step by step',
     'Every choice is yours: her brain, voice, ears, audio devices, plugins and her look.'],
];

export default w => ({
    id: 'mode', title: 'What kind of setup?', ownNext: true,

    render(body) {
        const last = w.state?.complete ? w.state.mode || '' : '';      // "last time" is a finished setup's
        const offered = w.state?.modes || KINDS.map(k => k[0]);
        body.innerHTML = `<div class="mode-cards">${KINDS.filter(k => offered.includes(k[0])).map(([id, icon, name, size, blurb]) => `
            <button type="button" class="mode-card" data-mode="${id}">
                <span class="mode-card-icon">${icon}</span>
                <span class="mode-card-name">${esc(name)}${id === last ? '<span class="feat-tag">last time</span>' : ''}</span>
                <span class="mode-card-size">${esc(size)}</span>
                <span class="mode-card-blurb">${esc(blurb)}</span>
            </button>`).join('')}</div>`;
        body.querySelector('.mode-cards').addEventListener('click', async e => {
            const card = e.target.closest('.mode-card');
            if (!card || card.disabled) return;
            body.querySelectorAll('.mode-card').forEach(b => { b.disabled = true; });
            try { await w.setMode(card.dataset.mode); }
            catch (err) {
                showToast(`Couldn't save that: ${err.message}`, 'error');
                body.querySelectorAll('.mode-card').forEach(b => { b.disabled = false; });
            }
        });
    },
});
