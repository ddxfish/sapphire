// Image Studio (sd-server plugin) — two modes:
//   Generate  — one-off tuning (prompt/CFG/seed/steps), POST .../generate
//   Slideshow — wildcard auto-reel: user-defined slots randomly combined into a
//               prompt, generated on a cooldown timer. Profiles persist as a
//               side-channel key in plugin settings (shallow-merged, safe).

let _container = null;
let _busy = false;

// slideshow state
let _profiles = [];
let _active = 0;
let _genDefaults = {};   // plugin-settings gen defaults, for placeholders + inherit
let _running = false;
let _timer = null;
let _inflight = false;
let _ring = [];
let _visHandler = null;
const RING_MAX = 12;
const SET_URL = '/api/webui/plugins/sd-server/settings';

const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Sampler/scheduler dropdowns for real-time tuning. Blank "(default)" = fall back
// to the saved plugin setting (backend: body.sampler_name || settings.default_sampler).
const SAMPLERS = ['', 'euler a', 'euler', 'heun', 'dpm2', 'dpm++ 2m', 'lcm', 'ddim',
                  'res multistep', 'res 2s', 'euler_cfg_pp', 'euler_a_cfg_pp'];
const SCHEDULERS = ['', 'karras', 'exponential', 'discrete', 'sgm_uniform', 'ays', 'gits'];
const OPTS = (vals, sel) => vals.map(v =>
    `<option value="${esc(v)}"${v === (sel || '') ? ' selected' : ''}>${v || '(default)'}</option>`).join('');

const DEFAULT_PROFILE = () => ({
    name: 'Default',
    slots: [
        { name: 'Subject', options: ['Sapphire'] },
        { name: 'Outfit', options: ['a cozy hoodie', 'a red sundress'] },
        { name: 'Location', options: ['in a spaceship', 'on a boat', 'underwater', 'a Tokyo street at night'] },
        { name: 'Style', options: ['cinematic', 'polaroid', '35mm film'] },
    ],
    interval_sec: 20,
    aspects: ['square'],
    expand: true,
    // Gen overrides — blank = inherit the plugin Settings defaults.
    steps: '', cfg_scale: '', negative_prompt: '', sampler_name: '', scheduler: '',
});

export async function render(container) {
    _container = container;
    container.innerHTML = `
      <style>
        .zis-wrap { max-width: 1100px; margin: 0 auto; padding: 16px; }
        .zis-tabs { display:flex; gap:8px; margin-bottom:14px; }
        .zis-tab { padding:8px 16px; border:1px solid var(--border,#333); background:var(--bg,#111);
            color:var(--text-dim,#9aa); border-radius:8px; cursor:pointer; font-weight:600; }
        .zis-tab.active { background:var(--accent,#4a7dff); color:#fff; border-color:transparent; }
        .zis-grid { display: grid; grid-template-columns: 360px 1fr; gap: 18px; align-items: start; }
        @media (max-width: 820px) { .zis-grid { grid-template-columns: 1fr; } }
        .zis-panel { background: var(--bg-elev, #1b1b22); border: 1px solid var(--border, #333);
                     border-radius: 10px; padding: 14px; }
        .zis-wrap h2 { margin: 0 0 4px; }
        .zis-sub { color: var(--text-dim, #9aa); font-size: 13px; margin: 0 0 14px; }
        .zis-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
        .zis-field { display: flex; flex-direction: column; gap: 4px; }
        .zis-field.grow { flex: 1 1 100%; }
        .zis-field label { font-size: 12px; color: var(--text-dim, #9aa); }
        .zis-field input, .zis-field textarea, .zis-field select {
            background: var(--bg, #111); color: var(--text, #eee);
            border: 1px solid var(--border, #333); border-radius: 6px; padding: 7px 9px; font: inherit; }
        .zis-field select option { background: var(--bg, #111); color: var(--text, #eee); }
        .zis-field textarea { min-height: 96px; resize: vertical; }
        .zis-field.w90 input { width: 84px; }
        .zis-check { display: flex; align-items: center; gap: 7px; font-size: 13px; margin-top: 12px; }
        .zis-btn { margin-top: 14px; width: 100%; padding: 11px; border: none; border-radius: 8px;
            background: var(--accent, #4a7dff); color: #fff; font-weight: 600; cursor: pointer; }
        .zis-btn.sec { background: var(--bg, #111); border:1px solid var(--border,#333); color:var(--text,#eee); }
        .zis-btn.stop { background:#c0392b; }
        .zis-btn:disabled { opacity: .55; cursor: default; }
        .zis-hint { font-size: 11px; color: var(--text-dim, #888); margin-top: 3px; }
        .zis-status { min-height: 20px; font-size: 13px; color: var(--text-dim, #9aa); margin-bottom: 10px; }
        .zis-status.err { color: #ff6b6b; }
        .zis-meta { font-size: 12px; color: var(--text-dim, #9aa); background: var(--bg, #111);
            border: 1px solid var(--border, #333); border-radius: 6px; padding: 8px 10px;
            margin-bottom: 12px; white-space: pre-wrap; word-break: break-word; }
        .zis-imgs { display: flex; flex-wrap: wrap; gap: 10px; }
        .zis-imgs figure { margin: 0; }
        .zis-imgs img { max-width: 100%; max-height: 70vh; border-radius: 8px; display: block;
            border: 1px solid var(--border, #333); cursor: zoom-in; }
        .zis-imgs figcaption { font-size: 11px; color: var(--text-dim, #888); margin-top: 3px;
            display: flex; gap: 10px; }
        .zis-imgs a { color: var(--accent, #4a7dff); text-decoration: none; }
        /* slideshow */
        .sl-pbar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
        .sl-pbar select { flex:1 1 140px; background:var(--bg,#111); color:var(--text,#eee);
            border:1px solid var(--border,#333); border-radius:6px; padding:6px 8px; }
        .sl-mini { padding:6px 9px; border:1px solid var(--border,#333); background:var(--bg,#111);
            color:var(--text,#eee); border-radius:6px; cursor:pointer; font-size:12px; }
        .sl-slot { border:1px solid var(--border,#333); border-radius:8px; padding:8px; margin-top:8px; }
        .sl-slot-hd { display:flex; gap:6px; align-items:center; margin-bottom:5px; }
        .sl-slot-hd input { flex:1; background:var(--bg,#111); color:var(--text,#eee);
            border:1px solid var(--border,#333); border-radius:6px; padding:5px 7px; font-weight:600; }
        .sl-slot textarea { width:100%; min-height:54px; resize:vertical; box-sizing:border-box;
            background:var(--bg,#111); color:var(--text,#eee); border:1px solid var(--border,#333);
            border-radius:6px; padding:6px 8px; font:inherit; }
        .sl-x { border:none; background:#3a2530; color:#ff8a8a; border-radius:6px; cursor:pointer; padding:4px 9px; }
        .sl-asp { display:flex; gap:14px; margin-top:6px; font-size:13px; }
        .sl-asp label { display:flex; gap:5px; align-items:center; }
        .sl-now img { max-width:100%; max-height:64vh; border-radius:10px; display:block;
            border:1px solid var(--border,#333); }
        .sl-ring { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
        .sl-ring figure { margin:0; width:84px; }
        .sl-ring img { width:84px; height:84px; object-fit:cover; border-radius:6px; cursor:pointer;
            border:1px solid var(--border,#333); }
        .sl-ring .cap { display:flex; gap:8px; justify-content:center; font-size:15px; margin-top:2px; }
        .sl-ring .cap span { cursor:pointer; }
      </style>
      <div class="zis-wrap">
        <h2>🖼️ Image Studio</h2>
        <p class="zis-sub">stable-diffusion.cpp (sd-server). Names you configured in Settings get swapped for appearances when Expand is on.</p>
        <div class="zis-tabs">
          <button class="zis-tab active" data-tab="generate">Generate</button>
          <button class="zis-tab" data-tab="slideshow">Slideshow</button>
        </div>

        <div data-panel="generate">
          <div class="zis-grid">
            <div class="zis-panel">
              <div class="zis-field grow">
                <label>Prompt</label>
                <textarea id="zis-prompt" placeholder="Sapphire, sitting by a window, soft light"></textarea>
              </div>
              <div class="zis-row">
                <div class="zis-field w90"><label>Count</label><input id="zis-count" type="number" min="1" value="1"></div>
                <div class="zis-field w90"><label>Steps</label><input id="zis-steps" type="number" min="1" placeholder="8"></div>
                <div class="zis-field w90"><label>CFG</label><input id="zis-cfg" type="number" step="0.1" placeholder="1.0"></div>
              </div>
              <div class="zis-row">
                <div class="zis-field w90"><label>Width</label><input id="zis-w" type="number" step="64" placeholder="1024"></div>
                <div class="zis-field w90"><label>Height</label><input id="zis-h" type="number" step="64" placeholder="1024"></div>
                <div class="zis-field grow"><label>Seed (blank = random)</label><input id="zis-seed" type="text" placeholder="random"></div>
              </div>
              <div class="zis-row">
                <div class="zis-field grow"><label>Sampler</label><select id="zis-sampler">${OPTS(SAMPLERS, '')}</select></div>
                <div class="zis-field grow"><label>Scheduler</label><select id="zis-scheduler">${OPTS(SCHEDULERS, '')}</select></div>
              </div>
              <div class="zis-field grow" style="margin-top:10px;">
                <label>Negative prompt</label>
                <input id="zis-neg" type="text" placeholder="(inert at CFG 1.0)">
                <div class="zis-hint">Z-Image Turbo ignores the negative prompt at CFG≈1. Raise CFG to make it bite.</div>
              </div>
              <label class="zis-check"><input id="zis-expand" type="checkbox" checked> Expand me / you + keywords</label>
              <button id="zis-go" class="zis-btn">Generate</button>
            </div>
            <div class="zis-panel">
              <div id="zis-status" class="zis-status">Ready.</div>
              <div id="zis-meta" class="zis-meta" style="display:none;"></div>
              <div id="zis-imgs" class="zis-imgs"></div>
            </div>
          </div>
        </div>

        <div data-panel="slideshow" style="display:none;">
          <div class="zis-grid">
            <div class="zis-panel">
              <div class="sl-pbar">
                <select id="sl-profile"></select>
                <button class="sl-mini" id="sl-save">Save</button>
                <button class="sl-mini" id="sl-new">New</button>
                <button class="sl-mini" id="sl-rename">Rename</button>
                <button class="sl-mini" id="sl-del">Delete</button>
              </div>
              <div id="sl-slots"></div>
              <button class="zis-btn sec" id="sl-addslot" style="margin-top:10px;">+ Add slot</button>
              <div class="zis-row" style="margin-top:12px;">
                <div class="zis-field w90"><label>Seconds between</label><input id="sl-interval" type="number" min="2" value="20"></div>
                <div class="zis-field" style="flex:1;">
                  <label>Aspect (random among checked)</label>
                  <div class="sl-asp">
                    <label><input type="checkbox" class="sl-asp-cb" value="square" checked> Square</label>
                    <label><input type="checkbox" class="sl-asp-cb" value="portrait"> Portrait</label>
                    <label><input type="checkbox" class="sl-asp-cb" value="landscape"> Landscape</label>
                  </div>
                </div>
              </div>
              <label class="zis-check"><input id="sl-expand" type="checkbox" checked> Expand names + keywords</label>
              <div class="zis-row" style="margin-top:10px;">
                <div class="zis-field w90"><label>Steps</label><input id="sl-steps" type="number" min="1"></div>
                <div class="zis-field w90"><label>CFG</label><input id="sl-cfg" type="number" step="0.1"></div>
                <div class="zis-field grow"><label>Negative prompt</label><input id="sl-neg" type="text"></div>
              </div>
              <div class="zis-row">
                <div class="zis-field grow"><label>Sampler</label><select id="sl-sampler">${OPTS(SAMPLERS, '')}</select></div>
                <div class="zis-field grow"><label>Scheduler</label><select id="sl-scheduler">${OPTS(SCHEDULERS, '')}</select></div>
              </div>
              <div class="zis-hint" style="margin-top:6px;">Blank = use Settings defaults (shown as placeholders). Negative prompts only bite at CFG &gt; 1 — raise CFG for SDXL/Pony.</div>
              <button class="zis-btn sec" id="sl-preview">Preview prompt</button>
              <button id="sl-go" class="zis-btn">▶ Start slideshow</button>
            </div>
            <div class="zis-panel">
              <div id="sl-status" class="zis-status">Pick or build a profile, then Start.</div>
              <div id="sl-prompt" class="zis-meta" style="display:none;"></div>
              <div class="sl-now" id="sl-now"></div>
              <div class="sl-ring" id="sl-ring"></div>
            </div>
          </div>
        </div>
      </div>`;

    // tabs
    container.querySelectorAll('.zis-tab').forEach(btn => btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        container.querySelectorAll('.zis-tab').forEach(b => b.classList.toggle('active', b === btn));
        container.querySelector('[data-panel="generate"]').style.display = tab === 'generate' ? '' : 'none';
        container.querySelector('[data-panel="slideshow"]').style.display = tab === 'slideshow' ? '' : 'none';
    }));

    // generate wiring (unchanged behavior)
    container.querySelector('#zis-go').addEventListener('click', generate);
    container.querySelector('#zis-prompt').addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') generate();
    });
    container.querySelector('#zis-imgs').addEventListener('click', (e) => {
        if (e.target.tagName === 'IMG') window.open(e.target.src, '_blank');
    });

    // slideshow wiring
    container.querySelector('#sl-addslot').addEventListener('click', () => addSlotRow('', ''));
    container.querySelector('#sl-new').addEventListener('click', newProfile);
    container.querySelector('#sl-rename').addEventListener('click', renameProfile);
    container.querySelector('#sl-del').addEventListener('click', deleteProfile);
    container.querySelector('#sl-save').addEventListener('click', saveProfile);
    container.querySelector('#sl-preview').addEventListener('click', previewPrompt);
    container.querySelector('#sl-go').addEventListener('click', toggleSlideshow);
    container.querySelector('#sl-profile').addEventListener('change', (e) => {
        _active = e.target.selectedIndex;
        applyProfileToUI(_profiles[_active]);
    });
    container.querySelector('#sl-ring').addEventListener('click', onRingClick);

    // pause the loop when the tab/page isn't visible (GPU rest, no wasted gens)
    _visHandler = () => { if (document.hidden && _running) pauseLoop(); else if (!document.hidden && _running) resumeLoop(); };
    document.addEventListener('visibilitychange', _visHandler);

    await loadProfiles();
}

/* ---------------- Generate mode (preserved) ---------------- */
async function generate() {
    if (_busy || !_container) return;
    const $ = (id) => _container.querySelector(id);
    const prompt = $('#zis-prompt').value.trim();
    const statusEl = $('#zis-status');
    if (!prompt) { statusEl.textContent = 'Enter a prompt first.'; return; }
    const body = {
        prompt, count: parseInt($('#zis-count').value) || 1,
        steps: $('#zis-steps').value, cfg_scale: $('#zis-cfg').value,
        width: $('#zis-w').value, height: $('#zis-h').value,
        seed: $('#zis-seed').value.trim(), negative_prompt: $('#zis-neg').value,
        sampler_name: $('#zis-sampler').value, scheduler: $('#zis-scheduler').value,
        expand: $('#zis-expand').checked,
    };
    _busy = true;
    const btn = $('#zis-go');
    btn.disabled = true; btn.textContent = 'Generating…';
    statusEl.className = 'zis-status'; statusEl.textContent = 'Generating…';
    try {
        const res = await fetch('/api/plugin/sd-server/generate', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
            statusEl.className = 'zis-status err';
            statusEl.textContent = 'Failed: ' + (data.error || `HTTP ${res.status}`);
            return;
        }
        renderResults(data);
    } catch (e) {
        statusEl.className = 'zis-status err';
        statusEl.textContent = 'Request failed: ' + e.message;
    } finally {
        _busy = false; btn.disabled = false; btn.textContent = 'Generate';
    }
}

function renderResults(data) {
    const statusEl = _container.querySelector('#zis-status');
    const metaEl = _container.querySelector('#zis-meta');
    const imgsEl = _container.querySelector('#zis-imgs');
    const p = data.params || {};
    statusEl.className = 'zis-status';
    statusEl.textContent = `${data.images.length} image(s) in ${data.elapsed}s  ·  ${p.width}×${p.height}  steps ${p.steps}  cfg ${p.cfg_scale}`;
    metaEl.style.display = '';
    metaEl.textContent = `Prompt sent${p.expanded ? ' (expanded)' : ''}:\n${data.final_prompt}\nSeeds: ${data.seeds.join(', ')}`;
    imgsEl.innerHTML = '';
    data.images.forEach((src, i) => {
        const fig = document.createElement('figure');
        const img = document.createElement('img');
        img.src = src; img.alt = `image ${i + 1}`;
        const cap = document.createElement('figcaption');
        const a = document.createElement('a');
        a.href = src; a.download = `sdimage_${data.seeds[i]}.png`; a.textContent = 'download';
        cap.append(Object.assign(document.createElement('span'), { textContent: `#${i + 1} · seed ${data.seeds[i]}` }), a);
        fig.append(img, cap);
        imgsEl.appendChild(fig);
    });
}

/* ---------------- Slideshow mode ---------------- */
async function loadProfiles() {
    try {
        const r = await fetch(SET_URL);
        const d = await r.json();
        const s = d.settings || d || {};
        _genDefaults = {
            steps: s.default_steps, cfg: s.default_cfg, negative: s.default_negative,
            sampler: s.default_sampler, scheduler: s.default_scheduler,
        };
        _profiles = Array.isArray(s.slideshow_profiles) && s.slideshow_profiles.length
            ? s.slideshow_profiles : [DEFAULT_PROFILE()];
        _active = Math.max(0, Math.min(parseInt(s.slideshow_active) || 0, _profiles.length - 1));
    } catch (e) {
        _profiles = [DEFAULT_PROFILE()]; _active = 0;
    }
    renderProfileSelect();
    applyProfileToUI(_profiles[_active]);
}

async function persistProfiles() {
    try {
        await fetch(SET_URL, {
            method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ settings: { slideshow_profiles: _profiles, slideshow_active: _active } }),
        });
    } catch (e) { /* best-effort */ }
}

function renderProfileSelect() {
    const sel = _container.querySelector('#sl-profile');
    sel.innerHTML = _profiles.map((p, i) =>
        `<option ${i === _active ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
}

function addSlotRow(name, optionsText) {
    const wrap = _container.querySelector('#sl-slots');
    const div = document.createElement('div');
    div.className = 'sl-slot';
    div.innerHTML = `
      <div class="sl-slot-hd">
        <input class="sl-name" value="${esc(name)}" placeholder="Slot name (e.g. Location)">
        <button class="sl-x">×</button>
      </div>
      <textarea class="sl-opts" placeholder="One option per line">${esc(optionsText)}</textarea>`;
    div.querySelector('.sl-x').addEventListener('click', () => div.remove());
    wrap.appendChild(div);
}

function applyProfileToUI(p) {
    if (!p) return;
    _container.querySelector('#sl-slots').innerHTML = '';
    (p.slots || []).forEach(s => addSlotRow(s.name || '', (s.options || []).join('\n')));
    _container.querySelector('#sl-interval').value = p.interval_sec || 20;
    _container.querySelector('#sl-expand').checked = p.expand !== false;
    const asp = new Set(p.aspects && p.aspects.length ? p.aspects : ['square']);
    _container.querySelectorAll('.sl-asp-cb').forEach(cb => { cb.checked = asp.has(cb.value); });
    // Gen overrides: value from profile (blank = inherit), placeholder = settings default.
    const g = _genDefaults || {};
    const ph = (sel, v) => { const el = _container.querySelector(sel); if (el && v != null && v !== '') el.placeholder = String(v); };
    _container.querySelector('#sl-steps').value = p.steps ?? '';
    _container.querySelector('#sl-cfg').value = p.cfg_scale ?? '';
    _container.querySelector('#sl-neg').value = p.negative_prompt ?? '';
    _container.querySelector('#sl-sampler').value = p.sampler_name || '';
    _container.querySelector('#sl-scheduler').value = p.scheduler || '';
    ph('#sl-steps', g.steps ?? 8);
    ph('#sl-cfg', g.cfg ?? 1.0);
    ph('#sl-neg', g.negative || '(settings default)');
}

function readUIProfile() {
    const slots = [];
    _container.querySelectorAll('#sl-slots .sl-slot').forEach(div => {
        const name = div.querySelector('.sl-name').value.trim();
        const options = div.querySelector('.sl-opts').value.split('\n').map(s => s.trim()).filter(Boolean);
        if (name || options.length) slots.push({ name: name || 'slot', options });
    });
    const aspects = [..._container.querySelectorAll('.sl-asp-cb')].filter(cb => cb.checked).map(cb => cb.value);
    return {
        name: _profiles[_active]?.name || 'Default',
        slots,
        interval_sec: Math.max(2, parseInt(_container.querySelector('#sl-interval').value) || 20),
        aspects: aspects.length ? aspects : ['square'],
        expand: _container.querySelector('#sl-expand').checked,
        steps: _container.querySelector('#sl-steps').value.trim(),
        cfg_scale: _container.querySelector('#sl-cfg').value.trim(),
        negative_prompt: _container.querySelector('#sl-neg').value,
        sampler_name: _container.querySelector('#sl-sampler').value,
        scheduler: _container.querySelector('#sl-scheduler').value,
    };
}

async function newProfile() {
    const name = (window.prompt('New profile name:') || '').trim();
    if (!name) return;
    _profiles.push({ ...DEFAULT_PROFILE(), name });
    _active = _profiles.length - 1;
    renderProfileSelect(); applyProfileToUI(_profiles[_active]); await persistProfiles();
}

async function renameProfile() {
    const cur = _profiles[_active];
    if (!cur) return;
    const name = (window.prompt('Rename profile:', cur.name) || '').trim();
    if (!name) return;
    cur.name = name; renderProfileSelect(); await persistProfiles();
}

async function deleteProfile() {
    if (_profiles.length <= 1) { setStatus('Keep at least one profile.', true); return; }
    if (!window.confirm(`Delete profile "${_profiles[_active].name}"?`)) return;
    _profiles.splice(_active, 1);
    _active = Math.max(0, _active - 1);
    renderProfileSelect(); applyProfileToUI(_profiles[_active]); await persistProfiles();
}

async function saveProfile() {
    _profiles[_active] = readUIProfile();
    await persistProfiles();
    setStatus('Profile saved.');
}

async function previewPrompt() {
    const p = readUIProfile();
    try {
        const r = await fetch('/api/plugin/sd-server/slideshow/preview', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ slots: p.slots, expand: p.expand }),
        });
        const d = await r.json();
        const el = _container.querySelector('#sl-prompt');
        el.style.display = '';
        el.textContent = d.success ? d.prompt : ('Error: ' + (d.error || 'preview failed'));
    } catch (e) { setStatus('Preview failed: ' + e.message, true); }
}

function toggleSlideshow() {
    if (_running) stopLoop(); else startLoop();
}

function startLoop() {
    _running = true;
    const go = _container.querySelector('#sl-go');
    go.textContent = '■ Stop slideshow'; go.classList.add('stop');
    slideNext();
}

function stopLoop() {
    _running = false;
    if (_timer) { clearTimeout(_timer); _timer = null; }
    const go = _container && _container.querySelector('#sl-go');
    if (go) { go.textContent = '▶ Start slideshow'; go.classList.remove('stop'); }
}

function pauseLoop() { if (_timer) { clearTimeout(_timer); _timer = null; } }
function resumeLoop() { if (_running && !_timer) slideNext(); }

async function slideNext() {
    // Single-flight: never let a second tick run concurrently (visibility-resume
    // or a double-start would otherwise fire back-to-back generations).
    if (!_running || _inflight) return;
    _inflight = true;
    if (_timer) { clearTimeout(_timer); _timer = null; }
    const p = readUIProfile();
    setStatus('Generating…');
    try {
        const r = await fetch('/api/plugin/sd-server/slideshow/next', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ slots: p.slots, aspects: p.aspects, expand: p.expand,
                steps: p.steps, cfg_scale: p.cfg_scale, negative_prompt: p.negative_prompt,
                sampler_name: p.sampler_name, scheduler: p.scheduler }),
        });
        const d = await r.json();
        if (d.success) { showNow(d); pushRing(d); setStatus(`${d.aspect} · ${d.elapsed}s · seed ${d.seed}`); }
        else { setStatus('Error: ' + (d.error || 'generation failed'), true); }
    } catch (e) {
        setStatus('Request failed: ' + e.message, true);
    } finally {
        _inflight = false;
    }
    if (_running && !document.hidden && !_timer) {
        _timer = setTimeout(slideNext, p.interval_sec * 1000);
    }
}

function showNow(d) {
    _container.querySelector('#sl-now').innerHTML = `<img src="${d.image}" alt="slide">`;
    const pe = _container.querySelector('#sl-prompt');
    pe.style.display = ''; pe.textContent = d.prompt;
}

function pushRing(d) {
    _ring.unshift({ src: d.image, seed: d.seed, prompt: d.prompt });
    if (_ring.length > RING_MAX) _ring.length = RING_MAX;
    renderRing();
}

function renderRing() {
    const el = _container.querySelector('#sl-ring');
    el.innerHTML = _ring.map((r, i) => `
      <figure>
        <img src="${r.src}" data-i="${i}" title="seed ${r.seed}">
        <div class="cap"><span data-fav="${i}" title="save">♥</span><span data-dl="${i}" title="download">⬇</span></div>
      </figure>`).join('');
}

function onRingClick(e) {
    const img = e.target.closest('img[data-i]');
    if (img) { showNowFromRing(_ring[+img.dataset.i]); return; }
    const fav = e.target.dataset.fav, dl = e.target.dataset.dl;
    if (fav !== undefined) downloadImage(_ring[+fav]);
    else if (dl !== undefined) downloadImage(_ring[+dl]);
}

function showNowFromRing(r) {
    if (!r) return;
    _container.querySelector('#sl-now').innerHTML = `<img src="${r.src}" alt="slide">`;
    const pe = _container.querySelector('#sl-prompt'); pe.style.display = ''; pe.textContent = r.prompt;
}

function downloadImage(r) {
    if (!r) return;
    const a = document.createElement('a');
    a.href = r.src; a.download = `slideshow_${r.seed}.png`;
    document.body.appendChild(a); a.click(); a.remove();
}

function setStatus(msg, err) {
    const el = _container && _container.querySelector('#sl-status');
    if (!el) return;
    el.className = 'zis-status' + (err ? ' err' : '');
    el.textContent = msg;
}

export function cleanup() {
    stopLoop();
    if (_visHandler) { document.removeEventListener('visibilitychange', _visHandler); _visHandler = null; }
    _container = null; _busy = false; _ring = [];
}
