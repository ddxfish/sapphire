// trigger-realtime.js — the purpose-built Realtime rule editor.
//
// A Realtime rule is stored as a daemon task (type:'daemon') with a realtime:true
// source, so the existing gating/routing/A1 machinery applies unchanged. This
// modal presents it as what it IS — a live-session switch — instead of the
// generic daemon form: Source · Sub-source · Callers · where-it-runs · greeting.
// It DROPS the free AI-config block; behavior comes from the chat (saved mode) or
// seeds the throwaway chat (per-caller mode). Tools default to none, on purpose.
// Uses PROMPT (not persona — a persona drags a toolset with it; capability stays
// explicit) and exposes Mind scopes so a caller with tools reaches an ISOLATED
// scope, never the owner's default.
import { fetchAIConfigData } from './trigger-editor/ai-config.js';
import { fetchEventSources, createTask, updateTask, deleteTask } from './continuity-api.js';
import { fetchChatList, createChat } from '../api.js';
import { getInitData } from './init-data.js';
import { renderScopeDropdowns, fetchScopeData, populateScopeOptions, readScopeSettingsFromDom } from './scope-dropdowns.js';
import { showToast } from './toast.js';

const RT_EMOJIS = ['📞', '⚡', '📡', '🎙️', '☎️', '🗣️', '🤖', '🔴', '📻', '🎧', '💬', '🌐'];
const RADROW = 'display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin:2px 0 8px';
const RADLBL = 'display:inline-flex;align-items:center;gap:6px;white-space:nowrap;cursor:pointer';

function _esc(s) { const d = document.createElement('div'); d.textContent = s ?? ''; return d.innerHTML; }

// A realtime source's sub-source = its primary dynamic-select task_field
// (Twilio: 'account' → the number). label + dynamic endpoint come from there.
function _subSourceField(source) {
    return (source?.task_fields || []).find(f => f.type === 'select' && f.dynamic) || null;
}

export async function openRealtimeEditor(task, refresh) {
    document.querySelector('.sched-editor-overlay')?.remove();

    const isEdit = !!task;
    const t = task || {};
    const tc = t.trigger_config || {};

    // Data
    const [aiData, allSources, chats, init] = await Promise.all([
        fetchAIConfigData(),
        fetchEventSources(),
        fetchChatList().then(d => d?.chats || d || []).catch(() => []),
        getInitData().catch(() => ({})),
    ]);
    const sources = allSources.filter(s => s && s.realtime);
    const { toolsets = [], providers = [], prompts = [] } = aiData;
    const scopeDeclarations = init?.scope_declarations || [];
    const enabledPlugins = new Set(init?.plugins_config?.enabled || []);
    const scopeData = scopeDeclarations.length ? await fetchScopeData(scopeDeclarations).catch(() => ({})) : {};

    // Initial state
    const curSource = tc.source || (sources[0]?.name || '');
    const filterCaller = (tc.filter && tc.filter.caller) || '';
    const callersMode = filterCaller ? 'only' : 'anyone';
    const ephemeral = !!tc.ephemeral;
    const chatNames = (chats || []).map(c => (typeof c === 'string' ? c : c.name)).filter(Boolean);

    const promptOpts = (cur) => `<option value="default" ${(!cur || cur === 'default') ? 'selected' : ''}>default</option>` +
        prompts.map(p => `<option value="${_esc(p.name)}" ${cur === p.name ? 'selected' : ''}>${_esc(p.name)}</option>`).join('');
    const toolsetOpts = (cur) => ['none', 'default', ...toolsets.map(ts => ts.name)]
        .filter((v, i, a) => a.indexOf(v) === i)
        .map(n => `<option value="${_esc(n)}" ${cur === n ? 'selected' : ''}>${_esc(n)}</option>`).join('');
    const providerOpts = (cur) => `<option value="auto" ${(!cur || cur === 'auto') ? 'selected' : ''}>Auto (default)</option>` +
        providers.filter(p => p.enabled).map(p => `<option value="${_esc(p.key)}" ${cur === p.key ? 'selected' : ''}>${_esc(p.display_name || p.key)}</option>`).join('');
    const sourceOpts = sources.map(s => `<option value="${_esc(s.name)}" ${curSource === s.name ? 'selected' : ''}>${_esc(s.label || s.name)}</option>`).join('');

    const emoji = t.emoji || '📞';

    const modal = document.createElement('div');
    modal.className = 'sched-editor-overlay';
    modal.innerHTML = `
        <div class="sched-editor">
            <div class="sched-editor-header">
                <div class="sched-hb-emoji-wrap" id="rt-emoji-wrap">
                    <span class="sched-hb-emoji-btn" id="rt-emoji-btn" title="Pick emoji">${emoji}</span>
                </div>
                <h3>${isEdit ? 'Edit' : 'New'} Realtime</h3>
                <div style="flex:1"></div>
                ${isEdit ? `<button class="btn-sm danger" id="rt-delete" style="margin-right:8px">Delete</button>` : ''}
                <button class="btn-icon" data-action="close">&times;</button>
            </div>
            <div class="sched-editor-body">
                <p class="sched-editor-blurb">A live inbound session — a caller, a room, a body — held open in real time. Behavior comes from the chat it runs in.</p>

                <div class="sched-field">
                    <label>Name</label>
                    <input type="text" id="rt-name" value="${_esc(t.name || '')}" placeholder="Support line">
                </div>
                <div class="sched-field">
                    <label>Source</label>
                    <select id="rt-source">${sourceOpts || '<option value="">No realtime sources installed</option>'}</select>
                </div>
                <div class="sched-field" id="rt-subsource-field">
                    <label id="rt-subsource-label">Endpoint</label>
                    <select id="rt-subsource"></select>
                </div>
                <div class="sched-field">
                    <label>Callers</label>
                    <div style="${RADROW}">
                        <label style="${RADLBL}"><input type="radio" name="rt-callers" value="anyone" ${callersMode === 'anyone' ? 'checked' : ''}> anyone</label>
                        <label style="${RADLBL}"><input type="radio" name="rt-callers" value="only" ${callersMode === 'only' ? 'checked' : ''}> only these</label>
                    </div>
                    <input type="text" id="rt-callers-list" value="${_esc(filterCaller)}" placeholder="+12405551234, +13015550000" style="display:${callersMode === 'only' ? 'block' : 'none'}">
                </div>

                <hr class="sched-divider" style="margin-top:16px">
                <div class="sched-field">
                    <label>Where the session runs</label>
                    <div style="${RADROW}">
                        <label style="${RADLBL}"><input type="radio" name="rt-route" value="saved" ${!ephemeral ? 'checked' : ''}> a saved chat</label>
                        <label style="${RADLBL}"><input type="radio" name="rt-route" value="ephemeral" ${ephemeral ? 'checked' : ''}> per caller chat history</label>
                    </div>

                    <div id="rt-saved" style="display:${ephemeral ? 'none' : 'block'}">
                        <div style="display:flex;gap:8px;align-items:center">
                            <select id="rt-chat" style="flex:1">
                                <option value="">Select a chat…</option>
                                ${chatNames.map(n => `<option value="${_esc(n)}" ${t.chat_target === n ? 'selected' : ''}>${_esc(n)}</option>`).join('')}
                            </select>
                            <button class="btn-sm" id="rt-newchat" type="button">+ new</button>
                        </div>
                        <div id="rt-mirror" class="text-muted" style="font-size:var(--font-sm);margin-top:6px"></div>
                    </div>

                    <div id="rt-ephemeral" style="display:${ephemeral ? 'block' : 'none'}">
                        <div class="sched-field">
                            <label>Keep chat for (minutes after last call)</label>
                            <input type="number" id="rt-ttl" value="${_esc(String(tc.ephemeral_minutes ?? 10))}" min="0">
                        </div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                            <div class="sched-field"><label>Prompt</label><select id="rt-prompt">${promptOpts(t.prompt)}</select></div>
                            <div class="sched-field"><label>Tools</label><select id="rt-toolset">${toolsetOpts(t.toolset || 'none')}</select></div>
                            <div class="sched-field"><label>Provider</label><select id="rt-provider">${providerOpts(t.provider)}</select></div>
                            <div class="sched-field"><label>Model</label><input type="text" id="rt-model" value="${_esc(t.model || '')}" placeholder="Provider default"></div>
                            <div class="sched-field"><label>Voice <span class="help-tip" data-tip="TTS voice for this rule's calls (greeting + replies). Default = the global voice.">?</span></label><select id="rt-voice"><option value="">Default voice</option></select></div>
                        </div>
                        <details style="margin-top:4px"><summary class="text-muted" style="cursor:pointer;font-size:var(--font-sm)">🧠 Mind — memory &amp; knowledge scopes</summary>
                            <div class="text-muted" style="font-size:var(--font-sm);margin:4px 0">Only matters if you grant tools. Pick an isolated scope so a caller never touches your default memory.</div>
                            <div id="rt-scope-dropdowns"></div>
                        </details>
                    </div>
                </div>

                <div class="sched-field">
                    <label>Greeting <span class="help-tip" data-tip="Spoken when the session connects.">?</span></label>
                    <textarea id="rt-greeting" rows="2" placeholder="Hi, you've reached Sapphire…">${_esc(tc.greeting || '')}</textarea>
                </div>
                <div class="sched-field">
                    <label>Phone context <span class="help-tip" data-tip="A per-turn note she sees so she knows it's a live call — invisible to the caller, never saved (rides the ghost rail). Use {caller} for the number. Leave blank for a sensible default.">?</span></label>
                    <textarea id="rt-phone-note" rows="2" placeholder="You're on a phone call with {caller}. Voice transcription — reply briefly, spoken aloud, no markdown.">${_esc(tc.phone_note || '')}</textarea>
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="rt-public" ${tc.public_line === false ? '' : 'checked'}> Public line — apply safety rails <span class="help-tip" data-tip="Appends the conduct rails from Settings > Plugins > Twilio Voice (bait defense, stranger posture — the 'don't repeat stuff' text). Uncheck for a trusted line like your own number, so she can be fully herself.">?</span></label>
                </div>
                <div class="sched-checkbox">
                    <label><input type="checkbox" id="rt-allow-elev" ${tc.allow_elevation ? 'checked' : ''}> Allow toolset elevation by passphrase <span class="help-tip" data-tip="A caller on this rule can unlock tools by speaking the passphrase (&quot;switch toolset, the key is alligator three&quot;). Word part fuzzy for voice transcription, number exact. 3 tries per call; elevation ends at hangup.">?</span></label>
                </div>
                <div id="rt-elev-fields" style="display:${tc.allow_elevation ? 'block' : 'none'};margin:2px 0 8px 22px">
                    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                        <div class="sched-field">
                            <label>Passphrase <span class="help-tip" data-tip="Use a word + a number (e.g. alligator3) — the number must be heard exactly, which is what makes it hard to guess.">?</span></label>
                            <input type="password" id="rt-elev-key" value="${_esc(tc.elevate_key || '')}" placeholder="e.g. alligator3" autocomplete="new-password">
                        </div>
                        <div class="sched-field">
                            <label>Unlocks toolset <span class="help-tip" data-tip="The key opens THIS toolset and nothing else (a caller naming another one is overridden). 'none' = the caller must name a toolset — not recommended.">?</span></label>
                            <select id="rt-elev-toolset">${toolsetOpts(tc.elevate_toolset || 'none')}</select>
                        </div>
                    </div>
                </div>

                <div style="font-size:var(--font-sm);opacity:.85;margin-top:4px">ⓘ A session is an outside line. Tools default to <b>none</b> — grant them on purpose.</div>

                <details style="margin-top:12px"><summary class="text-muted" style="cursor:pointer;font-size:var(--font-sm)">Advanced · Caller trust</summary>
                    <div class="text-muted" style="font-size:var(--font-sm);padding:8px 0">Per-caller trust tiers and voice unlock are coming. For now, capability comes from the chat above.</div>
                </details>
            </div>
            <div class="sched-editor-footer">
                <button class="btn-sm" data-action="close">Cancel</button>
                <button class="btn-primary" id="rt-save">${isEdit ? 'Save' : 'Create Realtime'}</button>
            </div>
        </div>`;

    document.body.appendChild(modal);

    // ── Tooltips ──
    const tipEl = document.createElement('div');
    tipEl.className = 'help-tip-popup';
    document.body.appendChild(tipEl);
    modal.addEventListener('mouseover', e => {
        const tip = e.target.closest('.help-tip');
        if (!tip?.dataset.tip) return;
        tipEl.textContent = tip.dataset.tip; tipEl.style.display = 'block';
        const r = tip.getBoundingClientRect();
        tipEl.style.left = (r.left + r.width / 2) + 'px'; tipEl.style.top = (r.top - 6) + 'px';
    });
    modal.addEventListener('mouseout', e => { if (e.target.closest('.help-tip')) tipEl.style.display = 'none'; });

    const close = () => { modal.remove(); tipEl.remove(); };
    modal.querySelectorAll('[data-action="close"]').forEach(b => b.addEventListener('click', close));
    modal.addEventListener('click', e => { if (e.target === modal) close(); });

    // ── Emoji picker ──
    const emojiBtn = modal.querySelector('#rt-emoji-btn');
    emojiBtn.addEventListener('click', e => {
        e.stopPropagation();
        const wrap = modal.querySelector('#rt-emoji-wrap');
        wrap.querySelector('.sched-hb-emoji-picker')?.remove();
        const picker = document.createElement('div');
        picker.className = 'sched-hb-emoji-picker';
        picker.innerHTML = `<div class="sched-hb-emoji-grid">${RT_EMOJIS.map(em => `<button class="sched-hb-emoji-opt" data-emoji="${em}">${em}</button>`).join('')}</div>`;
        wrap.appendChild(picker);
        picker.addEventListener('click', ev => {
            const opt = ev.target.closest('.sched-hb-emoji-opt');
            if (opt) { emojiBtn.textContent = opt.dataset.emoji; picker.remove(); }
        });
        const cp = ev => { if (!picker.contains(ev.target) && ev.target !== emojiBtn) { picker.remove(); document.removeEventListener('click', cp); } };
        setTimeout(() => document.addEventListener('click', cp), 0);
    });

    // ── Sub-source: render + load per selected source ──
    const subField = modal.querySelector('#rt-subsource-field');
    const subLabel = modal.querySelector('#rt-subsource-label');
    const subSel = modal.querySelector('#rt-subsource');
    async function syncSubSource(preselect) {
        const src = sources.find(s => s.name === modal.querySelector('#rt-source').value);
        const sf = _subSourceField(src);
        if (!sf) { subField.style.display = 'none'; subSel.dataset.key = ''; return; }
        subField.style.display = 'block';
        subLabel.textContent = sf.label || 'Endpoint';
        subSel.dataset.key = sf.key;
        subSel.innerHTML = '<option value="">Loading…</option>';
        try {
            const res = await fetch(sf.dynamic);
            const data = await res.json();
            const options = data.accounts || data.options || data || [];
            subSel.innerHTML = '<option value="">Select…</option>';
            for (const o of options) {
                const ov = typeof o === 'string' ? o : (o.value || o.id || o.name);
                const ol = typeof o === 'string' ? o : (o.label || o.name || o.value);
                const opt = document.createElement('option');
                opt.value = ov; opt.textContent = ol;
                subSel.appendChild(opt);
            }
            if (preselect) subSel.value = preselect;
        } catch { subSel.innerHTML = '<option value="">Could not load options</option>'; }
    }
    modal.querySelector('#rt-source').addEventListener('change', () => syncSubSource(null));
    const initSub = _subSourceField(sources.find(s => s.name === curSource));
    await syncSubSource(initSub ? tc[initSub.key] : null);

    // ── Voice options (async — the placeholder "Default voice" stands alone until loaded) ──
    (async () => {
        try {
            const res = await fetch('/api/tts/voices');
            const data = await res.json();
            const voiceSel = modal.querySelector('#rt-voice');
            for (const v of (data.voices || [])) {
                // Kokoro entries are {voice_id, name, category} — SAVE the id,
                // SHOW the name (storing the name was the silent-Alfred bug).
                const val = typeof v === 'string' ? v : (v.voice_id || v.id || v.value || v.name);
                if (!val) continue;
                const opt = document.createElement('option');
                opt.value = val;
                const label = typeof v === 'string' ? v : (v.name || v.label || val);
                opt.textContent = v.category ? `${label} (${v.category})` : label;
                voiceSel.appendChild(opt);
            }
            if (tc.tts_voice) voiceSel.value = tc.tts_voice;
        } catch { /* leave "Default voice" alone */ }
    })();

    // ── Callers radio ──
    modal.querySelectorAll('input[name="rt-callers"]').forEach(r => r.addEventListener('change', () => {
        modal.querySelector('#rt-callers-list').style.display =
            modal.querySelector('input[name="rt-callers"]:checked').value === 'only' ? 'block' : 'none';
    }));

    // ── Routing radio ──
    modal.querySelectorAll('input[name="rt-route"]').forEach(r => r.addEventListener('change', () => {
        const eph = modal.querySelector('input[name="rt-route"]:checked').value === 'ephemeral';
        modal.querySelector('#rt-saved').style.display = eph ? 'none' : 'block';
        modal.querySelector('#rt-ephemeral').style.display = eph ? 'block' : 'none';
    }));

    // ── Saved-chat mirror ──
    const mirror = modal.querySelector('#rt-mirror');
    async function showMirror(name) {
        if (!name) { mirror.innerHTML = ''; return; }
        try {
            const res = await fetch(`/api/chats/${encodeURIComponent(name)}/settings`);
            const s = await res.json();
            const tools = s.toolset || 'none';
            const armed = tools && tools !== 'none';
            mirror.innerHTML = `runs as <b>${_esc(name)}</b> — prompt: ${_esc(s.prompt || 'default')} · ` +
                `tools: <span style="color:${armed ? 'var(--warning, #e08a2b)' : 'inherit'};font-weight:${armed ? '600' : '400'}">${_esc(tools)}</span> · ` +
                `model: ${_esc(s.llm_primary || 'auto')}${s.llm_model ? ' / ' + _esc(s.llm_model) : ''}<br><span style="opacity:.7">to change, edit that chat</span>`;
        } catch { mirror.innerHTML = '<span style="opacity:.7">(could not read chat settings)</span>'; }
    }
    const chatSel = modal.querySelector('#rt-chat');
    chatSel.addEventListener('change', () => showMirror(chatSel.value));
    if (chatSel.value) showMirror(chatSel.value);

    // ── + new chat: create inline, born with NONE toolset, select + stay ──
    modal.querySelector('#rt-newchat').addEventListener('click', async () => {
        const name = prompt('New chat name:');
        if (!name || !name.trim()) return;
        const clean = name.trim();
        try {
            await createChat(clean);
            await fetch(`/api/chats/${encodeURIComponent(clean)}/settings`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ toolset: 'none' }),
            });
            const opt = document.createElement('option');
            opt.value = clean; opt.textContent = clean;
            chatSel.appendChild(opt); chatSel.value = clean;
            showMirror(clean);
            showToast(`Chat "${clean}" created (no tools)`, 'success', 2500);
        } catch (e) { showToast('Could not create chat', 'error'); }
    });

    // ── Elevation fields reveal on check ──
    const elevCheck = modal.querySelector('#rt-allow-elev');
    elevCheck.addEventListener('change', () => {
        modal.querySelector('#rt-elev-fields').style.display = elevCheck.checked ? 'block' : 'none';
    });

    // ── Tools orange when armed (per-caller) ──
    const toolSel = modal.querySelector('#rt-toolset');
    const armTools = () => {
        const armed = toolSel.value && toolSel.value !== 'none';
        toolSel.style.color = armed ? 'var(--warning, #e08a2b)' : '';
        toolSel.style.borderColor = armed ? 'var(--warning, #e08a2b)' : '';
    };
    toolSel.addEventListener('change', armTools); armTools();

    // ── Mind scope dropdowns (per-caller) ──
    const scopeContainer = modal.querySelector('#rt-scope-dropdowns');
    if (scopeContainer && scopeDeclarations.length) {
        renderScopeDropdowns(scopeContainer, scopeDeclarations, t, { idPrefix: 'rt-', enabledPlugins });
        await populateScopeOptions(scopeContainer, scopeDeclarations, scopeData, t, { idPrefix: 'rt-', enabledPlugins });
    }

    // ── Delete ──
    if (isEdit) modal.querySelector('#rt-delete')?.addEventListener('click', async () => {
        if (!confirm(`Delete "${t.name}"?`)) return;
        try { await deleteTask(t.id); close(); refresh?.(); } catch { showToast('Delete failed', 'error'); }
    });

    // ── Save ──
    modal.querySelector('#rt-save').addEventListener('click', async () => {
        const name = modal.querySelector('#rt-name').value.trim();
        if (!name) { alert('Name is required'); return; }
        const source = modal.querySelector('#rt-source').value;
        if (!source) { alert('Source is required'); return; }
        const subKey = subSel.dataset.key;
        const subVal = subSel.value;
        if (subKey && !subVal) { alert(`${subLabel.textContent} is required`); return; }

        const callersOnly = modal.querySelector('input[name="rt-callers"]:checked').value === 'only';
        const callerList = modal.querySelector('#rt-callers-list').value.trim();
        const filter = (callersOnly && callerList) ? { caller: callerList } : null;

        const allowElev = modal.querySelector('#rt-allow-elev').checked;
        const elevKey = modal.querySelector('#rt-elev-key').value.trim();
        if (allowElev && !elevKey) { alert('Elevation is on but no passphrase is set.'); return; }
        // 'none' in the unlock dropdown means NO lock (caller names the toolset) — store as blank.
        const elevTsRaw = modal.querySelector('#rt-elev-toolset').value || '';
        const elevTs = elevTsRaw === 'none' ? '' : elevTsRaw;

        const eph = modal.querySelector('input[name="rt-route"]:checked').value === 'ephemeral';
        const scopeFields = (eph && scopeContainer) ? readScopeSettingsFromDom(scopeContainer, { missingValue: 'none' }) : {};

        const trigger_config = {
            source, filter, greeting: modal.querySelector('#rt-greeting').value.trim(),
            tts_voice: modal.querySelector('#rt-voice').value || '',
            phone_note: modal.querySelector('#rt-phone-note').value.trim(),
            public_line: modal.querySelector('#rt-public').checked,
            allow_elevation: allowElev,
            elevate_key: allowElev ? elevKey : '',
            elevate_toolset: allowElev ? elevTs : '',
            ephemeral: eph,
            ephemeral_minutes: eph ? (parseInt(modal.querySelector('#rt-ttl').value) || 0) : 10,
        };
        if (subKey) trigger_config[subKey] = subVal;

        const data = {
            name, type: 'daemon', emoji: emojiBtn.textContent.trim() || '📞',
            initial_message: '',
            trigger_config,
            schedule: '0 0 31 2 *', chance: 100, active_hours_start: null, active_hours_end: null,
            // Saved mode: behavior is the chat's (A1). Per-caller: these seed the throwaway chat.
            chat_target: eph ? '' : (modal.querySelector('#rt-chat').value || ''),
            prompt: eph ? (modal.querySelector('#rt-prompt').value || 'default') : 'default',
            toolset: eph ? (modal.querySelector('#rt-toolset').value || 'none') : 'none',
            provider: eph ? (modal.querySelector('#rt-provider').value || 'auto') : 'auto',
            model: eph ? (modal.querySelector('#rt-model').value.trim() || '') : '',
            persona: '',
            voice: '', context_limit: 0, max_parallel_tools: 0, max_tool_rounds: 0, max_runs: 0,
            ...scopeFields,
        };

        try {
            if (isEdit) await updateTask(t.id, data);
            else await createTask(data);
            close(); refresh?.();
        } catch (e) { showToast('Save failed', 'error'); }
    });
}
