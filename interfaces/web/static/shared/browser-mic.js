// shared/browser-mic.js - the browser mic test: is THIS device's mic reaching
// Sapphire? Settings > Audio (Settings > STT on managed installs, where Audio is
// hidden). Pick a mic, watch the level, say something and read the words back.
//
// A site the person blocked can't be asked again: the browser refuses with no
// popup, so the panel gives the steps and carries on by itself once they allow
// it. A flat meter is its own finding, since a browser can hand over a working
// stream that carries only silence.
import { micAvailable, micAudio, micPermission, micWhy, micWords, chosenMic, chooseMic, MIC_PRIVACY_STEPS } from './mic-words.js';
import { downsample, encodeWAV, SAMPLE_RATE } from '../audio.js';
import { postAudio } from '../api.js';

const TICK_MS = 100;
const HEARD = 0.02;          // peak level that counts as sound, not hiss
const SILENT_AFTER_MS = 6000;
const STOP_AFTER_MS = 60000; // never leave a mic open on a forgotten page
const SAY_MS = 3000;

export function browserMicHtml() {
    const other = window.__managed ? '' : ' The Input Device above is the mic on the Sapphire computer, a different one.';
    return `
        <div class="audio-section browser-mic" data-browser-mic>
            <h4>Browser Mic (this device)</h4>
            <div class="field-hint">The mic push-to-talk and browser Conversation use.${other}</div>
            <div class="browser-mic-row">
                <select data-bm-select title="Which mic this browser uses"><option value="">Browser default</option></select>
                <button type="button" class="btn-sm" data-bm-test>🎙 Test</button>
            </div>
            <div class="browser-mic-meter hidden" data-bm-meter><div data-bm-level></div></div>
            <div class="test-result" data-bm-status role="status"></div>
            <div class="browser-mic-say hidden" data-bm-say-row>
                <button type="button" class="btn-sm" data-bm-say>Say something</button>
                <div class="test-result" data-bm-heard></div>
            </div>
        </div>`;
}

export function attachBrowserMic(el) {
    const root = el.querySelector('[data-browser-mic]');
    if (!root || root._bound) return;
    root._bound = true;
    const $ = sel => root.querySelector(sel);
    const select = $('[data-bm-select]'), testBtn = $('[data-bm-test]'), sayBtn = $('[data-bm-say]');
    let live = null;         // { stream, ctx, timer, recording, chunks, loudest, started }
    let waiting = false;     // refused: start by itself once the person allows it

    const say = (words, kind = '') => {
        const s = $('[data-bm-status]');
        s.textContent = words;
        s.className = `test-result${kind ? ` ${kind}` : ''}`;
    };
    const heard = (words, kind = '') => {
        const s = $('[data-bm-heard]');
        s.textContent = words;
        s.className = `test-result${kind ? ` ${kind}` : ''}`;
    };

    async function listMics() {
        let mics = [];
        try { mics = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput' && d.deviceId); } catch {}
        const want = chosenMic();
        select.replaceChildren(new Option('Browser default', ''));
        mics.forEach((m, i) => select.add(new Option(m.label || `Microphone ${i + 1}`, m.deviceId)));
        select.value = mics.some(m => m.deviceId === want) ? want : '';
    }

    function stop(words) {
        if (live) {
            clearInterval(live.timer);
            live.stream.getTracks().forEach(t => t.stop());
            try { live.ctx.close(); } catch {}
            live = null;
        }
        testBtn.textContent = '🎙 Test';
        $('[data-bm-meter]').classList.add('hidden');
        $('[data-bm-say-row]').classList.add('hidden');
        if (words) say(words);
    }

    function tick(analyser, track, samples) {
        // left the page or the tab: hand the mic back
        if (!root.isConnected || root.offsetParent === null) { stop(); return; }
        const age = Date.now() - live.started;
        if (track.readyState === 'ended') { stop('The mic went away. Plug it back in, then test again.'); return; }
        if (age > STOP_AFTER_MS) { stop('Test finished, the mic is closed.'); return; }
        analyser.getFloatTimeDomainData(samples);
        const peak = samples.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
        live.loudest = Math.max(live.loudest, peak);
        $('[data-bm-level]').style.width = `${Math.min(100, Math.round(peak * 200))}%`;
        const name = track.label || 'the mic';
        if (live.loudest >= HEARD) say(`Hearing you on ${name}.`, 'success');
        else if (age < SILENT_AFTER_MS) say(`Mic open on ${name}. Say something...`);
        else if (live.loudest === 0 || track.muted) say(`${name} opened but sends total silence. It may be muted or switched off, or the computer's microphone privacy setting is blocking it. Try another mic in the list.`, 'error');
        else say(`${name} is barely picking anything up. Try another mic in the list, or turn its level up in the computer's sound settings.`, 'error');
    }

    async function start() {
        stop();
        heard('');
        waiting = false;
        if (!micAvailable()) { say(micWords(null), 'error'); return; }
        say('Asking the browser for the mic. Allow it if a popup appears.');
        let stream;
        try {
            // the same ask as push-to-talk, so the test hears what she would
            stream = await navigator.mediaDevices.getUserMedia({
                audio: micAudio({ channelCount: 1, echoCancellation: true, noiseSuppression: true }),
            });
        } catch (e) {
            console.warn('[BrowserMic] refused:', e);
            waiting = true;
            const why = await micWhy(e);
            say(why.includes('privacy') ? `${why} ${MIC_PRIVACY_STEPS}` : why, 'error');
            return;
        }
        if (!root.isConnected) { stream.getTracks().forEach(t => t.stop()); return; }
        try {
            const ctx = new AudioContext();
            await ctx.resume();
            const source = ctx.createMediaStreamSource(stream);
            const analyser = ctx.createAnalyser();
            analyser.fftSize = 2048;
            source.connect(analyser);
            const capture = ctx.createScriptProcessor(4096, 1, 1);   // as push-to-talk records
            capture.onaudioprocess = e => { if (live?.recording) live.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
            source.connect(capture);
            capture.connect(ctx.destination);                        // silent; keeps it pulled
            const samples = new Float32Array(analyser.fftSize);
            const track = stream.getAudioTracks()[0];
            live = { stream, ctx, recording: false, chunks: [], loudest: 0, started: Date.now() };
            live.timer = setInterval(() => tick(analyser, track, samples), TICK_MS);
        } catch (e) {
            console.warn('[BrowserMic] capture failed:', e);
            stream.getTracks().forEach(t => t.stop());
            say(micWords(e), 'error');
            return;
        }
        testBtn.textContent = 'Stop';
        $('[data-bm-meter]').classList.remove('hidden');
        $('[data-bm-say-row]').classList.remove('hidden');
        await listMics();    // names only exist once the mic is allowed
    }

    async function sayCheck() {
        if (!live || live.recording) return;
        const run = live;
        run.chunks = [];
        run.recording = true;
        sayBtn.disabled = true;
        heard('Listening. Say a few words...');
        await new Promise(r => setTimeout(r, SAY_MS));
        run.recording = false;
        try {
            if (live !== run) { heard(''); return; }     // stopped or switched mid-take
            const total = run.chunks.reduce((n, c) => n + c.length, 0);
            const all = new Float32Array(total);
            let at = 0;
            for (const c of run.chunks) { all.set(c, at); at += c.length; }
            if (!all.some(v => Math.abs(v) >= HEARD)) {
                heard('That take was silent, so nothing was sent. Watch the level bar while you talk.', 'error');
                return;
            }
            heard('Turning it into text...');
            const res = await postAudio(encodeWAV(downsample(all, run.ctx.sampleRate, SAMPLE_RATE), SAMPLE_RATE));
            if (res?.text) heard(`Heard: "${res.text}". Your mic reaches Sapphire.`, 'success');
            else heard('The mic works, but no words came back. Try again, a little closer.', 'error');
        } catch (e) {
            heard(`The mic works, but speech-to-text said: ${e?.message || 'it failed'}`, 'error');
        } finally {
            sayBtn.disabled = false;
        }
    }

    testBtn.addEventListener('click', () => { live ? stop('Test stopped, the mic is closed.') : start(); });
    sayBtn.addEventListener('click', sayCheck);
    select.addEventListener('change', () => {
        chooseMic(select.value);
        if (live) start();
        else say('Saved for this browser. Test it to be sure.');
    });

    // What can be known without opening the mic: never a popup nobody clicked for.
    (async () => {
        if (!micAvailable()) { say(micWords(null), 'error'); return; }
        const perm = await micPermission();
        if (perm === 'granted') await listMics();
        else if (perm === 'denied') { waiting = true; say(micWords({ name: 'NotAllowedError' }, 'denied'), 'error'); }
        else say('Press Test. The browser will ask to use the mic.');
        try {
            const status = await navigator.permissions.query({ name: 'microphone' });
            root._permission = status;     // held, or the browser may drop the listener
            status.onchange = () => {
                if (root.isConnected && status.state === 'granted' && waiting && root.offsetParent !== null) start();
            };
        } catch {}
    })();
}
