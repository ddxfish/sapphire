// Background tab: the sound she must ignore. A room day through every microphone (hours into the shared room
// library, the ticked models on watch so a fire can be sorted), the library itself, one take of the room when
// that is all you need, uploads, the room clips, and the two sets both families mix in (rooms and music).
import { api, esc } from '../api.js';
import { qbtn, bindHelp } from '../help.js';
import * as rec from '../recorder.js';
import { encodeWAV, defaultDevice } from '../recorder.js';
import { browser } from '../clips.js';
import { drawDatasets } from '../datasets.js';
import { showConfirm } from '/static/shared/modal.js';

let watchPoll = null, meter = null;
const fmt = (s) => s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`;
const when = (t) => { if (!t) return ''; const d = new Date(t > 1e11 ? t : t * 1000); return isNaN(d) ? '' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };

export function render(el, ctx) {
    const p = ctx.state.project;
    if (!p) { el.innerHTML = '<div class="wwm-empty">Create a phrase first.</div>'; return; }
    el.innerHTML = `
      <div class="wwm-card" id="wwm-roomday-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">Room day ${qbtn('roomday')}</h2><span id="wwm-rd-target" class="wwm-muted"></span></div>
        <div class="wwm-row wwm-wrap"><label>for <input type="number" id="wwm-rd-min" min="1" max="1440" value="360" style="width:70px"> minutes</label><span id="wwm-rd-mics" class="wwm-row wwm-wrap"><span class="wwm-muted">finding microphones…</span></span>
          <label id="wwm-rd-test-l" style="display:none" title="the board counts its own fires and answers nothing: a candidate judged on a day of the room without her talking to the TV"><input type="checkbox" id="wwm-rd-test" checked> board test mode</label></div>
        <div class="wwm-row wwm-wrap"><span class="wwm-muted">On watch:</span><span id="wwm-rd-runs" class="wwm-row wwm-wrap"><span class="wwm-muted">…</span></span><button class="wwm-btn ghost" id="wwm-rd-pick">change on Train</button></div>
        <div class="wwm-row wwm-wrap"><button class="wwm-btn primary" id="wwm-rd-go">Record the room on every ticked mic</button><button class="wwm-btn" id="wwm-rd-stop">Stop them all</button><span class="wwm-muted" id="wwm-rd-note">Every piece goes to the shared room library, tagged by mic; every wake word trains on it. PC mics record from this tab: keep it open.</span></div>
        <div id="wwm-watch-out"></div>
      </div>
      <div class="wwm-card">
        <div class="wwm-row wwm-between"><h2 style="margin:0">Room library ${qbtn('library')}</h2><span class="wwm-muted" id="wwm-lib-sum"></span></div>
        <div id="wwm-lib" class="wwm-muted">reading…</div>
      </div>
      <div class="wwm-grid2">
        <div class="wwm-card">
          <div class="wwm-row wwm-between wwm-wrap"><h2 style="margin:0">One take of the room ${qbtn('room_take')}</h2>
            <span class="wwm-row"><select id="wwm-rt-mic"><option value="">this browser's default mic</option></select><button class="wwm-x" id="wwm-rt-refresh" title="look again">↻</button>
              <label id="wwm-rt-len-l" style="display:none">for <select id="wwm-rt-len"><option value="30">30 s</option><option value="60">1 min</option><option value="120">2 min</option><option value="300" selected>5 min</option></select></label></span></div>
          <div class="wwm-rec">
            <div class="wwm-phrase">Let the room be the room</div>
            <button class="wwm-btn big primary" id="wwm-rt-btn">● Record</button>
            <div class="wwm-level"><i id="wwm-rt-level"></i></div>
            <div class="wwm-muted" id="wwm-rt-note">Press, let it run, press again. Never say the phrase.</div>
            <label class="wwm-muted">Mic tag <input type="text" id="wwm-rt-dev" value="${esc(localStorage.getItem('wwm.device') || defaultDevice())}" style="min-width:140px" title="how takes from this microphone are labelled"></label>
          </div>
        </div>
        <div class="wwm-card">
          <h2>Upload room sound ${qbtn('upload')}</h2>
          <div class="wwm-drop" id="wwm-rt-drop">Drop files or a zip here, or click to choose</div>
          <input type="file" id="wwm-rt-file" multiple accept=".wav,.flac,.ogg,.mp3,.aiff,.aif,.zip" style="display:none">
          <div class="wwm-muted" id="wwm-rt-upnote">Minutes of a room, nobody saying the phrase. They stay marked as yours.</div>
        </div>
      </div>
      <div class="wwm-card wwm-browse" id="wwm-rt-browser"></div>
      <div class="wwm-card"><h2>Rooms and music ${qbtn('bg_datasets')}</h2><div id="wwm-bg-data">…</div></div>`;
    bindHelp(el);
    el.querySelector('#wwm-rd-pick').addEventListener('click', () => ctx.go('train'));

    // the microphones: satellites through their doors, this PC's inputs through this tab
    let sats = [], inputs = [];
    const micBox = el.querySelector('#wwm-rd-mics'), rtMic = el.querySelector('#wwm-rt-mic');
    const fillMics = async (ask) => {
        try { inputs = (await rec.inputs(ask)).filter(d => d.label); } catch { inputs = []; }
        micBox.innerHTML = sats.map(m => `<label><input type="checkbox" data-rd="sat" value="${esc(m.id)}" ${m.online ? 'checked' : 'disabled'}> ${esc(m.id)}${m.online ? '' : ' (offline)'}${m.capture ? ' · pushes its room' : ''}</label>`).join('')
            + inputs.map(d => `<label><input type="checkbox" data-rd="pc" value="browser:${esc(d.label.slice(0, 40))}" data-id="${esc(d.id)}" checked> ${esc(d.label)} (this PC)</label>`).join('')
            || '<span class="wwm-muted">no microphones found</span>';
        el.querySelector('#wwm-rd-test-l').style.display = sats.some(m => m.capture && m.online) ? '' : 'none';
        const keep = rtMic.value;
        rtMic.innerHTML = `<option value="">this browser's default mic</option>` + inputs.map(d => `<option value="in:${esc(d.id)}">${esc(d.label)}</option>`).join('')
            + sats.map(m => `<option value="sat:${esc(m.id)}" ${m.online ? '' : 'disabled'}>${esc(m.id)}${m.online ? '' : ' (offline)'}</option>`).join('');
        rtMic.value = [...rtMic.options].some(o => o.value === keep) ? keep : (localStorage.getItem('wwm.mic') || '');
        lenRow();
    };
    const lenRow = () => { el.querySelector('#wwm-rt-len-l').style.display = rtMic.value.startsWith('sat:') ? '' : 'none'; };
    rtMic.addEventListener('change', () => { localStorage.setItem('wwm.mic', rtMic.value); lenRow(); });
    api('/mics').then(r => { sats = r.mics || []; fillMics(true); }).catch(() => fillMics(true));
    el.querySelector('#wwm-rt-refresh').addEventListener('click', () => fillMics(true));
    const offChange = rec.onChange(() => fillMics(false));

    // the models on watch: the ones ticked on Train, else the ★ ones (the server's choice when runs is empty)
    let runs = [], sel = new Set();
    api(`/projects/${p.slug}/runs`).then(r => {
        runs = (r.runs || []).filter(x => x.state === 'done' && x.has_model);
        try { sel = new Set(JSON.parse(localStorage.getItem('wwm.testsel') || '[]')); } catch { /* fine */ }
        for (const id of [...sel]) if (!runs.find(x => x.id === id)) sel.delete(id);
        const box = el.querySelector('#wwm-rd-runs');
        if (sel.size) box.innerHTML = [...sel].map(id => { const x = runs.find(q => q.id === id); return `<span class="wwm-chip ${x.chosen ? 'wwm-chosen' : ''}">${x.trainer === 'mww' ? 'ESP32' : 'desktop/Pi'} · ${esc(x.label || x.id)}</span>`; }).join('');
        else box.innerHTML = `<span class="wwm-muted">${runs.some(x => x.chosen) ? 'the ★ models and the built-in ones' : runs.length ? 'the built-in models (★ one on Train, or tick some)' : 'no finished models yet: the room is kept, nothing is scored'}</span>`;
        refresh();
    }).catch(() => refresh());
    const label = (rid) => { if (rid.startsWith('board:')) return `the board itself · ${rid.slice(6)}`; const r = runs.find(x => x.id === rid); return r ? (r.label || rid) + (r.trainer === 'mww' ? ' (ESP32)' : '') : rid; };

    // the target line: minutes of room this wake word has, against the floor and the good mark
    const target = (g) => {
        const t = g?.progress?.ambient; if (!t) return;
        const state = t.have >= t.good ? 'good' : t.have >= t.min ? 'ok' : 'low';
        el.querySelector('#wwm-rd-target').innerHTML = `<span class="wwm-target ${state}" style="display:inline-block;min-width:220px"><span class="wwm-trow"><span>Room sound</span><b>${t.have} min</b><span class="wwm-muted">floor ${t.min} · good ${t.good}</span></span><span class="wwm-bar" style="display:block"><i style="width:${Math.min(100, 100 * t.have / t.good)}%"></i><em style="left:${Math.min(100, 100 * t.min / t.good)}%"></em></span></span>`;
    };
    const guide = () => api(`/projects/${p.slug}/guide`).then(target).catch(() => {});
    guide();

    // the watches: live and done, with the fires to sort
    const out = el.querySelector('#wwm-watch-out'), note = el.querySelector('#wwm-rd-note');
    const block = (w) => {
        const live = w.running && !w.ended;
        const hours = w.seconds / 3600;
        const per = {};
        for (const f of w.fires) for (const [rid, x] of Object.entries(f.runs)) if (x.fires) per[rid] = (per[rid] || 0) + 1;
        const open = w.fires.filter(f => !f.fate);
        return `<div class="wwm-watch" data-watch="${esc(w.id)}" style="margin:8px 0;padding:6px 0;border-top:1px solid var(--border, #333)">
          <div class="wwm-row wwm-between"><span class="wwm-muted">${live ? '● recording' : w.paused ? '⏸ paused' : 'recorded'} through <b>${esc(w.device)}</b>${w.pushed ? ' (pushed by the board' + (w.test ? ', test mode' : '') + ')' : ''}: ${fmt(w.seconds)} heard, ${w.fires.length} fire${w.fires.length === 1 ? '' : 's'}${w.kept ? ` · ${w.kept} piece${w.kept === 1 ? '' : 's'} kept (${fmt(w.kept_seconds || 0)})` : ''}${w.rearmed ? ` · re-armed ${w.rearmed}×` : ''}${w.errors?.length ? ` · <span class="wwm-error">${esc(w.errors[w.errors.length - 1])}</span>` : ''}</span>${live ? `<button class="wwm-btn" data-stop="${esc(w.device)}">Stop</button>` : ''}</div>
          ${Object.keys(per).length || w.seconds > 60 ? `<table class="wwm-table"><thead><tr><th>Model</th><th class="num">Fires</th><th class="num">Per hour</th></tr></thead><tbody>
            ${(w.runs || '').split(',').filter(Boolean).concat(w.board ? [`board:${w.board}`] : []).map(rid => `<tr><td>${esc(label(rid))}</td><td class="num">${per[rid] || 0}</td><td class="num">${hours > 0 ? ((per[rid] || 0) / hours).toFixed(1) : '–'}</td></tr>`).join('')}</tbody></table>` : ''}
          ${open.length ? `<h3>To sort (${open.length})</h3>` + open.slice(-30).reverse().map(f => `<div class="wwm-fire" data-fire="${esc(f.id)}" data-wid="${esc(w.id)}">
              <audio controls preload="none" src="/api/plugin/wakeword-maker/projects/${p.slug}/watch/${w.id}/${f.id}/audio"></audio>
              <span>at ${fmt(f.at)} · ${Object.entries(f.runs).filter(([, x]) => x.fires).map(([rid, x]) => `${esc(label(rid))} ${x.score.toFixed(2)}`).join(', ')}</span>
              <button class="wwm-btn" data-fate="negative" title="keep as a negative sample">False alarm</button>
              <button class="wwm-btn" data-fate="positive" title="keep as a phrase sample; the piece is dropped for this wake word">That was me</button>
              <button class="wwm-x" data-fate="discard" title="drop it">✕</button></div>`).join('') : (w.fires.length ? '<div class="wwm-muted">All sorted.</div>' : '')}</div>`;
    };
    const drawWatches = (list) => {
        if (!el.isConnected) { if (watchPoll) clearInterval(watchPoll); watchPoll = null; return; }   // the tab was left while this answer was on its way
        list = (list || []).filter(Boolean);
        out.innerHTML = list.map(block).join('');
        out.querySelectorAll('[data-fate]').forEach(b => b.addEventListener('click', async () => {
            const row = b.closest('.wwm-fire');
            try { await api(`/projects/${p.slug}/watch/${row.dataset.wid}/${row.dataset.fire}`, { method: 'POST', body: { as: b.dataset.fate } }); row.remove(); if (b.dataset.fate !== 'discard') ctx.toast(`Kept as a ${b.dataset.fate}`, 'success'); }
            catch (e) { ctx.toast(e.message, 'error'); }
        }));
        out.querySelectorAll('[data-stop]').forEach(b => b.addEventListener('click', () => stopOne(b.dataset.stop)));
        const anyLive = list.some(w => w.running && !w.ended);
        if (anyLive && !watchPoll) watchPoll = setInterval(refresh, 15000);
        if (!anyLive && watchPoll) { clearInterval(watchPoll); watchPoll = null; }
    };
    const refresh = () => api(`/projects/${p.slug}/watch`).then(r => drawWatches(r.watches || (r.watch ? [r.watch] : []))).catch(() => {});
    const stopOne = async (device) => {
        if (pcMics[device]) await pcMics[device].stop();          // a PC mic ends its own watch on the server too
        else { try { const r = await api(`/projects/${p.slug}/watch?device=${encodeURIComponent(device)}`, { method: 'DELETE' }); drawWatches(r.watches); } catch (e) { ctx.toast(e.message, 'error'); } }
        refresh(); library(); guide();
    };
    el.querySelector('#wwm-rd-go').addEventListener('click', async () => {
        const minutes = parseInt(el.querySelector('#wwm-rd-min').value) || 360;
        const satIds = [...micBox.querySelectorAll('[data-rd="sat"]:checked')].map(i => i.value);
        const pcs = [...micBox.querySelectorAll('[data-rd="pc"]:checked')].map(i => ({ device: i.value, id: i.dataset.id }));
        if (!satIds.length && !pcs.length) return ctx.toast('Tick at least one microphone.', 'warning');
        let started = [], refused = [], onWatch = '';
        try {
            const r = await api(`/projects/${p.slug}/room-day`, { method: 'POST', body: { minutes, devices: satIds, runs: [...sel].join(','), test: el.querySelector('#wwm-rd-test').checked } });
            started = r.started; refused = r.refused; onWatch = r.runs || '';
        } catch (e) { ctx.toast(e.message, 'error'); return; }
        for (const m of pcs) {
            if (pcMics[m.device]) { refused.push({ device: m.device, error: 'already recording from this tab' }); continue; }
            try {
                await api(`/projects/${p.slug}/watch`, { method: 'POST', body: { device: m.device, minutes, runs: onWatch, keep: true } });
                await pcStream(p.slug, m.device, m.id, minutes, onWatch, (t) => { note.textContent = t; });
                started.push({ device: m.device });
            } catch (e) { refused.push({ device: m.device, error: e.message }); }
        }
        note.textContent = `${started.length} microphone${started.length === 1 ? '' : 's'} recording for ${minutes} min${refused.length ? ` · refused: ${refused.map(r => `${r.device} (${r.error})`).join('; ')}` : ''}`;
        setTimeout(refresh, 1000);
    });
    el.querySelector('#wwm-rd-stop').addEventListener('click', async () => {
        for (const d of Object.keys(pcMics)) await pcMics[d].stop();
        try { const r = await api(`/projects/${p.slug}/room-day`, { method: 'DELETE' }); drawWatches(r.watches); } catch (e) { ctx.toast(e.message, 'error'); }
        note.textContent = 'Stopped every microphone of this wake word. A Pi finishes the piece it is on first.';
        library(); guide();
    });

    // the library: hours per microphone, the days that made them, delete a day
    const library = async () => {
        const lib = el.querySelector('#wwm-lib'), sum = el.querySelector('#wwm-lib-sum');
        let r;
        try { r = await api(`/room?project=${encodeURIComponent(p.slug)}`); } catch (e) { lib.innerHTML = `<span class="wwm-error">${esc(e.message)}</span>`; return; }
        if (!r.devices?.length) { sum.textContent = ''; lib.innerHTML = 'Empty so far. A Room day fills it.'; return; }
        sum.textContent = `${(r.minutes / 60).toFixed(1)} h over ${r.devices.length} mic${r.devices.length === 1 ? '' : 's'}${r.dropped_for ? ` · ${r.dropped_for} piece${r.dropped_for === 1 ? '' : 's'} dropped for this phrase` : ''}`;
        const days = (r.watches || []).slice(0, 12);
        lib.innerHTML = `<div class="wwm-grid2">
          <table class="wwm-table"><thead><tr><th>Microphone</th><th class="num">Hours</th><th class="num">Pieces</th><th class="num">Days</th></tr></thead><tbody>
            ${r.devices.map(d => `<tr><td>${esc(d.device)}</td><td class="num">${(d.minutes / 60).toFixed(1)}</td><td class="num">${d.pieces}</td><td class="num">${d.watches}</td></tr>`).join('')}</tbody></table>
          <table class="wwm-table"><thead><tr><th>Day</th><th>Microphone</th><th class="num">Minutes</th><th></th></tr></thead><tbody>
            ${days.map(w => `<tr><td>${esc(when(w.first))}${w.project && w.project !== p.slug ? ` <span class="wwm-muted">(${esc(w.project)})</span>` : ''}</td><td>${esc(w.device)}</td><td class="num">${w.minutes}</td><td class="num"><button class="wwm-x" data-delw="${esc(w.watch)}" title="delete every piece this day kept">✕</button></td></tr>`).join('')}</tbody></table></div>`;
        lib.querySelectorAll('[data-delw]').forEach(b => b.addEventListener('click', () => {
            const w = days.find(x => x.watch === b.dataset.delw);
            showConfirm(`Delete the ${w.minutes} minutes ${w.device} kept on ${when(w.first)}? Every wake word loses them.`, async () => {
                try { await api(`/room?watch=${encodeURIComponent(w.watch)}`, { method: 'DELETE' }); } catch (e) { ctx.toast(e.message, 'error'); }
                library(); guide(); clips.load();
            }, { title: 'Delete a day', saveLabel: 'Delete' });
        }));
    };
    library();

    // one take of the room: this browser's mic, or a satellite for a chosen length
    const btn = el.querySelector('#wwm-rt-btn'), rtNote = el.querySelector('#wwm-rt-note'), devName = el.querySelector('#wwm-rt-dev');
    devName.addEventListener('change', () => localStorage.setItem('wwm.device', devName.value.trim()));
    let busy = false;
    const kept = (r) => { p.counts = r.counts; if (r.guide) target(r.guide); else guide(); clips.prepend(r.clips || [r.clip], 'ambient/recorded'); };
    const toggle = async () => {
        if (busy) return;
        const mic = rtMic.value;
        if (mic.startsWith('sat:')) {
            const id = mic.slice(4);
            let secs = parseInt(el.querySelector('#wwm-rt-len').value) || 300;
            if (sats.find(m => m.id === id)?.format === 'tflite' && secs > 60) { secs = 60; ctx.toast('An ESP32 records the room 60 s at a time (its memory); taking 60 s.', 'info'); }
            busy = true; btn.textContent = '… recording'; btn.disabled = true;
            let left = secs; const tick = setInterval(() => { left -= 1; if (left > 0) rtNote.textContent = `${id} is recording the room: ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left. Never say the phrase.`; }, 1000);
            rtNote.textContent = `${id} is recording the room for ${secs >= 60 ? secs / 60 + ' min' : secs + ' s'}. Let it be the room; never say the phrase.`;
            try { const r = await api(`/projects/${p.slug}/record-satellite`, { method: 'POST', body: { device: id, collection: 'ambient/recorded', seconds: secs } }); rtNote.textContent = `Kept from ${id}: ${r.clip.seconds}s. Again?`; kept(r); }
            catch (e) { rtNote.textContent = `Not kept: ${e.message}`; }
            clearInterval(tick); btn.textContent = '● Record'; btn.disabled = false; busy = false; return;
        }
        if (!rec.isRecording()) {
            try { await rec.start(mic.startsWith('in:') ? mic.slice(3) : undefined); } catch (e) { ctx.toast(`Microphone: ${e.message}`, 'error'); return; }
            btn.textContent = '■ Stop'; btn.classList.add('rec'); rtNote.textContent = 'Recording the room. Stop whenever you have enough.';
            meter = setInterval(() => { el.querySelector('#wwm-rt-level').style.width = Math.min(100, rec.peak() * 140) + '%'; }, 80);
            return;
        }
        busy = true; clearInterval(meter); meter = null; btn.textContent = '● Record'; btn.classList.remove('rec'); el.querySelector('#wwm-rt-level').style.width = '0';
        const take = await rec.stop(); busy = false;
        if (!take) return;
        const form = new FormData();
        form.append('audio', take.blob, 'room.wav'); form.append('collection', 'ambient/recorded');
        form.append('device', devName.value.trim() || (rtMic.selectedOptions[0]?.value ? rtMic.selectedOptions[0].textContent.trim() : '') || defaultDevice());
        form.append('texts', ''); form.append('drop_head_ms', '80'); form.append('drop_tail_ms', '80');
        rtNote.textContent = 'Sending…';
        try { const r = await api(`/projects/${p.slug}/record`, { method: 'POST', form }); rtNote.textContent = `Kept: ${r.clip.seconds}s${r.refused?.length ? ` · not kept: ${r.refused[0]}` : ''}. Again?`; kept(r); }
        catch (e) { rtNote.textContent = `Not kept: ${e.data?.refused?.[0] || e.message}`; }
    };
    btn.addEventListener('click', toggle);

    // uploads into ambient/uploaded
    const drop = el.querySelector('#wwm-rt-drop'), file = el.querySelector('#wwm-rt-file'), upnote = el.querySelector('#wwm-rt-upnote');
    drop.addEventListener('click', () => file.click());
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); sendFiles(e.dataTransfer.files); });
    file.addEventListener('change', () => { sendFiles(file.files); file.value = ''; });
    async function sendFiles(files) {
        if (!files?.length) return;
        const form = new FormData();
        form.append('collection', 'ambient/uploaded');
        let bytes = 0;
        for (const f of files) { form.append('files', f, f.name); bytes += f.size; }
        upnote.textContent = `Uploading ${files.length} file(s), ${(bytes / 1e6).toFixed(1)} MB…`;
        try {
            const r = await api(`/projects/${p.slug}/upload`, { method: 'POST', form });
            upnote.textContent = `Added ${r.added}.` + (r.skipped.length ? ` Skipped ${r.skipped.length}: ${r.skipped.slice(0, 3).map(s => `${s.name} (${s.why})`).join('; ')}${r.skipped.length > 3 ? '…' : ''}` : '');
            p.counts = r.counts; guide(); clips.load();
        } catch (e) { upnote.textContent = e.message; }
    }

    const clips = browser(el.querySelector('#wwm-rt-browser'), ctx, p, { kinds: [['ambient', 'Room sound']], key: 'wwm.bgkind', onDelete: () => { guide(); library(); } });
    const dataBox = el.querySelector('#wwm-bg-data');
    const datasets = () => drawDatasets(dataBox, ctx, d => d.for === 'both');
    datasets();

    return {
        cleanup() {
            if (watchPoll) clearInterval(watchPoll); watchPoll = null;
            if (meter) clearInterval(meter); meter = null;
            if (rec.isRecording()) rec.stop();
            offChange();
        },
        onJob(j) { if (j.final && (j.kind === 'download' || j.kind === 'delete_dataset')) datasets(); },
    };
}


// ---- PC microphones on a room day: one stream each (their own AudioContext), 20 s pieces posted with the mic's
// name. Module-level so a tab switch does not end them; closing the page does. Satellites run on the server.
const pcMics = {};          // device -> {stop}

async function pcStream(slug, device, deviceId, minutes, runs, onNote) {
    const audio = { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false };
    if (deviceId) audio.deviceId = { exact: deviceId };
    const stream = await navigator.mediaDevices.getUserMedia({ audio });
    const ac = new AudioContext(); const source = ac.createMediaStreamSource(stream); const proc = ac.createScriptProcessor(4096, 1, 1);
    let chunks = [], alive = true, posting = false, held = 0, inflight = Promise.resolve();
    const end = Date.now() + minutes * 60000;
    const CAP = 120;              // seconds kept back while the server is slow or away; past that the oldest piece is let go
    proc.onaudioprocess = (e) => { if (alive) chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    source.connect(proc); proc.connect(ac.destination);
    const rearm = async () => {   // Sapphire restarted under us: its browser watches live in memory, so ask for ours again for the time left
        await api(`/projects/${slug}/watch`, { method: 'POST', body: { device, minutes: Math.max(1, Math.round((end - Date.now()) / 60000)), runs, keep: true } });
    };
    const flush = async (final = false) => {
        if (posting) return;                                     // the last piece is still on its way: this tick's audio rides with the next
        const n = chunks.reduce((a, c) => a + c.length, 0);
        if (n < ac.sampleRate) return;
        const mine = chunks; chunks = [];
        const all = new Float32Array(n); let o = 0; for (const c of mine) { all.set(c, o); o += c.length; }
        posting = true;
        try {
            const form = new FormData(); form.append('audio', encodeWAV(all, ac.sampleRate), 'piece.wav'); form.append('device', device);
            try { await api(`/projects/${slug}/watch/chunk`, { method: 'POST', form }); held = 0; }
            catch (e) {
                if (!/no browser watch/i.test(e.message)) throw e;
                await rearm();                                   // then the piece once more
                await api(`/projects/${slug}/watch/chunk`, { method: 'POST', form });
                onNote(`${device}: Sapphire came back; recording again`); held = 0;
            }
        } catch (e) {
            const secs = all.length / ac.sampleRate;
            if (!final && held + secs <= CAP) { chunks.unshift(all); held += secs; onNote(`${device}: ${e.message}; keeping ${Math.round(held)} s for the next try`); }
            else onNote(`${device}: ${e.message}; ${Math.round(secs)} s of room lost`);
        }
        posting = false;
    };
    const timer = setInterval(() => { if (Date.now() > end) { stop(); return; } inflight = flush(); }, 20000);
    const stop = async () => {
        if (!alive) return;
        alive = false; clearInterval(timer);
        try { await inflight; await flush(true); } catch { /* fine */ }   // the last partial piece goes too
        try { proc.disconnect(); source.disconnect(); } catch { /* fine */ }
        stream.getTracks().forEach(t => t.stop());
        try { await ac.close(); } catch { /* fine */ }
        delete pcMics[device];
        try { await api(`/projects/${slug}/watch?device=${encodeURIComponent(device)}`, { method: 'DELETE' }); } catch { /* away: the server lets a silent browser watch go on its own */ }
    };
    pcMics[device] = { stop };
    return stop;
}
