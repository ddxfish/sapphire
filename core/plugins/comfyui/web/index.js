// ComfyUI panel (Settings > Plugins > ComfyUI): where ComfyUI runs and the workflows she can use.
// The address, the default workflow and the waits ride the global Save. A workflow action
// (upload, import, delete, a description) is a file action: it happens when it is clicked.
// Workflow names, node titles and server words come from files and other machines, so every
// one of them is set as text, never as HTML.
// A row is built once per workflow and stays in the page: an upload or a delete elsewhere
// never rebuilds it, so a Check that is still running or a half-typed description is kept.
import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import pluginsAPI from '/static/shared/plugins-api.js';
import { fetchWithTimeout, errWords } from '/static/shared/fetch.js';
import { flash } from '/static/shared/notify.js';
import { showConfirm, showPrompt } from '/static/shared/modal.js';
import { saveUrl } from '/static/shared/download.js';

const API = '/api/plugin/comfyui';
const DEFAULTS = { comfy_url: 'http://127.0.0.1:8188', default_workflow: '', timeout: 300, video_timeout: 1200 };
const panels = new WeakMap();        // container -> { rows: Map(name -> { el, sig }), token }

async function call(path, opts = {}, timeout = 120000) {
  const got = await fetchWithTimeout(API + path, opts, timeout);
  if (got == null) throw new Error('You were signed out. Sign in and try again.');   // a 401 is on its way to the login page
  return got;
}
const send = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
const enc = encodeURIComponent;

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

function injectStyles() {
  if (document.getElementById('cfy-styles')) return;
  const style = document.createElement('style');
  style.id = 'cfy-styles';
  style.textContent = `
    .cfy { display:flex; flex-direction:column; gap:16px; }
    .cfy-group { display:flex; flex-direction:column; gap:6px; }
    .cfy-group > label { font-size:13px; font-weight:500; color:var(--text); }
    .cfy input[type=text], .cfy input[type=number], .cfy select {
      padding:8px 12px; border:1px solid var(--border); border-radius:var(--radius-md,6px);
      background:var(--input-bg,var(--bg-secondary,#1a1a1a)); color:var(--text); font-size:13px; min-width:0; }
    .cfy select option { background:var(--input-bg,var(--bg-secondary,#1a1a1a)); color:var(--text); }
    .cfy input:focus, .cfy select:focus { outline:none; border-color:var(--accent-blue); }
    .cfy-line { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .cfy-line > input[type=text] { flex:1; }
    .cfy-pair { display:grid; grid-template-columns:1fr 1fr 2fr; gap:12px; }
    .cfy-btn { padding:7px 12px; border:1px solid var(--border); border-radius:var(--radius-md,6px);
      background:var(--bg-tertiary); color:var(--text); cursor:pointer; font-size:13px; white-space:nowrap; }
    .cfy-btn:hover { background:var(--bg-hover); }
    .cfy-btn:disabled { opacity:.6; cursor:default; }
    .cfy-hint { font-size:11px; color:var(--text-muted); line-height:1.5; }
    .cfy-say { font-size:12px; color:var(--text-muted); line-height:1.5; }
    .cfy-say.ok { color:var(--success,#4caf50); }
    .cfy-say.bad { color:var(--error,#ef5350); }
    .cfy-section { border-top:1px solid var(--border); padding-top:16px; display:flex; flex-direction:column; gap:10px; }
    .cfy-title { font-size:14px; font-weight:600; color:var(--text); }
    .cfy-flow { border:1px solid var(--border); border-radius:var(--radius-md,6px); padding:10px 12px;
      background:var(--bg-secondary); display:flex; flex-direction:column; gap:8px; }
    .cfy-name { font-weight:600; color:var(--text); font-size:13px; }
    .cfy-tag { font-size:11px; padding:1px 7px; border-radius:10px; border:1px solid var(--border); color:var(--text-muted); }
    .cfy-wire { font-size:12px; color:var(--text-muted); }
    .cfy-wire select { padding:4px 8px; font-size:12px; max-width:100%; }
    .cfy-extra { display:grid; grid-template-columns:auto minmax(120px,1fr) 84px 84px auto; gap:6px; align-items:center;
      font-size:12px; color:var(--text-muted); }
    .cfy-extra input[type=text], .cfy-extra input[type=number] { padding:4px 8px; font-size:12px; }
  `;
  document.head.appendChild(style);
}

function say(node, text, tone = '') {
  node.textContent = text;
  node.className = 'cfy-say' + (tone ? ' ' + tone : '');
}

// Run `work` behind a button: disabled while it runs, a failure said where `where` is.
async function busy(btn, where, work) {
  btn.disabled = true;
  try {
    await work();
  } catch (e) {
    if (where) say(where, errWords(e), 'bad'); else flash(errWords(e), 'error');
  }
  btn.disabled = false;
}

function urlOf(container) {
  return container.querySelector('#cfy-url')?.value.trim() || '';
}

// Add a workflow; when one of that name is already here the server says so (409) and the person decides.
async function addFlow(container, where, post) {
  let got;
  try {
    got = await post(false);
  } catch (e) {
    if (e?.status !== 409) throw e;
    showConfirm(`${e.message} Replace it?`, async () => {
      try {
        const again = await post(true);
        say(where, `Replaced "${again.name}".`, 'ok');
        await refresh(container);
      } catch (err) { say(where, errWords(err), 'bad'); }
    }, { saveLabel: 'Replace' });
    return;
  }
  say(where, `${got.replaced ? 'Replaced' : 'Added'} "${got.name}".`, 'ok');
  await refresh(container);
}

function nodeSelect(nodes, picked, lead, onPick) {
  const list = nodes.some(n => n.id === picked) ? nodes : [{ id: picked, title: picked ? 'Chosen by itself' : lead }, ...nodes];
  const select = el('select', { onchange: () => onPick(select.value) },
    ...list.map(n => el('option', { value: n.id, text: n.id && nodes.includes(n) ? `${n.title} (#${n.id})` : n.title })));
  select.value = picked;
  return select;
}

// One control of a workflow: on or off for her, the name she sees, and how far a number may go.
// Only what differs from what was found is saved, so an untouched range follows the workflow.
function extraRow(c, patch) {
  const num = (v, title) => el('input', { type: 'number', step: 'any', value: v, title, onchange: save });
  const on = el('input', { type: 'checkbox', checked: !c.off, title: 'She can set this', onchange: save });
  const name = el('input', { type: 'text', value: c.name, maxLength: 40, placeholder: c.found, onchange: save });
  const lo = c.auto ? num(c.min, 'Lowest') : null, hi = c.auto ? num(c.max, 'Highest') : null;
  const own = typeof c.value === 'boolean' ? (c.value ? 'on' : 'off') : String(c.value);
  function save() {
    const row = { off: !on.checked };
    if (name.value.trim() && name.value.trim() !== c.found) row.name = name.value.trim();
    const typed = (box, auto) => (box && box.value !== '' && Number(box.value) !== auto ? Number(box.value) : undefined);
    row.min = typed(lo, c.auto?.[0]);
    row.max = typed(hi, c.auto?.[1]);
    patch({ extras: { [c.id]: row } });
  }
  return el('div', { class: 'cfy-extra' }, on, name, lo || el('span'), hi || el('span'),
    el('span', { text: `workflow's own: ${own}` }));
}

function flowRow(container, flow) {
  const out = el('div', { class: 'cfy-say' });
  const row = el('div', { class: 'cfy-flow' });
  // An edit is saved in place, one after the other: the row is never rebuilt for it, so what
  // the person sees is what they chose, and a failure is said right here.
  let queue = Promise.resolve();
  const patch = body => {
    queue = queue.then(async () => {
      try {
        await call(`/workflows/${enc(flow.name)}`, send('PUT', body));
        if (out.classList.contains('bad') || !out.textContent) say(out, 'Saved.', 'ok');
      } catch (e) { say(out, `Not saved. ${errWords(e)}`, 'bad'); }
    });
  };
  const remove = el('button', { type: 'button', class: 'cfy-btn', text: 'Delete', onclick: () =>
    showConfirm(`Delete the workflow "${flow.name}"?`, () => busy(remove, out, async () => {
      await call(`/workflows/${enc(flow.name)}`, { method: 'DELETE' });
      await refresh(container);
    }), { saveLabel: 'Delete' }) });

  if (flow.error) {
    row.append(el('div', { class: 'cfy-line' }, el('span', { class: 'cfy-name', text: flow.name })),
               el('div', { class: 'cfy-say bad', text: flow.error }), el('div', { class: 'cfy-line' }, remove), out);
    return row;
  }

  const tags = [flow.kind, flow.takes_image && 'takes an image', flow.kind !== 'audio' && (flow.shape || 'shape not settable')];
  row.append(el('div', { class: 'cfy-line' }, el('span', { class: 'cfy-name', text: flow.name }),
    ...tags.filter(Boolean).map(t => el('span', { class: 'cfy-tag', text: t }))));
  row.append(el('input', { type: 'text', value: flow.description || '', maxLength: 300,
    placeholder: 'What it is for. She reads this when she picks a workflow.',
    onchange: e => patch({ description: e.target.value }) }));

  if (flow.prompt_nodes.length > 1 || !flow.prompt_node) {
    row.append(el('div', { class: 'cfy-wire cfy-line' }, el('span', { text: 'Her prompt goes into' }),
      flow.prompt_nodes.length
        ? nodeSelect(flow.prompt_nodes, flow.prompt_node, 'Pick a node', id => patch({ prompt_node: id }))
        : el('span', { text: 'nothing: this workflow has no text input.' })));
  }
  if (flow.image_nodes.length > 1 || (flow.image_nodes.length && !flow.image_node)) {
    row.append(el('div', { class: 'cfy-wire cfy-line' }, el('span', { text: 'Her input image goes into' }),
      nodeSelect(flow.image_nodes, flow.image_node, 'None: these are all references', id => patch({ image_node: id }))));
  }

  if (flow.extras?.length) {
    row.append(el('div', { class: 'cfy-wire', text: 'She can also set (ticked), by this name, from / to:'
        + (flow.extras.length > 12 ? ' She is shown the first 12 that are ticked.' : '') }),
      ...flow.extras.map(c => extraRow(c, patch)));
  }

  const check = el('button', { type: 'button', class: 'cfy-btn', text: 'Check', title: 'Will it run on that ComfyUI?',
    onclick: () => busy(check, out, async () => {
      say(out, 'Asking ComfyUI…');
      const got = await call(`/workflows/${enc(flow.name)}/check`, send('POST', { url: urlOf(container) }));
      out.className = 'cfy-say ' + (got.ok ? 'ok' : 'bad');
      out.replaceChildren(...(got.lines || []).map(line => el('div', { text: line })));
    }) });
  const save = el('button', { type: 'button', class: 'cfy-btn', text: 'Download',
    onclick: () => saveUrl(`${API}/workflows/${enc(flow.name)}/file`, `${flow.name}.json`) });
  row.append(el('div', { class: 'cfy-line' }, check, save, remove), out);
  return row;
}

function fillDefault(container, flows) {
  const select = container.querySelector('#cfy-default');
  if (!select) return;
  const want = select.value.toLowerCase();          // the server matches a name whatever its case; so does this
  select.replaceChildren(el('option', { value: '', text: 'The first in the list' }),
    ...flows.map(f => el('option', { value: f.name, text: f.name })));
  select.value = flows.find(f => f.name.toLowerCase() === want)?.name || '';
}

// What a row was built from, minus what the row edits itself: a row is rebuilt only when
// the workflow under it changed (a replace), never for its own description or picks.
const sigOf = flow => JSON.stringify({ ...flow, description: null, prompt_node: null, image_node: null,
  extras: (flow.extras || []).map(c => [c.id, c.found, c.value, c.auto]) });

async function refresh(container) {
  const panel = panels.get(container);
  const list = container.querySelector('#cfy-flows');
  const clips = container.querySelector('#cfy-clips');
  if (!panel || !list) return;
  const mine = ++panel.token;
  let got;
  try {
    got = await call('/workflows');
  } catch (e) {
    if (mine === panel.token && !panel.rows.size) list.replaceChildren(el('div', { class: 'cfy-say bad', text: errWords(e) }));
    return;
  }
  if (mine !== panel.token) return;                 // a newer refresh has the newer list
  const flows = got.workflows || [];
  fillDefault(container, flows);
  const want = flows.map(flow => {
    const sig = sigOf(flow);
    let row = panel.rows.get(flow.name);
    if (!row || row.sig !== sig) {
      row = { el: flowRow(container, flow), sig };
      panel.rows.set(flow.name, row);
    }
    return row.el;
  });
  for (const name of [...panel.rows.keys()]) if (!flows.some(f => f.name === name)) panel.rows.delete(name);
  if (!want.length) want.push(el('div', { class: 'cfy-say', text: 'No workflows yet. Upload one, or import the last thing you ran in ComfyUI.' }));
  // put each row in its place without lifting the ones already there (a lifted input loses its focus)
  want.forEach((node, i) => { if (list.children[i] !== node) list.insertBefore(node, list.children[i] || null); });
  while (list.children.length > want.length) list.lastElementChild.remove();
  const n = got.outputs?.count || 0;
  say(clips, n ? `${n} file${n === 1 ? '' : 's'}, ${(got.outputs.bytes / 1048576).toFixed(1)} MB, in user/comfyui/outputs.`
    : 'None yet. Videos and audio a workflow makes are kept in user/comfyui/outputs.');
  container.querySelector('#cfy-clear').hidden = !n;
}

function render(container, settings) {
  const s = { ...DEFAULTS, ...(settings || {}) };
  panels.set(container, { rows: new Map(), token: 0 });
  const tested = el('div', { class: 'cfy-say' });
  const added = el('div', { class: 'cfy-say' });

  const test = el('button', { type: 'button', class: 'cfy-btn', text: 'Test', onclick: () => busy(test, tested, async () => {
    const field = container.querySelector('#cfy-url');
    const asked = field.value;
    say(tested, 'Asking…');
    const got = await call('/test', send('POST', { url: asked.trim() }), 20000);
    if (got.ok && got.url && field.value === asked) field.value = got.url;     // tidy it, unless they have typed since
    say(tested, got.text, got.ok ? 'ok' : 'bad');
  }) });

  const picker = el('input', { type: 'file', accept: '.json,.png,application/json,image/png', hidden: true,
    onchange: () => busy(upload, added, async () => {
      const file = picker.files[0];
      picker.value = '';
      if (!file) return;
      await addFlow(container, added, replace => {
        const form = new FormData();
        form.append('file', file);
        if (replace) form.append('replace', '1');
        return call('/workflows', { method: 'POST', body: form });
      });
    }) });
  const upload = el('button', { type: 'button', class: 'cfy-btn', text: 'Upload a workflow', onclick: () => picker.click() });
  const pull = el('button', { type: 'button', class: 'cfy-btn', text: 'Import last ComfyUI run', onclick: async () => {
    const name = (await showPrompt('Import last ComfyUI run', 'Name for this workflow', '') || '').trim();
    if (!name) return;
    busy(pull, added, () => addFlow(container, added,
      replace => call('/import', send('POST', { name, url: urlOf(container), replace }))));
  } });
  const clear = el('button', { type: 'button', class: 'cfy-btn', id: 'cfy-clear', text: 'Delete all', hidden: true, onclick: () =>
    showConfirm('Delete every clip ComfyUI made? The chats that show them keep their messages, but the players go empty.',
      () => busy(clear, null, async () => {
        const got = await call('/outputs', { method: 'DELETE' });
        if (got.left) flash(`${got.left} clip${got.left === 1 ? ' is' : 's are'} still open in a player. Close it and try again.`, 'warning');
        await refresh(container);
      }), { saveLabel: 'Delete all' }) });

  container.replaceChildren(el('div', { class: 'cfy' },
    el('div', { class: 'cfy-group' },
      el('label', { htmlFor: 'cfy-url', text: 'ComfyUI address' }),
      el('div', { class: 'cfy-line' },
        el('input', { type: 'text', id: 'cfy-url', value: s.comfy_url, placeholder: DEFAULTS.comfy_url }), test),
      tested,
      el('div', { class: 'cfy-hint', text: 'ComfyUI on another machine: use that machine\'s address, such as http://192.168.1.20:8188, '
        + 'and start ComfyUI there with --listen. Workflows stay here with Sapphire; only the models live with ComfyUI.' })),
    el('div', { class: 'cfy-pair' },
      el('div', { class: 'cfy-group' }, el('label', { htmlFor: 'cfy-timeout', text: 'Image wait (seconds)' }),
        el('input', { type: 'number', id: 'cfy-timeout', value: s.timeout, min: 10 })),
      el('div', { class: 'cfy-group' }, el('label', { htmlFor: 'cfy-video-timeout', text: 'Video wait (seconds)' }),
        el('input', { type: 'number', id: 'cfy-video-timeout', value: s.video_timeout, min: 10 })),
      el('div', { class: 'cfy-group' }, el('label', { htmlFor: 'cfy-default', text: 'Default workflow' }),
        // the saved name is the only choice until the list arrives, so a Save before then keeps it
        el('select', { id: 'cfy-default' }, el('option', { value: s.default_workflow || '', text: s.default_workflow || 'The first in the list' })))),
    el('div', { class: 'cfy-section' },
      el('div', { class: 'cfy-title', text: 'Workflows' }),
      el('div', { class: 'cfy-line' }, upload, pull, picker),
      el('div', { class: 'cfy-hint', text: 'Upload a workflow saved from ComfyUI with File > Export (API), or any PNG that ComfyUI saved. '
        + 'Or run a workflow once in ComfyUI and import it from there. Check tells you whether a workflow will run on the ComfyUI above.' }),
      added,
      el('div', { id: 'cfy-flows', class: 'cfy-group' })),
    el('div', { class: 'cfy-section' },
      el('div', { class: 'cfy-title', text: 'Kept clips' }),
      el('div', { class: 'cfy-line' }, el('div', { id: 'cfy-clips', class: 'cfy-say' }), clear))));
  refresh(container);
}

// Only what is on the page is saved. Before the panel has painted (the host shows Save
// from the start), or after its load failed, there is nothing here: an empty answer
// changes nothing, where a default would overwrite a remote address with localhost.
function getSettings(container) {
  const out = {};
  const url = container.querySelector('#cfy-url');
  const pick = container.querySelector('#cfy-default');
  if (url && url.value.trim()) out.comfy_url = url.value.trim();
  if (pick) out.default_workflow = pick.value || '';
  for (const [key, id] of [['timeout', '#cfy-timeout'], ['video_timeout', '#cfy-video-timeout']]) {
    const field = container.querySelector(id);
    const v = field ? parseInt(field.value, 10) : NaN;
    if (Number.isFinite(v)) {
      out[key] = Math.max(10, v);
      field.value = out[key];                        // show the wait that was really saved
    }
  }
  return out;
}

export default {
  name: 'comfyui',
  init() {
    injectStyles();
    registerPluginSettings({
      id: 'comfyui',
      name: 'ComfyUI',
      icon: '🎨',
      helpText: 'Images and video from your own ComfyUI workflows, on this machine or another one.',
      render,
      load: () => pluginsAPI.getSettings('comfyui'),
      save: settings => pluginsAPI.saveSettings('comfyui', settings),
      getSettings,
    });
  },
  destroy() {},
};
