// shared/settings-host.js - the Settings HOST (tmp/setup-wizard-plan.md, 2026-10-22).
//
// The snapshot of the settings blob, the ctx every tab module receives, the field
// renderers, the generic listeners and the batch save. Two SHELLS mount the tab modules
// in views/settings-tabs/*.js through it: views/settings.js (the Settings page: nav rail,
// mobile menu, Save button) and views/setup.js (the setup wizard, a stepper). A FACTORY,
// never module state: the import map pins every /static module to one instance, so the
// two views would otherwise share one snapshot and one pending-change set.
//
//   const host = createHost(shell);
//   await host.loadData();           host.mount(tab, el);        await host.save();
//
// `shell` is a bag of optional callbacks the host calls back into:
//   contentEl()        the element tabs are mounted in (flush + scroll restore read it)
//   repaint()          paint the current tab again (after loadData, a reset, a save)
//   isTabActive(id)    a tab's async second paint asks before touching the DOM
//   navigate(tab)      a tab asks to show another tab (plugin gear, "fix deps")
//   loadPluginTab(name, source), syncDynamicTabs(), refreshSidebar()   plugin-tab doors
//   afterPlugins(list) runs once the plugin list is fetched (the Settings shell loads
//                      every enabled plugin's settings tab here; the wizard does not)
//   onSaveBusy(bool)   the shell greys its Save button (or Next) while a save runs
//   wizard             true inside the setup view: tabs may hide their General/advanced
//                      sections (`ctx.wizard`)
//
// ctx exposes the snapshot through GETTERS (settings, pendingChanges, help, …): a tab
// that keeps a ctx across an await always reads the live objects, which loadData() and
// save() reassign. commit/commitMerge/getValue/markChanged read them the same way.
import * as api from './settings-api.js';
import { showToast } from './toast.js';
import { setupModalClose } from './modal.js';
import { snapScroll, editableFocused } from './dom-guard.js';
import { commitInto, mergeInto } from './settings-commit.js';
import * as eventBus from '../core/event-bus.js';
import { errWords } from './fetch.js';

// A provider switch is a job with a row (core/provider_switch.py): the save returns
// at once and `settings_changed` arrives when the job ends, with the LIVE value and
// `ok`. The tab that shows that key repaints then, so a failed switch never leaves
// the value it hoped for on screen.
const SWITCH_TABS = { STT_PROVIDER: 'stt', TTS_PROVIDER: 'tts', EMBEDDING_PROVIDER: 'embedding',
                      WAKE_WORD_ENABLED: 'wakeword', WAKEWORD_MODEL: 'wakeword' };

// Tabs hidden in managed mode (the Docker resale box: no local hardware, no system knobs).
// Shared by both shells through host.managedHides(tabId).
const MANAGED_HIDDEN_TABS = new Set(['audio', 'wakeword', 'system', 'network', 'embedding', 'devices', 'mcp']);

const LABEL_OVERRIDES = {
    TTS_LOCAL_PLAYBACK: 'Play speech here',
    LLM_REQUEST_TIMEOUT: 'LLM Request Timeout',   // error messages name it; "Llm" read as a typo
    LLM_MAX_HISTORY: 'LLM Max History',
};

export function formatLabel(key) {
    if (LABEL_OVERRIDES[key]) return LABEL_OVERRIDES[key];
    return key.replace(/_/g, ' ').split(' ')
        .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ');
}

export function escapeAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function createHost(shell = {}) {
    let settings = {};
    let help = {};
    let overrides = [];
    let defaults = {};
    let pendingChanges = {};
    let wakewordModels = [];
    let visionProviders = [];   // [key, name] of every enabled model line, the chat sidebar's list (Images › describer)
    let availableThemes = ['dark'];
    let providerMeta = {};
    let pluginList = [];
    let lockedPlugins = [];
    let pluginSurfaces = [];   // [{id,label}] — the app's surfaces (plugin 'Show in' strip)
    let managed = false;
    let docker = false;
    let unrestricted = false;
    // U1 (2026-09-20): a slow refetch must never overwrite a newer one (two tab
    // clicks in flight), and (c)'s future bus repaint needs to know a global
    // Save is mid-flight. Both counters live here, beside the snapshot they guard.
    let _loadSeq = 0;
    let _saveInFlight = 0;

    // ── Data Loading ──

    async function loadData() {
        const seq = ++_loadSeq;
        try {
            const [settingsData, helpData] = await Promise.all([
                api.getAllSettings(),
                api.getSettingsHelp().catch(() => ({ help: {} }))
            ]);
            if (seq !== _loadSeq) return;   // a newer load already landed — never paint the older blob
            settings = settingsData.settings || {};
            defaults = settingsData.defaults || {};
            overrides = settingsData.user_overrides || [];
            help = helpData.help || {};
            managed = settingsData.managed || false;
            docker = settingsData.docker || false;
            unrestricted = settingsData.unrestricted || false;

            await Promise.all([loadThemes(), loadWakewordModels(), loadProviderMeta(), loadPluginList()]);
        } catch (e) {
            console.warn('Settings load failed:', e);
        }
    }

    // Tab-switch refetch (U1 layer a): the settings blob + provider meta only.
    // loadData()'s plugin fan-out (a dynamic import per plugin tab) is boot-weight,
    // not click-weight. Sequence-guarded like loadData; a failure keeps the old
    // snapshot (same as before, just no longer silently stale on purpose).
    async function loadSettingsOnly() {
        const seq = ++_loadSeq;
        try {
            const settingsData = await api.getAllSettings();
            if (seq !== _loadSeq) return;
            settings = settingsData.settings || {};
            defaults = settingsData.defaults || {};
            overrides = settingsData.user_overrides || [];
            managed = settingsData.managed || false;
            docker = settingsData.docker || false;
            unrestricted = settingsData.unrestricted || false;
            await loadProviderMeta();
        } catch (e) {
            console.warn('Settings refetch failed:', e);
        }
    }

    async function loadPluginList() {
        try {
            const res = await fetch('/api/webui/plugins');
            if (res.ok) {
                const d = await res.json();
                pluginList = d.plugins || [];
                lockedPlugins = d.locked || [];
                pluginSurfaces = d.surfaces || [];
                // The Settings shell loads a settings tab for every enabled plugin
                // with a web UI here (parallel, max(RTT)); the wizard has no plugin tabs.
                await shell.afterPlugins?.(pluginList);
            }
        } catch {}
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
            if (res.ok) {
                const d = await res.json();
                providerMeta = d.metadata || {};
                // Every enabled line: picking one here IS saying it can see (the
                // describer forces vision on for that call). The eye marks the
                // ones already flagged as vision models.
                visionProviders = (d.providers || []).filter(p => p.enabled)
                    .map(p => [p.key, (p.display_name || p.key) + (p.supports_images ? ' \u{1F441}' : '')]);
            }
        } catch {}
    }

    function managedHides(tabId) {
        return managed && MANAGED_HIDDEN_TABS.has(tabId);
    }

    // ── Pending Change Preservation ──

    function flushCurrentInputs() {
        const el = shell.contentEl?.();
        if (!el) return;
        el.querySelectorAll('input[data-key], select[data-key], textarea[data-key]').forEach(input => {
            const key = input.dataset.key;
            if (!key || key === 'undefined') return;
            if (input.type === 'radio' && !input.checked) return;     // provider cards: one of a group
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

    // ── Context Object (passed to tab handlers) ──

    function createCtx() {
        return {
            get settings() { return settings; },
            get help() { return help; },
            get overrides() { return overrides; },
            get defaults() { return defaults; },
            get pendingChanges() { return pendingChanges; },
            get managed() { return managed; },
            get docker() { return docker; },
            get unrestricted() { return unrestricted; },
            get wakewordModels() { return wakewordModels; },
            get availableThemes() { return availableThemes; },
            get providerMeta() { return providerMeta; },
            get pluginList() { return pluginList; },
            get lockedPlugins() { return lockedPlugins; },
            get pluginSurfaces() { return pluginSurfaces; },
            wizard: !!shell.wizard,
            renderFields, renderAccordion, renderInput, formatLabel,
            attachAccordionListeners,
            markChanged(key, value) { pendingChanges[key] = value; },
            // Write-through for writes the tab ALREADY persisted itself (provider
            // card PUTs, self-management toggles, dashboard name…). Reads the
            // host variables at call time — `ctx.settings[key] = v` wrote into
            // whatever object was current at bind time, which loadData() and a
            // Save reassign. Commit only server-CONFIRMED values (after the awaited
            // PUT resolved), never the value you hoped to write. U1 (b), 2026-09-20.
            commit(key, value) { commitInto({ settings, pendingChanges }, key, value); },
            commitMerge(mapKey, subKey, updates) { mergeInto({ settings, pendingChanges }, mapKey, subKey, updates); },
            // Source-of-truth read for unsaved state. Custom controls (anything
            // not rendered via renderFields/renderInput) should use this instead
            // of reading `settings[key]` directly — otherwise tab-switch loses
            // their unsaved value. renderInput already uses this internally.
            getValue(key) {
                return key in pendingChanges ? pendingChanges[key] : settings[key];
            },
            async refreshTab() {
                // Same as a tab switch: a field the user typed in but never
                // blurred only reaches pendingChanges on 'change' — flush first or
                // the repaint drops it (20 call sites; DOM-refresh hunt 2026-09-08).
                flushCurrentInputs();
                // A refresh is the SAME tab — keep the user's scroll (a switch
                // goes through the shell's own paint and lands at the top).
                const restoreScroll = snapScroll(shell.contentEl?.());
                await loadData();
                shell.repaint?.();
                restoreScroll();
            },
            // For async completions (env-build polls etc.): refreshTab() re-renders
            // whatever tab is CURRENTLY active, wiping its unsaved edits — gate on
            // this before refreshing from a background task. The shell answers;
            // a shell with no notion of "active" (one tab per element) says yes.
            isTabActive(id) { return shell.isTabActive ? shell.isTabActive(id) : true; },
            // A tab asks to show another tab (the plugin gear, "fix deps"). The
            // Settings shell switches tabs; the wizard leaves for Settings.
            navigate(tab) { shell.navigate?.(tab); },
            loadPluginTab: (...a) => shell.loadPluginTab?.(...a),
            syncDynamicTabs: () => shell.syncDynamicTabs?.(),
            refreshSidebar: () => shell.refreshSidebar?.(),
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
                        ${isOverridden ? `<button class="btn-icon reset-btn" data-reset-key="${key}" title="Reset to default">↺</button>` : ''}
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
            // the saved value stays visible even when the library no longer lists it: a select without a
            // selected option shows its first one, and the next flush then committed Alexa (2026-10-19)
            const names = !value || wakewordModels.includes(value) ? wakewordModels : [...wakewordModels, value];
            const label = m => m.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) + (wakewordModels.includes(m) ? '' : ' (not in the library now)');
            return `<select id="${id}" data-key="${key}">
                ${names.map(m => `<option value="${m}" ${value === m ? 'selected' : ''}>${label(m)}</option>`).join('')}
            </select>`;
        }
        if (key === 'WAKEWORD_FRAMEWORK') {
            return `<select id="${id}" data-key="${key}">
                ${['onnx', 'tflite'].map(f => `<option value="${f}" ${value === f ? 'selected' : ''}>${f.toUpperCase()}</option>`).join('')}
            </select>`;
        }
        if (key === 'KOKORO_DEVICE' || key === 'FASTER_WHISPER_DEVICE') {
            const opts = [
                ['cuda', 'CUDA (NVIDIA GPU)'],
                ['cpu',  'CPU'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'TTS_STREAMING_SPLIT_MODE') {
            const opts = [
                ['paragraph', 'Paragraph (smoother prosody)'],
                ['sentence',  'Sentence (lower latency)'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'CONVERSATION_DTLN') {
            const opts = [
                ['none', 'None — headphones (no echo cancel)'],
                ['256',  'DTLN 256 — open speakers (fast, ~26 dB)'],
                ['512',  'DTLN 512 — open speakers (stronger, ~31 dB)'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${String(value) === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'CONVERSATION_START_WORD') {
            return `<input type="text" id="${id}" data-key="${key}" value="${escapeAttr(String(value))}" placeholder="hey sapphire">`;
        }
        if (key === 'SPICE_DELIVERY') {
            const opts = [
                ['ghost',  'Ghost message — labeled app context, cache-friendly (default)'],
                ['system', 'System prompt — unattributed, feels self-chosen; re-caches on rotation'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'IMAGE_DESCRIBE_ENGINE') {
            const opts = [
                ['clip', 'CLIP — built in, no model call (default)'],
                ...visionProviders.map(([k, n]) => [k, n]),
            ];
            if (value && !opts.some(([v]) => v === value)) opts.push([value, `${value} (not enabled)`]);
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${escapeAttr(v)}" ${value === v ? 'selected' : ''}>${escapeAttr(l)}</option>`).join('')}
            </select>`;
        }
        if (key === 'WEB_IMAGES_SAFESEARCH') {
            const opts = [
                ['off',      'Off — everything the engine has (default)'],
                ['moderate', 'Moderate — hides explicit images'],
                ['strict',   'Strict — hides explicit images and text'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'LOG_LEVEL') {
            const opts = [
                ['DEBUG',   'DEBUG (verbose, dev only)'],
                ['INFO',    'INFO (default)'],
                ['WARNING', 'WARNING (quiet)'],
                ['ERROR',   'ERROR (very quiet)'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'TTS_STREAMING_STAGE_PAUSE_STYLE') {
            const opts = [
                ['comma',    'Comma (gentle breath, default)'],
                ['period',   'Period (full sentence-end pause)'],
                ['ellipsis', 'Ellipsis (longer narrative pause)'],
                ['none',     'None (strip cleanly, legacy)'],
            ];
            return `<select id="${id}" data-key="${key}">
                ${opts.map(([v, l]) => `<option value="${v}" ${value === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>`;
        }
        if (key === 'STT_FIREWORKS_MODEL') {
            const models = [
                ['whisper-v3-turbo', 'Whisper V3 Turbo']
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
                    <span class="accordion-arrow">▶</span>
                    <h4>${title}</h4>
                </div>
                <div class="settings-accordion-body collapsed" data-accordion-body="${id}">
                    ${renderFields(keys)}
                </div>
            </div>
        `;
    }

    // ── Mount ──

    // One tab into one element: the body, the generic listeners (bound once per
    // element), the tab's own. Returns the ctx the tab was painted with.
    function mount(tab, el) {
        const ctx = createCtx();
        el.innerHTML = `<div class="settings-tab-body">${tab.render(ctx)}</div>`;
        attachGenericListeners(el);
        if (tab.attachListeners) tab.attachListeners(ctx, el);
        return ctx;
    }

    // The tab's `detach(el)` stops what it started on that element (the STT VAD
    // poll); then the element is emptied. The Settings page never calls this
    // (one persistent element, refilled); the wizard does on every step change.
    function unmount(tab, el) {
        try { tab?.detach?.(el); } catch (e) { console.warn('[settings-host] detach failed:', e); }
        el.innerHTML = '';
    }

    // ── Generic listeners ──

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

        });

        // Reset + Help clicks
        el.addEventListener('click', async e => {
            const resetBtn = e.target.closest('[data-reset-key]');
            if (resetBtn) {
                const key = resetBtn.dataset.resetKey;
                if (!confirm(`Reset "${formatLabel(key)}" to default?`)) return;
                try {
                    await api.deleteSetting(key);
                    showToast(`Reset ${formatLabel(key)}`, 'success');
                    delete pendingChanges[key];
                    await loadData();
                    shell.repaint?.();
                } catch { showToast('Reset failed', 'error'); }
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
                        showToast('No voices found. Check your ElevenLabs API key.', 'warning');
                        return;
                    }
                    const targetId = browseBtn.dataset.target;
                    const input = el.querySelector(`#${targetId}`);
                    _showVoicePicker(voices, input, el);
                } catch (err) {
                    showToast(`Couldn't fetch voices. ${errWords(err, 'Try again.')}`, 'error');
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

    // The global batch save: every pending change in one PUT. Returns the batch
    // result (restart_required, per-key results) or undefined when nothing was
    // saved. The Settings shell's Save button and the wizard's Next both land here;
    // plugin tabs with their own save flow never reach it (the shell routes those).
    async function saveChanges() {
        const valid = {};
        for (const [key, value] of Object.entries(pendingChanges)) {
            if (key && key !== 'undefined') valid[key] = value;
        }

        if (!Object.keys(valid).length) {
            showToast('No changes to save', 'info');
            return;
        }

        shell.onSaveBusy?.(true);

        // Embedding provider swap gate: swapping providers leaves existing stored
        // vectors stamped with the old provider, which means they're invisible to
        // vector search under the new one until re-embedded. Show the user real
        // counts and let them back out. This is the ONLY multi-setting gate — the
        // embedding surface touches 3 DBs and years of data.
        // The server enforces the same gate via 409 — this UI path is for the
        // friendly count-of-affected display.
        let confirmEmbeddingSwap = false;
        if ('EMBEDDING_PROVIDER' in valid && valid.EMBEDDING_PROVIDER !== settings.EMBEDDING_PROVIDER) {
            try {
                const res = await fetch('/api/embedding/integrity');
                if (res.ok) {
                    const report = await res.json();
                    const tables = report.tables || {};
                    const countAffected = (t) => (t.matching_active || 0) + (t.legacy_unstamped || 0);
                    const mem = countAffected(tables.memories || {});
                    const know = countAffected(tables.knowledge_entries || {});
                    const people = countAffected(tables.people || {});
                    const total = mem + know + people;
                    if (total > 0) {
                        const msg =
                            `Swap embedding provider to "${valid.EMBEDDING_PROVIDER}"?\n\n` +
                            `This will make existing vectors invisible to semantic search until re-embedded:\n` +
                            `  • ${mem} memory vectors\n` +
                            `  • ${know} knowledge-entry vectors\n` +
                            `  • ${people} people vectors\n\n` +
                            `The data itself is preserved. FTS5 text search still works on all rows. ` +
                            `A re-embed pipeline is coming soon — for now, plan to re-save memories/knowledge manually if needed.`;
                        if (!confirm(msg)) {
                            shell.onSaveBusy?.(false);
                            return;
                        }
                        confirmEmbeddingSwap = true;
                    }
                }
            } catch (e) {
                console.warn('[embedding] integrity pre-save check failed:', e);
                // Don't block save on a fetch failure — user is trying to do the right thing
            }
        }

        _saveInFlight++;
        let result;
        try {
            const parsed = {};
            for (const [key, value] of Object.entries(valid)) {
                // Coerce by the DEFAULTS type, not the currently-stored type.
                // The default's type is the schema — using settings[key] would
                // duck-type on whatever's currently stored, which lets a bad
                // value perpetuate forever (the silero data-poisoning bug class).
                // Fall back to settings[key] if no default registered (plugin keys etc).
                const referenceType = (key in defaults) ? defaults[key] : settings[key];
                parsed[key] = api.parseValue(value, referenceType);
            }

            result = await api.updateSettingsBatch(parsed, { confirm_embedding_swap: confirmEmbeddingSwap });
            await api.reloadSettings();
            // Per-key outcomes: a key the server refused used to ride under a
            // "Saved N" toast (found 2026-10-22). Provider keys come back
            // 'started': their row says how they end.
            const rows = result.results || [];
            const failed = rows.filter(r => r.status === 'error');
            const started = rows.filter(r => r.status === 'started').length;
            const saved = Object.keys(parsed).length - failed.length - started;
            if (saved > 0) showToast(`Saved ${saved} setting${saved === 1 ? '' : 's'}`, 'success');
            for (const r of failed) showToast(`${formatLabel(r.key)} was not saved: ${r.error || 'refused'}`, 'error');

            if (result.restart_required) {
                const keys = result.restart_keys || [];
                showToast(`Restart required for: ${keys.join(', ') || 'some settings'}`, 'warning');
            }

            pendingChanges = {};
            // A provider switch is still running: config holds the OLD value until
            // the job succeeds, so keep the pick staged (the card stays on it) until
            // the job's settings_changed says what happened (the listener below).
            for (const r of rows) {
                if (r.status === 'started' && r.key in parsed) pendingChanges[r.key] = parsed[r.key];
            }
            await loadData();
            shell.repaint?.();
        } catch (e) {
            showToast('Save failed: ' + e.message, 'error');
        } finally {
            _saveInFlight--;
            shell.onSaveBusy?.(false);
        }
        return result;
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
        setupModalClose(popup, () => popup.remove());
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
        setupModalClose(popup, () => popup.remove());
        popup.addEventListener('click', e => {
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

    // A switch job ended: refetch, and repaint the tab that shows it, unless the
    // person is typing in it (the DOM-refresh rule every provider tab follows).
    eventBus.on('settings_changed', async d => {
        const tab = SWITCH_TABS[d?.key];
        if (!tab || !('ok' in d)) return;
        // The job has spoken: the staged pick (and its legacy ENABLED twin) gives
        // way to the live value, whatever it is.
        delete pendingChanges[d.key];
        delete pendingChanges[d.key.replace('_PROVIDER', '_ENABLED')];
        if (shell.isTabActive && !shell.isTabActive(tab)) return;
        await loadSettingsOnly();
        const el = shell.contentEl?.();
        if (el && editableFocused(el)) return;
        shell.repaint?.();
    });

    return {
        loadData, loadSettingsOnly, flushCurrentInputs,
        createCtx, mount, unmount,
        save: saveChanges,
        managedHides,
        renderFields, renderAccordion, renderInput, formatLabel,
        get settings() { return settings; },
        get pendingChanges() { return pendingChanges; },
        get managed() { return managed; },
        get docker() { return docker; },
        get unrestricted() { return unrestricted; },
        get pluginList() { return pluginList; },
        get lockedPlugins() { return lockedPlugins; },
        get saving() { return _saveInFlight > 0; },
    };
}
