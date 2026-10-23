// views/setup.js - the setup wizard: a full-screen view at #setup (tmp/setup-wizard-plan.md, 2026-10-22).
//
// One stepper (shared/stepper.js) over the REAL Settings tab modules, mounted
// through the Settings host (shared/settings-host.js) one step at a time: the
// brain step IS the LLM tab, the voice step hosts the TTS and STT tabs and the
// wake word fields, the audio step IS the Audio tab, the look step IS the Visual
// tab. The wizard-only steps live in views/setup-steps/. A Downloads panel under
// the steps shows the notification center's active rows (model downloads,
// provider switches, package installs), the same rows the center shows.
//
// State is the server's (core/routes/setup.py): the step to resume at is saved
// as each step shows; Finish marks it complete, repaints the app from the
// server (main.js fullResync) and goes to the chat. No page reload. An
// unfinished setup is not a trap: "Skip for now" leaves, and it is back at the
// next boot (main.js gates the router on /api/init's `setup`).
import { createHost } from '../shared/settings-host.js';
import { createStepper } from '../shared/stepper.js';
import * as wizards from '../shared/wizards.js';
import * as notify from '../shared/notify.js';
import { fetchWithTimeout } from '../shared/fetch.js';
import { escapeHtml as esc } from '../shared/modal.js';
import { showToast } from '../shared/toast.js';
import { switchView } from '../core/router.js';
import * as eventBus from '../core/event-bus.js';
import llmTab from './settings-tabs/llm.js';
import audioTab from './settings-tabs/audio.js';
import consentStep from './setup-steps/consent.js';
import youStep from './setup-steps/you.js';
import voiceStep from './setup-steps/voice.js';
import pluginsStep from './setup-steps/plugins.js';
import lookStep from './setup-steps/look.js';
import nextStep from './setup-steps/next.js';

const DEFAULT_STEPS = ['consent', 'you', 'brain', 'voice', 'audio', 'gate', 'plugins', 'look', 'next'];
const LINGER_MS = 8000;      // a finished row stays in the Downloads panel this long

let container = null, stepperEl = null, dlEl = null;
let host = null, stepper = null, state = null, unsub = null;
let needsRestart = false, lastShown = null, lingerTimer = null;

// What every step gets: the host, the server state, and the doors out.
const w = {
    get host() { return host; },
    get state() { return state; },
    get needsRestart() { return needsRestart; },
    async reloadState() {
        state = await fetchWithTimeout('/api/setup/state', {}, 15000);
        return state;
    },
    async putState(patch) {
        state = await fetchWithTimeout('/api/setup/state', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
        }, 15000);
        return state;
    },
    // Flush typed fields, batch-save through the host (its toasts, its rows),
    // remember a restart for the end (only the audio device keys ask for one).
    async save() {
        host.flushCurrentInputs();
        const r = await host.save();
        if (r?.restart_required) needsRestart = true;
        return r;
    },
    // A tab asks for another tab (a plugin gear, "fix deps"): that is Settings' job.
    navigate(tab) {
        if (tab) window._settingsTab = tab;
        switchView('settings');
    },
    repaint: () => stepper?.repaint(),
};

// A step that IS one Settings tab.
function tabStep(id, title, tab) {
    return {
        id, title, tabs: [tab],
        render(body) { host.mount(tab, body); },
        leave: () => w.save(),
        unmount(body) { host.unmount(tab, body); },
    };
}

const gateStep = {
    id: 'gate', title: "You're set", kind: 'gate',
    render(body) {
        body.innerHTML = `
            <p class="setup-lead">That is everything she needs. Finish to start chatting, or keep going
            for the optional parts: featured plugins, her look, and what comes next.</p>`;
    },
};

function buildSteps(ids) {
    const all = {
        consent: consentStep(w), you: youStep(w),
        brain: tabStep('brain', 'Brain', llmTab),
        voice: voiceStep(w),
        audio: tabStep('audio', 'Audio', audioTab),
        gate: gateStep,
        plugins: pluginsStep(w), look: lookStep(w), next: nextStep(w),
    };
    return ids.map(id => all[id]).filter(Boolean);
}

// ── Downloads panel: the center's active rows, done/failed ones linger ──
const seen = new Map();     // row id -> 0 while active, else when it stopped

function dlRow(r) {
    const p = r.progress;
    const active = r.state === 'active';
    const indeterminate = active && (!p || p.indeterminate || p.percent == null);
    const glyph = r.state === 'done' ? '✓' : r.state === 'failed' ? '✗' : '';
    return `
        <div class="setup-dl-row ${esc(r.state)}">
            <div class="setup-dl-head"><span class="setup-dl-title">${esc(r.title)}</span>
                <span class="setup-dl-pct">${glyph || (indeterminate ? '' : `${p.percent}%`)}</span></div>
            ${active ? `<div class="nc-bar-track"><div class="nc-bar-fill${indeterminate ? ' indeterminate' : ''}" style="width:${indeterminate ? 100 : p.percent}%"></div></div>` : ''}
            ${r.body ? `<div class="setup-dl-body">${esc(r.body)}</div>` : ''}
        </div>`;
}

function paintDownloads() {
    if (!dlEl) return;
    const now = Date.now();
    const rows = notify.rows();
    const active = rows.filter(r => r.state === 'active');
    for (const r of active) seen.set(r.id, 0);
    const show = [...active];
    let lingering = false;
    for (const [id, t] of seen) {
        if (active.some(r => r.id === id)) continue;
        const r = rows.find(x => x.id === id);
        if (!r) { seen.delete(id); continue; }
        if (t === 0) seen.set(id, now);
        if (now - seen.get(id) > LINGER_MS) { seen.delete(id); continue; }
        show.push(r);
        lingering = true;
    }
    dlEl.innerHTML = show.length
        ? show.map(dlRow).join('')
        : '<div class="setup-dl-empty">Nothing is downloading. Model downloads, package installs and provider switches show here and in the notification center.</div>';
    clearTimeout(lingerTimer);
    if (lingering) lingerTimer = setTimeout(paintDownloads, LINGER_MS + 200);
}

// ── Doors out ──
async function finish() {
    try { await w.putState({ complete: true, step: '' }); }
    catch (e) { showToast(`Setup could not be marked complete: ${e.message}`, 'error'); return; }
    eventBus.dispatch('setup_complete', {});
    try {
        const main = await import('../main.js');
        await main.fullResync();
    } catch (e) { console.warn('[setup] resync after finish failed:', e); }
    switchView('chat');
    if (needsRestart) showToast('An audio device changed: that applies after a restart (Settings › System).', 'warning', 8000);
}

function exit() {
    switchView('chat');
    showToast('Setup is waiting under Settings › System › Run Setup Wizard.', 'info');
}

function onShow(step) {
    if (step.id === lastShown) return;
    lastShown = step.id;
    w.putState({ step: step.id }).catch(() => {});
}

wizards.register({
    id: 'setup', title: 'Setup', blurb: 'Her brain, voice and ears, audio, featured plugins and look.',
    open: () => switchView('setup'),
});

export default {
    init(el) {
        container = el;
        container.innerHTML = `
            <div class="setup-view">
                <div class="setup-wrap">
                    <div class="setup-brand"><span class="setup-brand-gem">◆</span><span>Sapphire setup</span></div>
                    <div class="setup-stepper"></div>
                    <aside class="setup-downloads">
                        <h4>Downloads &amp; switches</h4>
                        <div class="setup-dl-rows"></div>
                    </aside>
                </div>
            </div>`;
        stepperEl = container.querySelector('.setup-stepper');
        dlEl = container.querySelector('.setup-dl-rows');
        host = createHost({
            wizard: true,
            contentEl: () => stepper?.body() || stepperEl,
            repaint: () => stepper?.repaint(),
            isTabActive: id => !!stepper?.current()?.tabs?.some(t => t.id === id),
            navigate: tab => w.navigate(tab),
            onSaveBusy: b => stepper?.setBusy(b),
        });
        // a hosted tab's bubbling ask for another tab (plugin gear, "fix deps")
        container.addEventListener('settings-navigate', e => w.navigate(e.detail?.tab));
    },

    async show() {
        document.body.classList.add('setup-mode');
        try { await w.reloadState(); }
        catch (e) {
            console.warn('[setup] state unavailable:', e);
            state = { complete: false, step: '', consent: null, steps: DEFAULT_STEPS, packages: {} };
        }
        await host.loadData();
        const steps = buildSteps(state.steps || DEFAULT_STEPS);
        stepper = createStepper({ el: stepperEl, steps, onShow, onFinish: finish, onExit: exit });
        unsub = notify.onChange(paintDownloads);
        paintDownloads();
        const startId = steps.some(s => s.id === state.step) ? state.step : steps[0].id;
        await stepper.go(startId);
    },

    hide() {
        document.body.classList.remove('setup-mode');
        unsub?.(); unsub = null;
        clearTimeout(lingerTimer);
        stepper?.destroy(); stepper = null;
        lastShown = null;
    },
};
