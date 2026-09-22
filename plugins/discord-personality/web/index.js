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
`;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
