// index.js — Z-Image settings panel (Settings > Plugins)
import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const FALLBACK = {
  api_url: 'http://127.0.0.1:7861',
  default_negative: '',
  static_keywords: '',
  ai_name: '', ai_description: '',
  user_name: '', user_description: '',
  default_steps: 8,
  default_cfg: 1.0,
  default_sampler: '',
  default_scheduler: '',
  default_width: 1024,
  default_height: 1024,
  max_count: 6,
  timeout: 180,
};

// Sampler/scheduler names accepted by sd-server (stable-diffusion.cpp). Blank =
// omit the field so the server uses its own default — keeps Z-Image Turbo working
// untouched; SDXL/Pony users pick e.g. 'dpm++ 2m' + 'karras'. The live, build-exact
// lists are at GET /sdapi/v1/samplers and /sdapi/v1/schedulers.
const SAMPLERS = ['', 'euler a', 'euler', 'heun', 'dpm2', 'dpm++ 2m', 'lcm', 'ddim',
                  'res multistep', 'res 2s', 'euler_cfg_pp', 'euler_a_cfg_pp'];
const SCHEDULERS = ['', 'karras', 'exponential', 'discrete', 'sgm_uniform', 'ays', 'gits'];

function opts(values, selected) {
  const sel = selected || '';
  return values.map(v =>
    `<option value="${v}"${v === sel ? ' selected' : ''}>${v || '(server default)'}</option>`
  ).join('');
}

function injectStyles() {
  if (document.getElementById('zi-styles')) return;
  const style = document.createElement('style');
  style.id = 'zi-styles';
  style.textContent = `
    .zi-form { display:flex; flex-direction:column; gap:16px; }
    .zi-group { display:flex; flex-direction:column; gap:6px; }
    .zi-group label { font-size:13px; font-weight:500; color:var(--text); }
    .zi-group input, .zi-group textarea, .zi-group select {
      padding:8px 12px; border:1px solid var(--border); border-radius:6px;
      background:var(--bg-primary); color:var(--text); font-size:13px; }
    .zi-group input:focus, .zi-group textarea:focus, .zi-group select:focus { outline:none; border-color:var(--accent-blue); }
    /* --bg-primary is undefined in the theme — selects fall back to UA white, so set
       a real dark bg from a defined var; also style the option popup explicitly. */
    .zi-group select { background:var(--input-bg,#1a1a1a); }
    .zi-group select option { background:var(--input-bg,#1a1a1a); color:var(--text,#f5f5f5); }
    .zi-group input.error { border-color:var(--error,#e74c3c); }
    .zi-group textarea { resize:vertical; min-height:54px; }
    .zi-row { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
    .zi-section { border-top:1px solid var(--border); padding-top:16px; margin-top:8px; }
    .zi-section-title { font-size:14px; font-weight:600; color:var(--text); margin-bottom:12px; }
    .zi-hint { font-size:11px; color:var(--text-muted); margin-top:4px; }
    .zi-url-row { display:flex; gap:8px; align-items:flex-start; }
    .zi-url-row input { flex:1; }
    .zi-test-btn { padding:8px 14px; border:1px solid var(--border); border-radius:6px;
      background:var(--bg-tertiary); color:var(--text); cursor:pointer; font-size:13px;
      white-space:nowrap; transition:all .15s ease; }
    .zi-test-btn:hover { background:var(--bg-hover); }
    .zi-test-btn:disabled { opacity:.6; cursor:not-allowed; }
    .zi-test-btn.success { background:var(--success-light,#d4edda); border-color:var(--success,#28a745); color:var(--success,#28a745); }
    .zi-test-btn.error { background:var(--error-light,#f8d7da); border-color:var(--error,#dc3545); color:var(--error,#dc3545); }
    .zi-preview { background:var(--bg-tertiary); border:1px solid var(--border); border-radius:6px; padding:12px; margin-top:12px; }
    .zi-preview-title { font-size:12px; font-weight:600; color:var(--text-muted); margin-bottom:8px; text-transform:uppercase; letter-spacing:.5px; }
    .zi-preview-input { width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:6px;
      background:var(--bg-primary); color:var(--text); font-size:13px; margin-bottom:8px; }
    .zi-preview-output { padding:10px 12px; background:var(--bg-primary); border-radius:6px; font-size:13px;
      color:var(--text); line-height:1.5; word-break:break-word; }
    .zi-preview-output .replaced { background:var(--accent-blue-light,rgba(74,158,255,.2)); padding:1px 4px; border-radius:3px; }
  `;
  document.head.appendChild(style);
}

async function testConnection(container) {
  const btn = container.querySelector('#zi-test-btn');
  const url = (container.querySelector('#zi-api-url').value || '').trim();
  if (!url) { btn.textContent = 'No URL'; btn.className = 'zi-test-btn error'; return; }

  btn.disabled = true; btn.textContent = 'Testing...'; btn.className = 'zi-test-btn';
  try {
    const res = await fetch('/api/plugin/sd-server/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (data.success) {
      btn.textContent = '✓ Connected'; btn.className = 'zi-test-btn success';
    } else {
      btn.textContent = '✗ Failed'; btn.className = 'zi-test-btn error';
      btn.title = data.error || 'Connection failed';
    }
  } catch (e) {
    btn.textContent = '✗ Error'; btn.className = 'zi-test-btn error'; btn.title = e.message;
  }
  btn.disabled = false;
  setTimeout(() => { btn.textContent = 'Test'; btn.className = 'zi-test-btn'; btn.title = ''; }, 5000);
}

function updatePreview(container) {
  const input = container.querySelector('#zi-preview-input');
  const output = container.querySelector('#zi-preview-output');
  const pairs = [
    [container.querySelector('#zi-ai-name')?.value || '', container.querySelector('#zi-ai-desc')?.value || ''],
    [container.querySelector('#zi-user-name')?.value || '', container.querySelector('#zi-user-desc')?.value || ''],
  ];
  let text = (input.value || '').replace(/</g, '&lt;');
  for (const [name, desc] of pairs) {
    if (name && desc) {
      // whole-word, case-insensitive, first occurrence — mirrors the backend swap
      const re = new RegExp(`\\b${name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
      text = text.replace(re, `<span class="replaced">${desc.replace(/</g, '&lt;')}</span>`);
    }
  }
  output.innerHTML = text || '<em>Type a sample prompt above...</em>';
}

function renderForm(container, settings) {
  const s = { ...FALLBACK, ...(settings || {}) };

  container.innerHTML = `
    <div class="zi-form">
      <div class="zi-group">
        <label for="zi-api-url">sd-server URL</label>
        <div class="zi-url-row">
          <input type="text" id="zi-api-url" value="${s.api_url}" placeholder="http://127.0.0.1:7861">
          <button type="button" class="zi-test-btn" id="zi-test-btn">Test</button>
        </div>
        <div class="zi-hint">Use the server's LAN IP if Sapphire runs on a different machine.</div>
      </div>

      <div class="zi-group">
        <label for="zi-negative">Default negative prompt</label>
        <textarea id="zi-negative" rows="2" placeholder="Z-Image needs little to none">${s.default_negative}</textarea>
      </div>

      <div class="zi-group">
        <label for="zi-keywords">Static keywords</label>
        <input type="text" id="zi-keywords" value="${s.static_keywords}" placeholder="cinematic, wide shot">
        <div class="zi-hint">Always appended to the prompt.</div>
      </div>

      <div class="zi-section">
        <div class="zi-section-title">Character names &amp; appearances</div>
        <div class="zi-hint" style="margin-top:-8px;margin-bottom:12px;">
          The AI writes a name (e.g. "Sapphire") in the prompt; it's swapped for the appearance.
          Set any names you like — matching is whole-word and case-insensitive.
        </div>
        <div class="zi-row" style="grid-template-columns:1fr 2fr;">
          <div class="zi-group"><label for="zi-ai-name">AI name</label>
            <input type="text" id="zi-ai-name" value="${s.ai_name}" placeholder="Sapphire"></div>
          <div class="zi-group"><label for="zi-ai-desc">AI appearance</label>
            <input type="text" id="zi-ai-desc" value="${s.ai_description}" placeholder="a short woman with brown hair"></div>
        </div>
        <div class="zi-row" style="grid-template-columns:1fr 2fr; margin-top:8px;">
          <div class="zi-group"><label for="zi-user-name">User name</label>
            <input type="text" id="zi-user-name" value="${s.user_name}" placeholder="Fishy"></div>
          <div class="zi-group"><label for="zi-user-desc">User appearance</label>
            <input type="text" id="zi-user-desc" value="${s.user_description}" placeholder="a man with a beard"></div>
        </div>
        <div class="zi-preview">
          <div class="zi-preview-title">Preview replacement</div>
          <input type="text" class="zi-preview-input" id="zi-preview-input"
                 value="${(s.ai_name || 'Sapphire')} and ${(s.user_name || 'Fishy')} walking in the park">
          <div class="zi-preview-output" id="zi-preview-output"></div>
        </div>
      </div>

      <div class="zi-section">
        <div class="zi-section-title">Generation defaults</div>
        <div class="zi-row">
          <div class="zi-group"><label for="zi-width">Width (px)</label>
            <input type="number" id="zi-width" value="${s.default_width}" min="256" max="2048" step="64"></div>
          <div class="zi-group"><label for="zi-height">Height (px)</label>
            <input type="number" id="zi-height" value="${s.default_height}" min="256" max="2048" step="64"></div>
          <div class="zi-group"><label for="zi-steps">Steps</label>
            <input type="number" id="zi-steps" value="${s.default_steps}" min="1" max="50"></div>
        </div>
        <div class="zi-row" style="margin-top:8px;">
          <div class="zi-group"><label for="zi-cfg">CFG scale</label>
            <input type="number" id="zi-cfg" value="${s.default_cfg}" min="1" max="20" step="0.5"></div>
          <div class="zi-group"><label for="zi-max">Max per call</label>
            <input type="number" id="zi-max" value="${s.max_count}" min="1" max="12"></div>
          <div class="zi-group"></div>
        </div>
        <div class="zi-row" style="margin-top:8px;">
          <div class="zi-group"><label for="zi-sampler">Sampler</label>
            <select id="zi-sampler">${opts(SAMPLERS, s.default_sampler)}</select></div>
          <div class="zi-group"><label for="zi-scheduler">Scheduler</label>
            <select id="zi-scheduler">${opts(SCHEDULERS, s.default_scheduler)}</select></div>
          <div class="zi-group"></div>
        </div>
        <div class="zi-hint">Blank = let sd-server pick (fine for Z-Image Turbo). SDXL/Pony: sampler <code>dpm++ 2m</code>, scheduler <code>karras</code>.</div>
      </div>
    </div>
  `;

  container.querySelector('#zi-test-btn').addEventListener('click', () => testConnection(container));
  ['#zi-preview-input', '#zi-ai-name', '#zi-ai-desc', '#zi-user-name', '#zi-user-desc'].forEach(sel =>
    container.querySelector(sel).addEventListener('input', () => updatePreview(container)));
  updatePreview(container);
}

function getFormSettings(container) {
  return {
    api_url: container.querySelector('#zi-api-url')?.value?.trim() || FALLBACK.api_url,
    default_negative: container.querySelector('#zi-negative')?.value || '',
    static_keywords: container.querySelector('#zi-keywords')?.value || '',
    ai_name: container.querySelector('#zi-ai-name')?.value || '',
    ai_description: container.querySelector('#zi-ai-desc')?.value || '',
    user_name: container.querySelector('#zi-user-name')?.value || '',
    user_description: container.querySelector('#zi-user-desc')?.value || '',
    default_steps: parseInt(container.querySelector('#zi-steps')?.value) || FALLBACK.default_steps,
    default_cfg: parseFloat(container.querySelector('#zi-cfg')?.value) || FALLBACK.default_cfg,
    default_sampler: container.querySelector('#zi-sampler')?.value || '',
    default_scheduler: container.querySelector('#zi-scheduler')?.value || '',
    default_width: parseInt(container.querySelector('#zi-width')?.value) || FALLBACK.default_width,
    default_height: parseInt(container.querySelector('#zi-height')?.value) || FALLBACK.default_height,
    max_count: parseInt(container.querySelector('#zi-max')?.value) || FALLBACK.max_count,
  };
}

export default {
  name: 'sd-server',
  init(container) {
    injectStyles();
    registerPluginSettings({
      id: 'sd-server',
      name: 'Image (sd-server)',
      icon: '🖼️',
      helpText: 'stable-diffusion.cpp (sd-server) — runs Z-Image Turbo by default. The AI writes configured names, swapped for physical descriptions.',
      render: renderForm,
      load: async () => pluginsAPI.getSettings('sd-server'),
      save: (settings) => pluginsAPI.saveSettings('sd-server', settings),
      getSettings: getFormSettings,
    });
  },
  destroy() {},
};
