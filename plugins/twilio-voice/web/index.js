// Twilio Voice settings — multi-number account manager (A4).
// Uses the shared account-manager shell; accounts live in
// credentials_manager.twilio_accounts via /api/plugin/twilio-voice/accounts.
// Adding a number here does NOT make her answer it — answering is gated by an
// enabled rule in Triggers > Realtime that selects the number.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { createAccountManager } from '/static/shared/account-manager.js';

const API = '/api/plugin/twilio-voice/accounts';
const SETTINGS_API = '/api/webui/plugins/twilio-voice/settings';

let _providers = null;          // [{key, display_name, enabled}] cache
async function fetchProviders() {
    if (_providers) return _providers;
    try {
        const r = await fetch('/api/llm/providers');
        const d = r.ok ? await r.json() : {};
        _providers = (d.providers || []).filter(p => p.enabled);
    } catch { _providers = []; }
    return _providers;
}

const manager = createAccountManager({
    prefix: 'twv',
    entityName: 'Number',
    listEndpoint: API,
    listKey: 'accounts',
    deleteEndpoint: (scope) => `${API}/${encodeURIComponent(scope)}`,
    formatItem: (item) => ({
        name: item.number || item.scope,
        detail: item.configured
            ? `${item.sip_user}@${item.sip_domain}`
            : 'not configured',
    }),
    hint: 'Each entry is one Twilio phone number (SIP domain credential set). '
        + 'Sapphire only answers a number while an enabled Triggers > Realtime '
        + 'rule selects it — adding it here just makes it available.',
    addLabel: '+ Add Number',
    addPrompt: 'Name for this number (e.g. "default", "work"):',
    renderEditor: renderNumberEditor,
    listFooter: renderCallTuning,
});

// ── Call audio tuning (plugin-wide) ─────────────────────────────────────────
// Phone-profile overrides for the conversation engine, applied to every call.
// 0/blank = inherit the matching Settings > Conversation key.
const TUNING_FIELDS = [
    ['vad_threshold', 'VAD speech threshold', '0.6 rejects most line noise. 0 = inherit global.', '0.05'],
    ['endpoint_silence_ms', 'End-of-speech silence (ms)', 'Pause before she decides the caller is done. 0 = inherit (700).', '50'],
    ['min_speech_ms', 'Minimum utterance (ms)', 'Shorter speech is discarded as a blip. 0 = inherit (200).', '50'],
    ['barge_hold_ms', 'Barge-in hold (ms)', 'Sustained caller speech before it cuts her off. 200 keeps line noise from clipping her. 0 = inherit (90).', '10'],
    ['max_utterance_ms', 'Utterance hard cap (ms)', 'Backstop when noise pins the VAD open. 0 = inherit (30000). Try 15000-20000 on noisy lines.', '1000'],
];

async function renderCallTuning(footer, { csrfHeaders }) {
    let s = {};
    try {
        const r = await fetch(SETTINGS_API);
        s = r.ok ? (await r.json()).settings || {} : {};
    } catch { /* defaults below */ }
    const rows = TUNING_FIELDS.map(([key, label, hint, step]) => `
        <div class="am-group">
            <label for="twvt-${key}">${label}</label>
            <input type="number" id="twvt-${key}" value="${s[key] ?? 0}" min="0" step="${step}">
            <div class="am-hint">${hint}</div>
        </div>`).join('');
    footer.innerHTML = `
        <div style="border-top:1px solid var(--border);padding-top:16px;margin-top:16px">
            <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:4px">Call audio tuning</div>
            <div class="am-hint" style="margin-bottom:12px">Phone lines hear differently than your desk mic — these override Settings > Conversation for calls only (all numbers). Applies from the next call.</div>
            ${rows}
            <button type="button" class="am-action-btn" id="twvt-save">Save Tuning</button>
        </div>`;
    footer.querySelector('#twvt-save').addEventListener('click', async () => {
        const btn = footer.querySelector('#twvt-save');
        const settings = {};
        for (const [key] of TUNING_FIELDS) {
            settings[key] = parseFloat(footer.querySelector(`#twvt-${key}`).value) || 0;
        }
        btn.disabled = true;
        btn.textContent = 'Saving...';
        try {
            const res = await fetch(SETTINGS_API, {
                method: 'PUT',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ settings }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            btn.textContent = 'Saved';
            btn.className = 'am-action-btn success';
        } catch (e) {
            btn.textContent = 'Error';
            btn.className = 'am-action-btn error';
        }
        setTimeout(() => {
            btn.textContent = 'Save Tuning';
            btn.className = 'am-action-btn';
            btn.disabled = false;
        }, 3000);
    });
}


function renderNumberEditor(body, scope, item, helpers) {
    const s = item || {};
    body.innerHTML = `
        <div class="am-group">
            <label for="twv-domain">SIP Domain</label>
            <input type="text" id="twv-domain" value="${s.sip_domain || ''}" placeholder="yourname.sip.twilio.com">
            <div class="am-hint">The Twilio SIP domain this number's credential list belongs to.</div>
        </div>
        <div class="am-group">
            <label for="twv-user">SIP Username</label>
            <input type="text" id="twv-user" value="${s.sip_user || ''}" placeholder="sapphire">
        </div>
        <div class="am-group">
            <label for="twv-pass">SIP Password</label>
            <div class="am-row">
                <input type="password" id="twv-pass" placeholder="${s.configured ? 'Leave blank to keep existing...' : 'Enter password'}">
                <span class="am-action-btn${s.configured ? ' success' : ''}" style="cursor:default;padding:6px 12px;font-size:12px">
                    ${s.configured ? '✓ Stored' : 'Not set'}
                </span>
            </div>
            <div class="am-hint">The credential-list password from the Twilio console. Encrypted on disk.</div>
        </div>
        <div class="am-group">
            <label for="twv-number">Phone Number</label>
            <input type="text" id="twv-number" value="${s.number || ''}" placeholder="+15551234567">
            <div class="am-hint">The E.164 number, for display and call-event payloads.</div>
        </div>
        <div class="am-group">
            <label for="twv-transport">SIP Transport</label>
            <select id="twv-transport">
                <option value="tls"${(s.transport || 'tls') === 'tls' ? ' selected' : ''}>TLS (encrypted — works behind any router)</option>
                <option value="udp"${s.transport === 'udp' ? ' selected' : ''}>UDP (legacy — needs router SIP ALG off)</option>
            </select>
            <div class="am-hint">Takes effect on the next (de)register cycle — toggle the number's Realtime rule off/on to apply now.</div>
        </div>
        <div class="am-group">
            <label for="twv-greeting">Greeting</label>
            <input type="text" id="twv-greeting" value="${s.greeting || ''}" placeholder="Hey, this is Sapphire.">
            <div class="am-hint">Spoken on pickup. BLANK = greeting off — she stays quiet and waits for the caller to speak first. A Realtime rule's greeting overrides this per-rule.</div>
        </div>
        <div class="am-group">
            <label for="twv-call-provider">Call model (default)</label>
            <div class="am-row">
                <select id="twv-call-provider" style="flex:1"><option value="">— inherit (chat / global) —</option></select>
                <input type="text" id="twv-call-model" value="${s.call_model || ''}" placeholder="model override (optional)" style="flex:1">
            </div>
            <div class="am-hint">Default brain for calls on this number — pick something fast and non-thinking (phone latency). A Realtime rule's model, or her model= on phone_call, overrides per call.</div>
        </div>
        <div style="border-top:1px solid var(--border);padding-top:16px;margin-top:8px">
            <div style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:4px">Outbound calling (optional)</div>
            <div class="am-hint" style="margin-bottom:12px">Lets Sapphire place calls from this number (the phone_call tool). From the Twilio Console dashboard — separate from the SIP credentials above.</div>
            <div class="am-group">
                <label for="twv-sid">Account SID</label>
                <input type="text" id="twv-sid" value="${s.account_sid || ''}" placeholder="AC...">
            </div>
            <div class="am-group" style="margin-top:12px">
                <label for="twv-token">Auth Token</label>
                <div class="am-row">
                    <input type="password" id="twv-token" placeholder="${s.rest_configured ? 'Leave blank to keep existing...' : 'Enter auth token'}">
                    <span class="am-action-btn${s.rest_configured ? ' success' : ''}" style="cursor:default;padding:6px 12px;font-size:12px">
                        ${s.rest_configured ? '✓ Stored' : 'Not set'}
                    </span>
                </div>
                <div class="am-hint">Encrypted on disk. Full-account secret — only needed for outbound.</div>
            </div>
        </div>
        <button type="button" class="am-action-btn" id="twv-save">Save</button>
    `;

    // Provider dropdown fills async (shared /api/llm/providers cache).
    fetchProviders().then(provs => {
        const sel = body.querySelector('#twv-call-provider');
        if (!sel) return;
        for (const p of provs) {
            const opt = document.createElement('option');
            opt.value = p.key;
            opt.textContent = p.display_name || p.key;
            if (p.key === (s.call_provider || '')) opt.selected = true;
            sel.appendChild(opt);
        }
    });

    body.querySelector('#twv-save').addEventListener('click', async () => {
        const btn = body.querySelector('#twv-save');
        const payload = {
            scope,
            sip_domain: body.querySelector('#twv-domain').value.trim(),
            sip_user: body.querySelector('#twv-user').value.trim(),
            sip_pass: body.querySelector('#twv-pass').value.trim(),
            number: body.querySelector('#twv-number').value.trim(),
            transport: body.querySelector('#twv-transport').value,
            greeting: body.querySelector('#twv-greeting').value.trim(),
            account_sid: body.querySelector('#twv-sid').value.trim(),
            auth_token: body.querySelector('#twv-token').value.trim(),
            call_provider: body.querySelector('#twv-call-provider').value,
            call_model: body.querySelector('#twv-call-model').value.trim(),
        };
        if (!payload.sip_domain || !payload.sip_user) {
            helpers.showResult(false, 'SIP domain and username are required');
            return;
        }
        btn.disabled = true;
        btn.textContent = 'Saving...';
        try {
            const res = await fetch(API, {
                method: 'POST',
                headers: helpers.csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!data.ok) throw new Error(data.error || 'Save failed');
            btn.textContent = 'Saved';
            btn.className = 'am-action-btn success';
            await manager.loadItems();
        } catch (e) {
            btn.textContent = 'Error';
            btn.className = 'am-action-btn error';
            helpers.showResult(false, e.message);
        }
        setTimeout(() => {
            btn.textContent = 'Save';
            btn.className = 'am-action-btn';
            btn.disabled = false;
        }, 3000);
    });
}


export default {
    name: 'twilio-voice',

    init() {
        registerPluginSettings({
            id: 'twilio-voice',
            name: 'Twilio Voice',
            icon: '📞',
            helpText: 'Phone numbers Sapphire can answer, one entry per Twilio SIP '
                + 'credential set. To make her actually answer a number, enable a '
                + 'rule for it in Triggers > Realtime.',
            render: (c) => manager.renderList(c),
            load: async () => { await manager.loadItems(); return {}; },
        });
    },

    destroy() {},
};
