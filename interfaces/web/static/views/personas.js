// views/personas.js - Persona manager view
import { listPersonas, getPersona, createPersona, updatePersona, deletePersona,
         duplicatePersona, loadPersona, createFromChat, uploadAvatar, avatarUrl } from '../shared/persona-api.js';
import { renderPersonaTabs, bindPersonaTabs } from '../shared/persona-tabs.js';
import { getInitData } from '../shared/init-data.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';
import { switchView } from '../core/router.js';

let container = null;
let personas = [];
let selectedName = null;
let selectedData = null;
let saveTimer = null;

function updateSliderFill(slider) {
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 100;
    const pct = ((parseFloat(slider.value) - min) / (max - min)) * 100;
    slider.style.setProperty('--pct', `${pct}%`);
}

// Dropdown data (cached from loadSidebar-style fetches)
let initData = null;
let llmProviders = [];
let llmMetadata = {};

export default {
    init(el) { container = el; },
    async show() {
        await loadData();
        render();
    },
    hide() {}
};

async function loadData() {
    try {
        const [pRes, init, llmResp] = await Promise.allSettled([
            listPersonas(),
            getInitData(),
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null)
        ]);

        personas = pRes.status === 'fulfilled' ? (pRes.value?.personas || []) : [];
        initData = init.status === 'fulfilled' ? init.value : null;
        const llmData = llmResp.status === 'fulfilled' ? llmResp.value : null;
        if (llmData) {
            llmProviders = llmData.providers || [];
            llmMetadata = llmData.metadata || {};
        }

        if (!selectedName && personas.length) selectedName = personas[0].name;
        if (selectedName) {
            try { selectedData = await getPersona(selectedName); } catch { selectedData = null; }
        }
    } catch (e) {
        console.warn('Persona load failed:', e);
    }
}

function render() {
    if (!container) return;

    const s = selectedData?.settings || {};
    const isActive = selectedData?.name === getCurrentPersona();

    container.innerHTML = `
        ${renderPersonaTabs('personas')}
        <div class="two-panel">
            <div class="panel-left panel-list">
                <div class="panel-list-header">
                    <span class="panel-list-title">Personas</span>
                    <button class="btn-sm" id="pa-new" title="New from current chat">+</button>
                </div>
                <div class="panel-list-items" id="pa-list">
                    ${personas.map(p => `
                        <button class="panel-list-item${p.name === selectedName ? ' active' : ''}" data-name="${p.name}">
                            <img class="pa-list-avatar" src="${avatarUrl(p.name)}" alt="" loading="lazy" onerror="this.style.display='none'">
                            <div class="pa-list-info">
                                <span class="pa-list-name">${esc(p.name)}</span>
                                ${p.tagline ? `<span class="pa-list-tagline">${esc(p.tagline)}</span>` : ''}
                            </div>
                        </button>
                    `).join('')}
                    ${personas.length === 0 ? '<div class="text-muted" style="padding:16px;font-size:var(--font-sm)">No personas yet. Click + to create one from your current chat settings.</div>' : ''}
                </div>
            </div>
            <div class="panel-right">
                ${selectedData ? renderDetail(selectedData, isActive) : '<div class="view-placeholder"><p>Select a persona</p></div>'}
            </div>
        </div>
    `;

    bindPersonaTabs(container);
    bindEvents();
}

function getCurrentPersona() {
    // Check if chat has an active persona
    const chatSelect = document.getElementById('chat-select');
    // We'll check from the sidebar easy mode display
    return document.getElementById('sb-easy-name')?.textContent?.toLowerCase() || null;
}

function renderDetail(p, isActive) {
    const s = p.settings || {};
    return `
        <div class="view-header pa-header">
            <div class="pa-header-left">
                <div class="pa-avatar-wrap" id="pa-avatar-wrap">
                    <img class="pa-avatar-lg" id="pa-avatar" src="${avatarUrl(p.name)}" alt="${esc(p.name)}" loading="lazy"
                         onerror="this.src=''; this.alt='?'">
                    <div class="pa-avatar-overlay" id="pa-avatar-upload" title="Upload avatar">&#x1F4F7;</div>
                    <input type="file" id="pa-avatar-input" accept="image/*" style="display:none">
                </div>
                <div class="pa-header-text">
                    <input class="pa-name-input" id="pa-name" value="${esc(p.name)}" placeholder="Name" spellcheck="false">
                    <input class="pa-tagline-input" id="pa-tagline" value="${esc(p.tagline || '')}" placeholder="Tagline...">
                </div>
            </div>
            <div class="view-header-actions">
                <button class="btn-primary" id="pa-load">Load</button>
                <button class="btn-sm" id="pa-duplicate">Duplicate</button>
                <button class="btn-sm danger" id="pa-delete">Delete</button>
            </div>
        </div>
        <div class="view-body view-scroll pa-settings">
            <div class="pa-sections">

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">Prompt</span>
                        <span class="pa-section-desc">Character, scenario & behavior</span>
                        <a class="pa-section-link" data-nav="prompts">\u2197 Prompts</a>
                    </div>
                    <div class="pa-section-body pa-section-row">
                        ${renderSettingField('prompt', 'Prompt', s, renderPromptOptions(s.prompt))}
                    </div>
                </div>

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">Toolset</span>
                        <span class="pa-section-desc">Tools the AI can use</span>
                        <a class="pa-section-link" data-nav="toolsets">\u2197 Toolsets</a>
                    </div>
                    <div class="pa-section-body pa-section-row">
                        ${renderSettingField('toolset', 'Toolset', s, renderToolsetOptions(s.toolset))}
                    </div>
                </div>

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">Spice</span>
                        <span class="pa-section-desc">Style, flavor & personality injection</span>
                        <a class="pa-section-link" data-nav="spices">\u2197 Spices</a>
                    </div>
                    <div class="pa-section-body pa-section-row">
                        ${renderSettingField('spice_set', 'Set', s, renderSpiceSetOptions(s.spice_set))}
                        <div class="pa-field">
                            <label>Turns</label>
                            <input type="number" id="pa-s-spice_turns" min="1" max="20" value="${s.spice_turns || 3}" data-key="spice_turns">
                        </div>
                        <div class="pa-field pa-field-toggle">
                            <label><input type="checkbox" id="pa-s-spice_enabled" data-key="spice_enabled" ${s.spice_enabled !== false ? 'checked' : ''}> Enabled</label>
                        </div>
                    </div>
                </div>

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">TTS</span>
                        <span class="pa-section-desc">Voice synthesis settings</span>
                    </div>
                    <div class="pa-section-body">
                        <div class="pa-section-row">
                            ${renderSettingField('voice', 'Voice', s, renderVoiceOptions(s.voice))}
                        </div>
                        <div class="pa-section-row">
                            <div class="pa-field">
                                <label>Pitch: <span id="pa-pitch-val">${s.pitch || 0.98}</span></label>
                                <input type="range" id="pa-s-pitch" min="0.5" max="1.5" step="0.02" value="${s.pitch || 0.98}" data-key="pitch">
                            </div>
                            <div class="pa-field">
                                <label>Speed: <span id="pa-speed-val">${s.speed || 1.3}</span></label>
                                <input type="range" id="pa-s-speed" min="0.5" max="2.5" step="0.1" value="${s.speed || 1.3}" data-key="speed">
                            </div>
                        </div>
                    </div>
                </div>

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">Model</span>
                        <span class="pa-section-desc">LLM provider & model</span>
                    </div>
                    <div class="pa-section-body pa-section-row">
                        ${renderSettingField('llm_primary', 'Provider', s, renderProviderOptions(s.llm_primary))}
                    </div>
                </div>

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">Appearance</span>
                        <span class="pa-section-desc">Trim color & visual identity</span>
                    </div>
                    <div class="pa-section-body pa-section-row">
                        <div class="pa-field">
                            <label>Trim Color</label>
                            <input type="color" id="pa-s-trim_color" value="${s.trim_color || '#4a9eff'}" data-key="trim_color">
                        </div>
                    </div>
                </div>

                <div class="pa-section">
                    <div class="pa-section-header">
                        <span class="pa-section-title">Advanced</span>
                        <span class="pa-section-desc">State engine, datetime & context</span>
                    </div>
                    <div class="pa-section-body">
                        <div class="pa-toggles">
                            <label><input type="checkbox" id="pa-s-inject_datetime" data-key="inject_datetime" ${s.inject_datetime ? 'checked' : ''}> Date/Time</label>
                            <label><input type="checkbox" id="pa-s-state_engine_enabled" data-key="state_engine_enabled" ${s.state_engine_enabled ? 'checked' : ''}> State Engine</label>
                        </div>
                        <div class="pa-field pa-field-wide">
                            <label>Custom Context</label>
                            <textarea id="pa-s-custom_context" rows="3" placeholder="Injected into system prompt..." data-key="custom_context">${esc(s.custom_context || '')}</textarea>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    `;
}

function renderSettingField(key, label, settings, optionsHtml) {
    return `
        <div class="pa-field">
            <label>${label}</label>
            <select id="pa-s-${key}" data-key="${key}">
                ${optionsHtml}
            </select>
        </div>
    `;
}

function renderPromptOptions(current) {
    const list = initData?.prompts?.list || [];
    return list.map(p =>
        `<option value="${p.name}"${p.name === current ? ' selected' : ''}>${p.name}</option>`
    ).join('') || `<option value="${current || 'default'}">${current || 'default'}</option>`;
}

function renderToolsetOptions(current) {
    const list = (initData?.toolsets?.list || []).filter(t => t.type !== 'module');
    return list.map(t =>
        `<option value="${t.name}"${t.name === current ? ' selected' : ''}>${t.name} (${t.function_count})</option>`
    ).join('') || `<option value="${current || 'all'}">${current || 'all'}</option>`;
}

function renderSpiceSetOptions(current) {
    const list = initData?.spice_sets?.list || [];
    return list.map(s =>
        `<option value="${s.name}"${s.name === current ? ' selected' : ''}>${s.emoji ? s.emoji + ' ' : ''}${s.name}</option>`
    ).join('') || `<option value="${current || 'default'}">${current || 'default'}</option>`;
}

function renderVoiceOptions(current) {
    const voices = [
        ['am_adam', 'Adam'], ['am_eric', 'Eric'], ['am_liam', 'Liam'], ['am_michael', 'Michael'],
        ['af_bella', 'Bella'], ['af_nicole', 'Nicole'], ['af_heart', 'Heart'], ['af_jessica', 'Jessica'],
        ['af_sarah', 'Sarah'], ['af_river', 'River'], ['af_sky', 'Sky'],
        ['bf_emma', 'Emma'], ['bf_isabella', 'Isabella'], ['bf_alice', 'Alice'], ['bf_lily', 'Lily'],
        ['bm_george', 'George'], ['bm_daniel', 'Daniel'], ['bm_lewis', 'Lewis']
    ];
    return voices.map(([val, label]) =>
        `<option value="${val}"${val === current ? ' selected' : ''}>${label}</option>`
    ).join('');
}

function renderProviderOptions(current) {
    let html = '<option value="auto">Auto</option><option value="none">None</option>';
    html += llmProviders.filter(p => p.enabled).map(p =>
        `<option value="${p.key}"${p.key === current ? ' selected' : ''}>${p.display_name}</option>`
    ).join('');
    return html;
}

function bindEvents() {
    // Section nav links (e.g. "↗ Prompts")
    container.querySelectorAll('.pa-section-link[data-nav]').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            switchView(link.dataset.nav);
        });
    });

    // List selection
    container.querySelector('#pa-list')?.addEventListener('click', async e => {
        const item = e.target.closest('.panel-list-item');
        if (!item) return;
        selectedName = item.dataset.name;
        try { selectedData = await getPersona(selectedName); } catch { selectedData = null; }
        render();
    });

    // New persona from chat
    container.querySelector('#pa-new')?.addEventListener('click', async () => {
        const name = prompt('New persona name (from current chat settings):');
        if (!name?.trim()) return;
        try {
            await createFromChat(name.trim());
            selectedName = name.trim().replace(/\s+/g, '_').toLowerCase();
            await loadData();
            render();
            ui.showToast(`Created: ${name.trim()}`, 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Load persona
    container.querySelector('#pa-load')?.addEventListener('click', async () => {
        if (!selectedName) return;
        try {
            await loadPersona(selectedName);
            ui.showToast(`Loaded: ${selectedName}`, 'success');
            updateScene();
            // Refresh sidebar easy mode
            window.dispatchEvent(new CustomEvent('persona-loaded', { detail: { name: selectedName } }));
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Duplicate
    container.querySelector('#pa-duplicate')?.addEventListener('click', async () => {
        const newName = prompt(`Duplicate "${selectedName}" as:`, selectedName + '-copy');
        if (!newName?.trim()) return;
        try {
            await duplicatePersona(selectedName, newName.trim());
            selectedName = newName.trim().replace(/\s+/g, '_').toLowerCase();
            await loadData();
            render();
            ui.showToast(`Duplicated`, 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Delete
    container.querySelector('#pa-delete')?.addEventListener('click', async () => {
        if (!confirm(`Delete persona "${selectedName}"?`)) return;
        try {
            await deletePersona(selectedName);
            selectedName = null;
            selectedData = null;
            await loadData();
            render();
            ui.showToast('Deleted', 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Avatar upload
    const avatarUpload = container.querySelector('#pa-avatar-upload');
    const avatarInput = container.querySelector('#pa-avatar-input');
    if (avatarUpload && avatarInput) {
        avatarUpload.addEventListener('click', () => avatarInput.click());
        avatarInput.addEventListener('change', async e => {
            const file = e.target.files[0];
            if (!file || !selectedName) return;
            try {
                await uploadAvatar(selectedName, file);
                // Refresh avatar display
                const img = container.querySelector('#pa-avatar');
                if (img) img.src = avatarUrl(selectedName) + '?t=' + Date.now();
                ui.showToast('Avatar updated', 'success');
            } catch (e) { ui.showToast(e.message || 'Upload failed', 'error'); }
        });
    }

    // Name/tagline changes (debounced save)
    container.querySelector('#pa-name')?.addEventListener('input', () => debouncedSave());
    container.querySelector('#pa-tagline')?.addEventListener('input', () => debouncedSave());

    // Settings fields (debounced save)
    container.querySelectorAll('.pa-settings select, .pa-settings input, .pa-settings textarea').forEach(el => {
        const event = el.type === 'range' ? 'input' : (el.tagName === 'TEXTAREA' ? 'input' : 'change');
        el.addEventListener(event, () => {
            if (el.id === 'pa-s-pitch') {
                const label = container.querySelector('#pa-pitch-val');
                if (label) label.textContent = el.value;
            }
            if (el.id === 'pa-s-speed') {
                const label = container.querySelector('#pa-speed-val');
                if (label) label.textContent = el.value;
            }
            if (el.type === 'range') updateSliderFill(el);
            debouncedSave();
        });
    });

    // Init slider fills
    container.querySelectorAll('.pa-field input[type="range"]').forEach(updateSliderFill);
}

function collectSettings() {
    const get = (id) => container.querySelector(`#pa-s-${id}`)?.value || '';
    const getChecked = (id) => container.querySelector(`#pa-s-${id}`)?.checked || false;

    return {
        prompt: get('prompt'),
        toolset: get('toolset'),
        spice_set: get('spice_set') || 'default',
        voice: get('voice'),
        pitch: parseFloat(get('pitch')) || 0.98,
        speed: parseFloat(get('speed')) || 1.3,
        spice_enabled: getChecked('spice_enabled'),
        spice_turns: parseInt(get('spice_turns')) || 3,
        inject_datetime: getChecked('inject_datetime'),
        custom_context: get('custom_context'),
        llm_primary: get('llm_primary') || 'auto',
        llm_model: '',
        trim_color: get('trim_color') || '#4a9eff',
        memory_scope: selectedData?.settings?.memory_scope || 'default',
        goal_scope: selectedData?.settings?.goal_scope || 'default',
        knowledge_scope: selectedData?.settings?.knowledge_scope || 'default',
        people_scope: selectedData?.settings?.people_scope || 'default',
        state_engine_enabled: getChecked('state_engine_enabled'),
        state_preset: selectedData?.settings?.state_preset || null,
        state_vars_in_prompt: selectedData?.settings?.state_vars_in_prompt || false,
        state_story_in_prompt: selectedData?.settings?.state_story_in_prompt !== false,
    };
}

function debouncedSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
        if (!selectedName || !selectedData) return;
        const data = {
            tagline: container.querySelector('#pa-tagline')?.value || '',
            settings: collectSettings()
        };
        // Check for name change
        const nameInput = container.querySelector('#pa-name');
        if (nameInput && nameInput.value.trim() && nameInput.value.trim() !== selectedName) {
            data.name = nameInput.value.trim();
        }
        try {
            await updatePersona(selectedName, data);
            // Update local state
            if (data.name && data.name !== selectedName) {
                selectedName = data.name.replace(/\s+/g, '_').toLowerCase();
                await loadData();
                render();
            } else {
                selectedData.tagline = data.tagline;
                selectedData.settings = data.settings;
            }
        } catch (e) {
            console.warn('Persona save failed:', e);
        }
    }, 600);
}

function esc(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
}
