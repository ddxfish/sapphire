// views/daemons.js - Triggers › Daemons. Event-driven listeners (Discord,
// Telegram, …) that wake Sapphire when something happens. Single column.
// Per-daemon activity history is deferred to the notification-history system.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import * as TR from '../shared/trigger-common.js';
import { fetchTasksByType, fetchStatus, fetchRealtimeSourceNames } from '../shared/continuity-api.js';

let container = null;
let daemons = [];
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
        // Realtime-source tasks (live sessions, e.g. phone) live in the Realtime tab.
        daemons = d.filter(t => !rt.has(t.trigger_config?.source));
        status = s;
    } catch (e) { console.warn('Daemons load failed:', e); }
}

async function refresh() { await load(); update(); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: TR.TRIGGER_TABS, active: 'daemons', help: helpPills('Daemons', { video: '1DiQ4oUC6R0', doc: 'DAEMONS-WEBHOOKS.md', inline: true }), status: '' })}
        <div class="view-body view-scroll">
            <div class="trigger-single">
                <div class="sched-col-header">
                    <h3>Daemons</h3>
                    <button class="btn-sm" id="d-import" title="Import daemon">⬇</button>
                    <button class="btn-sm btn-primary" id="d-new">+ Daemon</button>
                </div>
                <div id="d-list"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    container.querySelector('#d-new')?.addEventListener('click', () => TR.openEditor(null, 'daemon', refresh));
    container.querySelector('#d-import')?.addEventListener('click', () => TR.importTask('daemon', daemons, refresh));
    TR.bindActions(container.querySelector('.view-body'), () => daemons, refresh);
    update();
}

function update() {
    const scrollEl = container?.querySelector('.view-scroll');
    const scrollTop = scrollEl?.scrollTop || 0;

    const list = container?.querySelector('#d-list');
    if (list) list.innerHTML = TR.renderDaemonList(daemons);
    if (scrollEl) scrollEl.scrollTop = scrollTop;

    const enabled = daemons.filter(d => d.enabled).length;
    const statusEl = container?.querySelector('.section-status');
    if (statusEl) statusEl.innerHTML = TR.statusRow({
        enabled, total: daemons.length, running: status.running,
        desc: 'Event-driven listeners that wake Sapphire when something happens.'
    });
}
