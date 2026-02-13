// views/chat.js - Chat view module with settings sidebar
import * as api from '../api.js';
import * as ui from '../ui.js';
import { getElements } from '../core/state.js';
import { updateScene, updateSendButtonLLM } from '../features/scene.js';
import { applyTrimColor } from '../features/chat-settings.js';
import { getInitData } from '../shared/init-data.js';

let sidebarLoaded = false;
let saveTimer = null;
let llmProviders = [];
let llmMetadata = {};

const SAVE_DEBOUNCE = 500;

export default {
    init(container) {
        // Sidebar collapse toggle
        const toggle = container.querySelector('#chat-sidebar-toggle');
        if (toggle) toggle.addEventListener('click', () => toggleSidebar(container));

        // Restore sidebar state
        const collapsed = localStorage.getItem('sapphire-chat-sidebar') === 'collapsed';
        const sidebar = container.querySelector('.chat-sidebar');
        if (sidebar && collapsed) sidebar.classList.add('collapsed');

        // Accordion headers in sidebar
        container.querySelectorAll('.sidebar-accordion-header').forEach(header => {
            header.addEventListener('click', () => {
                const content = header.nextElementSibling;
                const open = header.classList.toggle('open');
                content.style.display = open ? 'block' : 'none';
            });
        });

        // Auto-save on any sidebar input change
        container.querySelectorAll('.chat-sidebar select, .chat-sidebar input').forEach(el => {
            const event = el.type === 'range' ? 'input' : 'change';
            el.addEventListener(event, () => {
                // Immediate visual feedback for specific elements
                if (el.id === 'sb-pitch') {
                    const label = container.querySelector('#sb-pitch-val');
                    if (label) label.textContent = el.value;
                    updateSliderFill(el);
                }
                if (el.id === 'sb-speed') {
                    const label = container.querySelector('#sb-speed-val');
                    if (label) label.textContent = el.value;
                    updateSliderFill(el);
                }
                if (el.id === 'sb-llm-primary') {
                    updateModelSelector(container, el.value, '');
                }
                if (el.id === 'sb-trim-color') {
                    el.dataset.cleared = 'false';
                    applyTrimColor(el.value);
                }
                debouncedSave(container);
            });
        });

        // Trim reset button
        const resetTrim = container.querySelector('#sb-reset-trim');
        if (resetTrim) {
            resetTrim.addEventListener('click', () => {
                const input = container.querySelector('#sb-trim-color');
                const globalTrim = localStorage.getItem('sapphire-trim') || '#4a9eff';
                input.value = globalTrim;
                input.dataset.cleared = 'true';
                applyTrimColor('');
                debouncedSave(container);
            });
        }

        // New memory scope button
        const newScope = container.querySelector('#sb-new-scope');
        if (newScope) {
            newScope.addEventListener('click', async () => {
                const name = prompt('New memory slot name (lowercase, no spaces):');
                if (!name) return;
                const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
                if (!clean || clean.length > 32) {
                    ui.showToast('Invalid name', 'error');
                    return;
                }
                try {
                    const res = await fetch('/api/memory/scopes', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: clean })
                    });
                    if (res.ok) {
                        const sel = container.querySelector('#sb-memory-scope');
                        const opt = document.createElement('option');
                        opt.value = clean;
                        opt.textContent = `${clean} (0)`;
                        sel.appendChild(opt);
                        sel.value = clean;
                        debouncedSave(container);
                        ui.showToast(`Created: ${clean}`, 'success');
                    }
                } catch (e) {
                    ui.showToast('Failed', 'error');
                }
            });
        }

        // Save as defaults button
        const defaultsBtn = container.querySelector('#sb-save-defaults');
        if (defaultsBtn) {
            defaultsBtn.addEventListener('click', async () => {
                if (!confirm('Save current settings as defaults for new chats?')) return;
                try {
                    const settings = collectSettings(container);
                    const res = await fetch('/api/settings/chat-defaults', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(settings)
                    });
                    if (res.ok) ui.showToast('Saved as defaults', 'success');
                } catch (e) {
                    ui.showToast('Failed', 'error');
                }
            });
        }

        // State engine buttons
        setupStateButtons(container);
    },

    async show() {
        await loadSidebar();
    },

    hide() {}
};

function toggleSidebar(container) {
    const sidebar = container.querySelector('.chat-sidebar');
    if (!sidebar) return;
    const collapsed = sidebar.classList.toggle('collapsed');
    localStorage.setItem('sapphire-chat-sidebar', collapsed ? 'collapsed' : 'expanded');
}

async function loadSidebar() {
    const container = document.getElementById('view-chat');
    if (!container) return;

    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    try {
        const [settingsResp, initData, llmResp, scopesResp, presetsResp] = await Promise.allSettled([
            api.getChatSettings(chatName),
            getInitData(),
            fetch('/api/llm/providers').then(r => r.ok ? r.json() : null),
            fetch('/api/memory/scopes').then(r => r.ok ? r.json() : null),
            fetch('/api/state/presets').then(r => r.ok ? r.json() : null)
        ]);

        const settings = settingsResp.status === 'fulfilled' ? settingsResp.value.settings : {};
        const init = initData.status === 'fulfilled' ? initData.value : null;
        const llmData = llmResp.status === 'fulfilled' ? llmResp.value : null;
        const scopesData = scopesResp.status === 'fulfilled' ? scopesResp.value : null;
        const presetsData = presetsResp.status === 'fulfilled' ? presetsResp.value : null;

        // Populate prompt dropdown
        const promptSel = container.querySelector('#sb-prompt');
        if (promptSel && init?.prompts?.list) {
            promptSel.innerHTML = init.prompts.list.map(p =>
                `<option value="${p.name}">${p.name.charAt(0).toUpperCase() + p.name.slice(1)}</option>`
            ).join('');
            promptSel.value = settings.prompt || 'sapphire';
        }

        // Populate toolset dropdown
        const toolsetSel = container.querySelector('#sb-toolset');
        if (toolsetSel && init?.toolsets?.list) {
            toolsetSel.innerHTML = init.toolsets.list.map(t =>
                `<option value="${t.name}">${t.name} (${t.function_count})</option>`
            ).join('');
            toolsetSel.value = settings.toolset || settings.ability || 'default';
        }

        // Populate LLM dropdown
        if (llmData) {
            llmProviders = llmData.providers || [];
            llmMetadata = llmData.metadata || {};
            const llmSel = container.querySelector('#sb-llm-primary');
            if (llmSel) {
                llmSel.innerHTML = '<option value="auto">Auto</option><option value="none">None</option>' +
                    llmProviders.filter(p => p.enabled).map(p =>
                        `<option value="${p.key}">${p.display_name}${p.is_local ? ' \uD83C\uDFE0' : ' \u2601\uFE0F'}</option>`
                    ).join('');
                llmSel.value = settings.llm_primary || 'auto';
                updateModelSelector(container, settings.llm_primary || 'auto', settings.llm_model || '');
            }
        }

        // Populate memory scope dropdown
        const scopeSel = container.querySelector('#sb-memory-scope');
        if (scopeSel && scopesData) {
            scopeSel.innerHTML = '<option value="none">None</option>' +
                (scopesData.scopes || []).map(s =>
                    `<option value="${s.name}">${s.name} (${s.count})</option>`
                ).join('');
            scopeSel.value = settings.memory_scope || 'default';
        }

        // Populate state preset dropdown
        const presetSel = container.querySelector('#sb-state-preset');
        if (presetSel && presetsData) {
            presetSel.innerHTML = '<option value="">None</option>' +
                (presetsData.presets || []).map(p =>
                    `<option value="${p.name}">${p.display_name} (${p.key_count} keys)</option>`
                ).join('');
            presetSel.value = settings.state_preset || '';
        }

        // Set remaining form values
        setVal(container, '#sb-voice', settings.voice || 'af_heart');
        setVal(container, '#sb-pitch', settings.pitch || 0.94);
        setVal(container, '#sb-speed', settings.speed || 1.3);
        setChecked(container, '#sb-spice', settings.spice_enabled !== false);
        setVal(container, '#sb-spice-turns', settings.spice_turns || 3);
        setChecked(container, '#sb-datetime', settings.inject_datetime === true);
        setVal(container, '#sb-custom-context', settings.custom_context || '');
        setChecked(container, '#sb-state-enabled', settings.state_engine_enabled === true);
        setChecked(container, '#sb-state-story', settings.state_story_in_prompt !== false);
        setChecked(container, '#sb-state-vars', settings.state_vars_in_prompt === true);

        // Trim color
        const trimInput = container.querySelector('#sb-trim-color');
        if (trimInput) {
            if (settings.trim_color) {
                trimInput.value = settings.trim_color;
                trimInput.dataset.cleared = 'false';
            } else {
                trimInput.value = localStorage.getItem('sapphire-trim') || '#4a9eff';
                trimInput.dataset.cleared = 'true';
            }
        }

        // Update labels
        const pitchLabel = container.querySelector('#sb-pitch-val');
        if (pitchLabel) pitchLabel.textContent = settings.pitch || 0.94;
        const speedLabel = container.querySelector('#sb-speed-val');
        if (speedLabel) speedLabel.textContent = settings.speed || 1.3;

        // Update slider fills
        const pitchSlider = container.querySelector('#sb-pitch');
        const speedSlider = container.querySelector('#sb-speed');
        if (pitchSlider) updateSliderFill(pitchSlider);
        if (speedSlider) updateSliderFill(speedSlider);

        sidebarLoaded = true;
    } catch (e) {
        console.warn('Failed to load sidebar:', e);
    }
}

function debouncedSave(container) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveSettings(container), SAVE_DEBOUNCE);
}

async function saveSettings(container) {
    const chatSelect = getElements().chatSelect || document.getElementById('chat-select');
    const chatName = chatSelect?.value;
    if (!chatName) return;

    const settings = collectSettings(container);

    try {
        await api.updateChatSettings(chatName, settings);
        updateSendButtonLLM(settings.llm_primary, settings.llm_model);
    } catch (e) {
        console.warn('Auto-save failed:', e);
    }
}

function collectSettings(container) {
    const trimInput = container.querySelector('#sb-trim-color');
    const trimColor = trimInput?.dataset.cleared === 'true' ? '' : (trimInput?.value || '');

    return {
        prompt: getVal(container, '#sb-prompt'),
        toolset: getVal(container, '#sb-toolset'),
        voice: getVal(container, '#sb-voice'),
        pitch: parseFloat(getVal(container, '#sb-pitch')) || 0.94,
        speed: parseFloat(getVal(container, '#sb-speed')) || 1.3,
        spice_enabled: getChecked(container, '#sb-spice'),
        spice_turns: parseInt(getVal(container, '#sb-spice-turns')) || 3,
        inject_datetime: getChecked(container, '#sb-datetime'),
        custom_context: getVal(container, '#sb-custom-context'),
        llm_primary: getVal(container, '#sb-llm-primary') || 'auto',
        llm_model: getSelectedModel(container),
        trim_color: trimColor,
        memory_scope: getVal(container, '#sb-memory-scope') || 'default',
        state_engine_enabled: getChecked(container, '#sb-state-enabled'),
        state_preset: getVal(container, '#sb-state-preset') || null,
        state_story_in_prompt: getChecked(container, '#sb-state-story'),
        state_vars_in_prompt: getChecked(container, '#sb-state-vars')
    };
}

function updateModelSelector(container, providerKey, currentModel) {
    const group = container.querySelector('#sb-model-group');
    const customGroup = container.querySelector('#sb-model-custom-group');
    const select = container.querySelector('#sb-llm-model');
    const custom = container.querySelector('#sb-llm-model-custom');

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

function getSelectedModel(container) {
    const provider = getVal(container, '#sb-llm-primary');
    if (provider === 'auto' || provider === 'none') return '';

    const group = container.querySelector('#sb-model-group');
    if (group && group.style.display !== 'none') {
        return getVal(container, '#sb-llm-model') || '';
    }

    const customGroup = container.querySelector('#sb-model-custom-group');
    if (customGroup && customGroup.style.display !== 'none') {
        return (container.querySelector('#sb-llm-model-custom')?.value || '').trim();
    }
    return '';
}

function setupStateButtons(container) {
    container.querySelector('#sb-state-view')?.addEventListener('click', async () => {
        const chatName = (getElements().chatSelect || document.getElementById('chat-select'))?.value;
        if (!chatName) return;
        try {
            const resp = await fetch(`/api/state/${encodeURIComponent(chatName)}`);
            if (resp.ok) {
                const data = await resp.json();
                const str = Object.entries(data.state || {})
                    .map(([k, v]) => `${v.label || k}: ${JSON.stringify(v.value)}`).join('\n');
                alert(`State:\n\n${str || '(empty)'}`);
            }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });

    container.querySelector('#sb-state-reset')?.addEventListener('click', async () => {
        const chatName = (getElements().chatSelect || document.getElementById('chat-select'))?.value;
        if (!chatName || !confirm('Reset state?')) return;
        const preset = getVal(container, '#sb-state-preset');
        try {
            await fetch(`/api/state/${encodeURIComponent(chatName)}/reset`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset: preset || null })
            });
            ui.showToast('State reset', 'success');
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}

// Helpers
function getVal(c, sel) { return c.querySelector(sel)?.value || ''; }
function setVal(c, sel, v) { const el = c.querySelector(sel); if (el) el.value = v; }
function getChecked(c, sel) { return c.querySelector(sel)?.checked || false; }
function setChecked(c, sel, v) { const el = c.querySelector(sel); if (el) el.checked = v; }

function updateSliderFill(slider) {
    const min = parseFloat(slider.min) || 0;
    const max = parseFloat(slider.max) || 100;
    const pct = ((parseFloat(slider.value) - min) / (max - min)) * 100;
    const styles = getComputedStyle(document.documentElement);
    const fill = styles.getPropertyValue('--trim').trim() || '#4a9eff';
    const bg = styles.getPropertyValue('--bg-tertiary').trim() || '#2a2a2a';
    slider.style.background = `linear-gradient(to right, ${fill} ${pct}%, ${bg} ${pct}%)`;
}
