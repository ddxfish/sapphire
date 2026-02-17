// views/personas.js - Persona manager view
import { listPersonas, getPersona, createPersona, updatePersona, deletePersona,
         duplicatePersona, loadPersona, createFromChat, uploadAvatar, avatarUrl } from '../shared/persona-api.js';
import { renderPersonaTabs, bindPersonaTabs } from '../shared/persona-tabs.js';
import { getInitData } from '../shared/init-data.js';
import * as ui from '../ui.js';
import { updateScene } from '../features/scene.js';
import { applyTrimColor } from '../features/chat-settings.js';
import { switchView } from '../core/router.js';

let container = null;
let personas = [];
let selectedName = null;
let selectedData = null;
let saveTimer = null;
let defaultPersona = '';

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
let scopeData = { memory: [], goals: [], knowledge: [], people: [] };

export default {
    init(el) {
        container = el;
        window.addEventListener('persona-select', e => {
            if (e.detail?.name) selectedName = e.detail.name;
        });
    },
    async show() {
        if (container) container.innerHTML = '';
        await loadData();
        render();
    },
    hide() {
        // Restore active chat's trim color when leaving persona view
        fetch('/api/scene').then(r => r.ok ? r.json() : null).then(d => {
            applyTrimColor(d?.chat_settings?.trim_color || '');
        }).catch(() => applyTrimColor(''));
    }
};

async function loadData() {
    try {
        const [pRes, init, llmResp, memS, goalS, knowS, pplS] = await Promise.allSettled([
            listPersonas(),
            getInitData(),
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null),
            fetch('/api/memory/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/goals/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/knowledge/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/knowledge/people/scopes').then(r => r.ok ? r.json() : null)
        ]);

        personas = pRes.status === 'fulfilled' ? (pRes.value?.personas || []) : [];
        initData = init.status === 'fulfilled' ? init.value : null;
        defaultPersona = initData?.personas?.default || '';
        const llmData = llmResp.status === 'fulfilled' ? llmResp.value : null;
        if (llmData) {
            llmProviders = llmData.providers || [];
            llmMetadata = llmData.metadata || {};
        }
        scopeData = {
            memory: memS.status === 'fulfilled' ? (memS.value?.scopes || []) : [],
            goals: goalS.status === 'fulfilled' ? (goalS.value?.scopes || []) : [],
            knowledge: knowS.status === 'fulfilled' ? (knowS.value?.scopes || []) : [],
            people: pplS.status === 'fulfilled' ? (pplS.value?.scopes || []) : []
        };

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
    // Apply selected persona's trim color while browsing
    applyTrimColor(selectedData?.settings?.trim_color || '');

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
                            <img class="pa-list-avatar" src="${avatarUrl(p.name)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
                            <div class="pa-list-info">
                                <span class="pa-list-name">${esc(p.name)}${p.name === defaultPersona ? ' <span class="pa-default-star" title="Default persona">&#x2B50;</span>' : ''}</span>
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
    const trim = s.trim_color || '#4a9eff';
    return `
        <div class="view-body view-scroll pa-scroll">

            <div class="pa-header">
                <div class="pa-avatar-wrap" id="pa-avatar-wrap">
                    <img class="pa-avatar-lg" id="pa-avatar" src="${avatarUrl(p.name)}" alt="${esc(p.name)}" loading="lazy"
                         onerror="this.style.visibility='hidden'">
                    <div class="pa-avatar-overlay" id="pa-avatar-upload" title="Upload avatar">&#x1F4F7;</div>
                    <input type="file" id="pa-avatar-input" accept="image/*" style="display:none">
                </div>
                <div class="pa-header-right">
                    <div class="pa-header-top">
                        <div class="pa-header-text">
                            <input class="pa-name-input" id="pa-name" value="${esc(p.name)}" placeholder="Name" spellcheck="false">
                            <input class="pa-tagline-input" id="pa-tagline" value="${esc(p.tagline || '')}" placeholder="Tagline...">
                        </div>
                        <input type="color" id="pa-s-trim_color" class="pa-trim-swatch" value="${trim}" data-key="trim_color" title="Trim color">
                    </div>
                    <div class="pa-header-actions">
                        <button class="btn-primary" id="pa-load">Activate</button>
                        ${p.name === defaultPersona
                            ? '<button class="btn-sm" id="pa-clear-default" title="Remove as default">&#x2B50; Default</button>'
                            : '<button class="btn-sm" id="pa-set-default" title="Set as default for new chats">Set Default</button>'}
                        <button class="btn-sm" id="pa-duplicate">Duplicate</button>
                        <button class="btn-sm danger" id="pa-delete">Delete</button>
                    </div>
                </div>
            </div>

            <div class="pa-fences">

                <div class="pa-fence-group">
                    <div class="pa-fence-heading">
                        <span>Prompt & Tools</span>
                        <span class="pa-fence-heading-right">
                            <span class="pa-fence-toggle-label">Date/Time <span class="help-tip" data-tip="Inject current date & time into prompt">?</span></span>
                            <label class="pa-fence-toggle"><input type="checkbox" id="pa-s-inject_datetime" data-key="inject_datetime" ${s.inject_datetime ? 'checked' : ''}><span class="toggle-slider"></span></label>
                        </span>
                    </div>
                    <div class="pa-fence">
                        <div class="pa-fence-body">
                            ${renderSettingField('prompt', 'Prompt', s, renderPromptOptions(s.prompt), { tip: 'Character personality & scenario preset', view: 'prompts' })}
                            ${renderSettingField('toolset', 'Toolset', s, renderToolsetOptions(s.toolset), { tip: 'Functions the AI can call', view: 'toolsets' })}
                        </div>
                    </div>
                </div>

                <div class="pa-fence-group">
                    <div class="pa-fence-heading">
                        <span>Spice</span>
                        <span class="pa-fence-heading-right">
                            <label class="pa-fence-toggle">
                                <input type="checkbox" id="pa-s-spice_enabled" data-key="spice_enabled" ${s.spice_enabled !== false ? 'checked' : ''}>
                                <span class="toggle-slider"></span>
                            </label>
                        </span>
                    </div>
                    <div class="pa-fence">
                        <div class="pa-fence-body">
                            ${renderSettingField('spice_set', 'Set', s, renderSpiceSetOptions(s.spice_set), { tip: 'Flavor pack for AI responses', view: 'spices' })}
                            <div class="pa-field">
                                <label>Turns <span class="help-tip" data-tip="Spice activates every N turns">?</span></label>
                                <input type="number" id="pa-s-spice_turns" min="1" max="20" value="${s.spice_turns || 3}" data-key="spice_turns">
                            </div>
                        </div>
                    </div>
                </div>

                <div class="pa-fence-group">
                    <div class="pa-fence-heading">
                        <span>TTS</span>
                        <span class="pa-fence-heading-right">
                            <button class="btn-sm" id="pa-tts-test" title="Preview voice">&#x25B6; Test</button>
                        </span>
                    </div>
                    <div class="pa-fence">
                        <div class="pa-fence-body">
                            ${renderSettingField('voice', 'Voice', s, renderVoiceOptions(s.voice), { tip: 'Text-to-speech voice' })}
                            <div class="pa-field">
                                <label>Pitch <span class="help-tip" data-tip="Voice pitch multiplier">?</span> <span id="pa-pitch-val">${s.pitch || 0.98}</span></label>
                                <input type="range" id="pa-s-pitch" min="0.5" max="1.5" step="0.02" value="${s.pitch || 0.98}" data-key="pitch">
                            </div>
                            <div class="pa-field">
                                <label>Speed <span class="help-tip" data-tip="Speech speed multiplier">?</span> <span id="pa-speed-val">${s.speed || 1.3}</span></label>
                                <input type="range" id="pa-s-speed" min="0.5" max="2.5" step="0.1" value="${s.speed || 1.3}" data-key="speed">
                            </div>
                        </div>
                    </div>
                </div>

                <div class="pa-fence-group">
                    <div class="pa-fence-heading"><span>Model</span></div>
                    <div class="pa-fence">
                        <div class="pa-fence-body">
                            ${renderSettingField('llm_primary', 'Provider', s, renderProviderOptions(s.llm_primary), { tip: 'LLM API provider' })}
                            <div class="pa-field" id="pa-model-group" style="display:none">
                                <label>Model <span class="help-tip" data-tip="Specific model for this provider">?</span></label>
                                <select id="pa-s-llm_model" data-key="llm_model"></select>
                            </div>
                            <div class="pa-field" id="pa-model-custom-group" style="display:none">
                                <label>Model ID <span class="help-tip" data-tip="Custom model identifier">?</span></label>
                                <input type="text" id="pa-s-llm_model_custom" placeholder="model-name" data-key="llm_model">
                            </div>
                        </div>
                    </div>
                </div>

                <div class="pa-fence-group pa-fence-group-wide">
                    <div class="pa-fence-heading"><span>Mind Scopes</span></div>
                    <div class="pa-fence">
                        <div class="pa-fence-body pa-fence-body-grid">
                            ${renderScopeField('memory_scope', 'Memory', s, scopeData.memory)}
                            ${renderScopeField('goal_scope', 'Goals', s, scopeData.goals)}
                            ${renderScopeField('knowledge_scope', 'Knowledge', s, scopeData.knowledge)}
                            ${renderScopeField('people_scope', 'People', s, scopeData.people)}
                        </div>
                    </div>
                </div>

                <div class="pa-fence-group pa-fence-group-wide">
                    <div class="pa-fence-heading pa-fence-collapse-trigger">
                        <span class="accordion-arrow">&#x25B6;</span>
                        <span>Advanced</span>
                    </div>
                    <div class="pa-fence pa-fence-collapse-content" style="display:none">
                        <div class="pa-fence-body">
                            <div class="pa-field">
                                <label>Custom Context <span class="help-tip" data-tip="Extra text injected into system prompt">?</span></label>
                                <textarea id="pa-s-custom_context" rows="3" placeholder="Injected into system prompt..." data-key="custom_context">${esc(s.custom_context || '')}</textarea>
                            </div>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    `;
}

function renderSettingField(key, label, settings, optionsHtml, opts = {}) {
    const tip = opts.tip ? ` <span class="help-tip" data-tip="${esc(opts.tip)}">?</span>` : '';
    const link = opts.view ? `<a class="pa-field-edit pa-section-link" data-nav="${opts.view}">edit</a>` : '';
    return `
        <div class="pa-field">
            <label>${label}${tip}</label>
            <div class="pa-field-with-link">
                <select id="pa-s-${key}" data-key="${key}">
                    ${optionsHtml}
                </select>
                ${link}
            </div>
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
    const flag = v => v.startsWith('a') ? '\u{1F1FA}\u{1F1F8}' : '\u{1F1EC}\u{1F1E7}';
    const gender = v => v[1] === 'm' ? '\u{1F468}' : '\u{1F469}';
    const voices = [
        ['am_adam', 'Adam'], ['am_eric', 'Eric'], ['am_liam', 'Liam'], ['am_michael', 'Michael'],
        ['af_bella', 'Bella'], ['af_nicole', 'Nicole'], ['af_heart', 'Heart'], ['af_jessica', 'Jessica'],
        ['af_sarah', 'Sarah'], ['af_river', 'River'], ['af_sky', 'Sky'],
        ['bf_emma', 'Emma'], ['bf_isabella', 'Isabella'], ['bf_alice', 'Alice'], ['bf_lily', 'Lily'],
        ['bm_george', 'George'], ['bm_daniel', 'Daniel'], ['bm_lewis', 'Lewis']
    ];
    return voices.map(([val, label]) =>
        `<option value="${val}"${val === current ? ' selected' : ''}>${flag(val)}${gender(val)} ${label}</option>`
    ).join('');
}

function renderProviderOptions(current) {
    let html = '<option value="auto">Auto</option><option value="none">None</option>';
    html += llmProviders.filter(p => p.enabled).map(p =>
        `<option value="${p.key}"${p.key === current ? ' selected' : ''}>${p.display_name}</option>`
    ).join('');
    return html;
}

function renderScopeField(key, label, settings, scopes) {
    const current = settings[key] || 'default';
    const opts = scopes.map(s =>
        `<option value="${s.name}"${s.name === current ? ' selected' : ''}>${s.name} (${s.count})</option>`
    ).join('') || `<option value="${current}">${current}</option>`;
    return `
        <div class="pa-field">
            <label>${label}</label>
            <select id="pa-s-${key}" data-key="${key}">${opts}</select>
        </div>
    `;
}

function updateModelSelector(providerKey, currentModel) {
    const group = container.querySelector('#pa-model-group');
    const customGroup = container.querySelector('#pa-model-custom-group');
    const select = container.querySelector('#pa-s-llm_model');
    const custom = container.querySelector('#pa-s-llm_model_custom');

    if (group) group.style.display = 'none';
    if (customGroup) customGroup.style.display = 'none';

    if (providerKey === 'auto' || providerKey === 'none' || !providerKey) return;

    const meta = llmMetadata[providerKey];
    const conf = llmProviders.find(p => p.key === providerKey);

    if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
        const defaultModel = conf?.model || '';
        const defaultLabel = defaultModel ?
            `Default (${meta.model_options[defaultModel] || defaultModel})` : 'Default';

        select.innerHTML = `<option value="">${defaultLabel}</option>` +
            Object.entries(meta.model_options).map(([k, v]) =>
                `<option value="${k}" ${k === currentModel ? 'selected' : ''}>${v}</option>`
            ).join('');

        if (currentModel && !meta.model_options[currentModel]) {
            select.innerHTML += `<option value="${currentModel}" selected>${currentModel}</option>`;
        }
        if (group) group.style.display = '';
    } else if (providerKey === 'other') {
        if (custom) custom.value = currentModel || '';
        if (customGroup) customGroup.style.display = '';
    }
}

function getSelectedModel() {
    const provider = container.querySelector('#pa-s-llm_primary')?.value;
    if (provider === 'auto' || provider === 'none') return '';

    const group = container.querySelector('#pa-model-group');
    if (group && group.style.display !== 'none') {
        return container.querySelector('#pa-s-llm_model')?.value || '';
    }

    const customGroup = container.querySelector('#pa-model-custom-group');
    if (customGroup && customGroup.style.display !== 'none') {
        return (container.querySelector('#pa-s-llm_model_custom')?.value || '').trim();
    }
    return '';
}

function bindEvents() {
    // Section nav links (e.g. "edit prompts")
    container.querySelectorAll('.pa-section-link[data-nav]').forEach(link => {
        link.addEventListener('click', e => {
            e.preventDefault();
            switchView(link.dataset.nav);
        });
    });

    // Help tip tooltips
    let tipEl = document.getElementById('pa-tip-popup');
    if (!tipEl) {
        tipEl = document.createElement('div');
        tipEl.id = 'pa-tip-popup';
        tipEl.className = 'help-tip-popup';
        document.body.appendChild(tipEl);
    }
    container.addEventListener('mouseover', e => {
        const tip = e.target.closest('.help-tip');
        if (!tip) return;
        const text = tip.dataset.tip;
        if (!text) return;
        tipEl.textContent = text;
        tipEl.style.display = 'block';
        const r = tip.getBoundingClientRect();
        tipEl.style.left = (r.left + r.width / 2) + 'px';
        tipEl.style.top = (r.top - 6) + 'px';
    });
    container.addEventListener('mouseout', e => {
        if (e.target.closest('.help-tip') && !e.target.closest('.help-tip').contains(e.relatedTarget))
            tipEl.style.display = 'none';
    });

    // Collapsible fence toggle
    container.querySelectorAll('.pa-fence-collapse-trigger').forEach(trigger => {
        trigger.addEventListener('click', () => {
            const content = trigger.nextElementSibling;
            if (!content) return;
            const open = content.style.display === 'none';
            content.style.display = open ? '' : 'none';
            trigger.querySelector('.accordion-arrow')?.classList.toggle('open', open);
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

    // Set as default
    container.querySelector('#pa-set-default')?.addEventListener('click', async () => {
        if (!selectedName) return;
        try {
            await fetch('/api/personas/default', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: selectedName })
            });
            defaultPersona = selectedName;
            render();
            ui.showToast(`${selectedName} set as default`, 'success');
        } catch (e) { ui.showToast(e.message || 'Failed', 'error'); }
    });

    // Clear default
    container.querySelector('#pa-clear-default')?.addEventListener('click', async () => {
        try {
            await fetch('/api/personas/default', { method: 'DELETE' });
            defaultPersona = '';
            render();
            ui.showToast('Default cleared', 'success');
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
                const bust = '?t=' + Date.now();
                // Refresh main avatar
                const img = container.querySelector('#pa-avatar');
                if (img) { img.src = avatarUrl(selectedName) + bust; img.style.visibility = ''; }
                // Refresh list thumbnail
                const listItem = container.querySelector(`.panel-list-item[data-name="${selectedName}"] .pa-list-avatar`);
                if (listItem) { listItem.src = avatarUrl(selectedName) + bust; listItem.style.visibility = ''; }
                ui.showToast('Avatar updated', 'success');
            } catch (e) { ui.showToast(e.message || 'Upload failed', 'error'); }
        });
    }

    // TTS test button
    container.querySelector('#pa-tts-test')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = '...';
        try {
            const voice = container.querySelector('#pa-s-voice')?.value;
            const pitch = parseFloat(container.querySelector('#pa-s-pitch')?.value) || 0.98;
            const speed = parseFloat(container.querySelector('#pa-s-speed')?.value) || 1.3;
            const name = selectedData?.name || 'Sapphire';
            const settings = await fetch('/api/settings/DEFAULT_USERNAME').then(r => r.ok ? r.json() : null);
            const userName = settings?.value || 'friend';
            const resp = await fetch('/api/tts/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: `Hello ${userName}, I'm ${name}!`, voice, pitch, speed })
            });
            if (!resp.ok) throw new Error('TTS failed');
            const blob = await resp.blob();
            const audio = new Audio(URL.createObjectURL(blob));
            audio.onended = () => URL.revokeObjectURL(audio.src);
            audio.play();
        } catch (err) {
            ui.showToast(err.message || 'TTS preview failed', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '\u25B6 Test';
        }
    });

    // Name/tagline changes (debounced save)
    container.querySelector('#pa-name')?.addEventListener('input', () => debouncedSave());
    container.querySelector('#pa-tagline')?.addEventListener('input', () => debouncedSave());

    // Provider change → update model dropdown
    const providerSelect = container.querySelector('#pa-s-llm_primary');
    if (providerSelect) {
        providerSelect.addEventListener('change', () => {
            updateModelSelector(providerSelect.value, '');
            debouncedSave();
        });
    }

    // Settings fields (debounced save)
    container.querySelectorAll('.pa-scroll select, .pa-scroll input, .pa-scroll textarea').forEach(el => {
        if (el.id === 'pa-s-llm_primary') return; // handled above
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
            if (el.id === 'pa-s-trim_color') applyTrimColor(el.value);
            debouncedSave();
        });
    });

    // Init slider fills + model selector
    container.querySelectorAll('.pa-field input[type="range"]').forEach(updateSliderFill);
    if (selectedData?.settings) {
        updateModelSelector(selectedData.settings.llm_primary || 'auto', selectedData.settings.llm_model || '');
    }
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
        llm_model: getSelectedModel(),
        trim_color: get('trim_color') || '#4a9eff',
        memory_scope: get('memory_scope') || 'default',
        goal_scope: get('goal_scope') || 'default',
        knowledge_scope: get('knowledge_scope') || 'default',
        people_scope: get('people_scope') || 'default',
        state_engine_enabled: selectedData?.settings?.state_engine_enabled || false,
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
