// setup-steps/consent.js - Welcome, and the two lines to agree to before anything is set up.
// One big button: "I understand" is the agreement AND the way on (2026-11-15,
// tmp/easy-mode-plan.md). The server stamps the time (SETUP_CONSENT); a setup
// run after that skips this step (views/setup.js).
import { showToast } from '../../shared/toast.js';

export default w => ({
    id: 'consent', title: 'Welcome', ownNext: true,

    render(body) {
        body.innerHTML = `
            <p class="setup-lead">Sapphire is an AI assistant that runs here, on your computer. This short setup
            sets up her brain, her voice and her ears, and turns on what you want. Everything here can be changed
            later under Settings.</p>
            <div class="setup-consent">
                <p>Two things to know before we start:</p>
                <ul>
                    <li><b>Any AI</b> connected to this system can make mistakes. It is software, not a person.</li>
                    <li><b>You</b> choose which AI model to use, and how much access to give it to your computer
                        and your accounts.</li>
                </ul>
            </div>
            <div class="setup-understand-wrap">
                <button type="button" class="setup-understand" id="setup-understand">I understand</button>
            </div>`;
        const btn = body.querySelector('#setup-understand');
        btn.addEventListener('click', async () => {
            btn.disabled = true;
            if (!w.state?.consent) {
                try { await w.putState({ consent: true }); }
                catch (e) { showToast(`Couldn't save that: ${e.message}`, 'error'); btn.disabled = false; return; }
            }
            w.next();
        });
    },
});
