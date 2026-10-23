// views/scheduled.js - Triggers › Scheduled. One-off & recurring tasks at set
// times. Two columns: what YOU scheduled vs what Sapphire scheduled herself
// (source === "ai_scheduled") or her plugins declared (source "plugin:<name>").
// + scheduled timeline.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import * as TR from '../shared/trigger-common.js';
import { fetchNonHeartbeatTasks, fetchStatus, fetchMergedTimeline } from '../shared/continuity-api.js';

let container = null;
let tasks = [];
let status = {};
let timeline = { now: null, past: [], future: [] };
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
        const [t, s, mt] = await Promise.all([
            fetchNonHeartbeatTasks(), fetchStatus(), fetchMergedTimeline(12, 12)
        ]);
        tasks = t; status = s; timeline = mt;
    } catch (e) { console.warn('Scheduled load failed:', e); }
}

async function refresh() { await load(); update(); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: TR.TRIGGER_TABS, active: 'scheduled', help: helpPills('Scheduled', { video: '-XGqK8MsIK8', doc: 'CONTINUITY.md', inline: true }), status: '' })}
        <div class="view-body view-scroll">
            <div id="sc-timeline"></div>
            <div class="sched-layout trigger-2col">
                <div id="sc-user-col">
                    <div class="sched-col-header">
                        <h3>You scheduled</h3>
                        <button class="btn-sm" id="sc-import" title="Import task">⬇</button>
                        <button class="btn-sm btn-primary" id="sc-new">+ Task</button>
                    </div>
                    <div id="sc-user-list"></div>
                </div>
                <div id="sc-ai-col">
                    <div class="sched-col-header"><h3>Sapphire scheduled</h3></div>
                    <div id="sc-ai-list"></div>
                </div>
            </div>
        </div>`;
    bindSectionHeader(container);
    container.querySelector('#sc-new')?.addEventListener('click', () => TR.openEditor(null, 'task', refresh));
    container.querySelector('#sc-import')?.addEventListener('click', () => TR.importTask('task', tasks, refresh));
    TR.bindActions(container.querySelector('.view-body'), () => tasks, refresh);
    update();
}

function update() {
    const scrollEl = container?.querySelector('.view-scroll');
    const scrollTop = scrollEl?.scrollTop || 0;

    const isSapphire = t => t.source === 'ai_scheduled' || (t.source || '').startsWith('plugin:');
    const ai = tasks.filter(isSapphire);
    const user = tasks.filter(t => !isSapphire(t));

    const tl = container?.querySelector('#sc-timeline');
    if (tl) tl.innerHTML = TR.renderTimeline(timeline, tasks);
    const userList = container?.querySelector('#sc-user-list');
    if (userList) userList.innerHTML = TR.renderTaskList(user, 'Nothing scheduled by you yet. Create a task to get started.');
    const aiList = container?.querySelector('#sc-ai-list');
    if (aiList) aiList.innerHTML = TR.renderTaskList(ai, 'Sapphire hasn’t scheduled anything herself yet.');

    if (scrollEl) scrollEl.scrollTop = scrollTop;

    const enabled = tasks.filter(t => t.enabled).length;
    const statusEl = container?.querySelector('.section-status');
    if (statusEl) statusEl.innerHTML = TR.statusRow({
        enabled, total: tasks.length, running: status.running,
        desc: 'Tasks at set times — yours, and the ones Sapphire and her plugins set.'
    });
}
