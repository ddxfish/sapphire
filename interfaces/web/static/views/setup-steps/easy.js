// setup-steps/easy.js - Easy setup: one page of what Sapphire picked for this
// computer, then Finish (tmp/easy-mode-plan.md, 2026-11-15).
//
// The rows are the server's (core/setup_easy.py: voice, ears, wake word, each
// with its one line of why and the settings behind it) plus the Brain row
// (which LLM would answer now, polled while none does). On a first setup the
// picks are staged as pending changes in the Settings host the moment the page
// opens, so "Change" unfolds the REAL Settings tab under a row, already showing
// the pick, and whatever the person does there is what Finish saves: one batch
// save, the switches run as jobs with their rows (the notification center shows
// them after the wizard is gone). `wake_providers` lets a switch install and
// turn on what its provider needs (Piper is a plugin that ships off).
//
// A second run stages nothing: a row shows what is set, offers the pick beside
// it ("Use it"), and Finish changes only what the person clicked. A row whose
// switch failed reads the same way: what is set, and the pick offered again.
//
// Every row has a light and a Test (Krem 2026-11-15). The light is what is LIVE
// behind the row: green works, yellow is being set up, red failed, grey is not
// set up yet (a staged pick waits for Finish). Test: her LLM says hello, her
// voice speaks (a tone until she has one), her ears turn a few seconds into
// words (the Sapphire computer says so and beeps three times first), the wake
// word waits for its phrase. A test never installs anything by itself; "Set up now" saves
// what is staged early, the same save Finish makes.
import ttsTab from '../settings-tabs/tts.js';
import sttTab from '../settings-tabs/stt.js';
import wakewordTab from '../settings-tabs/wakeword.js';
import llmTab from '../settings-tabs/llm.js';
import { renderYou, stageName, refreshBadge } from './you.js';
import { fetchWithTimeout, responseError, errWords, sessionId } from '../../shared/fetch.js';
import { escapeHtml as esc } from '../../shared/modal.js';

const TABS = { brain: llmTab, voice: ttsTab, ears: sttTab, wake: wakewordTab };
const PROVIDER_KEY = { voice: 'TTS_PROVIDER', ears: 'STT_PROVIDER' };
const SWITCH_KEY = { ...PROVIDER_KEY, wake: 'WAKE_WORD_ENABLED' };       // the switch a row's test may be waiting on
const POLL_MS = 4000;
const phrase = n => String(n || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
// The speakers and microphone are the Sapphire computer's: "this computer" only
// when the browser is on it.
const BOX = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname) ? 'this computer' : 'the Sapphire computer';
const Box = BOX[0].toUpperCase() + BOX.slice(1);

export default w => {
    let plan = null;            // the server's rows, read once per visit
    let had = {};               // the settings each row's `current` was read against
    let brain = null;           // { state, text, hint }
    let lights = null;          // { voice|ears|wake: { state, why } }: what is live behind each row
    let wiped = false, seeded = false;
    let rowsEl = null, youEl = null, footEl = null, timer = null, lightTimer = null;
    let hearing = 0;            // the microphone test running now (0 = none): a late line from an old one is dropped
    let takes = 0;
    let settingUp = false;
    const testing = new Set();  // rows whose test runs now: their button waits
    const open = new Set();     // rows whose tab is unfolded: kept across repaints
    const said = {};            // each row's last test line, kept across repaints (a save repaints the step)
    const waiting = {};         // row -> [key, value]: "Set up now" started that switch, the line says how it ended

    const ctx = () => w.host.createCtx();
    const dropPending = () => { const p = w.host.pendingChanges; for (const k of Object.keys(p)) delete p[k]; };
    const readPlan = async () => {
        const p = await fetchWithTimeout('/api/setup/easy', {}, 200000);
        const s = ctx().settings;
        had = Object.fromEntries(p.rows.flatMap(r => Object.keys(r.settings).map(k => [k, s[k]])));
        return p;
    };

    // What a row is set to (staged or saved), by name.
    function words(r, c) {
        if (r.id === 'wake') return c.getValue('WAKE_WORD_ENABLED') ? `On, say "${phrase(c.getValue('WAKEWORD_MODEL'))}"` : 'Off';
        const v = c.getValue(PROVIDER_KEY[r.id]);
        return v && v !== 'none' ? (plan.labels?.[r.tab]?.[v] || v) : 'Off';
    }

    // Her ears are off (as staged or as set): a wake word would hear nothing.
    const deaf = () => !!plan?.rows.some(r => r.id === 'ears') && (ctx().getValue('STT_PROVIDER') || 'none') === 'none';

    // What a row says now: the pick while its settings stand; what is set, with
    // the pick offered, when nothing of its is staged (a second run, or a switch
    // that failed); else the person's own pick by name.
    function view(r) {
        const c = ctx();
        const keys = Object.keys(r.settings);
        if (r.id === 'wake' && c.getValue('WAKE_WORD_ENABLED') && deaf()) {
            return { ...r, text: c.settings.WAKE_WORD_ENABLED ? words(r, c) : 'Off', note: '', was: '',
                     why: 'She cannot hear a wake word while her ears are off.' };
        }
        if (keys.every(k => c.getValue(k) === r.settings[k])) return r;
        if (keys.every(k => c.getValue(k) === c.settings[k])) {
            const fresh = r.current && keys.every(k => c.settings[k] === had[k]);
            return { ...r, text: fresh ? r.current : words(r, c), why: '', note: '', was: '', offer: r };
        }
        return { ...r, text: words(r, c), why: 'Your pick.', note: '', was: '' };
    }

    // A row's light: [dot, why]. Staged and not started = grey: Finish sets it up.
    function light(r) {
        const st = lights?.[r.id];
        if (!st) return ['', ''];
        const c = ctx();
        if (st.state === 'loading') return ['loading', st.why];
        if (Object.keys(r.settings).some(k => c.getValue(k) !== c.settings[k])) return ['idle', ''];
        if (st.state === 'ok') return ['ok', ''];
        return st.state === 'failed' ? ['failed', st.why] : ['idle', ''];
    }

    // Which rows this computer can test: the Brain always, the rest need its speakers or its microphone.
    const testable = id => id === 'brain' || (!!plan?.sound && (id === 'voice' ? plan.speakers !== false : plan.mic !== false));

    function head(id, title, text, small, state = '', offer = null) {
        return `
            <span class="easy-row-title">${esc(title)}</span>
            <span class="easy-row-pick">
                <span class="easy-row-text">${state ? `<span class="easy-dot ${esc(state)}"></span>` : ''}${esc(text)}</span>
                ${small ? `<span class="easy-row-why">${esc(small)}</span>` : ''}
                ${offer ? `<span class="easy-row-offer">I'd pick: ${esc(offer.text)}. ${esc([offer.why, offer.note].filter(Boolean).join(' '))}
                    <button type="button" class="btn-sm" data-use="${id}">Use it</button></span>` : ''}
            </span>
            <span class="easy-row-acts">
                ${testable(id) ? `<button type="button" class="btn-sm" data-test="${id}"${testing.has(id) ? ' disabled' : ''}${
                    id === 'ears' ? ` title="${Box} says so and beeps three times before it listens"` : ''}>Test</button>` : ''}
                <button type="button" class="btn-sm easy-change" data-change="${id}">${open.has(id) ? 'Close' : 'Change'}</button>
            </span>`;
    }

    // A head is painted only when its words changed: a poll must not replace a
    // button between the press and the release of a click.
    function setHead(id, html) {
        const el = rowsEl?.querySelector(`[data-row="${id}"] .easy-row-head`);
        if (el && el._html !== html) { el.innerHTML = html; el._html = html; }
    }

    function paintBrain() {
        setHead('brain', brain
            ? head('brain', 'Brain', brain.text, brain.hint, brain.state)
            : head('brain', 'Brain', 'Looking for her LLM…', ''));
    }

    function paintHeads() {
        if (!rowsEl) return;
        paintBrain();
        for (const r of plan?.rows || []) {
            const v = view(r);
            const [dot, why] = light(r);
            const small = [v.why, v.note, v.was ? `Changes from ${v.was}.` : '', why].filter(Boolean).join(' ');
            setHead(r.id, head(r.id, r.title, v.text, small, dot, v.offer));
        }
    }

    function setFold(id, on) {
        const fold = rowsEl?.querySelector(`[data-row="${id}"] .easy-row-fold`);
        if (!fold) return;
        if (on) { fold.hidden = false; w.host.mount(TABS[id], fold); }
        else { w.host.unmount(TABS[id], fold); fold.hidden = true; }
    }

    // "Use it": the offered pick becomes the row's staged change.
    function use(id) {
        const r = plan?.rows.find(x => x.id === id);
        if (!r) return;
        const c = ctx();
        for (const [k, v] of Object.entries(r.settings)) {
            if (c.settings[k] !== v) c.markChanged(k, v);
            else delete w.host.pendingChanges[k];
        }
        if (open.has(id)) { setFold(id, false); setFold(id, true); }     // its tab shows the pick
        paintHeads();
    }

    // The poll while no LLM answers. Light (the launcher only) unless the Brain
    // row is unfolded: the person may be adding a provider of their own there.
    function armBrain() {
        clearTimeout(timer);
        if (brain && brain.state !== 'running' && brain.state !== 'ok') {
            timer = setTimeout(() => loadBrain(!open.has('brain')), POLL_MS);
        }
    }

    async function loadBrain(light) {
        clearTimeout(timer);
        const el = rowsEl;
        try { brain = await fetchWithTimeout(`/api/setup/easy/brain${light ? '?light=true' : ''}`, {}, 30000); }
        catch (e) { brain = brain || { state: 'none', text: 'Her LLM could not be checked.', hint: e.message }; }
        if (!rowsEl || rowsEl !== el) return;             // the step left while this was out
        paintBrain();
        armBrain();
    }

    // The lights: read on every paint of the step and after each test, and
    // polled only while something is being set up.
    async function loadLights() {
        clearTimeout(lightTimer);
        const el = rowsEl;
        try { lights = await fetchWithTimeout('/api/setup/easy/status', {}, 20000); }
        catch { /* the lights keep what they showed */ }
        if (!rowsEl || rowsEl !== el) return;
        paintHeads();
        settle();
        if (Object.values(lights || {}).some(s => s.state === 'loading') || Object.keys(waiting).length) {
            lightTimer = setTimeout(loadLights, POLL_MS);
        }
    }

    // Everything staged, in one batch save: Finish, and "Set up now".
    async function saveStaged() {
        if (youEl) stageName(w, youEl);
        const c = ctx(), p = w.host.pendingChanges;
        if (p.WAKE_WORD_ENABLED === true && deaf()) delete p.WAKE_WORD_ENABLED;     // nothing to wake her with
        // a pick put back to what is already set is no change: no switch job for it
        for (const [k, v] of Object.entries(p)) if (c.settings[k] === v) delete p[k];
        if (!Object.keys(p).length) return;
        const r = await w.save({ wake_providers: true });
        if (r === false) return false;
        if (r) refreshBadge();
        return r;
    }

    // ── each row's test ──

    // A row's test line. `key`: the switch behind it; while that pick is staged
    // and not being set up, the line offers to set it up now.
    function result(which, text, kind = '', key = '', busy = false) {
        said[which] = [text, kind, key, busy];
        const el = rowsEl?.querySelector(`[data-result="${which}"]`);
        if (!el) return;
        el.parentElement.hidden = !text;
        el.className = `easy-try-result${kind ? ` ${kind}` : ''}`;
        el.textContent = text;
        if (key && !busy && key in w.host.pendingChanges && !(which in waiting)) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'btn-sm';
            b.dataset.setup = '1';
            b.textContent = 'Set up now';
            el.append(' ', b);
        }
    }

    // One test per row at a time; its button waits, and the lights are read again after.
    async function test(id, run) {
        if (testing.has(id)) return;
        testing.add(id);
        paintHeads();
        try { await run(); }
        catch (e) { result(id, errWords(e), 'error'); }
        finally {
            testing.delete(id);
            paintHeads();
            if (rowsEl) { loadLights(); if (id === 'brain') loadBrain(false); }
        }
    }

    // Her LLM says hello, in its own words.
    async function helloTest() {
        result('brain', 'Asking her LLM to say hello…');
        const r = await fetchWithTimeout('/api/setup/easy/hello', { method: 'POST' }, 120000);
        if (r.ok) result('brain', `${r.name} says: "${r.text}"`, 'success');
        else result('brain', r.why || 'No LLM answered.', 'error');
    }

    async function sayTest() {
        result('voice', 'Playing…');
        const r = await fetchWithTimeout('/api/setup/easy/say', { method: 'POST' }, 60000);
        const on = r.device ? ` on ${r.device}` : '';
        const staged = 'TTS_PROVIDER' in w.host.pendingChanges;
        if (!r.ok) result('voice', `${r.error || 'The speakers could not be played.'} ${r.why || ''}`, 'error', 'TTS_PROVIDER', r.busy);
        else if (r.kind === 'tone') result('voice', `A test tone played${on}. ${r.why}`, '', 'TTS_PROVIDER', r.busy);
        else if (staged && r.busy) result('voice', `She spoke${on}, in the voice set now. Your pick is still being set up.`);
        else if (staged) result('voice', `She spoke${on}, in the voice set now, not your pick.`, '', 'TTS_PROVIDER');
        else result('voice', `She spoke${on}.`, 'success');
    }

    // The wake word waits for its phrase. Nothing is recorded: it only says whether it heard.
    async function wakeTest() {
        const name = phrase(ctx().settings.WAKEWORD_MODEL || 'hey_sapphire');
        result('wake', `Say "${name}" now…`);
        const r = await fetchWithTimeout('/api/setup/easy/wake', { method: 'POST' }, 30000);
        if (!r.ok) result('wake', r.why || 'The wake word is not listening.', '', 'WAKE_WORD_ENABLED', r.busy);
        else if (r.heard) result('wake', `She heard "${name}".`, 'success');
        else result('wake', `I did not hear "${name}". Try again, a little closer.`, 'error');
    }

    async function listenTest() {
        const meter = () => rowsEl?.querySelector('[data-row="ears"] .easy-meter');     // a repaint mid-test makes a new one
        const show = yes => { const m = meter(); if (m) m.hidden = !yes; };
        let mic = 'the microphone', closed = false;
        // the row glows with it: yellow while she warns and counts, red while the microphone is open
        const glow = how => {
            const row = rowsEl?.querySelector('[data-row="ears"]');
            if (row) { row.classList.toggle('warning', how === 'warning'); row.classList.toggle('listening', how === 'listening'); }
        };
        const take = hearing = ++takes;
        const on = ev => {
            if (hearing !== take) return;       // the person went back: this take's words are nobody's
            if (ev.done) closed = true;
            // the Sapphire computer says so first: nobody near it is listened to quietly
            if (ev.warn) { glow('warning'); result('ears', `She is saying on ${BOX} that she is about to listen…`); }
            else if (ev.count) { glow('warning'); result('ears', `Listening in ${ev.count}…`); }
            else if (ev.mic) { glow('listening'); mic = ev.mic; show(true); result('ears', `Listening on ${mic}. Say something…`); }
            else if (ev.clear) { glow(''); show(false); if (!said.ears?.[1]) result('ears', `Done listening: ${mic} is closed again.`); }
            else if ('level' in ev) { glow('listening'); show(true); meter()?.firstElementChild.style.setProperty('width', `${Math.min(100, Math.round(ev.level * 200))}%`); }
            else if (ev.thinking) { show(false); result('ears', 'Turning it into words…'); }
            else if (ev.error) result('ears', ev.error, 'error');
            else if (!ev.heard) result('ears', ev.why || `I opened ${mic}, but heard nothing. Check that it is not muted.`, 'error');
            else if (ev.text) result('ears', `Heard: "${ev.text}"`, 'success');
            else if (ev.ears) result('ears', `I hear you on ${mic}. ${ev.ears}`, '', 'STT_PROVIDER', ev.busy);
            else result('ears', `I hear you on ${mic}, but caught no words. Try again, a little closer.`);
        };
        result('ears', 'Opening the microphone…');
        // Never aborted: the take is a few seconds, ends by itself on the server,
        // and a save's repaint (which unmounts the step) must not cut it short.
        try {
            const res = await fetch('/api/setup/easy/listen', {
                method: 'POST',
                headers: { 'X-Session-ID': sessionId, 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' },
            });
            if (!res.ok) throw await responseError(res);
            const reader = res.body.getReader(), text = new TextDecoder();
            let buf = '';
            for (;;) {
                const { done, value } = await reader.read();
                if (done) break;
                buf += text.decode(value, { stream: true });
                let i;
                while ((i = buf.indexOf('\n')) >= 0) {
                    on(JSON.parse(buf.slice(0, i)));
                    buf = buf.slice(i + 1);
                }
            }
            if (buf.trim()) on(JSON.parse(buf));
            // the stream ended with no closing line (Sapphire restarted, a proxy cut it)
            if (!closed && hearing === take) result('ears', 'The microphone test stopped early. Try again.', 'error');
        } catch (e) {
            if (hearing === take && !closed) result('ears', `The microphone test stopped early: ${errWords(e)}`, 'error');
        } finally {
            glow('');
            if (hearing === take) { hearing = 0; show(false); }
        }
    }

    const TESTS = { brain: helloTest, voice: sayTest, ears: listenTest, wake: wakeTest };

    // The staged picks, saved now instead of at Finish, so a test reaches them.
    async function setUpNow() {
        if (settingUp) return;
        settingUp = true;
        try {
            const p = w.host.pendingChanges;
            const lines = Object.keys(said).filter(which => said[which][2] in p);
            for (const which of lines) {
                waiting[which] = [said[which][2], p[said[which][2]]];
                result(which, 'Setting up now: its progress shows under Downloads.');
            }
            if ((await saveStaged()) === false) {
                for (const which of lines) {
                    const [key] = waiting[which];
                    delete waiting[which];
                    result(which, 'That could not be saved.', 'error', key);
                }
                return;
            }
            try { plan = await readPlan(); } catch { /* the rows keep their words */ }
            paintHeads();
            loadLights();
        } finally { settingUp = false; }
    }

    // A switch "Set up now" started has ended: its test line says how.
    function settle() {
        const c = ctx();
        for (const [which, [key, value]] of Object.entries(waiting)) {
            if (key in w.host.pendingChanges) continue;         // still running
            delete waiting[which];
            if (c.settings[key] === value) result(which, 'It is set up. Test it again.');
            else result(which, 'That could not be set up: its row under Downloads says why.', 'error');
        }
    }

    return {
        id: 'easy', title: 'Your setup', kind: 'gate', tabs: Object.values(TABS),

        async render(body) {
            body.innerHTML = `
                <p class="setup-lead">${w.state?.complete
                    ? `This is what you have set. Where I would pick differently for ${BOX}, the row
                       says so: nothing changes unless you click.`
                    : `I looked at ${BOX} and picked what should work best on it.
                       Change anything you like, or just finish: downloads carry on in the background and
                       show in the notification center.`}</p>
                <div class="easy-rows"><span class="setting-help">Looking at ${BOX}…</span></div>
                <div class="easy-foot"></div>
                <div class="easy-you"></div>`;
            const mine = rowsEl = body.querySelector('.easy-rows');
            const lead = body.querySelector('.setup-lead');
            youEl = body.querySelector('.easy-you');
            footEl = body.querySelector('.easy-foot');
            if (!wiped) { dropPending(); wiped = true; }      // nothing half-done from another step rides this save
            renderYou(w, youEl);
            youEl.querySelector('#setup-name')?.addEventListener('input', () => stageName(w, youEl));

            if (!plan) {
                try { plan = await readPlan(); }
                catch (e) {
                    if (rowsEl === mine) {
                        lead.hidden = true;
                        rowsEl.innerHTML = `<span class="setting-help">${Box} could not be read: ${esc(e.message)}.
                            Go back and pick Advanced, or finish and set her up under Settings.</span>`;
                    }
                    return;
                }
            }
            if (rowsEl !== mine) return;                       // the step left during the read
            if (!plan.rows.length) lead.textContent = 'Her voice and her ears are looked after for you here. Check her brain, tell her your name, and finish.';
            if (!seeded) {
                const c = ctx();
                // a second run keeps what is set: its picks are offered, never staged
                for (const r of plan.rerun ? [] : plan.rows) {
                    for (const [k, v] of Object.entries(r.settings)) if (c.settings[k] !== v) c.markChanged(k, v);
                }
                seeded = true;
            }
            rowsEl.innerHTML = ['brain', ...plan.rows.map(r => r.id)].map(id => `
                <section class="easy-row" data-row="${id}">
                    <div class="easy-row-head"></div>
                    <div class="easy-row-test" hidden>${id === 'ears' ? '<span class="easy-meter" hidden><span></span></span>' : ''}<span
                        class="easy-try-result" data-result="${id}"></span></div>
                    <div class="easy-row-fold" hidden></div>
                </section>`).join('');
            footEl.innerHTML = !plan.rows.length ? ''
                : plan.sound ? '<span class="setting-help">No sound, or the wrong microphone? Pick them under Settings › Audio once setup is done.</span>'
                : plan.sound_why ? `<span class="setting-help">${esc(plan.sound_why)}</span>` : '';
            paintHeads();
            for (const [which, line] of Object.entries(said)) result(which, ...line);
            settle();
            for (const id of open) setFold(id, true);
            rowsEl.addEventListener('click', e => {
                const b = e.target.closest('button');
                if (!b || b.disabled || b.closest('.easy-row-fold')) return;       // a button inside an unfolded tab is that tab's
                if (b.dataset.setup) { setUpNow(); return; }
                const id = b.dataset.use || b.dataset.test || b.dataset.change;
                if (!id) return;
                if (b.dataset.test) { test(id, TESTS[id]); return; }
                if (b.dataset.use) use(id);
                else {
                    const on = !open.has(id);
                    if (on) open.add(id); else open.delete(id);
                    setFold(id, on);
                    paintHeads();
                    if (id === 'brain') loadBrain(false);     // opened: the poll looks wider; closed: what was added counts now
                }
                rowsEl?.querySelector(`[data-change="${id}"]`)?.focus();     // the head was painted again
            });
            // a field changed inside an unfolded tab: the row says the new pick
            rowsEl.addEventListener('change', () => setTimeout(paintHeads, 0));
            // the whole look once per visit; a repaint keeps what it knows and its poll
            if (brain) armBrain(); else loadBrain(false);
            loadLights();
        },

        async leave(body, dir) {
            if (dir < 0) {          // back to the kind of setup: nothing staged here follows
                dropPending();
                if (rowsEl) for (const id of open) setFold(id, false);      // each tab's own detach, before the set is gone
                plan = null; brain = null; lights = null; wiped = seeded = false; open.clear();
                for (const which of Object.keys(said)) delete said[which];
                for (const which of Object.keys(waiting)) delete waiting[which];
                hearing = 0;
                return;
            }
            if ((await saveStaged()) === false) return false;
        },

        unmount() {
            clearTimeout(timer);
            clearTimeout(lightTimer);
            if (rowsEl) for (const id of open) setFold(id, false);
            rowsEl = youEl = footEl = null;
        },
    };
};
