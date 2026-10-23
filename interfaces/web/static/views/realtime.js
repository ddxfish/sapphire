// views/realtime.js - Triggers › Realtime. Live, held-open INBOUND sessions
// from an external party (phone now; Discord voice / robot later). Distinct from
// Daemons (event→task) — these are present-and-live. Under the hood a realtime
// rule is still a daemon task (type:'daemon') with a realtime:true source, so the
// existing gating/routing applies; this tab just shows the realtime-source subset.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import * as TR from '../shared/trigger-common.js';
import { fetchTasksByType, fetchStatus, fetchRealtimeSourceNames, updateTask, deleteTask } from '../shared/continuity-api.js';
import { openRealtimeEditor } from '../shared/trigger-realtime.js';

let container = null;
let rules = [];
let status = {};
let pollTimer = null;

export default {
    init(el) { container = el; },
    async show() { await load(); render(); startPoll(); },
    hide() { stopPoll(); }
};

function startPoll() { stopPoll(); pollTimer = setInterval(refresh, 5000); }
function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

async function load() {
    try {
        const [d, s, rt] = await Promise.all([
            fetchTasksByType('daemon'), fetchStatus(), fetchRealtimeSourceNames()
        ]);
        // Only realtime-source tasks belong here; the rest stay in Daemons.
        rules = d.filter(t => rt.has(t.trigger_config?.source));
        status = s;
    } catch (e) { console.warn('Realtime load failed:', e); }
}

async function refresh() { await load(); update(); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: TR.TRIGGER_TABS, active: 'realtime', help: helpPills('Realtime', { doc: 'REALTIME.md', inline: true }), status: '' })}
        <div class="view-body view-scroll">
            <div class="trigger-single">
                <div class="sched-col-header">
                    <h3>Realtime</h3>
                    <button class="btn-sm" id="rt-import" title="Import realtime rule">⬇</button>
                    <button class="btn-sm btn-primary" id="rt-new">+ Realtime</button>
                </div>
                <div id="rt-list"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    container.querySelector('#rt-new')?.addEventListener('click', () => openRealtimeEditor(null, refresh));
    container.querySelector('#rt-import')?.addEventListener('click', () => TR.importTask('daemon', rules, refresh));
    bindRealtimeActions(container.querySelector('.view-body'));
    update();
}

// Own action handler (edit routes to the Realtime modal, not the daemon one).
// Bound once per render on the fresh .view-body — no stacking.
function bindRealtimeActions(root) {
    if (!root) return;
    root.addEventListener('change', async e => {
        const cb = e.target.closest('input[data-action="toggle"]');
        if (!cb) return;
        const rule = rules.find(r => String(r.id) === String(cb.dataset.id));
        if (!rule) return;
        try { await updateTask(rule.id, { enabled: cb.checked }); } catch { cb.checked = !cb.checked; }
        refresh();
    });
    root.addEventListener('click', async e => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        const rule = rules.find(r => String(r.id) === String(btn.dataset.id));
        if (!rule) return;
        if (btn.dataset.action === 'edit') openRealtimeEditor(rule, refresh);
        else if (btn.dataset.action === 'delete') { if (confirm(`Delete "${rule.name}"?`)) { try { await deleteTask(rule.id); refresh(); } catch {} } }
        else if (btn.dataset.action === 'export') TR.exportTask(rule);
    });
}

function update() {
    const scrollEl = container?.querySelector('.view-scroll');
    const scrollTop = scrollEl?.scrollTop || 0;

    const list = container?.querySelector('#rt-list');
    if (list) list.innerHTML = TR.renderDaemonList(rules);
    if (scrollEl) scrollEl.scrollTop = scrollTop;

    const enabled = rules.filter(r => r.enabled).length;
    const statusEl = container?.querySelector('.section-status');
    if (statusEl) statusEl.innerHTML = TR.statusRow({
        enabled, total: rules.length, running: status.running,
        desc: 'Live inbound sessions — a caller, a room, a body — held open in real time.'
    });
}
