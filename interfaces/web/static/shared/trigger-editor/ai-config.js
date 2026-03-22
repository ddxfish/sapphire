// trigger-editor/ai-config.js - Shared AI configuration section for all trigger types
// Persona, AI (prompt/toolset/provider/model), Chat, Voice, Mind (scopes), Execution Limits
import { fetchPrompts, fetchToolsets, fetchLLMProviders,
         fetchMemoryScopes, fetchKnowledgeScopes, fetchPeopleScopes, fetchGoalScopes, fetchEmailAccounts,
         fetchPersonas, fetchPersona } from '../continuity-api.js';

let _ttsVoicesCache = null;

/**
 * Fetch all data needed for AI config fields
 * @returns {Object} { prompts, toolsets, providers, metadata, scopes, personas, voices }
 */
export async function fetchAIConfigData() {
    let prompts = [], toolsets = [], providers = [], metadata = {};
    let memoryScopes = [], knowledgeScopes = [], peopleScopes = [], goalScopes = [], emailAccounts = [];
    let personas = [];

    try {
        const [p, ts, llm, ms, ks, ps, gs, ea, per, ttsV] = await Promise.all([
            fetchPrompts(), fetchToolsets(), fetchLLMProviders(),
            fetchMemoryScopes(), fetchKnowledgeScopes(), fetchPeopleScopes(), fetchGoalScopes(),
            fetchEmailAccounts(), fetchPersonas(),
            fetch('/api/tts/voices').then(r => r.ok ? r.json() : null)
        ]);
        prompts = p || []; toolsets = ts || [];
        providers = llm.providers || []; metadata = llm.metadata || {};
        memoryScopes = ms || []; knowledgeScopes = ks || [];
        peopleScopes = ps || []; goalScopes = gs || [];
        emailAccounts = ea || [];
        personas = per || [];
        _ttsVoicesCache = ttsV;
    } catch (e) { console.warn('AI config: failed to fetch options', e); }

    return { prompts, toolsets, providers, metadata,
             memoryScopes, knowledgeScopes, peopleScopes, goalScopes, emailAccounts,
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
            memoryScopes, knowledgeScopes, peopleScopes, goalScopes, emailAccounts,
            personas, voices } = data;
    const { isHeartbeat } = opts;

    const providerOpts = providers
        .filter(p => p.enabled)
        .map(p => `<option value="${p.key}" ${t.provider === p.key ? 'selected' : ''}>${p.display_name}${p.is_local ? ' \uD83C\uDFE0' : ' \u2601\uFE0F'}</option>`)
        .join('');

    let voiceOpts = voices.map(v =>
        `<option value="${v.voice_id}" ${t.voice === v.voice_id ? 'selected' : ''}>${v.name}${v.category ? ' (' + v.category + ')' : ''}</option>`
    ).join('');
    if (t.voice && !voices.some(v => v.voice_id === t.voice)) {
        voiceOpts = `<option value="${t.voice}" selected>${t.voice} (other provider)</option>` + voiceOpts;
    }

    return `
        <div class="sched-field" style="margin-top:16px">
            <label>\uD83D\uDC64 Persona <span class="help-tip" data-tip="Auto-fills prompt, voice, toolset, model, scopes, and more from a persona profile. You can still override individual settings below.">?</span></label>
            <select id="ed-persona">
                <option value="">None (manual settings)</option>
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
                            ${prompts.map(p => `<option value="${p.name}" ${t.prompt === p.name ? 'selected' : ''}>${p.name}</option>`).join('')}
                        </select>
                    </div>
                    <div class="sched-field">
                        <label>Toolset</label>
                        <select id="ed-toolset">
                            <option value="none" ${t.toolset === 'none' ? 'selected' : ''}>none</option>
                            <option value="default" ${t.toolset === 'default' ? 'selected' : ''}>default</option>
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
            </div></div>
        </details>

        <details class="sched-accordion">
            <summary class="sched-acc-header">Chat <span class="sched-preview" id="ed-chat-preview">${t.chat_target ? _esc(t.chat_target) : 'No history'}</span></summary>
            <div class="sched-acc-body"><div class="sched-acc-inner">
                <div class="sched-field">
                    <label>Chat Name <span class="help-tip" data-tip="Run in a named chat (conversation saved). Leave blank for ephemeral background execution.">?</span></label>
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
                ${_renderScopeField('Memory', 'ed-memory', t.memory_scope, memoryScopes, '/api/memory/scopes')}
                ${_renderScopeField('Knowledge', 'ed-knowledge', t.knowledge_scope, knowledgeScopes, '/api/knowledge/scopes')}
                ${_renderScopeField('People', 'ed-people', t.people_scope, peopleScopes, '/api/knowledge/people/scopes')}
                ${_renderScopeField('Goals', 'ed-goals', t.goal_scope, goalScopes, '/api/goals/scopes')}
                ${_renderScopeField('Email', 'ed-email', t.email_scope, emailAccounts.map(a => ({name: a.scope, count: null})), null)}
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
            </div></div>
        </details>`;
}

/**
 * Wire all AI config event listeners on the modal
 * @param {HTMLElement} modal - The editor modal element
 * @param {Object} t - Existing task data
 * @param {Object} data - From fetchAIConfigData()
 */
export function wireAIConfig(modal, t, data) {
    const { providers, metadata } = data;

    // Persona auto-fill
    const personaSel = modal.querySelector('#ed-persona');
    personaSel?.addEventListener('change', async () => {
        const name = personaSel.value;
        if (!name) {
            const set = (id, val) => { const el = modal.querySelector(id); if (el) el.value = val; };
            set('#ed-memory', 'none');
            set('#ed-knowledge', 'none');
            set('#ed-people', 'none');
            set('#ed-goals', 'none');
            return;
        }
        try {
            const persona = await fetchPersona(name);
            if (!persona?.settings) return;
            const s = persona.settings;
            const set = (id, val) => { const el = modal.querySelector(id); if (el && val != null) el.value = val; };
            set('#ed-prompt', s.prompt || 'default');
            set('#ed-toolset', s.toolset || 'none');
            set('#ed-voice', s.voice || '');
            set('#ed-pitch', s.pitch ?? '');
            set('#ed-speed', s.speed ?? '');
            set('#ed-memory', s.memory_scope || 'none');
            set('#ed-knowledge', s.knowledge_scope || 'none');
            set('#ed-people', s.people_scope || 'none');
            set('#ed-goals', s.goal_scope || 'none');
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
        if (key === 'auto' || !key) return;
        const meta = metadata[key];
        const pConfig = providers.find(p => p.key === key);
        if (meta?.model_options && Object.keys(meta.model_options).length > 0) {
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
        } else if (key === 'other' || key === 'lmstudio') {
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

    // Scope "+" buttons
    modal.querySelectorAll('.sched-add-scope').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = prompt('New scope name (lowercase, no spaces):');
            if (!name) return;
            const clean = name.trim().toLowerCase().replace(/[^a-z0-9_]/g, '');
            if (!clean || clean.length > 32) { alert('Invalid name'); return; }
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const apis = ['/api/memory/scopes', '/api/knowledge/scopes', '/api/knowledge/people/scopes', '/api/goals/scopes'];
            try {
                const results = await Promise.allSettled(apis.map(url =>
                    fetch(url, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                        body: JSON.stringify({ name: clean })
                    })
                ));
                const anyOk = results.some(r => r.status === 'fulfilled' && r.value.ok);
                if (anyOk) {
                    modal.querySelectorAll('.sched-add-scope').forEach(b => {
                        const sel = b.previousElementSibling;
                        if (sel && !sel.querySelector(`option[value="${clean}"]`)) {
                            const opt = document.createElement('option');
                            opt.value = clean;
                            opt.textContent = clean;
                            sel.appendChild(opt);
                        }
                    });
                    const sel = btn.previousElementSibling;
                    if (sel) sel.value = clean;
                } else {
                    const err = await results[0]?.value?.json?.().catch(() => ({})) || {};
                    alert(err.error || err.detail || 'Failed');
                }
            } catch { alert('Failed to create scope'); }
        });
    });
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

    return {
        persona: modal.querySelector('#ed-persona')?.value || '',
        prompt: modal.querySelector('#ed-prompt')?.value || 'default',
        toolset: modal.querySelector('#ed-toolset')?.value || 'none',
        provider: modal.querySelector('#ed-provider')?.value || 'auto',
        model: modelValue,
        chat_target: modal.querySelector('#ed-chat')?.value?.trim() || '',
        inject_datetime: modal.querySelector('#ed-datetime')?.checked || false,
        voice: modal.querySelector('#ed-voice')?.value || '',
        pitch: pitchVal ? parseFloat(pitchVal) : null,
        speed: speedVal ? parseFloat(speedVal) : null,
        tts_enabled: modal.querySelector('#ed-tts')?.checked || false,
        browser_tts: modal.querySelector('#ed-browser-tts')?.checked || false,
        memory_scope: modal.querySelector('#ed-memory')?.value || 'none',
        knowledge_scope: modal.querySelector('#ed-knowledge')?.value || 'none',
        people_scope: modal.querySelector('#ed-people')?.value || 'none',
        goal_scope: modal.querySelector('#ed-goals')?.value || 'none',
        email_scope: modal.querySelector('#ed-email')?.value || 'default',
        context_limit: parseInt(modal.querySelector('#ed-context-limit')?.value) || 0,
        max_parallel_tools: parseInt(modal.querySelector('#ed-max-parallel')?.value) || 0,
        max_tool_rounds: parseInt(modal.querySelector('#ed-max-rounds')?.value) || 0,
    };
}

// ── Private helpers ──

function _voicePreviewText(t, isHeartbeat) {
    const serverOn = isHeartbeat ? !!t.tts_enabled : t.tts_enabled !== false;
    if (t.browser_tts) return 'Browser';
    if (serverOn) return t.voice || 'Server';
    return 'No TTS';
}

function _renderScopeField(label, id, currentValue, scopes, apiUrl) {
    const opts = scopes.map(s => {
        const name = typeof s === 'string' ? s : s.name;
        const count = typeof s === 'object' && s.count != null ? ` (${s.count})` : '';
        return `<option value="${name}" ${currentValue === name ? 'selected' : ''}>${name}${count}</option>`;
    }).join('');
    const addBtn = apiUrl ? `<button type="button" class="btn-sm sched-add-scope" data-api="${apiUrl}" title="New scope">+</button>` : '';
    return `
        <div class="sched-field">
            <label>${label}</label>
            <div style="display:flex;gap:8px">
                <select id="${id}" style="flex:1">
                    <option value="none" ${!currentValue || currentValue === 'none' ? 'selected' : ''}>None</option>
                    <option value="default" ${currentValue === 'default' ? 'selected' : ''}>default</option>
                    ${opts}
                </select>
                ${addBtn}
            </div>
        </div>`;
}

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
