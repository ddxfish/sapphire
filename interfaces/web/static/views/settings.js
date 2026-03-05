// views/settings.js - Settings view core
// Tab handlers live in settings-tabs/*.js — this file stays lean.
import * as api from '../shared/settings-api.js';
import * as ui from '../ui.js';

// Tab registry
import appearanceTab from './settings-tabs/appearance.js';
import audioTab from './settings-tabs/audio.js';
import ttsTab from './settings-tabs/tts.js';
import sttTab from './settings-tabs/stt.js';
import embeddingTab from './settings-tabs/embedding.js';
import llmTab from './settings-tabs/llm.js';
import toolsTab from './settings-tabs/tools.js';
import networkTab from './settings-tabs/network.js';
import wakewordTab from './settings-tabs/wakeword.js';
import pluginsTab from './settings-tabs/plugins.js';

import backupTab from './settings-tabs/backup.js';
import systemTab from './settings-tabs/system.js';

import { getRegisteredTabs } from '../shared/plugin-registry.js';

const STATIC_TABS = [appearanceTab, audioTab, ttsTab, sttTab, embeddingTab, llmTab, toolsTab, networkTab, wakewordTab, pluginsTab, backupTab, systemTab];

let container = null;
let activeTab = 'appearance';
let settings = {};
let help = {};
let overrides = [];
let pendingChanges = {};
let wakewordModels = [];
let availableThemes = ['dark'];
let avatarPaths = { user: null, assistant: null }; // kept for plugin compat
let providerMeta = {};
let dynamicTabs = [];
let pluginList = [];
let lockedPlugins = [];
let mobileMenuCleanup = null;

export default {
    init(el) { container = el; },
    async show() {
        await loadData();
        render();
    },
    hide() {}
};

// ── Data Loading ──

async function loadData() {
    try {
        const [settingsData, helpData] = await Promise.all([
            api.getAllSettings(),
            api.getSettingsHelp().catch(() => ({ help: {} }))
        ]);
        settings = settingsData.settings || {};
        overrides = settingsData.user_overrides || [];
        help = helpData.help || {};

        await Promise.all([loadThemes(), loadWakewordModels(), loadProviderMeta(), loadPluginList()]);
        // custom-tools tab removed — plugin manifest settings is the one path now
    } catch (e) {
        console.warn('Settings load failed:', e);
    }
}

async function loadPluginList() {
    try {
        const res = await fetch('/api/webui/plugins');
        if (res.ok) {
            const d = await res.json();
            pluginList = d.plugins || [];
            lockedPlugins = d.locked || [];
            // Auto-load settings tabs for enabled plugins that have a web UI
            for (const p of pluginList) {
                if (p.enabled && p.settingsUI) await loadPluginTab(p.name, p.settingsUI).catch(() => {});
            }
        }
    } catch {}
}

async function loadPluginTab(name, source) {
    try {
        if (source === 'manifest') {
            const plugin = pluginList.find(p => p.name === name);
            if (!plugin?.settings_schema) return;
            const { renderSettingsForm, readSettingsForm } = await import('/static/shared/plugin-settings-renderer.js');
            const { registerPluginSettings } = await import('/static/shared/plugin-registry.js');
            const pluginsAPI = (await import('/static/shared/plugins-api.js')).default;
            registerPluginSettings({
                id: name,
                name: plugin.title || name,
                icon: plugin.icon || '\u2699\uFE0F',
                helpText: `${plugin.title || name} settings`,
                render: (box, settings) => renderSettingsForm(box, plugin.settings_schema, settings),
                load: () => pluginsAPI.getSettings(name),
                save: (s) => pluginsAPI.saveSettings(name, s),
                getSettings: (box) => readSettingsForm(box, plugin.settings_schema),
            });
            syncDynamicTabs();
            return;
        }
        const _v = window.__v ? `?v=${window.__v}` : '';
        const url = source === 'plugin'
            ? `/plugin-web/${name}/index.js${_v}`
            : `/static/core-ui/${name}/index.js${_v}`;
        const mod = await import(url);
        const plugin = mod.default;
        if (plugin?.init) {
            const dummy = document.createElement('div');
            plugin.init(dummy);
        }
        syncDynamicTabs();
    } catch {
        // Plugin has no settings tab — that's fine
    }
}

function syncDynamicTabs() {
    const registered = getRegisteredTabs();
    dynamicTabs = registered.map(reg => ({
        id: reg.id,
        name: reg.name,
        icon: reg.icon,
        description: reg.helpText || `${reg.name} plugin settings`,
        isPlugin: true,
        _reg: reg,
        render(ctx) {
            return `<div class="plugin-tab-container" id="ptab-${reg.id}"></div>`;
        },
        async attachListeners(ctx, el) {
            const box = el.querySelector(`#ptab-${reg.id}`);
            if (!box) return;
            try {
                const settings = await reg.load();
                reg.render(box, settings);
            } catch (e) {
                box.innerHTML = `<p style="color:var(--error)">Failed to load: ${e.message}</p>`;
            }
        }
    }));
}

function getAllTabs() {
    // Insert dynamic tabs between plugins and system
    const idx = STATIC_TABS.findIndex(t => t.id === 'system');
    const before = STATIC_TABS.slice(0, idx);
    const after = STATIC_TABS.slice(idx);
    return [...before, ...dynamicTabs, ...after];
}

async function loadThemes() {
    try {
        const res = await fetch('/static/themes/themes.json');
        if (res.ok) { const d = await res.json(); availableThemes = d.themes || ['dark']; }
    } catch {}
}

async function loadWakewordModels() {
    try {
        const res = await fetch('/api/settings/wakeword-models');
        if (res.ok) { const d = await res.json(); wakewordModels = d.all || []; }
    } catch {}
}

async function loadProviderMeta() {
    try {
        const res = await fetch('/api/llm/providers');
        if (res.ok) { const d = await res.json(); providerMeta = d.metadata || {}; }
    } catch {}
}


// ── Pending Change Preservation ──

function flushCurrentInputs() {
    const el = container?.querySelector('#settings-content');
    if (!el) return;
    el.querySelectorAll('input[data-key], select[data-key], textarea[data-key]').forEach(input => {
        const key = input.dataset.key;
        if (!key || key === 'undefined') return;
        const value = input.type === 'checkbox' ? input.checked : input.value;
        const original = settings[key];
        if (input.type === 'checkbox') {
            if (value !== !!original) pendingChanges[key] = value;
        } else {
            const originalStr = original === undefined ? ''
                : (typeof original === 'object' ? JSON.stringify(original, null, 2) : String(original));
            if (value !== originalStr) pendingChanges[key] = value;
        }
    });
}

// ── Rendering ──

function render() {
    if (!container) return;
    const meta = getTabMeta();

    const tabs = getAllTabs();
    container.innerHTML = `
        <div class="settings-view">
            <div class="settings-sidebar">
                ${tabs.map(t => `
                    <button class="settings-nav-item${t.id === activeTab ? ' active' : ''}${t.isPlugin ? ' plugin-tab' : ''}" data-tab="${t.id}">
                        <span class="settings-nav-icon">${t.icon}</span>
                        <span class="settings-nav-label">${t.name}</span>
                    </button>
                `).join('')}
            </div>
            <div class="settings-main">
                <div class="settings-mobile-nav">
                    <button class="settings-mobile-trigger" id="settings-mobile-trigger">
                        <span class="settings-mobile-icon">${meta.icon}</span>
                        <span class="settings-mobile-label">${meta.name}</span>
                        <span class="settings-mobile-arrow">&#x25BE;</span>
                    </button>
                    <div class="settings-mobile-menu hidden" id="settings-mobile-menu">
                        ${tabs.map(t => `
                            <button class="settings-mobile-option${t.id === activeTab ? ' active' : ''}" data-tab="${t.id}">
                                <span class="settings-mobile-opt-icon">${t.icon}</span>
                                <span>${t.name}</span>
                            </button>
                        `).join('')}
                    </div>
                </div>
                <div class="settings-header">
                    <div class="view-header-left">
                        <h2 id="stab-title">${meta.icon} ${meta.name}</h2>
                        <span class="view-subtitle" id="stab-desc">${meta.description || ''}</span>
                    </div>
                    <div class="view-header-actions">
                        <button class="btn-sm" id="settings-reload">Reload</button>
                        <button class="btn-primary" id="settings-save">Save Changes</button>
                    </div>
                </div>
                <div class="settings-content view-scroll" id="settings-content"></div>
            </div>
        </div>
    `;

    renderTabContent();
    bindShellEvents();
}

function getTabMeta() {
    return getAllTabs().find(t => t.id === activeTab) || STATIC_TABS[0];
}

function renderTabContent() {
    const el = container?.querySelector('#settings-content');
    if (!el) return;

    const tab = getTabMeta();
    const ctx = createCtx();
    el.innerHTML = `<div class="settings-tab-body">${tab.render(ctx)}</div>`;

    // Generic listeners (input changes, resets, help, accordion)
    attachGenericListeners(el);

    // Tab-specific listeners
    if (tab.attachListeners) tab.attachListeners(ctx, el);
}

// ── Context Object (passed to tab handlers) ──

function createCtx() {
    return {
        settings, help, overrides, pendingChanges,
        wakewordModels, availableThemes, avatarPaths, providerMeta,
        pluginList, lockedPlugins,
        renderFields, renderAccordion, renderInput, formatLabel,
        attachAccordionListeners,
        markChanged(key, value) { pendingChanges[key] = value; },
        async refreshTab() {
            await loadData();
            renderTabContent();
        },
        loadPluginTab,
        syncDynamicTabs,
        refreshSidebar() { render(); }
    };
}

// ── Generic Field Renderer ──

function renderFields(keys) {
    const rows = keys.map(key => {
        const value = settings[key];
        if (value === undefined) return '';

        const isOverridden = overrides.includes(key);
        const inputType = api.getInputType(value);
        const h = help[key];

        const isFullWidth = key.endsWith('_ENABLED');
        const isModified = key in pendingChanges;
        return `
            <div class="setting-row${isOverridden ? ' overridden' : ''}${isFullWidth ? ' full-width' : ''}${isModified ? ' modified' : ''}" data-key="${key}">
                <div class="setting-label">
                    <div class="setting-label-row">
                        <label>${formatLabel(key)}</label>
                        ${h ? `<span class="help-icon" data-help-key="${key}" title="Details">?</span>` : ''}
                        ${isOverridden ? '<span class="override-badge">Custom</span>' : ''}
                    </div>
                    ${h?.short ? `<div class="setting-help">${h.short}</div>` : ''}
                </div>
                <div class="setting-input">
                    ${renderInput(key, value, inputType)}
                </div>
                <div class="setting-actions">
                    ${isOverridden ? `<button class="btn-icon reset-btn" data-reset-key="${key}" title="Reset to default">\u21BA</button>` : ''}
                </div>
            </div>
        `;
    }).join('');
    return `<div class="settings-grid">${rows}</div>`;
}

function renderInput(key, value, type) {
    // Apply pending (unsaved) changes over persisted values
    if (key in pendingChanges) value = pendingChanges[key];

    const id = `setting-${key}`;

    // Special dropdowns
    if (key === 'WAKEWORD_MODEL' && wakewordModels.length) {
        return `<select id="${id}" data-key="${key}">
            ${wakewordModels.map(m => `<option value="${m}" ${value === m ? 'selected' : ''}>${m.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>`).join('')}
        </select>`;
    }
    if (key === 'WAKEWORD_FRAMEWORK') {
        return `<select id="${id}" data-key="${key}">
            ${['onnx', 'tflite'].map(f => `<option value="${f}" ${value === f ? 'selected' : ''}>${f.toUpperCase()}</option>`).join('')}
        </select>`;
    }
    if (key === 'STT_FIREWORKS_MODEL') {
        const models = [
            ['whisper-v3-turbo', 'Whisper V3 Turbo (Fast)'],
            ['whisper-v3', 'Whisper V3 (Quality)']
        ];
        return `<select id="${id}" data-key="${key}">
            ${models.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select>`;
    }
    if (key === 'TTS_ELEVENLABS_MODEL') {
        const models = [
            ['eleven_flash_v2_5', 'Flash v2.5 (Fast, 50% cheaper)'],
            ['eleven_multilingual_v2', 'Multilingual v2 (Highest quality)'],
            ['eleven_turbo_v2_5', 'Turbo v2.5 (Balanced)']
        ];
        return `<select id="${id}" data-key="${key}">
            ${models.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select>`;
    }
    if (key === 'TTS_ELEVENLABS_VOICE_ID') {
        return `<div class="voice-selector-row">
            <input type="text" id="${id}" data-key="${key}" value="${escapeAttr(String(value))}" placeholder="Click Browse to select a voice">
            <button class="btn-small browse-voices-btn" data-target="${id}">Browse</button>
        </div>`;
    }
    if (key.endsWith('_API_KEY')) {
        return `<input type="password" id="${id}" data-key="${key}" value="${escapeAttr(String(value))}" autocomplete="off">`;
    }
    if (type === 'checkbox') {
        return `<label class="setting-toggle">
            <input type="checkbox" id="${id}" data-key="${key}" ${value ? 'checked' : ''}>
            <span>${value ? 'Enabled' : 'Disabled'}</span>
        </label>`;
    }
    if (type === 'json') {
        const content = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
        return `<textarea id="${id}" data-key="${key}" class="setting-json" rows="4">${content}</textarea>`;
    }
    if (type === 'number') {
        return `<input type="number" id="${id}" data-key="${key}" value="${value}" step="any">`;
    }
    return `<input type="text" id="${id}" data-key="${key}" value="${escapeAttr(String(value))}">`;
}

function renderAccordion(id, keys, title = 'Advanced Settings') {
    return `
        <div class="settings-accordion" data-accordion="${id}">
            <div class="settings-accordion-header collapsed" data-accordion-toggle="${id}">
                <span class="accordion-arrow">\u25B6</span>
                <h4>${title}</h4>
            </div>
            <div class="settings-accordion-body collapsed" data-accordion-body="${id}">
                ${renderFields(keys)}
            </div>
        </div>
    `;
}

// ── Events ──

function bindShellEvents() {
    // Sidebar nav
    container.querySelector('.settings-sidebar')?.addEventListener('click', e => {
        const btn = e.target.closest('.settings-nav-item');
        if (!btn) return;
        flushCurrentInputs();
        activeTab = btn.dataset.tab;
        container.querySelectorAll('.settings-nav-item').forEach(b =>
            b.classList.toggle('active', b.dataset.tab === activeTab));

        const meta = getTabMeta();
        const title = container.querySelector('#stab-title');
        const desc = container.querySelector('#stab-desc');
        if (title) title.textContent = `${meta.icon} ${meta.name}`;
        if (desc) desc.textContent = meta.description || '';

        renderTabContent();
    });

    // Mobile tab dropdown
    if (mobileMenuCleanup) mobileMenuCleanup();
    const mobileTrigger = container.querySelector('#settings-mobile-trigger');
    const mobileMenu = container.querySelector('#settings-mobile-menu');
    if (mobileTrigger && mobileMenu) {
        mobileTrigger.addEventListener('click', () => {
            mobileMenu.classList.toggle('hidden');
            mobileTrigger.querySelector('.settings-mobile-arrow').textContent =
                mobileMenu.classList.contains('hidden') ? '\u25BE' : '\u25B4';
        });

        mobileMenu.addEventListener('click', e => {
            const opt = e.target.closest('.settings-mobile-option');
            if (!opt) return;
            flushCurrentInputs();
            activeTab = opt.dataset.tab;

            // Sync desktop sidebar
            container.querySelectorAll('.settings-nav-item').forEach(b =>
                b.classList.toggle('active', b.dataset.tab === activeTab));

            // Update mobile trigger + header
            const meta = getTabMeta();
            mobileTrigger.querySelector('.settings-mobile-icon').textContent = meta.icon;
            mobileTrigger.querySelector('.settings-mobile-label').textContent = meta.name;
            mobileTrigger.querySelector('.settings-mobile-arrow').textContent = '\u25BE';

            const title = container.querySelector('#stab-title');
            const desc = container.querySelector('#stab-desc');
            if (title) title.textContent = `${meta.icon} ${meta.name}`;
            if (desc) desc.textContent = meta.description || '';

            // Update menu active states
            mobileMenu.querySelectorAll('.settings-mobile-option').forEach(o =>
                o.classList.toggle('active', o.dataset.tab === activeTab));

            mobileMenu.classList.add('hidden');
            renderTabContent();
        });

        const outsideHandler = e => {
            if (!mobileMenu.classList.contains('hidden') && !e.target.closest('.settings-mobile-nav')) {
                mobileMenu.classList.add('hidden');
                const arrow = mobileTrigger.querySelector('.settings-mobile-arrow');
                if (arrow) arrow.textContent = '\u25BE';
            }
        };
        document.addEventListener('click', outsideHandler);
        mobileMenuCleanup = () => document.removeEventListener('click', outsideHandler);
    }

    container.querySelector('#settings-save')?.addEventListener('click', saveChanges);

    container.querySelector('#settings-reload')?.addEventListener('click', async () => {
        try {
            await api.reloadSettings();
            ui.showToast('Reloaded from disk', 'success');
            await loadData();
            renderTabContent();
        } catch { ui.showToast('Reload failed', 'error'); }
    });
}

function attachGenericListeners(el) {
    // Prevent stacking — these delegate on a stable parent
    if (el._genericBound) { attachAccordionListeners(el); return; }
    el._genericBound = true;

    // Input changes → pendingChanges
    el.addEventListener('change', e => {
        const key = e.target.dataset.key;
        if (!key || key === 'undefined') return;
        const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
        pendingChanges[key] = value;

        const row = e.target.closest('.setting-row');
        if (row) row.classList.add('modified');

        // Update toggle label
        if (e.target.type === 'checkbox') {
            const span = e.target.parentElement?.querySelector('span');
            if (span) span.textContent = e.target.checked ? 'Enabled' : 'Disabled';
        }
    });

    // Reset + Help clicks
    el.addEventListener('click', async e => {
        const resetBtn = e.target.closest('[data-reset-key]');
        if (resetBtn) {
            const key = resetBtn.dataset.resetKey;
            if (!confirm(`Reset "${formatLabel(key)}" to default?`)) return;
            try {
                await api.deleteSetting(key);
                ui.showToast(`Reset ${formatLabel(key)}`, 'success');
                delete pendingChanges[key];
                await loadData();
                renderTabContent();
            } catch { ui.showToast('Reset failed', 'error'); }
            return;
        }

        const helpBtn = e.target.closest('[data-help-key]');
        if (helpBtn) showHelpPopup(helpBtn.dataset.helpKey);

        // Browse ElevenLabs voices
        const browseBtn = e.target.closest('.browse-voices-btn');
        if (browseBtn) {
            browseBtn.disabled = true;
            browseBtn.textContent = 'Loading...';
            try {
                // Pass API key from the input field (may not be saved yet)
                const keyInput = el.querySelector('#setting-TTS_ELEVENLABS_API_KEY');
                const apiKey = keyInput?.value?.trim() || pendingChanges['TTS_ELEVENLABS_API_KEY'] || '';
                const res = await fetch('/api/tts/voices', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ api_key: apiKey })
                });
                const data = await res.json();
                const voices = data.voices || [];
                if (!voices.length) {
                    ui.showToast('No voices found. Check your ElevenLabs API key.', 'warning');
                    return;
                }
                const targetId = browseBtn.dataset.target;
                const input = el.querySelector(`#${targetId}`);
                _showVoicePicker(voices, input, el);
            } catch (err) {
                ui.showToast('Failed to fetch voices', 'error');
            } finally {
                browseBtn.disabled = false;
                browseBtn.textContent = 'Browse';
            }
        }
    });

    attachAccordionListeners(el);
}

function attachAccordionListeners(el) {
    el.querySelectorAll('[data-accordion-toggle]').forEach(header => {
        header.addEventListener('click', () => {
            const id = header.dataset.accordionToggle;
            const body = el.querySelector(`[data-accordion-body="${id}"]`);
            const arrow = header.querySelector('.accordion-arrow');
            header.classList.toggle('collapsed');
            if (body) body.classList.toggle('collapsed');
            if (arrow) arrow.style.transform = header.classList.contains('collapsed') ? '' : 'rotate(90deg)';
        });
    });
}

// ── Save ──

async function saveChanges() {
    // Plugin tabs have their own save flow
    const tab = getTabMeta();
    if (tab.isPlugin && tab._reg) {
        const reg = tab._reg;
        const box = container?.querySelector(`#ptab-${reg.id}`);
        if (box && reg.getSettings && reg.save) {
            const saveBtn = container?.querySelector('#settings-save');
            if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }
            try {
                const s = reg.getSettings(box);
                await reg.save(s);
                ui.showToast(`${reg.name} settings saved`, 'success');
            } catch (e) {
                ui.showToast(`Save failed: ${e.message}`, 'error');
            } finally {
                if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
            }
        }
        return;
    }

    const valid = {};
    for (const [key, value] of Object.entries(pendingChanges)) {
        if (key && key !== 'undefined') valid[key] = value;
    }

    if (!Object.keys(valid).length) {
        ui.showToast('No changes to save', 'info');
        return;
    }

    const saveBtn = container?.querySelector('#settings-save');
    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }

    try {
        const parsed = {};
        for (const [key, value] of Object.entries(valid)) {
            parsed[key] = api.parseValue(value, settings[key]);
        }

        const result = await api.updateSettingsBatch(parsed);
        await api.reloadSettings();
        ui.showToast(`Saved ${Object.keys(parsed).length} settings`, 'success');

        if (result.restart_required) {
            const keys = result.restart_keys || [];
            ui.showToast(`Restart required for: ${keys.join(', ') || 'some settings'}`, 'warning');
        }

        pendingChanges = {};
        await loadData();
        renderTabContent();
    } catch (e) {
        ui.showToast('Save failed: ' + e.message, 'error');
    } finally {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
    }
}

// ── Help Popup ──

function showHelpPopup(key) {
    const h = help[key];
    if (!h) return;

    const popup = document.createElement('div');
    popup.className = 'sched-editor-overlay';
    popup.innerHTML = `
        <div style="background:var(--bg-secondary);border-radius:var(--radius-lg);padding:20px;max-width:500px;width:90%">
            <div style="display:flex;justify-content:space-between;margin-bottom:12px">
                <h3 style="margin:0">${formatLabel(key)}</h3>
                <button class="btn-icon" id="help-close">&times;</button>
            </div>
            <p style="line-height:1.5;color:var(--text)">${h.long || h.short || ''}</p>
            ${h.short && h.long ? `<p style="margin-top:12px;font-size:var(--font-sm);color:var(--text-muted)"><strong>Summary:</strong> ${h.short}</p>` : ''}
        </div>
    `;
    document.body.appendChild(popup);
    popup.addEventListener('click', e => { if (e.target === popup) popup.remove(); });
    popup.querySelector('#help-close')?.addEventListener('click', () => popup.remove());
}

// ── Voice Picker ──

function _showVoicePicker(voices, targetInput, parentEl) {
    const popup = document.createElement('div');
    popup.className = 'sched-editor-overlay';
    const rows = voices.map(v =>
        `<div class="voice-option" data-voice-id="${v.voice_id}" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border-subtle);display:flex;justify-content:space-between;align-items:center">
            <div>
                <strong>${v.name}</strong>
                <span style="font-size:0.8em;color:var(--text-muted);margin-left:8px">${v.category || ''}</span>
            </div>
            <span style="font-size:0.75em;color:var(--text-muted);font-family:monospace">${v.voice_id.slice(0, 12)}...</span>
        </div>`
    ).join('');

    popup.innerHTML = `
        <div style="background:var(--bg-secondary);border-radius:var(--radius-lg);padding:20px;max-width:500px;width:90%;max-height:70vh;display:flex;flex-direction:column">
            <div style="display:flex;justify-content:space-between;margin-bottom:12px">
                <h3 style="margin:0">Select Voice</h3>
                <button class="btn-icon" id="voice-close">&times;</button>
            </div>
            <div style="overflow-y:auto;border:1px solid var(--border-subtle);border-radius:var(--radius-md)">${rows}</div>
        </div>
    `;
    document.body.appendChild(popup);
    popup.addEventListener('click', e => {
        if (e.target === popup) { popup.remove(); return; }
        const opt = e.target.closest('.voice-option');
        if (opt) {
            const id = opt.dataset.voiceId;
            if (targetInput) {
                targetInput.value = id;
                targetInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
            popup.remove();
        }
    });
    popup.querySelector('#voice-close')?.addEventListener('click', () => popup.remove());
}

// ── Helpers ──

function formatLabel(key) {
    return key.replace(/_/g, ' ').split(' ')
        .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

function escapeAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
