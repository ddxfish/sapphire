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
// The kind of setup decides the steps (tmp/easy-mode-plan.md, 2026-11-15): after
// the welcome the person picks Easy (one page of what Sapphire chose for this
// computer, setup-steps/easy.js) or Advanced (every step below). The server
// lists the steps of the kind; picking one rebuilds the stepper.
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
import modeStep from './setup-steps/mode.js';
import easyStep from './setup-steps/easy.js';
import youStep from './setup-steps/you.js';
import voiceStep from './setup-steps/voice.js';
import pluginsStep from './setup-steps/plugins.js';
import lookStep from './setup-steps/look.js';
import nextStep from './setup-steps/next.js';

const DEFAULT_STEPS = ['consent', 'mode'];
const LINGER_MS = 8000;      // a finished row stays in the Downloads panel this long

let container = null, stepperEl = null, dlEl = null;
let host = null, stepper = null, state = null, unsub = null;
let needsRestart = false, lastShown = null, lingerTimer = null;
let agreedBefore = false;    // the welcome was agreed to on an earlier visit: this one skips it

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
    // false = the save failed or a key was refused: the host toasted why, and
    // a step's leave() returns it so Next stays put (hunt 19: it moved on, and
    // Finish could mark setup done with that step's choices lost).
    async save(opts) {
        host.flushCurrentInputs();
        const r = await host.save(opts);
        if (r?.restart_required) needsRestart = true;
        if (r === false || (r?.results || []).some(x => x.status === 'error')) return false;
        return r;
    },
    leaveSave: async dir => ((await w.save()) === false && dir > 0 ? false : undefined),
    // A tab asks for another tab (a plugin gear, "fix deps"): that is Settings' job.
    navigate(tab) {
        if (tab) window._settingsTab = tab;
        switchView('settings');
    },
    repaint: () => stepper?.repaint(),
    // A step with its own continue (the welcome's button).
    next: () => stepper?.next(),
    // The kind of setup, picked on its step: the steps follow it, and the
    // wizard goes on to the first of them.
    async setMode(kind) {
        await w.putState({ mode: kind });
        const ids = state.steps || DEFAULT_STEPS;
        await build(ids[ids.indexOf('mode') + 1]);
    },
    // The last step's doors: setup is complete the moment the user walks through one.
    finishTo: to => finish(null, to),
};

// A step that IS one Settings tab.
function tabStep(id, title, tab) {
    return {
        id, title, tabs: [tab],
        render(body) { host.mount(tab, body); },
        leave: (body, dir) => w.leaveSave(dir),
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

// A setup run after the welcome was agreed to starts at the kind of setup. The
// run that showed the welcome keeps it in the count (the pips never go back).
function buildSteps(ids) {
    if (agreedBefore) ids = ids.filter(id => id !== 'consent');
    const all = {
        consent: consentStep(w), mode: modeStep(w), easy: easyStep(w), you: youStep(w),
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
    const v = notify.view(r);
    const glyph = r.state === 'done' ? '✓' : r.state === 'failed' ? '✗' : '';
    return `
        <div class="setup-dl-row ${esc(r.state)}${v.phase ? ` ${v.phase}` : ''}">
            <div class="setup-dl-head"><span class="setup-dl-title">${esc(r.title)}</span>
                <span class="setup-dl-pct">${glyph || (v.phase ? v.word : v.indeterminate ? '' : `${v.percent}%`)}</span></div>
            ${notify.barHtml(r)}
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
        // a failed row stays for the visit: the bell is hidden here, so this is where its reason shows
        if (r.state !== 'failed' && now - seen.get(id) > LINGER_MS) { seen.delete(id); continue; }
        show.push(r);
        if (r.state !== 'failed') lingering = true;
    }
    dlEl.innerHTML = show.length
        ? show.map(dlRow).join('')
        : '<div class="setup-dl-empty">Nothing is downloading. Model downloads, package installs and provider switches show here and in the notification center.</div>';
    clearTimeout(lingerTimer);
    if (lingering) lingerTimer = setTimeout(paintDownloads, LINGER_MS + 200);
}

// ── Doors out ──
// `to` = { view } (a nav view, the chat by default) | { tab } (a Settings tab) | { run } (a plugin wizard).
async function finish(_step, to = {}) {
    try { await w.putState({ complete: true, step: '' }); }
    catch (e) { showToast(`Setup could not be marked complete: ${e.message}`, 'error'); return; }
    eventBus.dispatch('setup_complete', {});
    try {
        const main = await import('../main.js');
        await main.fullResync();
    } catch (e) { console.warn('[setup] resync after finish failed:', e); }
    if (to.run) to.run();
    else if (to.tab) w.navigate(to.tab);
    else switchView(to.view || 'chat');
    if (needsRestart) showToast('An audio device changed: that applies after a restart (Settings › System).', 'warning', 8000);
}

// Leaving without finishing. A finished setup opened again has nothing waiting:
// it goes back to where its button is.
function exit() {
    if (state?.complete) { w.navigate('system'); return; }
    switchView('chat');
    showToast('Setup is waiting under Settings › System › Run Setup Wizard.', 'info');
}

// The stepper over the steps the server lists now, opened at `startId` when
// that is one of them, else at the first.
async function build(startId) {
    await stepper?.destroy();
    lastShown = null;
    const steps = buildSteps(state.steps || DEFAULT_STEPS);
    stepper = createStepper({ el: stepperEl, steps, onShow, onFinish: finish, onExit: exit,
                              exitLabel: state.complete ? 'Cancel' : 'Skip for now',
                              scroller: container.querySelector('.setup-view') });
    await stepper.go(steps.some(s => s.id === startId) ? startId : steps[0].id);
}

function onShow(step) {
    if (step.id === lastShown) return;
    lastShown = step.id;
    w.putState({ step: step.id }).catch(() => {});
}

wizards.register({
    id: 'setup', title: 'Setup', blurb: 'Easy: one page of what fits this computer. Advanced: every choice, step by step.',
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
            state = { complete: false, step: '', consent: null, mode: '', steps: DEFAULT_STEPS, packages: {} };
        }
        await host.loadData();
        // A visit starts with nothing staged: what an earlier visit left unsaved
        // (Easy's picks, left by the browser's back button) must not ride this
        // one's first save (hunt 2026-11-15).
        for (const k of Object.keys(host.pendingChanges)) delete host.pendingChanges[k];
        agreedBefore = !!state.consent;
        needsRestart = false;
        seen.clear();
        unsub = notify.onChange(paintDownloads);
        paintDownloads();
        // a finished setup opened again starts at the kind of setup, not where it ended
        await build(state.complete ? 'mode' : state.step);
    },

    hide() {
        document.body.classList.remove('setup-mode');
        unsub?.(); unsub = null;
        clearTimeout(lingerTimer);
        stepper?.destroy(); stepper = null;
        lastShown = null;
    },
};
