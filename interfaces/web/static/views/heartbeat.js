// views/heartbeat.js - Triggers › Heartbeat. Recurring self-pulses Sapphire
// runs on her own rhythm, and beside them the Idle Heartbeats that wake her
// once you have been quiet a while. Timeline across both, two columns.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import * as TR from '../shared/trigger-common.js';
import { fetchHeartbeats, fetchStatus, fetchMergedTimeline } from '../shared/continuity-api.js';

let container = null;
let heartbeats = [];
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
        const [hb, s, mt] = await Promise.all([
            fetchHeartbeats(), fetchStatus(), fetchMergedTimeline(12, 12)
        ]);
        heartbeats = hb; status = s; timeline = mt;
    } catch (e) { console.warn('Heartbeat load failed:', e); }
}

async function refresh() { await load(); update(); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: TR.TRIGGER_TABS, active: 'heartbeat', help: helpPills('Heartbeat', { video: '-XGqK8MsIK8', doc: 'CONTINUITY.md', inline: true }), status: '' })}
        <div class="view-body view-scroll">
            <div id="hb-timeline"></div>
            <div class="sched-layout trigger-2col">
                <div id="hb-beat-col">
                    <div class="sched-col-header">
                        <h3>Heartbeats</h3>
                        <button class="btn-sm" id="hb-import" title="Import heartbeat">⬇</button>
                        <button class="btn-sm btn-primary" id="hb-new">+ Heartbeat</button>
                    </div>
                    <div id="hb-vitals"></div>
                </div>
                <div id="hb-idle-col">
                    <div class="sched-col-header">
                        <h3>Idle Heartbeats</h3>
                        <button class="btn-sm btn-primary" id="hb-new-idle">+ Idle</button>
                    </div>
                    <div id="hb-idle-vitals"></div>
                </div>
            </div>
        </div>`;
    bindSectionHeader(container);
    container.querySelector('#hb-new')?.addEventListener('click', () => TR.openEditor(null, 'heartbeat', refresh));
    container.querySelector('#hb-new-idle')?.addEventListener('click', () => TR.openEditor(null, 'heartbeat', refresh, { idle: true }));
    container.querySelector('#hb-import')?.addEventListener('click', () => TR.importTask('heartbeat', heartbeats, refresh));
    TR.bindActions(container.querySelector('.view-body'), () => heartbeats, refresh);
    update();
}

function update() {
    const scrollEl = container?.querySelector('.view-scroll');
    const scrollTop = scrollEl?.scrollTop || 0;
    const vitals = container?.querySelector('#hb-vitals');
    const idleVitals = container?.querySelector('#hb-idle-vitals');
    const body = container?.querySelector('.view-body');

    // Preserve expanded response accordions across the surgical update
    const openCards = new Set();
    for (const d of body?.querySelectorAll('details.hb-response-wrap[open]') || []) {
        const card = d.closest('.hb-card');
        if (card) openCards.add(card.id);
    }

    const tl = container?.querySelector('#hb-timeline');
    if (tl) tl.innerHTML = TR.renderTimeline(timeline, heartbeats);
    if (vitals) vitals.innerHTML = TR.renderMission(heartbeats.filter(h => !TR.isIdle(h)), timeline);
    if (idleVitals) idleVitals.innerHTML = TR.renderMission(heartbeats.filter(TR.isIdle), timeline,
        'An Idle Heartbeat wakes her once you have been quiet a while.');

    for (const id of openCards) {
        const det = body?.querySelector(`#${id} details.hb-response-wrap`);
        if (det) det.open = true;
    }
    if (scrollEl) scrollEl.scrollTop = scrollTop;

    const enabled = heartbeats.filter(h => h.enabled).length;
    const statusEl = container?.querySelector('.section-status');
    if (statusEl) statusEl.innerHTML = TR.statusRow({
        enabled, total: heartbeats.length, running: status.running,
        desc: 'Recurring self-pulses — Sapphire checks in on her own rhythm, or once you have been quiet a while.'
    });
}
