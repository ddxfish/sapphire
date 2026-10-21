// Human tab: the phrase, one big button, a meter, one line of status. Upload and the newest clips below.
// The three things you say: the phrase, near misses, other words. The room is the Background tab's.
import { api, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import * as rec from '../recorder.js';
import { defaultDevice } from '../recorder.js';
import { browser, TAGS, TAG_DEFAULT, TAG_WORD } from '../clips.js';
import { showHelpModal } from '/static/shared/modal.js';

// what each pill asks for: two lines under the phrase, and a plain-words modal behind the ? by the button
const MODES = {
    'positive/recorded': {
        say: 'the wake word, the way you really call her', avoid: 'saying it the same way every time',
        help: ['Says it', `These teach her your voice saying the phrase. The whole list, so you only do this once (about 240 takes is the floor; 440 is the target: twice what made the first clean models, so a quarter can be held back to judge):
• every mic she will hear you through, about 80 takes each: the satellites, your headset, the webcam, a phone
• per mic: 30 normal, 10 fast, 10 from across the room or the next room, 10 soft or whispered, 10 loud or excited, 10 with the TV or music on
• tag every take: how you said it, where from, what was on, who. The judge reports by each tag, and a grid can leave one out to see if it helps
• move while you do the 30 normal ones: face the mic, then face a little left, a little right, look up at the ceiling, look down at your hands, from the doorway, from the couch
• anyone else in the house: 20 each, any way they like
• a slice (25% unless you change it) is held back to judge the model, so a thin tag gets a thin judge: tick "for the judge" and ten takes from the next room become a judge instead of a tenth of one
Do:
• say it like you mean it, as if she were across the room
• change speed, distance, mood and where you face between takes; the real you is never square to the mic
• with the TV or music on, a satellite records a fixed 4 seconds: press, say it, done
Don't:
• don't say it the same flat way thirty times, sitting still
• don't add words before or after it
• don't whisper every one, or shout every one`] },
    near: {
        say: 'words that sound like the wake word but are not it', avoid: 'the real wake word, even by accident',
        help: ['Near miss', `These teach her what is NOT the phrase even when it sounds close. In your own voice they count the most.
Do:
• read the prompts, or type names and phrases you actually say
• say the last word alone, and the first word alone
• use multiple mics, and a few whispered or far away
• a few takes per phrase, in your normal voice
Don't:
• never say the real wake word here
• don't invent nonsense words; use real ones you would say`] },
    'negative/recorded': {
        say: 'anything but the wake word', avoid: 'anything that sounds like your wake word',
        help: ["Doesn't say it", `Ordinary talk near her: what she hears all day and must ignore.
Do:
• talk to someone, read a message aloud, grumble at the TV
• a sentence or two per take
• use multiple mics; some whispered, some shouted, some from the next room
• other voices in the house too
Don't:
• don't say the wake word
• don't record silence or just the room; that is Room sound, on Background`] },
};

const WHAT = [['positive/recorded', 'Says it'], ['near', 'Near miss'], ['negative/recorded', "Doesn't say it"]];
let meter = null, keyHandler = null;

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    const device = localStorage.getItem('wwm.device') || defaultDevice();
    el.innerHTML = `
      <div class="wwm-card">
        <h2 class="wwm-center-h">Record yourself ${qbtn('record')}</h2>
        <div class="wwm-row wwm-center"><div class="wwm-seg small" id="wwm-what">${WHAT.map(([v, l]) => `<button data-v="${v}">${l}</button>`).join('')}</div></div>
        <div class="wwm-row wwm-center wwm-recsel">
          <label>Speak through <select id="wwm-mic" title="microphone"><option value="">this browser's default mic</option></select><button class="wwm-x" id="wwm-mic-refresh" title="look for microphones again">↻</button></label>
          <span id="wwm-tags" class="wwm-row">${Object.entries(TAGS).map(([k, vals]) => `<label>${TAG_WORD[k]} <select id="wwm-tag-${k}" title="${k === 'how' ? 'how you say it this take' : k === 'where' ? 'where you are, relative to the mic' : 'what else is making sound'}">${vals.map(v => `<option ${(localStorage.getItem('wwm.' + k) || TAG_DEFAULT[k]) === v ? 'selected' : ''}>${v}</option>`).join('')}</select></label>`).join('')}${qbtn('tags')}</span>
          <span id="wwm-satmic-row" style="display:none" class="wwm-row"><label>Mic gain <input type="number" id="wwm-satgain" min="0" max="37.5" step="0.5" style="width:70px"> dB</label><label><input type="checkbox" id="wwm-satagc"> AGC</label>${qbtn('satmic')}</span>
        </div>
        <div class="wwm-rec">
          <div class="wwm-phrase" id="wwm-say">${esc(p.phrase)}</div>
          <div class="wwm-row wwm-center" id="wwm-near-row" style="display:none"><button class="wwm-btn ghost" id="wwm-near-next">next phrase ▸</button><input type="text" id="wwm-near-own" placeholder="or type your own near miss" style="min-width:200px"></div>
          <div class="wwm-sayavoid"><div><b>Say:</b> <span id="wwm-say-line"></span></div><div><b>Avoid:</b> <span id="wwm-avoid-line"></span></div></div>
          <div class="wwm-row wwm-center"><button class="wwm-btn big primary" id="wwm-recbtn">● Record</button><button class="wwm-q wwm-q-big" id="wwm-mode-help" title="how to do this one">?</button></div>
          <div class="wwm-level"><i id="wwm-levelbar"></i></div>
          <div class="wwm-muted" id="wwm-rec-note">Press, speak, press again. Space bar works too.</div>
          <div class="wwm-row wwm-center wwm-tagrow">
            <label class="wwm-muted">Mic tag <input type="text" id="wwm-devname" value="${esc(device)}" style="min-width:140px" title="how clips from this microphone are labelled"></label>
            <label class="wwm-muted">Who <input type="text" id="wwm-tag-who" value="${esc(localStorage.getItem('wwm.who') || '')}" placeholder="you, or who is saying it" style="min-width:120px" title="whose voice; stays until you change it"></label>
            <label class="wwm-muted">Note on these takes <input type="text" id="wwm-takenote" value="${esc(localStorage.getItem('wwm.takenote') || '')}" placeholder="anything for later; stays until you change it" style="min-width:200px"></label>
            <label class="wwm-muted" id="wwm-judge-l" title="held out from the start and never trained on: the way to give a thin tag a real judge"><input type="checkbox" id="wwm-judge"> for the judge</label>${qbtn('judge_mark')}
          </div>
        </div>
      </div>
      <div class="wwm-grid2">
        <div class="wwm-card">
          <h2>Upload files ${qbtn('upload')}</h2>
          <div class="wwm-row"><div class="wwm-seg small" id="wwm-upcol">
            <button data-v="positive/uploaded">Says it</button><button data-v="negative/uploaded">Doesn't say it</button></div></div>
          <div class="wwm-drop" id="wwm-drop">Drop files or a zip here, or click to choose</div>
          <input type="file" id="wwm-file" multiple accept=".wav,.flac,.ogg,.mp3,.aiff,.aif,.zip" style="display:none">
          <div class="wwm-muted" id="wwm-upnote"></div>
        </div>
        <div class="wwm-card wwm-guide">
          <div class="wwm-row wwm-between"><h2 style="margin:0">Guide ${qbtn('guide')}</h2><button class="wwm-btn" id="wwm-mictest" title="records a second of silence from the chosen mic and judges its noise">Test this mic</button></div>
          <div class="wwm-muted" id="wwm-mictest-out"></div>
          <div id="wwm-targets"></div>
          <ul class="wwm-checks" id="wwm-checks"></ul>
        </div>
      </div>
      <div class="wwm-card wwm-browse" id="wwm-browser"></div>
    </div>`;
    bindHelp(el);

    const segWhat = el.querySelector('#wwm-what'), micSel = el.querySelector('#wwm-mic');
    const tagSel = Object.fromEntries(Object.keys(TAG_DEFAULT).map(k => [k, el.querySelector(`#wwm-tag-${k}`)]));
    Object.entries(tagSel).forEach(([k, i]) => i.addEventListener('change', () => localStorage.setItem('wwm.' + k, i.value.trim())));
    const tagVals = () => Object.fromEntries(Object.entries(tagSel).map(([k, i]) => [k, i.value.trim()]));
    const judgeBox = el.querySelector('#wwm-judge');
    const recBox = el.querySelector('.wwm-rec');
    judgeBox.addEventListener('change', () => { recBox.classList.toggle('judge', judgeBox.checked); el.querySelector('#wwm-rec-note').textContent = judgeBox.checked ? 'For the judge: these takes are held out from the start and never trained on.' : 'Press, speak, press again. Space bar works too.'; });
    const btn = el.querySelector('#wwm-recbtn'), note = el.querySelector('#wwm-rec-note'), say = el.querySelector('#wwm-say');
    const devName = el.querySelector('#wwm-devname');
    devName.addEventListener('change', () => localStorage.setItem('wwm.device', devName.value.trim()));
    const takeNote = el.querySelector('#wwm-takenote');
    takeNote.addEventListener('change', () => localStorage.setItem('wwm.takenote', takeNote.value.trim()));
    let what = localStorage.getItem('wwm.what') || 'positive/recorded';
    if (!MODES[what]) what = 'positive/recorded';              // the room moved to Background
    let nearList = [], nearAt = parseInt(localStorage.getItem('wwm.nearAt') || '0') || 0;
    const nearText = () => (el.querySelector('#wwm-near-own').value.trim() || nearList[nearAt % Math.max(1, nearList.length)] || 'a word that sounds like the phrase');
    const showNear = () => { say.textContent = `Say: ${nearText()}`; };
    api(`/projects/${p.slug}/near-misses`).then(r => { nearList = r.phrases || []; if (what === 'near') showNear(); }).catch(() => {});
    el.querySelector('#wwm-near-next').addEventListener('click', () => { nearAt++; localStorage.setItem('wwm.nearAt', nearAt); el.querySelector('#wwm-near-own').value = ''; showNear(); });
    el.querySelector('#wwm-near-own').addEventListener('input', showNear);
    const collectionOf = (v) => v === 'near' ? 'negative/recorded' : v;
    const setWhat = (v) => {
        what = v;
        localStorage.setItem('wwm.what', v);
        segWhat.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
        el.querySelector('#wwm-near-row').style.display = v === 'near' ? '' : 'none';
        if (v === 'near') showNear();
        else say.textContent = v.startsWith('positive') ? p.phrase : 'Talk normally';
        el.querySelector('#wwm-say-line').textContent = MODES[v].say;
        el.querySelector('#wwm-avoid-line').textContent = MODES[v].avoid;
    };
    segWhat.querySelectorAll('button').forEach(b => b.addEventListener('click', () => setWhat(b.dataset.v)));
    el.querySelector('#wwm-mode-help').addEventListener('click', () => { const h = MODES[what].help; showHelpModal(h[0], h[1]); });
    let upcol = 'positive/uploaded';
    const segUp = el.querySelector('#wwm-upcol');
    const setUp = (v) => { upcol = v; segUp.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.v === v)); };
    segUp.querySelectorAll('button').forEach(b => b.addEventListener('click', () => setUp(b.dataset.v)));
    setUp(upcol);

    // microphones: this machine's inputs (named once the browser lets us), then every satellite with a mic
    let sats = [];
    const fillMics = async (ask) => {
        const keep = micSel.value;
        const list = await rec.inputs(ask);
        const named = list.filter(d => d.label);
        micSel.innerHTML = `<option value="">this browser's default mic</option>` +
            named.map(d => `<option value="in:${esc(d.id)}">${esc(d.label)}</option>`).join('') +
            (list.length && !named.length ? `<option value="" disabled>${list.length} mic(s) here; record once and their names appear</option>` : '') +
            sats.map(m => `<option value="sat:${esc(m.id)}" ${m.online ? '' : 'disabled'}>${esc(m.id)}${m.location ? ' · ' + esc(m.location) : ''}${m.online ? '' : ' (offline)'}</option>`).join('');
        if ([...micSel.options].some(o => o.value === keep)) micSel.value = keep;
        else { try { micSel.value = localStorage.getItem('wwm.mic') || ''; } catch { /* fine */ } }
    };
    const satMic = async () => {
        const row = el.querySelector('#wwm-satmic-row');
        if (!micSel.value.startsWith('sat:')) { row.style.display = 'none'; return; }
        try {
            const r = await api(`/mics/${micSel.value.slice(4)}/settings`);
            if (!r.supported) { row.style.display = 'none'; return; }
            row.style.display = '';
            el.querySelector('#wwm-satgain').value = r.gain_db; el.querySelector('#wwm-satgain').max = r.gain_max_db;
            el.querySelector('#wwm-satagc').checked = !!r.agc;
        } catch { row.style.display = 'none'; }
    };
    micSel.addEventListener('change', () => { localStorage.setItem('wwm.mic', micSel.value); satMic(); });
    el.querySelector('#wwm-satgain').addEventListener('change', async (e) => {
        try { const r = await api(`/mics/${micSel.value.slice(4)}/settings`, { method: 'POST', body: { gain: parseFloat(e.target.value) } }); e.target.value = r.gain_db; ctx.toast(r.told, 'success'); }
        catch (err) { ctx.toast(err.message, 'error'); }
    });
    el.querySelector('#wwm-satagc').addEventListener('change', async (e) => {
        try { const r = await api(`/mics/${micSel.value.slice(4)}/settings`, { method: 'POST', body: { agc: e.target.checked } }); ctx.toast(r.told || 'AGC set', 'info'); }
        catch (err) { ctx.toast(err.message, 'error'); e.target.checked = !e.target.checked; }
    });
    api('/mics').then(r => { sats = r.mics || []; fillMics(false).then(satMic); }).catch(() => fillMics(false));
    fillMics(false);
    el.querySelector('#wwm-mic-refresh').addEventListener('click', () => fillMics(true));
    el.querySelector('#wwm-mictest').addEventListener('click', async (e) => {
        // two seconds of the room from the chosen mic, judged for noise and level; nothing is kept
        const out = el.querySelector('#wwm-mictest-out');
        const mic = micSel.value;
        e.target.disabled = true;
        try {
            let r;
            if (mic.startsWith('sat:')) {
                out.textContent = `${mic.slice(4)} is listening to the room for 2 s. Stay quiet.`;
                r = await api(`/projects/${p.slug}/record-test`, { method: 'POST', body: { device: mic.slice(4) } });
            } else {
                if (rec.isRecording()) throw new Error('a take is in progress');
                out.textContent = 'Listening to the room for 2 s. Stay quiet.';
                await rec.start(mic.startsWith('in:') ? mic.slice(3) : undefined);
                await new Promise(res => setTimeout(res, 2000));
                const got = await rec.stop();
                const fd = new FormData();
                fd.append('audio', got.blob, 'test.wav');
                r = await api(`/projects/${p.slug}/record-test`, { method: 'POST', form: fd });
            }
            out.textContent = `${r.verdict}: ${r.advice} (floor ${r.floor_db ?? '?'} dB, peak ${r.peak_db ?? '?'} dB)`;
        } catch (err) { out.textContent = err.message; }
        e.target.disabled = false;
    });
    const offChange = rec.onChange(() => fillMics(false));

    // takes queue up and go to the server a few at a time: one request per take would hit the plugin's
    // per-minute limit at a brisk pace. The key press that starts and stops a take is cut off at the server
    // (drop_head_ms / drop_tail_ms), more for the space bar than for a mouse click.
    let busy = false, startedBy = 'mouse';
    const queue = [];
    let sending = false, lastSent = 0, sendTimer = null, retryWait = 0;
    const unload = (e) => { if (queue.length || sending) { e.preventDefault(); e.returnValue = ''; } };   // takes still on their way: ask before the page goes
    window.addEventListener('beforeunload', unload);
    const flush = async () => {
        if (sending || !queue.length) return;
        const first = queue[0];
        const run = queue.findIndex(t => t.what !== first.what || t.near !== first.near || t.key !== first.key);
        const limit = run === -1 ? 8 : Math.min(8, run);
        const wait = 1800 - (Date.now() - lastSent);
        if (wait > 0 && queue.length < 4) { clearTimeout(sendTimer); sendTimer = setTimeout(flush, wait); return; }
        sending = true;
        const batch = queue.splice(0, limit);
        const form = new FormData();
        batch.forEach((t, i) => form.append('audio', t.blob, `take${i}.wav`));
        form.append('collection', batch[0].what);
        form.append('device', batch[0].device);
        for (const k in TAG_DEFAULT) if (batch[0].tags[k]) form.append(k, batch[0].tags[k]);
        if (batch[0].judge) form.append('judge', '1');
        if (batch[0].near) form.append('near', '1');
        if (batch[0].note) form.append('note', batch[0].note);
        form.append('texts', batch.map(t => t.text || '').join('\n'));
        form.append('drop_head_ms', batch.map(t => t.head).join(','));
        form.append('drop_tail_ms', batch.map(t => t.tail).join(','));
        try {
            const r = await api(`/projects/${p.slug}/record`, { method: 'POST', form });
            lastSent = Date.now(); retryWait = 0;
            const last = r.clip;
            note.textContent = `Kept: ${last.seconds}s, peak ${Math.round(last.peak * 100)}%${last.clipped ? ' (clipped: back off the mic)' : ''}` +
                (r.refused?.length ? ` · ${r.refused.length} not kept (${r.refused[0]})` : '') + (queue.length ? ` · ${queue.length} more sending` : '') + '. Again?';
            p.counts = r.counts;
            if (r.guide) drawGuide(r.guide); else drawCounts();
            prependClips(r.clips, batch[0].what);
            if (batch.length < queue.length + batch.length) clearTimeout(sendTimer);
        } catch (e) {
            lastSent = Date.now();
            const again = e.status === 429 || !e.status || e.status >= 500;      // too fast, a restart, a blip: the takes wait and go again
            if (again) { queue.unshift(...batch); retryWait = Math.min(15000, (retryWait || 2500) * 2); }
            note.textContent = again ? `${queue.length} take${queue.length === 1 ? '' : 's'} waiting (${e.status === 429 ? 'too fast for the server' : e.message}); sending again in ${Math.round(retryWait / 1000)} s` : `Not kept: ${e.data?.refused?.[0] || e.message}`;
        }
        sending = false;
        if (queue.length) { clearTimeout(sendTimer); sendTimer = setTimeout(flush, retryWait || 1900); }
    };
    // a take as the queue carries it; the batch key splits batches on anything the whole batch shares
    const takeOf = (out, how) => {
        const micName = micSel.selectedOptions[0]?.value ? micSel.selectedOptions[0].textContent.trim() : '';
        const device = devName.value.trim() || micName || defaultDevice(), note = takeNote.value.trim();
        return { blob: out.blob, what: collectionOf(what), near: what === 'near', text: what === 'near' ? nearText() : (what.startsWith('positive') ? p.phrase : ''),
            device, tags: tagVals(), judge: judgeBox.checked, key: JSON.stringify([tagVals(), judgeBox.checked, device, note]), note,
            head: startedBy === 'key' ? 250 : 80, tail: how === 'key' ? 200 : 80 };
    };
    const toggle = async (how = 'mouse') => {
        if (busy) return;
        const mic = micSel.value;
        if (mic.startsWith('sat:')) return recordSatellite(mic.slice(4));
        if (!rec.isRecording()) {
            try { await rec.start(mic.startsWith('in:') ? mic.slice(3) : undefined); }
            catch (e) { ctx.toast(`Microphone: ${e.message}`, 'error'); return; }
            startedBy = how;
            btn.textContent = '■ Stop'; btn.classList.add('rec');
            note.textContent = 'Say it now…';
            meter = setInterval(() => { el.querySelector('#wwm-levelbar').style.width = Math.min(100, rec.peak() * 140) + '%'; }, 80);
            return;
        }
        busy = true;
        clearInterval(meter); meter = null;
        btn.textContent = '● Record'; btn.classList.remove('rec');
        el.querySelector('#wwm-levelbar').style.width = '0';
        const out = await rec.stop();
        busy = false;
        if (!out) return;
        queue.push(takeOf(out, how));
        note.textContent = queue.length > 1 ? `${queue.length} takes sending…` : 'Sending…';
        flush();
        if ([...micSel.options].some(o => o.disabled && /names appear/.test(o.textContent))) fillMics(false);
    };
    btn.addEventListener('click', () => toggle('mouse'));
    keyHandler = (e) => { if (e.code === 'Space' && !e.repeat && !['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(document.activeElement?.tagName) && !document.querySelector('.modal-overlay, .modal, dialog[open]')) { e.preventDefault(); toggle('key'); } };
    document.addEventListener('keydown', keyHandler);

    async function recordSatellite(id) {
        busy = true;
        btn.textContent = '… listening'; btn.disabled = true;
        note.textContent = `${id}: say it when its ring turns yellow.`;
        try {
            const r = await api(`/projects/${p.slug}/record-satellite`, { method: 'POST', body: { device: id, collection: collectionOf(what), ...tagVals(), judge: judgeBox.checked, seconds: 8, text: what === 'near' ? nearText() : (what.startsWith('positive') ? p.phrase : ''), near: what === 'near', note: takeNote.value.trim() } });
            note.textContent = `Kept from ${id}: ${r.clip.seconds}s. Again?`;
            p.counts = r.counts; drawCounts(); prependClips([r.clip], collectionOf(what));
        } catch (e) { note.textContent = `Not kept: ${e.message}`; }
        btn.textContent = '● Record'; btn.disabled = false; busy = false;
    }

    const drop = el.querySelector('#wwm-drop'), file = el.querySelector('#wwm-file'), upnote = el.querySelector('#wwm-upnote');
    drop.addEventListener('click', () => file.click());
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); sendFiles(e.dataTransfer.files); });
    file.addEventListener('change', () => { sendFiles(file.files); file.value = ''; });
    async function sendFiles(files) {
        if (!files?.length) return;
        const form = new FormData();
        form.append('collection', upcol);
        let bytes = 0;
        for (const f of files) { form.append('files', f, f.name); bytes += f.size; }
        upnote.textContent = `Uploading ${files.length} file(s), ${(bytes / 1e6).toFixed(1)} MB…`;
        try {
            const r = await api(`/projects/${p.slug}/upload`, { method: 'POST', form });
            upnote.textContent = `Added ${r.added}.` + (r.skipped.length ? ` Skipped ${r.skipped.length}: ${r.skipped.slice(0, 3).map(s => `${s.name} (${s.why})`).join('; ')}${r.skipped.length > 3 ? '…' : ''}` : '');
            p.counts = r.counts; drawCounts(); loadClips();
        } catch (e) { upnote.textContent = e.message; }
    }

    async function drawCounts() {
        let g;
        try { g = await api(`/projects/${p.slug}/guide`); } catch (e) { el.querySelector('#wwm-checks').innerHTML = `<li class="bad">${esc(e.message)}</li>`; return; }
        drawGuide(g);
    }
    function drawGuide(g) {
        const bar = (label, t) => {
            const pct = Math.min(100, 100 * t.have / t.good);
            const state = t.have >= t.good ? 'good' : t.have >= t.min ? 'ok' : 'low';
            return `<div class="wwm-target ${state}"><div class="wwm-trow"><span>${label}</span><b>${t.have}${t.unit ? ' ' + t.unit : ''}</b><span class="wwm-muted">min ${t.min} · good ${t.good}</span></div><div class="wwm-bar"><i style="width:${pct}%"></i><em style="left:${Math.min(100, 100 * t.min / t.good)}%"></em></div></div>`;
        };
        el.querySelector('#wwm-targets').innerHTML = bar('Says it', g.progress.positive) + (g.progress.near ? bar('Near miss', g.progress.near) : '') + bar("Doesn't say it", g.progress.negative);
        const mark = { ok: '✓', info: 'ℹ', warn: '⚠', bad: '✗' };
        const order = { bad: 0, warn: 1, info: 2, ok: 3 };
        el.querySelector('#wwm-checks').innerHTML = g.checks.slice().sort((a, b) => order[a.level ?? (a.ok ? 'ok' : 'warn')] - order[b.level ?? (b.ok ? 'ok' : 'warn')])
            .map(c => { const l = c.level || (c.ok ? 'ok' : 'warn'); return `<li class="${l}">${mark[l]} ${esc(c.text)}</li>`; }).join('') || '<li class="wwm-muted">Record a few and checks appear here.</li>';
    }
    drawCounts();
    const clips = browser(el.querySelector('#wwm-browser'), ctx, p, { kinds: [['positive', 'Positive'], ['near', 'Near miss'], ['negative', 'Negative']], onDelete: () => drawCounts() });
    const prependClips = (c, col) => clips.prepend(c, col);
    const loadClips = () => clips.load();
    setWhat(what);

    return () => {
        if (meter) clearInterval(meter);
        if (keyHandler) document.removeEventListener('keydown', keyHandler);
        window.removeEventListener('beforeunload', unload);
        if (rec.isRecording()) rec.stop().then(out => { if (out) { queue.push(takeOf(out, 'mouse')); flush(); } });   // a take in progress is kept, not thrown away
        offChange();
        meter = null; keyHandler = null;
    };
}
