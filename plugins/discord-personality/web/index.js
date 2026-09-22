import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';
import pluginsAPI from '/static/shared/plugins-api.js';

// Discord Personality — one toggle per module at the top; each module's knobs
// sit in a section below, greyed while its toggle is off. The toggles ARE the
// `<module>.enabled` manifest settings (same #ps-<key> ids the shared reader
// expects), so Save reads the whole page with readSettingsForm.
const PLUGIN = 'discord-personality';
let SCHEMA = [];

const CSS = `
.dpz-strip { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }
.dpz-toggle { display:flex; align-items:center; gap:8px; padding:8px 12px; border:1px solid var(--border);
  border-radius:10px; background:var(--input-bg, var(--bg)); cursor:pointer; user-select:none; }
.dpz-toggle.on { border-color: var(--accent, #5865f2); }
.dpz-toggle input { accent-color: var(--accent, #5865f2); }
.dpz-toggle.locked { opacity:.55; cursor:not-allowed; }
.dpz-section { border:1px solid var(--border); border-radius:10px; padding:12px 14px; margin-bottom:14px; }
.dpz-section.off { opacity:.5; }
.dpz-section h4 { margin:0 0 6px; font-size:1rem; }
.dpz-section .dpz-help { font-size:.85em; color:var(--text-muted); margin-bottom:8px; }
.dpz-none { font-size:.85em; color:var(--text-muted); font-style:italic; }
.dpz-note { font-size:.85em; color:var(--text-muted); margin-bottom:14px; }
.dpz-people { margin-top:10px; border-top:1px dashed var(--border); padding-top:10px; }
.dpz-people-bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
.dpz-people-list { display:flex; flex-direction:column; gap:6px; margin-bottom:10px; }
.dpz-person { display:grid; grid-template-columns: 1fr auto auto auto; gap:10px; align-items:center;
  padding:6px 10px; border:1px solid var(--border); border-radius:8px; }
.dpz-people-form { display:flex; gap:8px; flex-wrap:wrap; }
.dpz-people-form input { flex:1; min-width:140px; }
.dpz-btn { border:1px solid var(--border); background:var(--bg); color:var(--text); border-radius:6px; padding:6px 12px; cursor:pointer; font:inherit; }
.dpz-btn-small { padding:2px 8px; font-size:.85em; }
`;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

async function api(path, options = {}) {
  const response = await fetch(`/api/plugin/${PLUGIN}/${path}`, {
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.error) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
  return body;
}

// ── People browser (Birthdays section): who she knows on a bot, with their birthday.
// Facts arrive in S2 with the discord_people tool; here the row is the birthday's home.
const PEOPLE_HTML = `
  <div class="dpz-people">
    <div class="dpz-people-bar">
      <label>Bot account <select id="dpz-people-account"></select></label>
      <button type="button" class="dpz-btn" id="dpz-people-refresh">Refresh</button>
      <span class="dpz-none" id="dpz-people-status"></span>
    </div>
    <div id="dpz-people-list" class="dpz-people-list"><div class="dpz-none">Pick a bot account.</div></div>
    <div class="dpz-people-form">
      <input id="dpz-person-id" placeholder="Discord user id (Developer Mode → Copy User ID)" inputmode="numeric">
      <input id="dpz-person-name" placeholder="Display name">
      <input id="dpz-person-bday" placeholder="Birthday MM-DD (blank = none)" size="18">
      <button type="button" class="dpz-btn" id="dpz-person-save">Save person</button>
    </div>
  </div>`;

async function loadAccounts(select) {
  try {
    const res = await fetch('/api/plugin/discord/accounts', { headers: { 'X-CSRF-Token': CSRF() } });
    const data = await res.json().catch(() => ({}));
    const names = (data.accounts || []).map(a => a.name).filter(Boolean);
    select.innerHTML = names.length
      ? names.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('')
      : '<option value="">(no Discord bot accounts yet)</option>';
  } catch {
    select.innerHTML = '<option value="">(Discord plugin unavailable)</option>';
  }
}

function initPeopleBrowser(root) {
  const select = root.querySelector('#dpz-people-account');
  const list = root.querySelector('#dpz-people-list');
  const status = root.querySelector('#dpz-people-status');
  if (!select || !list) return;
  const account = () => select.value || '';
  const render = (rows) => {
    if (!rows.length) { list.innerHTML = '<div class="dpz-none">Nobody yet — add a person below.</div>'; return; }
    list.innerHTML = rows.map(r => `
      <div class="dpz-person" data-user="${esc(r.user_id)}">
        <span><strong>${esc(r.display_name || r.user_id)}</strong> <span class="dpz-none">${esc(r.user_id)}</span></span>
        <span>${r.birthday_mm_dd ? '🎂 ' + esc(r.birthday_mm_dd) : '<span class="dpz-none">no birthday</span>'}</span>
        <button type="button" class="dpz-btn dpz-btn-small" data-edit="${esc(r.user_id)}">Edit</button>
        <button type="button" class="dpz-btn dpz-btn-small" data-forget="${esc(r.user_id)}">Forget</button>
      </div>`).join('');
    list.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => {
      const r = rows.find(x => x.user_id === b.dataset.edit) || {};
      root.querySelector('#dpz-person-id').value = r.user_id || '';
      root.querySelector('#dpz-person-name').value = r.display_name || '';
      root.querySelector('#dpz-person-bday').value = r.birthday_mm_dd || '';
    }));
    list.querySelectorAll('[data-forget]').forEach(b => b.addEventListener('click', async () => {
      if (!window.confirm('Forget this person (and their birthday)?')) return;
      try {
        await api('people/delete', { method: 'POST', body: JSON.stringify({ account: account(), user_id: b.dataset.forget }) });
        await refresh();
      } catch (e) { status.textContent = e.message; }
    }));
  };
  const refresh = async () => {
    if (!account()) { render([]); return; }
    status.textContent = 'Loading…';
    try {
      const data = await api(`people?account=${encodeURIComponent(account())}`);
      render(data.people || []);
      status.textContent = `${(data.people || []).length} known`;
    } catch (e) { status.textContent = e.message; }
  };
  root.querySelector('#dpz-people-refresh')?.addEventListener('click', refresh);
  select.addEventListener('change', refresh);
  root.querySelector('#dpz-person-save')?.addEventListener('click', async () => {
    const body = {
      account: account(),
      user_id: root.querySelector('#dpz-person-id').value.trim(),
      display_name: root.querySelector('#dpz-person-name').value.trim(),
      birthday: root.querySelector('#dpz-person-bday').value.trim(),
    };
    try {
      await api('people', { method: 'POST', body: JSON.stringify(body) });
      status.textContent = 'Saved.';
      await refresh();
    } catch (e) { status.textContent = e.message; }
  });
  loadAccounts(select).then(refresh);
}

function modulesFromSchema(schema) {
  // A module = a `<name>.enabled` field; its knobs = every other field with the same prefix.
  return schema.filter(f => f.key.endsWith('.enabled')).map(f => {
    const name = f.key.slice(0, -'.enabled'.length);
    return { name, toggle: f, fields: schema.filter(g => g.key.startsWith(name + '.') && g.key !== f.key) };
  });
}

const NEEDS = { birthdays: 'people' };

function render(container, values) {
  values = values || {};
  const mods = modulesFromSchema(SCHEMA);
  const on = (m) => Boolean(values[m.toggle.key] ?? m.toggle.default);
  container.innerHTML = `
    <style>${CSS}</style>
    <div class="dpz-note">Modules ride the Discord plugin's hooks. A module that is off registers nothing.</div>
    <div class="dpz-strip">${mods.map(m => `
      <label class="dpz-toggle ${on(m) ? 'on' : ''}" data-mod="${esc(m.name)}">
        <input type="checkbox" id="ps-${esc(m.toggle.key)}" ${on(m) ? 'checked' : ''}>
        <span>${esc(m.toggle.label)}</span>
      </label>`).join('')}
    </div>
    ${mods.map(m => `
      <div class="dpz-section ${on(m) ? '' : 'off'}" data-mod="${esc(m.name)}">
        <h4>${esc(m.toggle.label)}</h4>
        ${m.toggle.help ? `<div class="dpz-help">${esc(m.toggle.help)}</div>` : ''}
        <div class="dpz-fields"></div>
      </div>`).join('')}
  `;
  for (const m of mods) {
    const box = container.querySelector(`.dpz-section[data-mod="${m.name}"] .dpz-fields`);
    if (m.fields.length) renderSettingsForm(box, m.fields, values, {});
    else box.innerHTML = '<div class="dpz-none">No knobs yet — this module is built in a later step.</div>';
    if (m.name === 'birthdays') {
      box.insertAdjacentHTML('beforeend', PEOPLE_HTML);
      initPeopleBrowser(box);
    }
  }
  const sync = () => {
    const state = {};
    for (const m of mods) state[m.name] = container.querySelector(`#${CSS_escape(`ps-${m.toggle.key}`)}`)?.checked ?? false;
    for (const m of mods) {
      const need = NEEDS[m.name];
      const locked = need ? !state[need] : false;
      const active = state[m.name] && !locked;
      container.querySelector(`.dpz-toggle[data-mod="${m.name}"]`)?.classList.toggle('on', active);
      container.querySelector(`.dpz-toggle[data-mod="${m.name}"]`)?.classList.toggle('locked', locked);
      container.querySelector(`.dpz-toggle[data-mod="${m.name}"]`)?.setAttribute('title', locked ? `Needs ${need}` : '');
      const sec = container.querySelector(`.dpz-section[data-mod="${m.name}"]`);
      sec?.classList.toggle('off', !active);
      sec?.querySelectorAll('input,select,textarea,button').forEach(el => { el.disabled = !active; });
    }
  };
  const CSS_escape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/([.#:[\]])/g, '\\$1');
  container.querySelectorAll('.dpz-strip input').forEach(el => el.addEventListener('change', sync));
  sync();
}

async function load() {
  const [plugins, values] = await Promise.all([pluginsAPI.listPlugins(), pluginsAPI.getSettings(PLUGIN)]);
  const me = (plugins || []).find(p => p.name === PLUGIN);
  SCHEMA = me?.settings_schema || [];
  return values || {};
}

registerPluginSettings({
  id: PLUGIN,
  name: 'Personality',
  icon: '🦓',
  helpText: 'Discord personality modules — people memory, birthdays, reminders, typos & edits, presence cycling. One toggle each.',
  load,
  render,
  getSettings: (container) => readSettingsForm(container, SCHEMA),
  save: (s) => pluginsAPI.saveSettings(PLUGIN, s),
  reset: () => pluginsAPI.resetSettings(PLUGIN),
});
