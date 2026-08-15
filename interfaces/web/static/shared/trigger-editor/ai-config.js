// trigger-editor/ai-config.js - Shared AI configuration section for all trigger types
// Persona, AI (prompt/toolset/provider/model), Chat, Voice, Mind (scopes), Execution Limits
import { fetchPrompts, fetchToolsets, fetchLLMProviders,
         fetchPersonas, fetchPersona } from '../continuity-api.js';
import { getInitData } from '../init-data.js';
import {
    renderScopeDropdowns,
    fetchScopeData,
    populateScopeOptions,
    readScopeSettingsFromDom
} from '../scope-dropdowns.js';

let _ttsVoicesCache = null;

/**
 * Fetch all data needed for AI config fields.
 * Scope data is fetched generically via the shared scope-dropdowns renderer,
 * driven by /api/init scope_declarations — adding a new plugin scope is
 * zero-touch for this file.
 */
export async function fetchAIConfigData() {
    let prompts = [], toolsets = [], providers = [], metadata = {};
    let personas = [];
    let scopeDeclarations = [], scopeData = {};

    try {
        // Grab init data first so we know which scope_declarations to fetch
        const init = await getInitData();
        scopeDeclarations = init?.scope_declarations || [];

        const [p, ts, llm, sd, per, ttsV] = await Promise.all([
            fetchPrompts(), fetchToolsets(), fetchLLMProviders(),
            fetchScopeData(scopeDeclarations),
            fetchPersonas(),
            fetch('/api/tts/voices').then(r => r.ok ? r.json() : null)
        ]);
        prompts = p || []; toolsets = ts || [];
        providers = llm.providers || []; metadata = llm.metadata || {};
        scopeData = sd || {};
        personas = per || [];
        _ttsVoicesCache = ttsV;
    } catch (e) { console.warn('AI config: failed to fetch options', e); }

    let vaultRefs = {};
    try {
        const init = await getInitData();
        vaultRefs = init?.prompts?.vault_refs || {};
    } catch { /* labels degrade to "(missing)" */ }

    return { prompts, toolsets, providers, metadata,
             scopeDeclarations, scopeData, vaultRefs,
             personas, voices: _ttsVoicesCache?.voices || [] };
}

/**
 * Render the AI config HTML sections (persona + accordions)
 * @param {Object} t - Existing task data (or {} for new)
 * @param {Object} data - From fetchAIConfigData()
 * @param {Object} opts - { isHeartbeat: bool }
 * @returns {string} HTML string
 */
export function renderAIConfig(t, data, opts = {}) {
    const { prompts, toolsets, providers, metadata,
            personas, voices } = data;
    // scopeDeclarations + scopeData are used by wireAIConfig (post-mount renderer);
    // renderAIConfig just drops a placeholder <div id="ed-scope-dropdowns"></div>
    const { isHeartbeat } = opts;

    // Synthetic option for a saved value that's no longer in its list (the
    // prompt/toolset/persona was renamed or deleted elsewhere). Show it as
    // "(missing)" instead of silently snapping to the first option — Save reads
    // .value blind, so a snap would overwrite the task's real binding.
    const missingOpt = (value, known) =>
        (value && !known.includes(value))
            ? `<option value="${_esc(value)}" selected>${_esc(value)} (missing)</option>`
            : '';

    const enabledProviders = providers.filter(p => p.enabled);
    const coreProvs = enabledProviders.filter(p => p.is_core);
    const customProvs = enabledProviders.filter(p => !p.is_core);
    let providerOpts = coreProvs
        .map(p => `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}</option>`)
        .join('');
    if (customProvs.length && coreProvs.length) {
        providerOpts += '<option disabled>\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500</option>';
    }
    providerOpts += customProvs
        .map(p => {
            const model = p.model ? ` (${p.model.split('/').pop()})` : '';
            return `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}${model}</option>`;
        }).join('');
    if (t.provider && t.provider !== 'auto' && !enabledProviders.some(p => p.key === t.provider)) {
        providerOpts = `<option value="${_esc(t.provider)}" selected>${_esc(t.provider)} (missing)</option>` + providerOpts;
    }

    let voiceOpts = voices.map(v =>
        `<option value="${v.voice_id}" ${t.voice === v.voice_id ? 'selected' : ''}>${v.name}${v.category ? ' (' + v.category + ')' : ''}</option>`
    ).join('');
    if (t.voice && !voices.some(v => v.voice_id === t.voice)) {
        voiceOpts = `<option value="${t.voice}" selected>${t.voice} (other provider)</option>` + voiceOpts;
    }

    return `
        <div id="ed-validation-notice" class="task-validation-notice" style="display:none;margin-top:12px"></div>

        <div class="sched-field" style="margin-top:16px">
            <label>\uD83D\uDC64 Persona <span class="help-tip" data-tip="Auto-fills prompt, voice, toolset, model, scopes, and more from a persona profile. You can still override individual settings below.">?</span></label>
            <select id="ed-persona">
                <option value="">None (manual settings)</option>
                ${missingOpt(t.persona, personas.map(p => p.name))}
                ${personas.map(p => `<option value="${p.name}" ${t.persona === p.name ? 'selected' : ''}>${p.name}${p.tagline ? ' \u2014 ' + p.tagline : ''}</option>`).join('')}
            </select>
        </div>

        <hr class="sched-divider">

        <details class="sched-accordion">
            <summary class="sched-acc-header">AI <span class="sched-preview" id="ed-ai-preview">${t.prompt && t.prompt !== 'default' ? _esc(t.prompt) : ''}</span></summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Prompt</label>
                        <select id="ed-prompt">
                            <option value="default">default</option>
                            ${(() => {
                                // Prompt-specific missing handler: a dangling
                                // name that's a REFERENCED vault entry reads
                                // "🗝 (vault)" — asleep, not broken.
                                if (!t.prompt || t.prompt === 'default' || prompts.some(p => p.name === t.prompt)) return '';
                                const label = (data.vaultRefs || {})[t.prompt]
                                    ? `${_esc(t.prompt)} \u{1F5DD} (vault)` : `${_esc(t.prompt)} (missing)`;
                                return `<option value="${_esc(t.prompt)}" selected>${label}</option>`;
                            })()}
                            ${prompts.map(p => `<option value="${p.name}" ${t.prompt === p.name ? 'selected' : ''}>${p.name}${p.vault ? ' \u{1F5DD}' : ''}</option>`).join('')}
                        </select>
                    </div>
                    <div class="sched-field">
                        <label>Toolset</label>
                        <select id="ed-toolset">
                            <option value="none" ${t.toolset === 'none' ? 'selected' : ''}>none</option>
                            <option value="default" ${t.toolset === 'default' ? 'selected' : ''}>default</option>
                            ${missingOpt(t.toolset, ['none', 'default', ...toolsets.map(ts => ts.name)])}
                            ${toolsets.map(ts => `<option value="${ts.name}" ${t.toolset === ts.name ? 'selected' : ''}>${ts.name}</option>`).join('')}
                        </select>
                    </div>
                </div>
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Provider</label>
                        <select id="ed-provider">
                            <option value="auto" ${t.provider === 'auto' || !t.provider ? 'selected' : ''}>Auto (default)</option>
                            ${providerOpts}
                        </select>
                    </div>
                    <div class="sched-field" id="ed-model-field" style="display:none">
                        <label>Model</label>
                        <select id="ed-model"><option value="">Provider default</option></select>
                    </div>
                    <div class="sched-field" id="ed-model-custom-field" style="display:none">
                        <label>Model</label>
                        <input type="text" id="ed-model-custom" value="${_esc(t.model || '')}" placeholder="Model name">
                    </div>
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="ed-privacy" ${t.privacy_required ? 'checked' : ''}> Private (local providers only) <span class="help-tip" data-tip="Force this task onto local providers only. Auto-checked when the pinned prompt requires privacy — the server keeps it on while the prompt demands it.">?</span></label>
                </div>
            </div></div>
        </details>

        <details class="sched-accordion">
            <summary class="sched-acc-header">Chat <span class="sched-preview" id="ed-chat-preview">${t.chat_target === '__locked__' ? '🔒 locked vault' : (t.chat_target ? _esc(t.chat_target) : 'No history')}</span></summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <div class="sched-field">
                    <label>Chat Name <span class="help-tip" data-tip="Run in a named chat (conversation saved). Leave blank for ephemeral background execution. '__locked__' means the target is a private chat sealed in the locked vault — saving keeps the real target; type a name to retarget.">?</span></label>
                    <input type="text" id="ed-chat" value="${_esc(t.chat_target || '')}" placeholder="Leave blank for ephemeral">
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="ed-datetime" ${t.inject_datetime ? 'checked' : ''}> Inject date/time</label>
                </div>
            </div></div>
        </details>

        <details class="sched-accordion">
            <summary class="sched-acc-header">Voice <span class="sched-preview" id="ed-voice-preview">${_voicePreviewText(t, isHeartbeat)}</span></summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <div class="sched-checkbox"${window.__managed ? ' style="display:none"' : ''}>
                    <label><input type="checkbox" id="ed-tts" ${t.tts_enabled !== false && !isHeartbeat ? 'checked' : ''}${isHeartbeat && t.tts_enabled ? ' checked' : ''}> Speak on server speakers</label>
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="ed-browser-tts" ${t.browser_tts ? 'checked' : ''}> Play in browser <span class="help-tip" data-tip="Send TTS audio to open browser tabs instead of server speakers. One tab claims and plays.">?</span></label>
                </div>
                <div class="sched-field">
                    <label>Voice <span class="help-tip" data-tip="TTS voice to use. Leave on default to use whatever voice is currently active.">?</span></label>
                    <select id="ed-voice">
                        <option value="">Default (current voice)</option>
                        ${voiceOpts}
                    </select>
                </div>
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Pitch</label>
                        <input type="number" id="ed-pitch" value="${t.pitch ?? ''}" min="0.5" max="2.0" step="0.05" placeholder="default" style="width:80px">
                    </div>
                    <div class="sched-field">
                        <label>Speed</label>
                        <input type="number" id="ed-speed" value="${t.speed ?? ''}" min="0.5" max="2.0" step="0.05" placeholder="default" style="width:80px">
                    </div>
                </div>
            </div></div>
        </details>

        <details class="sched-accordion">
            <summary class="sched-acc-header">Mind</summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <div id="ed-scope-dropdowns"></div>
            </div></div>
        </details>

        <details class="sched-accordion">
            <summary class="sched-acc-header">Execution Limits</summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <p class="text-muted" style="font-size:var(--font-xs);margin:0 0 10px">Override app defaults for this task. 0 = use global setting.</p>
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Context window <span class="help-tip" data-tip="Token limit for conversation history. 0 = app default. Set higher for long tasks needing more context.">?</span></label>
                        <div style="display:flex;align-items:center;gap:4px">
                            <input type="number" id="ed-context-limit" value="${t.context_limit || 0}" min="0" style="width:90px">
                            <span class="text-muted">tokens</span>
                        </div>
                    </div>
                </div>
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Max parallel tools <span class="help-tip" data-tip="Tools AI can call at once per response. 0 = app default.">?</span></label>
                        <input type="number" id="ed-max-parallel" value="${t.max_parallel_tools || 0}" min="0" style="width:60px">
                    </div>
                    <div class="sched-field">
                        <label>Max tool rounds <span class="help-tip" data-tip="Tool-result loops before forcing a final reply. 0 = app default.">?</span></label>
                        <input type="number" id="ed-max-rounds" value="${t.max_tool_rounds || 0}" min="0" style="width:60px">
                    </div>
                </div>
                <div class="sched-field-row">
                    <div class="sched-field">
                        <label>Max runs <span class="help-tip" data-tip="Auto-disable after N runs. 1 = one-shot task. 0 = unlimited.">?</span></label>
                        <div style="display:flex;align-items:center;gap:6px">
                            <input type="number" id="ed-max-runs" value="${t.max_runs || 0}" min="0" style="width:60px">
                            ${(t.max_runs || 0) > 0 ? `<span class="text-muted">${t.run_count || 0}/${t.max_runs} done</span>` : ''}
                        </div>
                    </div>
                    <div class="sched-field">
                        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
                            <input type="checkbox" id="ed-delete-after-run" ${t.delete_after_run ? 'checked' : ''}>
                            Delete after run <span class="help-tip" data-tip="Automatically delete this task after it runs once. For one-shot tasks that should leave no trace.">?</span>
                        </label>
                    </div>
                </div>
            </div></div>
        </details>`;
}

/**
 * Wire all AI config event listeners on the modal. Also mounts the shared
 * scope-dropdowns renderer into the #ed-scope-dropdowns placeholder that
 * renderAIConfig() left behind.
 *
 * @param {HTMLElement} modal - The editor modal element
 * @param {Object} t - Existing task data
 * @param {Object} data - From fetchAIConfigData()
 */
export async function wireAIConfig(modal, t, data) {
    const { providers, metadata, scopeDeclarations = [], scopeData = {} } = data;

    // ── Mount shared scope dropdowns into the Mind accordion ──
    const scopeContainer = modal.querySelector('#ed-scope-dropdowns');
    if (scopeContainer && scopeDeclarations.length) {
        // Get enabled plugins so the renderer can hide scopes for disabled plugins
        let enabledPlugins = new Set();
        try {
            const init = await getInitData();
            enabledPlugins = new Set(init?.plugins_config?.enabled || []);
        } catch (e) { /* fail-soft — all plugin scopes will be visible */ }

        const rendererOptions = {
            idPrefix: 'ed-',
            enabledPlugins,
            // Preserve the legacy bulk-create-across-mind-APIs UX: when the user
            // clicks "+" on any mind-domain scope, prompt for a name and create
            // that scope in all 4 mind APIs (memory/knowledge/people/goals) at once.
            // The renderer only shows "+" on declarations with a mind nav_target,
            // so plugin scopes won't get a "+" button (matching current behavior).
            onCreateScope: async (_scopeKey) => {
                const name = prompt('New scope name (lowercase, no spaces):');
                if (!name) return;
                const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
                if (!clean || clean.length > 32) { alert('Invalid name'); return; }
                const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
                const apis = ['/api/memory/scopes', '/api/knowledge/scopes',
                              '/api/knowledge/people/scopes', '/api/goals/scopes'];
                try {
                    const results = await Promise.allSettled(apis.map(url =>
                        fetch(url, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                            body: JSON.stringify({ name: clean })
                        })
                    ));
                    const anyOk = results.some(r => r.status === 'fulfilled' && r.value.ok);
                    if (!anyOk) {
                        const err = await results[0]?.value?.json?.().catch(() => ({})) || {};
                        alert(err.error || err.detail || 'Failed');
                        return;
                    }
                    // Add the new option to every mind-domain scope dropdown and
                    // select it in all of them — mirrors the legacy UX.
                    const mindDecls = scopeDeclarations.filter(d => d.nav_target?.startsWith('mind:'));
                    for (const decl of mindDecls) {
                        const sel = scopeContainer.querySelector(`#ed-${decl.key}-scope`);
                        if (sel && !sel.querySelector(`option[value="${clean}"]`)) {
                            const opt = document.createElement('option');
                            opt.value = clean;
                            opt.textContent = clean;
                            sel.appendChild(opt);
                        }
                        if (sel) sel.value = clean;
                    }
                } catch { alert('Failed to create scope'); }
            },
        };
        renderScopeDropdowns(scopeContainer, scopeDeclarations, t, rendererOptions);
        await populateScopeOptions(scopeContainer, scopeDeclarations, scopeData, t, rendererOptions);
    }

    // ── Persona auto-fill ──
    const personaSel = modal.querySelector('#ed-persona');
    personaSel?.addEventListener('change', async () => {
        const name = personaSel.value;
        const set = (id, val) => { const el = modal.querySelector(id); if (el && val != null) el.value = val; };

        if (!name) {
            // No persona — reset all scope dropdowns to 'none' dynamically
            for (const decl of scopeDeclarations) {
                const el = modal.querySelector(`#ed-${decl.key}-scope`);
                if (el) el.value = 'none';
            }
            return;
        }
        try {
            const persona = await fetchPersona(name);
            if (!persona?.settings) return;
            const s = persona.settings;
            set('#ed-prompt', s.prompt || 'default');
            set('#ed-toolset', s.toolset || 'none');
            set('#ed-voice', s.voice || '');
            set('#ed-pitch', s.pitch ?? '');
            set('#ed-speed', s.speed ?? '');
            // Dynamic scope auto-fill — iterate every registered scope and pull
            // the persona's value for it. Zero-touch when new plugin scopes land.
            for (const decl of scopeDeclarations) {
                const settingKey = `${decl.key}_scope`;
                set(`#ed-${decl.key}-scope`, s[settingKey] || 'none');
            }
            if (s.inject_datetime != null) modal.querySelector('#ed-datetime').checked = !!s.inject_datetime;
            if (s.llm_primary) {
                set('#ed-provider', s.llm_primary);
                updateModels();
                if (s.llm_model) setTimeout(() => set('#ed-model', s.llm_model), 50);
            }
            const aiPrev = modal.querySelector('#ed-ai-preview');
            if (aiPrev) aiPrev.textContent = s.prompt && s.prompt !== 'default' ? s.prompt : '';
            const voicePrev = modal.querySelector('#ed-voice-preview');
            if (voicePrev) voicePrev.textContent = s.voice || '';
            // Toolset just changed via auto-fill — re-run cross-field validation
            // so the amber notice updates without requiring a manual edit.
            modal.querySelector('#ed-toolset')?.dispatchEvent(new Event('change'));
        } catch (e) { console.warn('Failed to load persona:', e); }
    });

    // Provider -> model logic
    const providerSel = modal.querySelector('#ed-provider');
    const updateModels = () => {
        const key = providerSel.value;
        const modelField = modal.querySelector('#ed-model-field');
        const modelCustomField = modal.querySelector('#ed-model-custom-field');
        const modelSel = modal.querySelector('#ed-model');
        modelField.style.display = 'none';
        modelCustomField.style.display = 'none';
        modelSel.disabled = false;
        if (key === 'auto' || !key) return;
        const meta = metadata[key];
        const pConfig = providers.find(p => p.key === key);
        const isCore = pConfig?.is_core;
        if (isCore && meta?.model_options && Object.keys(meta.model_options).length > 0) {
            // Core provider: show model dropdown with options
            const defaultModel = pConfig?.model || '';
            const defaultLabel = defaultModel ? `Provider default (${meta.model_options[defaultModel] || defaultModel})` : 'Provider default';
            modelSel.innerHTML = `<option value="">${defaultLabel}</option>` +
                Object.entries(meta.model_options)
                    .map(([k, v]) => `<option value="${k}"${k === (t.model || '') ? ' selected' : ''}>${v}</option>`)
                    .join('');
            if (t.model && !meta.model_options[t.model]) {
                modelSel.innerHTML += `<option value="${t.model}" selected>${t.model}</option>`;
            }
            modelField.style.display = '';
        } else if (!isCore) {
            // Custom provider: model is baked in, show as disabled
            const model = pConfig?.model || '(default)';
            modelSel.innerHTML = `<option value="${pConfig?.model || ''}">${model}</option>`;
            modelSel.disabled = true;
            modelField.style.display = '';
        } else {
            modelCustomField.style.display = '';
        }
    };
    providerSel.addEventListener('change', updateModels);
    updateModels();

    // AI preview chip
    modal.querySelector('#ed-prompt')?.addEventListener('change', () => {
        const v = modal.querySelector('#ed-prompt').value;
        const el = modal.querySelector('#ed-ai-preview');
        if (el) el.textContent = v && v !== 'default' ? v : '';
    });

    // Voice preview chip
    const updateVoicePreview = () => {
        const el = modal.querySelector('#ed-voice-preview');
        if (!el) return;
        const browserTts = modal.querySelector('#ed-browser-tts')?.checked;
        if (browserTts) { el.textContent = 'Browser'; return; }
        const ttsOn = modal.querySelector('#ed-tts')?.checked;
        el.textContent = ttsOn ? (modal.querySelector('#ed-voice')?.value || 'Server') : 'No TTS';
    };
    modal.querySelector('#ed-voice')?.addEventListener('change', updateVoicePreview);
    modal.querySelector('#ed-tts')?.addEventListener('change', updateVoicePreview);
    modal.querySelector('#ed-browser-tts')?.addEventListener('change', updateVoicePreview);

    // Chat name preview
    modal.querySelector('#ed-chat')?.addEventListener('input', () => {
        const el = modal.querySelector('#ed-chat-preview');
        if (el) el.textContent = modal.querySelector('#ed-chat').value.trim() || 'No history';
    });

    // Scope "+" button handler is now wired via the shared renderer's onCreateScope
    // callback (see the scopeContainer block above). No separate querySelectorAll needed.

    // Cross-field validation: re-runs on toolset / context-limit change.
    // Only one rule today — context_limit too low for the selected toolset's
    // schema cost. Top-of-modal amber notice; never blocks save.
    const runValidation = () => _validateAIConfig(modal, data);
    modal.querySelector('#ed-toolset')?.addEventListener('change', runValidation);
    modal.querySelector('#ed-context-limit')?.addEventListener('input', runValidation);
    runValidation();
}

// ── Validation ──

// Rough estimate — a typical OpenAI-style tool schema runs 60-150 tokens
// depending on description length. 100/tool is the empirical mean across
// Sapphire's built-in toolsets. Used only for warn thresholds, not budgets.
const _TOKENS_PER_TOOL_ESTIMATE = 100;

function _validateAIConfig(modal, data) {
    const notice = modal.querySelector('#ed-validation-notice');
    if (!notice) return;

    const toolsetName = modal.querySelector('#ed-toolset')?.value || 'none';
    const ctxLimit = parseInt(modal.querySelector('#ed-context-limit')?.value) || 0;

    const issues = [];

    // Rule 1: context_limit too low for selected toolset.
    // ctxLimit=0 means "use global" — skip this rule, can't reason about it
    // here. (Server-side trim will handle it against config.CONTEXT_LIMIT.)
    if (ctxLimit > 0 && toolsetName && toolsetName !== 'none') {
        const ts = (data.toolsets || []).find(t => t.name === toolsetName);
        const toolCount = ts?.function_count || 0;
        const estCost = toolCount * _TOKENS_PER_TOOL_ESTIMATE;
        // Warn if schemas alone would consume >2/3 of the budget. The actual
        // overflow check on the backend uses real tiktoken counts, but for a
        // heads-up at modal time this estimate catches the obvious cases.
        if (toolCount > 5 && estCost * 1.5 > ctxLimit) {
            issues.push(
                `<strong>Context window may be too low</strong> &mdash; the ` +
                `<em>${toolsetName}</em> toolset has ~${toolCount} tools ` +
                `(~${estCost.toLocaleString()} schema tokens) which approaches ` +
                `or exceeds your ${ctxLimit.toLocaleString()}-token budget. ` +
                `The task will likely fail on first run. ` +
                `Raise context window or use a smaller toolset.`
            );
        }
    }

    if (issues.length === 0) {
        notice.style.display = 'none';
        notice.innerHTML = '';
    } else {
        notice.style.display = '';
        notice.innerHTML = issues.map(i => `<div>${i}</div>`).join('');
    }
}

/**
 * Read AI config values from the modal
 * @param {HTMLElement} modal - The editor modal element
 * @returns {Object} AI config fields for the task data
 */
export function readAIConfig(modal) {
    const modelField = modal.querySelector('#ed-model-field');
    const modelSel = modal.querySelector('#ed-model');
    const modelCustom = modal.querySelector('#ed-model-custom');
    let modelValue = '';
    if (modelField?.style.display !== 'none') modelValue = modelSel?.value || '';
    else if (modal.querySelector('#ed-model-custom-field')?.style.display !== 'none') modelValue = modelCustom?.value?.trim() || '';

    const pitchVal = modal.querySelector('#ed-pitch')?.value;
    const speedVal = modal.querySelector('#ed-speed')?.value;

    // Read all scope dropdown values by DOM discovery — no declarations list needed.
    // Each scope field was rendered with a data-scope-key attribute; this helper
    // iterates them and returns { memory_scope: ..., email_scope: ..., ... }.
    // Trigger editor tasks default missing-value to 'none' (disabled), not 'default'.
    const scopeContainer = modal.querySelector('#ed-scope-dropdowns');
    const scopeFields = readScopeSettingsFromDom(scopeContainer, { missingValue: 'none' });

    return {
        persona: modal.querySelector('#ed-persona')?.value || '',
        prompt: modal.querySelector('#ed-prompt')?.value || 'default',
        toolset: modal.querySelector('#ed-toolset')?.value || 'none',
        provider: modal.querySelector('#ed-provider')?.value || 'auto',
        privacy_required: modal.querySelector('#ed-privacy')?.checked || false,
        model: modelValue,
        chat_target: modal.querySelector('#ed-chat')?.value?.trim() || '',
        inject_datetime: modal.querySelector('#ed-datetime')?.checked || false,
        voice: modal.querySelector('#ed-voice')?.value || '',
        pitch: pitchVal ? parseFloat(pitchVal) : null,
        speed: speedVal ? parseFloat(speedVal) : null,
        tts_enabled: modal.querySelector('#ed-tts')?.checked || false,
        browser_tts: modal.querySelector('#ed-browser-tts')?.checked || false,
        ...scopeFields,
        context_limit: parseInt(modal.querySelector('#ed-context-limit')?.value) || 0,
        max_parallel_tools: parseInt(modal.querySelector('#ed-max-parallel')?.value) || 0,
        max_tool_rounds: parseInt(modal.querySelector('#ed-max-rounds')?.value) || 0,
        max_runs: parseInt(modal.querySelector('#ed-max-runs')?.value) || 0,
        delete_after_run: modal.querySelector('#ed-delete-after-run')?.checked || false,
    };
}

// ── Private helpers ──

function _voicePreviewText(t, isHeartbeat) {
    const serverOn = isHeartbeat ? !!t.tts_enabled : t.tts_enabled !== false;
    if (t.browser_tts) return 'Browser';
    if (serverOn) return t.voice || 'Server';
    return 'No TTS';
}

// _renderScopeField deleted in Phase 2d — scope fields are now rendered by the
// shared scope-dropdowns.js module driven by /api/init scope_declarations.

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
