// plugins/agents/web/index.js — Agent roster settings UI
import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';

const BASIC_SCHEMA = [
    { key: 'max_concurrent', type: 'number', label: 'Max Concurrent Agents', default: 3, help: 'Maximum agents running simultaneously (1-5)' },
    { key: 'default_toolset', type: 'string', label: 'Default Toolset', default: 'default', help: 'Toolset for LLM agents when not specified' },
];

let providers = [];

function injectStyles() {
    if (document.getElementById('agent-settings-css')) return;
    const style = document.createElement('style');
    style.id = 'agent-settings-css';
    style.textContent = `
        .agent-settings { display: flex; flex-direction: column; gap: 20px; }
        .agent-section-title { font-size: 14px; font-weight: 600; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 6px; margin-bottom: 4px; }
        .agent-roster { display: flex; flex-direction: column; gap: 8px; }
        .agent-roster-row { display: grid; grid-template-columns: 1fr 1fr 1fr 32px; gap: 8px; align-items: center; }
        .agent-roster-row.header { font-size: 12px; font-weight: 500; color: var(--text-muted); }
        .agent-roster-row input, .agent-roster-row select {
            padding: 6px 10px; font-size: 13px; border: 1px solid var(--border);
            border-radius: 6px; background: var(--bg-tertiary); color: var(--text);
        }
        .agent-roster-row input:focus, .agent-roster-row select:focus { border-color: var(--trim); outline: none; }
        .agent-del { background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 16px; padding: 4px; border-radius: 4px; }
        .agent-del:hover { color: #d9534f; background: rgba(217,83,79,0.1); }
        .agent-add-btn { align-self: flex-start; padding: 6px 14px; font-size: 13px; background: var(--bg-tertiary); color: var(--text-secondary);
            border: 1px dashed var(--border); border-radius: 6px; cursor: pointer; }
        .agent-add-btn:hover { border-color: var(--trim); color: var(--trim); }
        .agent-hint { font-size: 12px; color: var(--text-muted); line-height: 1.4; }
    `;
    document.head.appendChild(style);
}

function csrfHeaders(extra = {}) {
    const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
    return { 'X-CSRF-Token': token, ...extra };
}

async function loadProviders() {
    if (providers.length) return providers;
    try {
        const res = await fetch('/api/agents/providers', { headers: csrfHeaders() });
        const data = await res.json();
        providers = data.providers || [];
    } catch (e) {
        console.warn('[Agents] Failed to load providers:', e);
    }
    return providers;
}

function buildProviderSelect(selected) {
    let html = '<option value="">-- Provider --</option>';
    for (const p of providers) {
        const sel = p.key === selected ? ' selected' : '';
        html += `<option value="${p.key}"${sel}>${esc(p.name)}</option>`;
    }
    return html;
}

function buildModelSelect(providerKey, selectedModel) {
    const prov = providers.find(p => p.key === providerKey);
    if (!prov) return '<option value="">-- select provider first --</option>';

    let html = '';
    const models = prov.models || {};
    const keys = Object.keys(models);

    if (keys.length === 0) {
        html += `<option value="${esc(prov.current_model)}">${esc(prov.current_model)} (current)</option>`;
    } else {
        if (prov.current_model && !models[prov.current_model]) {
            const sel = prov.current_model === selectedModel ? ' selected' : '';
            html += `<option value="${esc(prov.current_model)}"${sel}>${esc(prov.current_model)} (current)</option>`;
        }
        for (const [mid, label] of Object.entries(models)) {
            const sel = mid === selectedModel ? ' selected' : '';
            html += `<option value="${esc(mid)}"${sel}>${esc(label)}</option>`;
        }
    }
    return html;
}

function syncRosterFromDom(container, roster) {
    const rows = container.querySelectorAll('.agent-roster-row:not(.header)');
    rows.forEach((row, i) => {
        if (i < roster.length) {
            roster[i].name = row.querySelector('.agent-r-name')?.value?.trim() || '';
            roster[i].provider = row.querySelector('.agent-r-provider')?.value || '';
            roster[i].model = row.querySelector('.agent-r-model')?.value || '';
        }
    });
}

function renderRoster(container, roster) {
    const rosterDiv = container.querySelector('#agent-roster-list');
    rosterDiv.innerHTML = '';

    for (let i = 0; i < roster.length; i++) {
        const entry = roster[i];
        const row = document.createElement('div');
        row.className = 'agent-roster-row';
        row.dataset.idx = i;
        row.innerHTML = `
            <input type="text" class="agent-r-name" value="${esc(entry.name || '')}" placeholder="e.g. Big Brain">
            <select class="agent-r-provider">${buildProviderSelect(entry.provider)}</select>
            <select class="agent-r-model">${buildModelSelect(entry.provider, entry.model)}</select>
            <button class="agent-del" title="Remove">\u00d7</button>
        `;

        row.querySelector('.agent-r-provider').addEventListener('change', (e) => {
            const modelSel = row.querySelector('.agent-r-model');
            modelSel.innerHTML = buildModelSelect(e.target.value, '');
        });

        row.querySelector('.agent-del').addEventListener('click', () => {
            syncRosterFromDom(container, roster);
            roster.splice(i, 1);
            renderRoster(container, roster);
        });

        rosterDiv.appendChild(row);
    }
}

function readRoster(container) {
    const rows = container.querySelectorAll('.agent-roster-row:not(.header)');
    const roster = [];
    for (const row of rows) {
        const name = row.querySelector('.agent-r-name')?.value?.trim();
        const provider = row.querySelector('.agent-r-provider')?.value;
        const model = row.querySelector('.agent-r-model')?.value;
        if (name && provider) {
            roster.push({ name, provider, model: model || '' });
        }
    }
    return roster;
}

function esc(s) {
    if (!s) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export default {
    name: 'agents',

    init() {
        injectStyles();

        let roster = [];

        registerPluginSettings({
            id: 'agents',
            name: 'Agents',
            icon: '\ud83d\udd2d',
            helpText: 'Background AI workers with their own model and tools',

            render: async (container, settings) => {
                await loadProviders();
                roster = settings.roster || [];

                container.innerHTML = `
                    <div class="agent-settings">
                        <div>
                            <div class="agent-section-title">General</div>
                            <div id="agent-basic-settings"></div>
                        </div>
                        <div>
                            <div class="agent-section-title">Model Roster</div>
                            <p class="agent-hint">Define named models Sapphire can use for LLM agents. She sees the friendly name and knows which provider backs it. If empty, agents use the current chat model.</p>
                            <div class="agent-roster">
                                <div class="agent-roster-row header">
                                    <span>Friendly Name</span><span>Provider</span><span>Model</span><span></span>
                                </div>
                                <div id="agent-roster-list"></div>
                            </div>
                            <button class="agent-add-btn" id="agent-add-entry">+ Add Model</button>
                        </div>
                    </div>
                `;

                renderSettingsForm(container.querySelector('#agent-basic-settings'), BASIC_SCHEMA, settings);
                renderRoster(container, roster);

                container.querySelector('#agent-add-entry').addEventListener('click', () => {
                    syncRosterFromDom(container, roster);
                    roster.push({ name: '', provider: '', model: '' });
                    renderRoster(container, roster);
                    const rows = container.querySelectorAll('.agent-roster-row:not(.header)');
                    const last = rows[rows.length - 1];
                    if (last) last.querySelector('.agent-r-name')?.focus();
                });
            },

            load: async () => {
                try {
                    const res = await fetch('/api/webui/plugins/agents/settings', { headers: csrfHeaders() });
                    const data = await res.json();
                    return data.settings || data || {};
                } catch { return {}; }
            },

            save: async (settings) => {
                return fetch('/api/webui/plugins/agents/settings', {
                    method: 'PUT',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ settings }),
                });
            },

            getSettings: (container) => {
                const basic = readSettingsForm(container.querySelector('#agent-basic-settings'), BASIC_SCHEMA);
                const rosterData = readRoster(container);
                return { ...basic, roster: rosterData };
            },
        });
    },

    destroy() {},
};
