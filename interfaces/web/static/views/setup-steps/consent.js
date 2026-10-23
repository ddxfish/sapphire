// setup-steps/consent.js - Welcome, and the two lines to agree to before anything is set up.
// The server stamps the time (SETUP_CONSENT); the box reads checked once it has.
import { showToast } from '../../shared/toast.js';
import { escapeHtml as esc } from '../../shared/modal.js';

export default w => ({
    id: 'consent', title: 'Welcome',

    render(body) {
        const when = w.state?.consent;
        let agreed = '';
        if (when) { try { agreed = new Date(when).toLocaleString(); } catch { agreed = when; } }
        body.innerHTML = `
            <p class="setup-lead">Sapphire is an AI assistant that runs here, on your computer. This short setup
            picks her brain, her voice and her ears, and turns on what you want. Everything here can be changed
            later under Settings.</p>
            <div class="setup-consent">
                <p>Two things to know before we start:</p>
                <ul>
                    <li><b>Any AI</b> connected to this system can make mistakes. It is software, not a person.</li>
                    <li><b>You</b> choose which AI model to use, and how much access to give it to your computer
                        and your accounts.</li>
                </ul>
                <label class="setup-check">
                    <input type="checkbox" id="setup-consent-box" ${when ? 'checked' : ''}>
                    <span>I understand</span>
                </label>
                ${agreed ? `<div class="setting-help">Agreed ${esc(agreed)}</div>` : ''}
            </div>`;
    },

    async leave(body, dir) {
        if (dir < 0) return;
        if (!body.querySelector('#setup-consent-box')?.checked) {
            showToast('Tick "I understand" to continue', 'warning');
            return false;
        }
        if (!w.state?.consent) await w.putState({ consent: true });
    },
});
