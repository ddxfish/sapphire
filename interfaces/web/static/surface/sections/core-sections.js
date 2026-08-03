// surface/sections/core-sections.js — shared per-chat-settings sidebar
// sections (tmp/chat-surface-plan.md: modes share organs, the primitive holds
// the scars once). First consumer: game mode's sidebar. Chat mode's sidebar
// migrates here opportunistically later; story mode arrives already served.
//
// A section: { key, title, icon, bare?, open?, html(), async init(el, ctx) }.
// bare: true → host renders it as plain fields (chat's top-of-sidebar block);
// otherwise it belongs in an accordion.
// ctx: {
//   settings: {...},            // the session chat's settings snapshot
//   save(patch): Promise,       // merge into that chat's settings (PUT by name)
// }
// Markup reuses chat's .sb-field classes so the look inherits from style.css;
// hooks are gs- classes scoped to the section element — no document ids.

import { getInitData } from '/static/shared/init-data.js';

const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

// ------------------------------------------------------------------ brain

export const brainSection = {
    key: 'brain', title: 'Brain', icon: '\u{1F9E0}', bare: true,
    html() {
        return `
            <div class="sb-field">
                <label>provider</label>
                <select class="gs-llm-primary"><option value="auto">Auto</option></select>
            </div>
            <div class="sb-field gs-model-group" style="display:none">
                <label>model</label>
                <select class="gs-llm-model"></select>
            </div>
            <div class="sb-field gs-model-custom-group" style="display:none">
                <label>model</label>
                <input type="text" class="gs-llm-model-custom" placeholder="Model name">
            </div>
            <div class="gs-resolved" title="Resolved brain for her seat"></div>`;
    },
    async init(el, ctx) {
        let providers = [], metadata = {};
        try {
            const r = await fetch('/api/llm/providers');
            if (r.ok) { const d = await r.json(); providers = d.providers || []; metadata = d.metadata || {}; }
        } catch (e) { /* dropdown stays Auto-only */ }

        const sel = el.querySelector('.gs-llm-primary');
        const core = providers.filter(p => p.enabled && p.is_core);
        const custom = providers.filter(p => p.enabled && !p.is_core);
        let opts = '<option value="auto">Auto</option><option value="none">None</option>';
        opts += core.map(p => `<option value="${esc(p.key)}">${esc(p.display_name)}${p.is_local ? ' \u{1F3E0}' : ' ☁️'}</option>`).join('');
        if (custom.length) {
            opts += '<option disabled>────────</option>';
            opts += custom.map(p => {
                const model = p.model ? ` (${esc(p.model.split('/').pop())})` : '';
                return `<option value="${esc(p.key)}">${esc(p.display_name)}${model}${p.is_local ? ' \u{1F3E0}' : ' ☁️'}</option>`;
            }).join('');
        }
        sel.innerHTML = opts;
        sel.value = ctx.settings.llm_primary || 'auto';
        if (sel.value !== (ctx.settings.llm_primary || 'auto')) sel.value = 'auto';

        const modelGroup = el.querySelector('.gs-model-group');
        const customGroup = el.querySelector('.gs-model-custom-group');
        const modelSel = el.querySelector('.gs-llm-model');
        const customInput = el.querySelector('.gs-llm-model-custom');

        const refreshModel = (providerKey, currentModel) => {
            modelGroup.style.display = 'none';
            customGroup.style.display = 'none';
            if (providerKey === 'auto' || providerKey === 'none' || !providerKey) return;
            const meta = metadata[providerKey];
            const conf = providers.find(p => p.key === providerKey);
            if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
                const def = conf?.model || '';
                const defLabel = def ? `Default (${esc(meta.model_options[def] || def)})` : 'Default';
                modelSel.innerHTML = `<option value="">${defLabel}</option>` +
                    Object.entries(meta.model_options).map(([k, v]) =>
                        `<option value="${esc(k)}"${k === currentModel ? ' selected' : ''}>${esc(v)}</option>`).join('');
                if (currentModel && !meta.model_options[currentModel]) {
                    modelSel.innerHTML += `<option value="${esc(currentModel)}" selected>${esc(currentModel)}</option>`;
                }
                modelGroup.style.display = '';
            } else {
                customInput.value = currentModel || '';
                customGroup.style.display = '';
            }
        };
        refreshModel(sel.value, ctx.settings.llm_model || '');

        sel.addEventListener('change', () => {
            refreshModel(sel.value, '');
            ctx.save({ llm_primary: sel.value, llm_model: '' });
        });
        modelSel.addEventListener('change', () => ctx.save({ llm_model: modelSel.value }));
        customInput.addEventListener('change', () => ctx.save({ llm_model: customInput.value.trim() }));
    },
};

// ------------------------------------------------------------------ voice

export const voiceSection = {
    key: 'voice', title: 'TTS (Voice)', icon: '\u{1F50A}',
    html() {
        return `
            <div class="sb-field">
                <label>voice</label>
                <select class="gs-voice"></select>
            </div>
            <div class="sb-field sb-field-stack">
                <label>Pitch: <span class="gs-pitch-val"></span></label>
                <input type="range" class="gs-pitch" min="0.5" max="1.5" step="0.02">
            </div>
            <div class="sb-field sb-field-stack">
                <label>Speed: <span class="gs-speed-val"></span></label>
                <input type="range" class="gs-speed" min="0.5" max="2.5" step="0.1">
            </div>`;
    },
    async init(el, ctx) {
        let voices = [], defVoice = '';
        try {
            const r = await fetch('/api/tts/voices');
            if (r.ok) { const d = await r.json(); voices = d.voices || []; defVoice = d.default_voice || ''; }
        } catch (e) { /* empty dropdown */ }

        const sel = el.querySelector('.gs-voice');
        sel.innerHTML = voices.map(v => {
            const id = v.voice_id || v.id || v.name;
            return `<option value="${esc(id)}">${esc(v.name || id)}${id === defVoice ? ' (default)' : ''}</option>`;
        }).join('');
        if (ctx.settings.voice) sel.value = ctx.settings.voice;
        sel.addEventListener('change', () => ctx.save({ voice: sel.value }));

        const bindRange = (cls, valCls, key, fallback) => {
            const range = el.querySelector(cls);
            const label = el.querySelector(valCls);
            range.value = ctx.settings[key] ?? fallback;
            label.textContent = range.value;
            range.addEventListener('input', () => { label.textContent = range.value; });
            range.addEventListener('change', () => ctx.save({ [key]: parseFloat(range.value) }));
        };
        bindRange('.gs-pitch', '.gs-pitch-val', 'pitch', 0.98);
        bindRange('.gs-speed', '.gs-speed-val', 'speed', 1.3);
    },
};

// ------------------------------------------------------------------ prompt

export const promptSection = {
    key: 'prompt', title: 'Prompt', icon: '\u{1F464}', bare: true,
    html() {
        return `
            <div class="sb-field">
                <label>prompt</label>
                <select class="gs-prompt"></select>
            </div>`;
    },
    async init(el, ctx) {
        const init = await getInitData().catch(() => null);
        const sel = el.querySelector('.gs-prompt');
        const prompts = init?.prompts?.list || [];
        sel.innerHTML = prompts.map(p =>
            `<option value="${esc(p.name)}">${esc(p.name.charAt(0).toUpperCase() + p.name.slice(1))}</option>`).join('');
        sel.value = ctx.settings.prompt || 'sapphire';
        sel.addEventListener('change', () => ctx.save({ prompt: sel.value }));
    },
};

// ------------------------------------------------------------------ system prompt extras

export const sysPromptSection = {
    key: 'sysprompt', title: 'System Prompt', icon: '\u{1F4DD}',
    html() {
        return `
            <div class="sb-field sb-field-stack">
                <label>Custom Context</label>
                <textarea class="gs-custom-context" rows="3" placeholder="Injected into system prompt..."></textarea>
            </div>
            <div class="sb-field sb-field-stack">
                <label>Ghost Message</label>
                <textarea class="gs-ghost-context" rows="3" placeholder="Injected as a per-turn ghost message (empty = none)..."></textarea>
            </div>`;
    },
    async init(el, ctx) {
        const bindText = (cls, key) => {
            const ta = el.querySelector(cls);
            ta.value = ctx.settings[key] || '';
            ta.addEventListener('change', () => ctx.save({ [key]: ta.value }));
        };
        bindText('.gs-custom-context', 'custom_context');
        bindText('.gs-ghost-context', 'ghost_context');
    },
};

// ------------------------------------------------------------------ toolset

export const toolsetSection = {
    key: 'toolset', title: 'Toolset', icon: '\u{1F527}', bare: true,
    html() {
        return `
            <div class="sb-field">
                <label>toolset</label>
                <select class="gs-toolset"></select>
            </div>`;
    },
    async init(el, ctx) {
        const init = await getInitData().catch(() => null);
        const sel = el.querySelector('.gs-toolset');
        const sets = (init?.toolsets?.list || []).filter(t => t.type !== 'module');
        sel.innerHTML = sets.map(t =>
            `<option value="${esc(t.name)}">${esc(t.name)} (${t.function_count})</option>`).join('');
        sel.value = ctx.settings.toolset || 'all';
        sel.addEventListener('change', () => ctx.save({ toolset: sel.value }));
    },
};

// Order matters: bare fields render in chat's top-of-sidebar order
// (prompt, toolset, provider+model), accordions follow.
export const coreSections = [promptSection, toolsetSection, brainSection, voiceSection, sysPromptSection];
