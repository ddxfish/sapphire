// settings-tabs/tools.js - Function calling settings + AI self-management
import { updateSettingsBatch } from '../../shared/settings-api.js';
import { showDangerConfirm } from '../../shared/danger-confirm.js';
import { showToast } from '../../shared/toast.js';

// Local mirror of the self-management settings, kept in sync on save.
// NOTE: these inputs intentionally have NO data-key attribute — they save
// immediately via updateSettingsBatch, not through the generic
// pendingChanges flow (data-key would double-handle them).
const state = {
    toolsetEnabled: false,
    modelEnabled: false,
    roster: [],
    ratchet: false,
    providers: {},   // key -> {display_name?, enabled?, is_local?}
};

function providerLabel(key) {
    const p = state.providers[key];
    if (!p) return `${key} (not configured)`;
    const name = p.display_name || key;
    return p.is_local ? `${name} (local)` : name;
}

function rosterRows() {
    if (!state.roster.length) {
        return `<div class="setting-help" style="padding:6px 0;">No models yet — add one below.</div>`;
    }
    const last = state.roster.length - 1;
    return state.roster.map((key, i) => `
        <div style="display:flex;align-items:center;gap:8px;padding:4px 0;">
            <span style="opacity:.6;width:1.4em;text-align:right;">${i + 1}.</span>
            <span style="flex:1;">${providerLabel(key)}</span>
            <button class="btn-icon" data-roster-up="${i}" title="Move up (less private)" ${i === 0 ? 'disabled' : ''}>▲</button>
            <button class="btn-icon" data-roster-down="${i}" title="Move down (more private)" ${i === last ? 'disabled' : ''}>▼</button>
            <button class="btn-icon" data-roster-del="${i}" title="Remove">✕</button>
        </div>`).join('');
}

function addableOptions() {
    return Object.entries(state.providers)
        .filter(([k, p]) => p && p.enabled && !state.roster.includes(k))
        .map(([k]) => `<option value="${k}">${providerLabel(k)}</option>`)
        .join('');
}

function gateRow(id, label, help, checked) {
    return `
        <div class="setting-row full-width">
            <div class="setting-label">
                <div class="setting-label-row"><label>${label}</label></div>
                <div class="setting-help">${help}</div>
            </div>
            <div class="setting-input">
                <label class="setting-toggle">
                    <input type="checkbox" id="${id}" ${checked ? 'checked' : ''}>
                </label>
            </div>
        </div>`;
}

function selfMgmtHtml() {
    const modelBlock = !state.modelEnabled ? '' : `
        <div style="margin:4px 0 8px 12px;padding:10px 12px;border:1px solid var(--border-color, #444);border-radius:8px;">
            <div class="setting-help" style="margin-bottom:4px;">Models the AI may switch between. <b>Top = least private.</b></div>
            <div id="ai-roster">${rosterRows()}</div>
            <div style="display:flex;gap:8px;align-items:center;margin-top:6px;">
                <select id="ai-roster-add-select" style="flex:1;">${addableOptions() || '<option value="">(no more enabled providers)</option>'}</select>
                <button class="btn-small" id="ai-roster-add">+ Add</button>
            </div>
            <label class="setting-toggle" style="margin-top:10px;">
                <input type="checkbox" id="ai-ratchet" ${state.ratchet ? 'checked' : ''}>
                <span>Privacy ratchet — only allow switches DOWN the list (never back up)</span>
            </label>
        </div>`;

    return `
        <h3 style="margin-top:28px;">AI Self-Management</h3>
        <div class="setting-help">Both off by default. While disabled, the tools are completely hidden from the AI — it cannot see or call them.</div>
        <div class="settings-grid" style="margin-top:8px;">
            ${gateRow('ai-toolset-switch', 'Allow AI Toolset Switching',
                      'Adds switch_toolset — the AI can change its own active toolset (per chat).', state.toolsetEnabled)}
            ${gateRow('ai-model-switch', 'Allow AI Model Switching',
                      'Adds switch_model — the AI can switch between the models listed below (per chat).', state.modelEnabled)}
        </div>
        ${modelBlock}`;
}

async function save(patch) {
    try {
        await updateSettingsBatch(patch);
    } catch (e) {
        showToast(`Failed to save: ${e.message || e}`, 'error');
        throw e;
    }
}

function rerender(wrap) {
    wrap.innerHTML = selfMgmtHtml();
}

async function confirmGate(kind) {
    if (kind === 'toolset') {
        return showDangerConfirm({
            title: 'Allow AI Toolset Switching',
            warnings: [
                'The AI gains a switch_toolset tool and can change its own toolset mid-conversation',
                'It may pick up tools you did not expect in that moment (e.g. coding or file tools)',
                'The switch persists for that chat until changed in the sidebar',
            ],
            buttonLabel: 'Allow Toolset Switching',
        });
    }
    return showDangerConfirm({
        title: 'Allow AI Model Switching',
        warnings: [
            'The AI gains a switch_model tool and can switch between the models you list',
            'Switching to a cloud model sends the conversation to that provider (privacy)',
            'Switching to a paid model can increase costs',
            'Only models you add to the list are switchable',
        ],
        buttonLabel: 'Allow Model Switching',
    });
}

export default {
    id: 'tools',
    name: 'Tools',
    icon: '🔧',
    description: 'Function calling and tool settings',
    essentialKeys: ['MAX_TOOL_ITERATIONS', 'MAX_PARALLEL_TOOLS'],
    advancedKeys: ['DEBUG_TOOL_CALLING'],

    render(ctx) {
        state.toolsetEnabled = !!ctx.getValue('AI_TOOLSET_SWITCH_ENABLED');
        state.modelEnabled = !!ctx.getValue('AI_MODEL_SWITCH_ENABLED');
        state.roster = [...(ctx.getValue('AI_MODEL_SWITCH_ROSTER') || [])];
        state.ratchet = !!ctx.getValue('AI_MODEL_SWITCH_RATCHET');
        state.providers = {
            ...(ctx.getValue('LLM_PROVIDERS') || {}),
            ...(ctx.getValue('LLM_CUSTOM_PROVIDERS') || {}),
        };

        return ctx.renderFields(this.essentialKeys) +
               `<div id="ai-selfmgmt">${selfMgmtHtml()}</div>` +
               ctx.renderAccordion('tools-adv', this.advancedKeys);
    },

    attachListeners(ctx, el) {
        const wrap = el.querySelector('#ai-selfmgmt');
        if (!wrap || wrap._aiBound) return;
        wrap._aiBound = true;

        // Delegated once on the stable wrapper — rerender() only replaces
        // children, so handlers never stack.
        wrap.addEventListener('change', async e => {
            if (e.target.id === 'ai-toolset-switch') {
                const enabling = e.target.checked;
                if (enabling && !(await confirmGate('toolset'))) {
                    e.target.checked = false;
                    return;
                }
                await save({ AI_TOOLSET_SWITCH_ENABLED: enabling });
                state.toolsetEnabled = enabling;
                rerender(wrap);
            } else if (e.target.id === 'ai-model-switch') {
                const enabling = e.target.checked;
                if (enabling && !(await confirmGate('model'))) {
                    e.target.checked = false;
                    return;
                }
                await save({ AI_MODEL_SWITCH_ENABLED: enabling });
                state.modelEnabled = enabling;
                rerender(wrap);
            } else if (e.target.id === 'ai-ratchet') {
                await save({ AI_MODEL_SWITCH_RATCHET: e.target.checked });
                state.ratchet = e.target.checked;
            }
        });

        wrap.addEventListener('click', async e => {
            const btn = e.target.closest('button');
            if (!btn) return;
            if (btn.id === 'ai-roster-add') {
                const sel = wrap.querySelector('#ai-roster-add-select');
                if (!sel || !sel.value) return;
                state.roster.push(sel.value);
                await save({ AI_MODEL_SWITCH_ROSTER: state.roster });
                rerender(wrap);
                return;
            }
            const up = btn.dataset.rosterUp, down = btn.dataset.rosterDown, del = btn.dataset.rosterDel;
            if (up === undefined && down === undefined && del === undefined) return;
            if (up !== undefined) {
                const i = Number(up);
                [state.roster[i - 1], state.roster[i]] = [state.roster[i], state.roster[i - 1]];
            } else if (down !== undefined) {
                const i = Number(down);
                [state.roster[i], state.roster[i + 1]] = [state.roster[i + 1], state.roster[i]];
            } else {
                state.roster.splice(Number(del), 1);
            }
            await save({ AI_MODEL_SWITCH_ROSTER: state.roster });
            rerender(wrap);
        });
    }
};
