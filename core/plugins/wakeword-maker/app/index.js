// Wakeword Maker - the page. One project (a phrase) at a time, tabs for the stages, a strip of running jobs
// fed by the event bus. Tabs live in ./tabs/*.js and get the shared ctx.
import { api, getSettings, esc } from './api.js';
import * as eventBus from '/static/core/event-bus.js';
import { showToast } from '/static/shared/toast.js';
import { showConfirm, showModal } from '/static/shared/modal.js';
import * as settingsTab from './tabs/settings.js';
import * as wakewordsTab from './tabs/wakewords.js';
import * as recordTab from './tabs/record.js';
import * as voicesTab from './tabs/voices.js';
import * as checkTab from './tabs/check.js';
import * as variationsTab from './tabs/variations.js';
import * as trainTab from './tabs/train.js';
import * as testTab from './tabs/test.js';
import * as installTab from './tabs/install.js';

const TABS = [
    ['wakewords', 'Wakewords', wakewordsTab], ['voices', 'Voices', voicesTab], ['record', 'Record', recordTab],
    ['check', 'Check', checkTab], ['train', 'Train', trainTab],
    ['test', 'Test', testTab], ['install', 'Install', installTab], ['settings', 'Settings', settingsTab],
];

const state = { status: null, settings: {}, projects: [], slug: null, project: null, tab: 'wakewords', jobs: [], storage: null, el: null };
let unsubscribe = null, poll = null, current = null;

export async function render(container) {
    state.el = container;
    container.classList.add('wwm');
    if (!document.getElementById('wwm-css')) {
        const link = document.createElement('link');
        link.id = 'wwm-css'; link.rel = 'stylesheet'; link.href = '/plugin-web/wakeword-maker/app/app.css';
        document.head.appendChild(link);
    }
    container.innerHTML = '<div class="wwm-loading">Loading…</div>';
    try {
        state.slug = localStorage.getItem('wwm.slug') || null;
        state.tab = localStorage.getItem('wwm.tab') || 'wakewords';
    } catch { /* fine */ }
    if (state.tab === 'phrase' || state.tab === 'negatives') state.tab = 'wakewords';
    if (state.tab === 'variations') state.tab = 'train';
    await ctx.refresh();
    eventBus.connect();
    const onJob = (data) => { jobEvent(data); };
    eventBus.on('wakeword_maker.job', onJob);
    unsubscribe = () => eventBus.off('wakeword_maker.job', onJob);
    poll = setInterval(() => { if (!document.hidden) ctx.refreshJobs().catch(() => {}); }, 15000);   // a background browser tab stops asking
    return cleanup;
}

export function cleanup() {
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    if (poll) clearInterval(poll);
    poll = null;
    if (current?.cleanup) { try { current.cleanup(); } catch { /* fine */ } }
    current = null;
}

const ctx = {
    state, api, esc,
    toast: (msg, type = 'info') => showToast(msg, type),
    async reload() {
        // the state only; the page is not redrawn (a take or a room watch in progress survives)
        try {
            [state.status, state.settings] = await Promise.all([api('/status'), getSettings().catch(() => ({}))]);
        } catch (e) {
            if (state.status?.root_ok) { ctx.toast(`Lost touch for a moment: ${e.message}`, 'error'); return; }   // keep what we know; a 429 is not a missing folder
            state.status = { error: e.message };
        }
        if (state.status?.root_ok) {
            try { state.projects = (await api('/projects')).projects; } catch { state.projects = []; }
            if (state.tab === 'wakewords' || !state.storage) state.storage = await api('/storage').catch(() => state.storage);   // walks every clip: only when the page shows it
            if (!state.projects.find(p => p.slug === state.slug)) state.slug = state.projects[0]?.slug || null;
            await ctx.loadProject();
            await ctx.refreshJobs().catch(() => {});
        } else {
            state.projects = []; state.project = null; state.jobs = []; state.storage = null;
            if (state.tab !== 'settings') state.tab = 'wakewords';
        }
    },
    async refresh() {
        await ctx.reload();
        draw();
    },
    async loadProject() {
        state.project = null;
        if (!state.slug) return;
        try { state.project = (await api(`/projects/${state.slug}`)).project; } catch { state.project = null; }
    },
    async refreshJobs() {
        if (!state.status?.root_ok) return;
        state.jobs = (await api('/jobs?limit=12')).jobs || [];
        drawStatus();
    },
    select(slug) {
        state.slug = slug;
        try { localStorage.setItem('wwm.slug', slug || ''); } catch { /* fine */ }
        ctx.loadProject().then(draw);
    },
    async refreshProjects() {
        try { state.projects = (await api('/projects')).projects; } catch { /* keep what we have */ }
    },
    go(tab) {
        state.tab = tab;
        try { localStorage.setItem('wwm.tab', tab); } catch { /* fine */ }
        draw();
    },
    async startJob(kind, args = {}) {
        const r = await api('/jobs', { method: 'POST', body: { kind, project: state.slug, args } });
        await ctx.refreshJobs();
        return r.job;
    },
    draw,
};

function draw() {
    const el = state.el;
    if (!el) return;
    if (current?.cleanup) { try { current.cleanup(); } catch { /* fine */ } }
    current = null;
    const s = state.status || {};
    el.innerHTML = `
      <header class="wwm-head">
        <div class="wwm-title"><span class="wwm-mark">🗣️</span><h1>Wakeword Maker</h1></div>
        <div class="wwm-project">${state.project ? `<button class="wwm-btn ghost" id="wwm-current" title="all wake words">${esc(state.project.phrase)} ▾</button>` : ''}</div>
      </header>
      <nav class="wwm-tabs">${TABS.map(([id, label]) => `<button data-tab="${id}" class="${id === state.tab ? 'on' : ''}">${label}</button>`).join('')}</nav>
      <section id="wwm-status" class="wwm-status"></section>
      <main id="wwm-body" class="wwm-body"></main>`;
    el.querySelector('#wwm-current')?.addEventListener('click', () => ctx.go('wakewords'));
    el.querySelectorAll('.wwm-tabs button').forEach(b => b.addEventListener('click', () => ctx.go(b.dataset.tab)));
    drawStatus();
    const body = el.querySelector('#wwm-body');
    const mod = TABS.find(t => t[0] === state.tab)?.[2] || wakewordsTab;
    try {
        const ret = mod.render(body, ctx, state.tab);
        current = typeof ret === 'function' ? { cleanup: ret } : (ret && typeof ret === 'object' ? ret : { cleanup: mod.cleanup });
    } catch (e) {
        body.innerHTML = `<div class="wwm-error">This tab broke: ${esc(e.message)}</div>`;
        console.error('[wwm]', e);
    }
}

const STEP_TABS = ['voices', 'record', 'check', 'train', 'test', 'install'];
const LIVE_TABS = ['record', 'test'];      // tabs that may hold a microphone: a finished job never redraws them
const STEP_DESC = {
    voices: 'Generate the phrase in many synthetic voices',
    record: 'Record positive, negative, and ambient samples from your mic(s)',
    check: 'Rate every clip; drop the ones that do not say it',
    train: 'Train the desktop/Pi model and the ESP32 model',
    test: 'Measure hits and false alarms; try it live',
    install: 'Put the finished model on your devices',
};

function drawStatus() {
    const box = state.el?.querySelector('#wwm-status');
    if (!box) return;
    const p = state.project;
    const live = state.jobs.filter(j => (j.state === 'running' || j.state === 'interrupted') && (!j.project || j.project === state.slug));
    const pg = p?.progress;
    const idx = STEP_TABS.indexOf(state.tab);
    const doneHere = idx >= 0 && pg?.done?.[state.tab];
    const here = idx >= 0 ? STEP_TABS[idx] : null;                       // describe the tab you are on
    const stepText = p ? (here ? `Step ${idx + 1} of ${STEP_TABS.length}: ${esc(TABS.find(t => t[0] === here)?.[1] || '')}` : (pg?.step ? `Next: step ${pg.index} of ${pg.of}, ${esc(pg.label)}` : 'All steps done')) : '';
    box.innerHTML = `
      <div class="wwm-st-line">
        <span class="wwm-st-who">${p ? esc(p.phrase) : ''}</span>
        <span class="wwm-st-step">${stepText}${here && STEP_DESC[here] ? ` <span class="wwm-st-desc">· ${esc(STEP_DESC[here])}</span>` : ''}${here && pg?.done?.[here] ? ' <span class="wwm-pill ok">done</span>' : ''}</span>
        ${p && idx >= 0 && state.tab !== 'settings' ? `<button class="wwm-btn ${doneHere ? 'ghost' : 'primary'} wwm-done" id="wwm-done">${doneHere ? 'Done ✓ · Next ▸' : 'Done ▸'}</button>` : ''}
      </div>
      ${live.length ? `<div class="wwm-st-jobs">${live.map(j => j.state === 'interrupted'
        ? `<div class="wwm-st-job off" data-id="${esc(j.id)}"><span class="dot"></span><b>${esc(jobLabel(j))}</b> <span class="msg">stopped by a restart at ${Math.round(j.pct || 0)}%</span> <button class="wwm-btn small" data-retry="${esc(j.id)}">Run again</button><button class="wwm-x" data-forget="${esc(j.id)}" title="forget it">✕</button></div>`
        : `<div class="wwm-st-job" data-id="${esc(j.id)}"><i class="wwm-st-bar" style="width:${Math.min(100, j.pct || 0)}%"></i><span class="dot"></span><b>${esc(jobLabel(j))}</b> <span class="msg">${esc(j.msg || j.step || '')}</span> <span class="pct">${Math.round(j.pct || 0)}%</span><button class="wwm-x" data-cancel="${esc(j.id)}" title="stop">✕</button></div>`).join('')}</div>` : ''}`;
    box.classList.toggle('has', !!(p || live.length));
    box.querySelectorAll('[data-cancel]').forEach(b => b.addEventListener('click', () => {
        showConfirm('Stop this job?', async () => { await api(`/jobs/${b.dataset.cancel}`, { method: 'DELETE' }); ctx.refreshJobs(); }, { title: 'Stop job', saveLabel: 'Stop' });
    }));
    box.querySelectorAll('[data-retry]').forEach(b => b.addEventListener('click', async () => {
        try { await api('/jobs', { method: 'POST', body: { retry: b.dataset.retry } }); ctx.toast('Queued again.', 'success'); } catch (e) { ctx.toast(e.message, 'error'); }
        ctx.refreshJobs().catch(() => {});
    }));
    box.querySelectorAll('[data-forget]').forEach(b => b.addEventListener('click', async () => {
        try { await api(`/jobs/${b.dataset.forget}`, { method: 'DELETE' }); } catch { /* fine */ }
        ctx.refreshJobs().catch(() => {});
    }));
    box.querySelector('#wwm-done')?.addEventListener('click', () => markDone(state.tab));
}

async function markDone(tab) {
    const p = state.project;
    if (!p) return;
    const idx = STEP_TABS.indexOf(tab);
    const next = STEP_TABS[idx + 1] || 'install';
    const pg = p.progress || {};
    const finish = async () => {
        try { await api(`/projects/${p.slug}`, { method: 'PUT', body: { steps: { [tab]: true } } }); } catch (e) { ctx.toast(e.message, 'error'); return; }
        await ctx.refreshProjects();
        await ctx.loadProject();
        ctx.go(next);
    };
    if (pg.done?.[tab]) return finish();
    if (tab === 'voices' && (pg.synth || 0) < 2000) {
        const target = p.settings?.synth?.target || 20000;
        const have = pg.synth || 0;
        return showModal('Samples not generated', [
            { type: 'html', value: `<p style="margin:0 0 8px;color:var(--text-secondary)">${have ? `Only ${have.toLocaleString()} synthetic clips exist` : 'No synthetic clips exist yet'}; the Quick size is 2,000 and you chose ${Number(target).toLocaleString()}. Without them the model only knows the voices you record yourself.</p>` },
            { id: 'how', type: 'select', label: 'What now', options: ['generate', 'skip'], labels: ['Generate all samples now and move on', 'Move on without generating'], value: 'generate' },
        ], async (data) => {
            if (data.how === 'generate') {
                try { await ctx.startJob('synth', {}); ctx.toast('Generating. The status line shows progress.', 'info'); } catch (e) { ctx.toast(e.message, 'error'); return; }
            }
            await finish();
        }, { saveLabel: 'Continue' });
    }
    if (tab === 'record' && (pg.recorded || 0) < (pg.recorded_floor || 120)) {
        return showConfirm(`Only ${pg.recorded || 0} recordings of your own voice so far; ${pg.recorded_floor || 120} is the floor for a model that knows you (three mics, forty takes each). Move on anyway? You can come back and record more at any time.`, finish, { title: 'Few recordings', saveLabel: 'Move on' });
    }
    if (tab === 'check' && !pg.rated) {
        return showModal('Clips not rated', [
            { type: 'html', value: '<p style="margin:0 0 8px;color:var(--text-secondary)">Nothing has been rated yet, so clips that do not say the phrase would go into training as if they did.</p>' },
            { id: 'how', type: 'select', label: 'What now', options: ['rate', 'skip'], labels: ['Rate the clips now and move on', 'Move on without rating'], value: 'rate' },
        ], async (data) => {
            if (data.how === 'rate') {
                try { await ctx.startJob('qa', { bar: p.settings?.qa_bar ?? 60 }); ctx.toast('Rating…', 'info'); } catch (e) { ctx.toast(e.message, 'error'); return; }
            }
            await finish();
        }, { saveLabel: 'Continue' });
    }
    return finish();
}

function jobLabel(j) {
    const k = { download: 'Download', delete_dataset: 'Delete', synth: 'Make the set', qa: 'Check', voices: 'Fetch voices', features: 'Features', train: 'Train desktop/Pi', tune: 'Sweep', features_mww: 'ESP32 features', train_mww: 'Train ESP32', judge: 'Judge the models' }[j.kind] || j.kind;
    const what = j.args?.dataset || j.project || '';
    return what ? `${k} · ${what}` : k;
}

let lastLookup = 0;
function jobEvent(d) {
    if (!d?.id) return;
    let j = state.jobs.find(x => x.id === d.id);
    if (!j) {   // a job the list has not caught up with: one refetch, not one per progress line
        if (Date.now() - lastLookup > 5000) { lastLookup = Date.now(); ctx.refreshJobs().catch(() => {}); }
        return;
    }
    if (d.final) {
        j.state = d.state; j.pct = d.pct; j.msg = d.msg; j.result = d.result;
        ctx.toast(`${jobLabel(j)}: ${d.state === 'done' ? 'done' : d.msg}`, d.state === 'done' ? 'success' : d.state === 'interrupted' ? 'warning' : 'error');
        ctx.reload().then(() => {
            if (LIVE_TABS.includes(state.tab)) { drawStatus(); current?.onJob?.({ ...j, final: true }); }   // a mic may be open: no redraw
            else draw();
        }).catch(() => {});
        return;
    }
    j.pct = d.pct; j.msg = d.msg; j.step = d.step; j.metrics = d.metrics;
    const row = state.el?.querySelector(`.wwm-st-job[data-id="${CSS.escape(d.id)}"]`);
    if (row) {
        row.querySelector('.msg').textContent = d.msg || d.step || '';
        row.querySelector('.pct').textContent = Math.round(d.pct || 0) + '%';
        row.querySelector('.wwm-st-bar').style.width = Math.min(100, d.pct || 0) + '%';
    } else drawStatus();
    current?.onJob?.(j);
}
