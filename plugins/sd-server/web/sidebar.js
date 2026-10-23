// Reel — ambient slideshow in the chat sidebar. Plays the active Slideshow
// profile (configured in Image Studio) on its cooldown timer, so it runs while
// you chat. Pauses when the tab is hidden (GPU rest, no wasted gens).
//
// chat.js re-mounts this accordion on every chat switch and the module is
// CACHED (module-level state is shared across mounts), so init() is made
// idempotent: it tears down any prior loop/listener before wiring the new DOM.

const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const SET_URL = '/api/webui/plugins/sd-server/settings';

let _running = false;
let _timer = null;
let _inflight = false;
let _content = null;
let _vis = null;

async function loadActiveProfile() {
    try {
        const r = await fetch(SET_URL);
        const d = await r.json();
        const s = d.settings || d || {};
        const profs = Array.isArray(s.slideshow_profiles) ? s.slideshow_profiles : [];
        const idx = Math.max(0, Math.min(parseInt(s.slideshow_active) || 0, profs.length - 1));
        return profs[idx] || null;
    } catch (e) { return null; }
}

function status(msg) {
    const el = _content && _content.querySelector('#reel-status');
    if (el) el.textContent = msg || '';
}

function setBtn(on) {
    const b = _content && _content.querySelector('#reel-go');
    if (b) { b.textContent = on ? '■ Stop reel' : '▶ Start reel'; b.classList.toggle('stop', on); }
}

async function tick() {
    // Single-flight: a visibility-resume (or any extra trigger) must not start a
    // second concurrent loop — that's what caused 2 images back-to-back.
    if (!_running || _inflight) return;
    _inflight = true;
    if (_timer) { clearTimeout(_timer); _timer = null; }
    let interval = 20;
    try {
        // Re-read each tick so a profile edit / switch in the Studio applies live.
        const p = await loadActiveProfile();
        if (!p || !(p.slots || []).length) { status('No active profile — set one in Image Studio.'); stop(); return; }
        interval = Math.max(2, p.interval_sec || 20);
        status('Generating…');
        const r = await fetch('/api/plugin/sd-server/slideshow/next', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ slots: p.slots, aspects: p.aspects || ['square'], expand: p.expand !== false,
                steps: p.steps, cfg_scale: p.cfg_scale, negative_prompt: p.negative_prompt,
                sampler_name: p.sampler_name, scheduler: p.scheduler }),
        });
        const d = await r.json();
        if (d.success) {
            const img = _content.querySelector('#reel-img');
            img.src = d.image; img.style.display = 'block';
            status(`${p.name} · ${d.aspect} · ${d.elapsed}s`);
        } else {
            status('Error: ' + (d.error || 'failed'));
        }
    } catch (e) {
        status('Request failed');
    } finally {
        _inflight = false;
    }
    if (_running && !document.hidden && !_timer) {
        _timer = setTimeout(tick, interval * 1000);
    }
}

function start() { _running = true; setBtn(true); tick(); }

function stop() {
    _running = false;
    if (_timer) { clearTimeout(_timer); _timer = null; }
    setBtn(false);
}

export function init(content) {
    // Idempotent: a prior mount may have left a running loop / listener (cached module).
    if (_vis) { document.removeEventListener('visibilitychange', _vis); _vis = null; }
    if (_timer) { clearTimeout(_timer); _timer = null; }
    _running = false;
    _content = content;

    const btn = content.querySelector('#reel-go');
    if (btn) btn.addEventListener('click', () => (_running ? stop() : start()));

    _vis = () => {
        if (document.hidden) { if (_timer) { clearTimeout(_timer); _timer = null; } }
        else if (_running && !_timer) { tick(); }
    };
    document.addEventListener('visibilitychange', _vis);
}
