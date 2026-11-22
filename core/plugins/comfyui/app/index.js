// ComfyUI page (Apps > ComfyUI): run a workflow by hand. No model in between.
// Left: the workflow, its prompt, an input picture when it takes one, the shape, and the
// workflow's own settings (the same ones the AI is shown as "extras"). Right: the result, large.
// A run is a job on the server: this page starts it, asks every two seconds how it is doing,
// and can stop it. Leaving the page stops the asking, not the job; coming back picks it up.
// Workflow names, node titles and server words come from files and other machines, so every
// one of them is set as text, never as HTML.
import { fetchWithTimeout, errWords } from '/static/shared/fetch.js';
import { saveUrl } from '/static/shared/download.js';
import { switchView } from '/static/core/router.js';

const API = '/api/plugin/comfyui';
const SHOWN = 12;                    // the settings a workflow is given here: the same twelve the AI is shown
const st = {                         // kept while the app is loaded, so a visit to another page loses nothing
  flows: [], flow: '', prompt: '', shape: '', seed: '', pictures: [], job: null, last: null, shown: null,
  sending: false,                    // Generate was pressed and the server has not answered yet: busy, though no job id exists
  status: ['', ''],                  // the last line said, and its tone: a failure must still be there after a visit elsewhere
  extras: {},                        // workflow name -> {setting: what the person typed}
  round: 0,                          // goes up at every render and cleanup: an answer from an older round is dropped
};
let root = null, timer = null, thumbs = [];

async function call(path, opts = {}, timeout = 60000) {
  const got = await fetchWithTimeout(API + path, opts, timeout);
  if (got == null) throw new Error('You were signed out. Sign in and try again.');
  return got;
}

function el(tag, props = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node[k] = v;
  }
  node.append(...kids.filter(Boolean));
  return node;
}

const $ = sel => root?.querySelector(sel);
const flowNow = () => st.flows.find(f => f.name === st.flow && !f.error);
const busy = () => st.sending || !!st.job;        // one run at a time from this page
const clock = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

function styles() {
  if (document.getElementById('cfa-styles')) return;
  const style = el('style', { id: 'cfa-styles' });
  style.textContent = `
    .cfa { display:grid; grid-template-columns:340px minmax(0,1fr); gap:16px; height:100%; min-height:0; width:100%;
      box-sizing:border-box; padding:12px; color:var(--text); }
    .app-host-content > .cfa { padding:0; }
    @media (max-width: 860px) { .cfa { grid-template-columns:1fr; height:auto; } }
    .cfa [hidden] { display:none !important; }
    .cfa-side { display:flex; flex-direction:column; gap:10px; overflow-y:auto; min-height:0; padding-right:4px; overflow-wrap:anywhere; }
    .cfa-side label { font-size:12px; color:var(--text-muted); display:flex; flex-direction:column; gap:4px; }
    .cfa input[type=text], .cfa input[type=number], .cfa select, .cfa textarea {
      padding:8px 10px; border:1px solid var(--border); border-radius:var(--radius-md,6px); font:inherit; font-size:13px;
      background:var(--input-bg,var(--bg-secondary,#1a1a1a)); color:var(--text); min-width:0; box-sizing:border-box; width:100%; }
    .cfa select option { background:var(--input-bg,var(--bg-secondary,#1a1a1a)); color:var(--text); }
    .cfa textarea { min-height:120px; resize:vertical; }
    .cfa input:focus, .cfa select:focus, .cfa textarea:focus { outline:none; border-color:var(--accent-blue); }
    .cfa-about { font-size:12px; color:var(--text-muted); line-height:1.5; }
    .cfa-two { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .cfa-link { background:none; border:none; padding:0; color:var(--accent-blue,#4a7dff); cursor:pointer; font-size:12px; text-align:left; }
    .cfa-link:hover { text-decoration:underline; }
    .cfa-check { flex-direction:row !important; align-items:center; gap:8px !important; font-size:13px !important; color:var(--text) !important; }
    .cfa-btn { padding:9px 12px; border:1px solid var(--border); border-radius:var(--radius-md,6px);
      background:var(--bg-tertiary); color:var(--text); cursor:pointer; font-size:13px; white-space:nowrap; }
    .cfa-btn:hover { background:var(--bg-hover); }
    .cfa-btn:disabled { opacity:.55; cursor:default; }
    .cfa-go { background:var(--accent-blue,#4a7dff); border-color:transparent; color:#fff; font-weight:600; flex:1; }
    .cfa-go:hover { background:var(--accent-blue,#4a7dff); filter:brightness(1.1); }
    .cfa-line { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .cfa-pic img { width:48px; height:48px; object-fit:cover; border-radius:6px; border:1px solid var(--border); cursor:pointer; }
    .cfa-say { font-size:12px; color:var(--text-muted); line-height:1.5; }
    .cfa-say.ok { color:var(--success,#4caf50); }
    .cfa-say.bad { color:var(--error,#ef5350); }
    .cfa-main { display:flex; flex-direction:column; gap:10px; min-width:0; min-height:0; }
    .cfa-stage { flex:1; min-height:240px; display:flex; align-items:center; justify-content:center; overflow:hidden;
      border:1px solid var(--border); border-radius:var(--radius-md,6px); background:var(--bg-secondary); }
    .cfa-stage img, .cfa-stage video { max-width:100%; max-height:100%; object-fit:contain; display:block; }
    .cfa-stage audio { width:min(520px,90%); }
    .cfa-notes { font-size:12px; color:var(--text-muted); line-height:1.5; overflow-wrap:anywhere; }
    .cfa-strip { display:flex; gap:6px; overflow-x:auto; flex-shrink:0; padding-bottom:2px; }
    .cfa-tile { width:72px; height:72px; flex:0 0 auto; border:1px solid var(--border); border-radius:6px; overflow:hidden;
      background:var(--bg-tertiary); color:var(--text-muted); cursor:pointer; padding:0; font-size:22px;
      display:flex; align-items:center; justify-content:center; }
    .cfa-tile img { width:100%; height:100%; object-fit:cover; }
    .cfa-tile.on { border-color:var(--accent-blue); }
  `;
  document.head.appendChild(style);
}

function say(text, tone = '') {
  st.status = [text, tone];
  const node = $('#cfa-status');
  if (!node) return;
  node.textContent = text;
  node.className = 'cfa-say' + (tone ? ' ' + tone : '');
}

// -- the left side ---------------------------------------------------------------------------

// What a workflow is, in a line, and the fields only it has: the picture, the shape, its settings.
function fitToFlow() {
  const flow = flowNow();
  const about = $('#cfa-about'), extras = $('#cfa-extras');
  if (!about) return;
  const bits = flow ? [flow.kind, flow.takes_image && (flow.image_slots > 1 ? `takes up to ${flow.image_slots} input pictures` : 'takes an input picture'),
    flow.kind !== 'audio' && (flow.shape ? `${flow.shape} by default` : 'shape not settable')].filter(Boolean) : [];
  about.textContent = flow ? bits.join(', ') + (flow.description ? `. ${flow.description}` : '')
    : (st.flows.find(f => f.name === st.flow)?.error || '');
  $('#cfa-pic').hidden = !flow?.takes_image;
  showPicture();
  $('#cfa-shape-row').hidden = !flow?.shape || flow.kind === 'audio';
  $('#cfa-prompt-row').hidden = !!flow && !flow.prompt_node;
  $('#cfa-go').disabled = !flow || busy();
  const typed = flow ? (st.extras[flow.name] ||= {}) : {};      // what the person set stays through a visit elsewhere
  extras.replaceChildren(...(flow?.extras || []).filter(c => !c.off).slice(0, SHOWN).map(c => {
    if (typeof c.value === 'boolean') {
      return el('label', { class: 'cfa-check' },
        el('input', { type: 'checkbox', checked: c.name in typed ? typed[c.name] : c.value, cfaName: c.name, cfaOwn: c.value,
          onchange: e => { typed[c.name] = e.target.checked; } }), el('span', { text: c.name }));
    }
    return el('label', {}, el('span', { text: `${c.name} (${c.min} to ${c.max})` }),
      el('input', { type: 'number', min: c.min, max: c.max, step: c.whole ? 1 : 'any', placeholder: String(c.value), cfaName: c.name,
        value: typed[c.name] ?? '', oninput: e => { typed[c.name] = e.target.value; } }));
  }));
}

// Only what the person changed is sent: an empty box and an untouched switch are the workflow's own.
function extrasNow() {
  const out = {};
  for (const box of root.querySelectorAll('#cfa-extras input')) {
    if (box.type === 'checkbox') { if (box.checked !== box.cfaOwn) out[box.cfaName] = box.checked; }
    else if (box.validity?.badInput) throw new Error(`${box.cfaName} isn't a number the browser can read.`);
    else if (box.value !== '') out[box.cfaName] = Number(box.value);
  }
  return out;
}

// How many pictures this workflow has room for (one, unless it loads several).
const room = () => Math.max(1, flowNow()?.image_slots || 1);

// The input pictures, first slot first. A click on one takes it out. A workflow with room for
// more than were given runs with the rest of its slots empty.
function showPicture() {
  const name = $('#cfa-pic-name'), row = $('#cfa-pic-row'), add = $('#cfa-pic-add');
  if (!name) return;
  thumbs.forEach(u => URL.revokeObjectURL(u));
  const mine = st.pictures.slice(0, room());
  thumbs = mine.map(f => URL.createObjectURL(f));
  row.replaceChildren(...mine.map((f, i) => el('img', { src: thumbs[i], alt: '', title: `${f.name}: click to remove`,
    onclick: () => { st.pictures.splice(i, 1); showPicture(); } })));
  add.textContent = !mine.length ? 'Choose a picture' : mine.length < room() ? 'Add another' : 'Change the last one';
  name.textContent = !mine.length ? 'none: the workflow uses the picture it was saved with'
    : mine.map(f => f.name).join(', ') + (room() > 1 ? ` (${mine.length} of ${room()})` : '');
}

// A new picture takes the next free slot; with none free it replaces the last one.
function takePicture(file) {
  st.pictures = [...st.pictures.slice(0, Math.min(st.pictures.length, room() - 1)), file];
  showPicture();
}

// -- the result ------------------------------------------------------------------------------

function player(file, big) {
  const quiet = node => {              // the browser's own Download can't work on a self-signed certificate: ours is below
    node.controlsList = 'nodownload';
    node.addEventListener('contextmenu', ev => ev.preventDefault());
    return node;
  };
  if (file.kind === 'video') return quiet(el('video', { src: file.url, controls: true, playsInline: true, loop: true, autoplay: big, muted: false }));
  if (file.kind === 'audio') return quiet(el('audio', { src: file.url, controls: true }));
  // what a browser can draw: a TIFF is a picture, but not one it shows
  if (/\.(gif|webp|png|jpe?g|bmp)$/i.test(file.name)) return el('img', { src: file.url, alt: file.name });
  return el('div', { class: 'cfa-say', text: `${file.name} can't be shown here. Download it below.` });
}

function show(file, notes = [], seed = null) {
  st.shown = file;
  const stage = $('#cfa-stage'), under = $('#cfa-under'), lines = $('#cfa-notes');
  if (!stage) return;
  stage.replaceChildren(file ? player(file, true) : el('div', { class: 'cfa-say', text: 'What you make shows here.' }));
  lines.replaceChildren(...notes.map(n => el('div', { text: n })));
  under.replaceChildren();
  if (!file) return;
  under.append(el('button', { type: 'button', class: 'cfa-btn', text: `↓ ${file.name}`, onclick: () => saveUrl(file.url, file.name) }));
  if (file.kind === 'image') {
    under.append(el('button', { type: 'button', class: 'cfa-btn', text: 'Use as input picture', onclick: async ev => {
      ev.target.disabled = true;
      try {
        const res = await fetch(file.url, { credentials: 'same-origin' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        takePicture(new File([await res.blob()], file.name));
        say(flowNow()?.takes_image ? 'That picture is the input now.' : 'That picture is set. Pick a workflow that takes an input picture.', 'ok');
      } catch (e) { say(`Couldn't take that picture. ${errWords(e)}`, 'bad'); }
      ev.target.disabled = false;
    } }));
  }
  if (seed) under.append(el('span', { class: 'cfa-say', text: `seed ${seed}` }));
  for (const tile of root.querySelectorAll('.cfa-tile')) tile.classList.toggle('on', tile.cfaName === file.name);
}

async function strip() {
  const row = $('#cfa-strip');
  if (!row) return;
  let got;
  try { got = await call('/outputs'); } catch { return; }
  row.replaceChildren(...(got.files || []).map(file => {
    const tile = el('button', { type: 'button', class: 'cfa-tile', title: file.name, cfaName: file.name, onclick: () => show(file) });
    if (file.kind === 'image') tile.append(el('img', { src: file.url, alt: '', loading: 'lazy' }));
    else tile.textContent = file.kind === 'video' ? '▶' : file.kind === 'audio' ? '♪' : '…';
    tile.classList.toggle('on', st.shown?.name === file.name);
    return tile;
  }));
}

// -- a run -----------------------------------------------------------------------------------

function running(on) {
  if ($('#cfa-go')) $('#cfa-go').disabled = on || busy() || !flowNow();
  if ($('#cfa-stop')) $('#cfa-stop').hidden = !on;
}

async function ask() {
  timer = null;
  const id = st.job, round = st.round;
  if (!id) return;
  const mine = () => id === st.job && round === st.round;      // still this job, still this visit to the page
  let job;
  try {
    job = await call(`/run/${encodeURIComponent(id)}`);
  } catch (e) {
    if (!mine()) return;
    if (e?.status === 404) { st.job = null; running(false); say(errWords(e), 'bad'); return; }
    timer = root && setTimeout(ask, 4000);            // a hiccup on the line is not the job's end
    return;
  }
  if (!mine()) return;
  if (job.state === 'running') {
    running(true);
    say(job.phase === 'sending' ? 'Sending it to ComfyUI…' : `Working in ComfyUI… ${clock(job.seconds)}`);
    timer = root && setTimeout(ask, 2000);
    return;
  }
  st.job = null;
  running(false);
  if (job.state === 'done') {
    st.last = job;
    say(`Done in ${clock(job.seconds)}.`, 'ok');
    show(job.files[0], job.notes || [], job.seed);
    strip();
  } else {
    say(job.state === 'stopped' ? 'Stopped.' : job.error || 'It failed and gave no reason.', job.state === 'stopped' ? '' : 'bad');
  }
}

// The page is busy from the press, not from the server's answer: a second Ctrl+Enter, a
// workflow switch or a visit elsewhere in between must not start a second run nobody can stop.
async function go() {
  const flow = flowNow();
  if (!flow || busy()) return;
  let extras;
  try { extras = extrasNow(); } catch (e) { say(e.message, 'bad'); return; }
  const form = new FormData();
  form.append('workflow', flow.name);
  form.append('prompt', $('#cfa-prompt').value);
  if (flow.shape && $('#cfa-shape').value) form.append('shape', $('#cfa-shape').value);
  if ($('#cfa-seed').value.trim()) form.append('seed', $('#cfa-seed').value.trim());
  if (Object.keys(extras).length) form.append('extras', JSON.stringify(extras));
  if (flow.takes_image) for (const pic of st.pictures.slice(0, room())) form.append('image', pic, pic.name);
  st.sending = true;
  running(true);
  say('Sending it to ComfyUI…');
  try {
    st.job = (await call('/run', { method: 'POST', body: form }, 180000)).id;
  } catch (e) {
    say(errWords(e), 'bad');
  }
  st.sending = false;
  running(!!st.job);
  if (st.job && root && !timer) timer = setTimeout(ask, 1000);
}

async function stop() {
  const id = st.job;
  if (!id) return;
  say('Stopping…');
  if ($('#cfa-stop')) $('#cfa-stop').disabled = true;
  try { await call(`/run/${encodeURIComponent(id)}`, { method: 'DELETE' }); }
  catch (e) { say(errWords(e), 'bad'); }
  if ($('#cfa-stop')) $('#cfa-stop').disabled = false;
}

// -- the page --------------------------------------------------------------------------------

async function load() {
  let got;
  try {
    got = await call('/workflows');
  } catch (e) { say(errWords(e), 'bad'); return; }
  if (!root) return;
  st.flows = got.workflows || [];
  const pick = $('#cfa-flow');
  pick.replaceChildren(...st.flows.map(f => el('option', { value: f.name, text: f.name + (f.error ? ' (unusable)' : '') })));
  if (!st.flows.some(f => f.name === st.flow)) {
    let saved = '';
    try { saved = (await fetchWithTimeout('/api/webui/plugins/comfyui/settings'))?.settings?.default_workflow || ''; } catch { /* the first one, then */ }
    st.flow = st.flows.find(f => f.name.toLowerCase() === String(saved).toLowerCase())?.name || st.flows.find(f => !f.error)?.name || '';
  }
  if (!root) return;
  pick.value = st.flow;
  if (!st.flows.length) say('No workflows yet. Add one in Settings > Plugins > ComfyUI.');
  fitToFlow();
}

export function render(container) {
  styles();
  root = container;
  st.round++;
  const text = (id, value, props = {}) => el('input', { type: 'text', id, value, ...props });
  container.replaceChildren(el('div', { class: 'cfa' },
    el('aside', { class: 'cfa-side' },
      el('label', {}, el('span', { text: 'Workflow' }),
        el('select', { id: 'cfa-flow', onchange: e => { st.flow = e.target.value; fitToFlow(); } })),
      el('div', { id: 'cfa-about', class: 'cfa-about' }),
      el('button', { type: 'button', class: 'cfa-link', text: 'Add or change workflows, and where ComfyUI runs',
        onclick: () => { window._settingsTab = 'comfyui'; switchView('settings'); } }),
      el('label', { id: 'cfa-prompt-row' }, el('span', { text: 'Prompt' }),
        el('textarea', { id: 'cfa-prompt', value: st.prompt, placeholder: 'What to make. For video: what happens.',
          oninput: e => { st.prompt = e.target.value; },
          onkeydown: e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) go(); } })),
      el('div', { id: 'cfa-pic', class: 'cfa-pic', hidden: true },
        el('div', { class: 'cfa-about', text: 'Input picture' }),
        el('div', { class: 'cfa-line' },
          el('span', { id: 'cfa-pic-row', class: 'cfa-line' }),
          el('button', { type: 'button', class: 'cfa-btn', id: 'cfa-pic-add', text: 'Choose a picture', onclick: () => $('#cfa-pic-file').click() }),
          el('input', { type: 'file', id: 'cfa-pic-file', accept: 'image/*', hidden: true,
            onchange: e => { const file = e.target.files[0]; e.target.value = ''; if (file) takePicture(file); } })),
        el('div', { id: 'cfa-pic-name', class: 'cfa-say' })),
      el('label', { id: 'cfa-shape-row' }, el('span', { text: 'Shape' }),
        el('select', { id: 'cfa-shape', onchange: e => { st.shape = e.target.value; } },
          el('option', { value: '', text: 'The input picture\'s, or the workflow\'s own' }),
          el('option', { value: 'square', text: 'Square' }),
          el('option', { value: 'portrait', text: 'Portrait (taller)' }),
          el('option', { value: 'landscape', text: 'Landscape (wider)' }))),
      el('div', { id: 'cfa-extras', class: 'cfa-two' }),
      el('label', {}, el('span', { text: 'Seed' }),
        text('cfa-seed', st.seed, { inputMode: 'numeric', placeholder: 'random', oninput: e => { st.seed = e.target.value; } })),
      el('div', { class: 'cfa-line' },
        el('button', { type: 'button', class: 'cfa-btn cfa-go', id: 'cfa-go', text: 'Generate', disabled: true, onclick: go }),
        el('button', { type: 'button', class: 'cfa-btn', id: 'cfa-stop', text: 'Stop', hidden: true, onclick: stop })),
      el('div', { id: 'cfa-status', class: 'cfa-say' })),
    el('main', { class: 'cfa-main' },
      el('div', { id: 'cfa-stage', class: 'cfa-stage' }),
      el('div', { id: 'cfa-notes', class: 'cfa-notes' }),
      el('div', { id: 'cfa-under', class: 'cfa-line' }),
      el('div', { id: 'cfa-strip', class: 'cfa-strip' }))));
  $('#cfa-shape').value = st.shape;
  showPicture();
  show(st.shown, st.shown && st.last?.files?.[0]?.name === st.shown.name ? st.last.notes || [] : [],
       st.shown && st.last?.files?.[0]?.name === st.shown.name ? st.last.seed : null);
  $('#cfa-status').textContent = st.status[0];
  $('#cfa-status').className = 'cfa-say' + (st.status[1] ? ' ' + st.status[1] : '');
  load();
  strip();
  running(busy());
  if (st.job) ask();
}

export function cleanup() {
  st.round++;
  if (timer) clearTimeout(timer);
  timer = null;
  thumbs.forEach(u => URL.revokeObjectURL(u));
  thumbs = [];
  root = null;
}
