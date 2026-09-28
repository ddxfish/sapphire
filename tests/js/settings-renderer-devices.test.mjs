// Corpus for the three form features the Devices page added to
// shared/plugin-settings-renderer.js - run under node by
// tests/test_settings_renderer_devices_js.py. A tiny fake DOM proves:
//   rows      a list of objects round-trips through the hidden JSON input
//   show_if   the row carries its condition
//   secret    a secret textarea never echoes its value, and an empty one
//             means "keep what is stored"
// 2026-09-27 (tmp/device-manager-plan.md, C5).

const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
globalThis.document = {
    createElement: () => ({ _t: '', set textContent(v) { this._t = v; }, get innerHTML() { return esc(this._t); } }),
    querySelector: () => null,
};
globalThis.CSS = { escape: s => String(s) };

// A container that records the HTML it is given and answers id lookups from
// a table of stub elements.
function box(els = {}) {
    return {
        html: '', els,
        set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; },
        querySelector(sel) { return this.els[sel] || null; },
        querySelectorAll() { return []; },
        addEventListener() {},
    };
}

const { renderSettingsForm, readSettingsForm } =
    await import('../../interfaces/web/static/shared/plugin-settings-renderer.js');

let passed = 0;
function ok(cond, msg) { if (!cond) throw new Error('FAIL: ' + msg); passed++; }

const SCHEMA = [
    { key: 'host', type: 'string', label: 'Host' },
    { key: 'auth', type: 'string', widget: 'select', label: 'Login', default: 'auto',
      options: [{ label: 'Auto', value: 'auto' }, { label: 'Password', value: 'password' }] },
    { key: 'password', type: 'string', widget: 'password', secret: true, label: 'Password',
      show_if: { auth: 'password' } },
    { key: 'private_key', type: 'string', widget: 'textarea', secret: true, label: 'Private key',
      show_if: { auth: 'key_paste' } },
    { key: 'notes', type: 'string', widget: 'textarea', label: 'Notes' },
    { key: 'commands', widget: 'rows', label: 'Premade commands',
      columns: ['name', 'command'], add_label: '+ Add Command' },
];

// -- render ------------------------------------------------------------------
{
    const c = box();
    renderSettingsForm(c, SCHEMA, {
        host: 'tower', auth: 'password', password: 'set', private_key: 'TOP-SECRET-KEY-BODY',
        notes: 'plain notes', commands: [{ name: 'close_firefox', command: 'pkill firefox' }],
    });
    ok(!c.html.includes('TOP-SECRET-KEY-BODY'), 'a secret textarea never echoes its value');
    ok(c.html.includes('Paste a new one to replace it'), 'a set secret textarea says so');
    ok(c.html.includes('plain notes'), 'an ordinary textarea still shows its value');
    ok(c.html.includes('data-show-if="{&quot;auth&quot;:&quot;password&quot;}"'), 'show_if rides the row');
    ok((c.html.match(/data-show-if=/g) || []).length === 2, 'only show_if fields carry the attribute');
    ok(c.html.includes('data-rows-key="commands"'), 'rows widget rendered');
    ok(c.html.includes('+ Add Command'), 'rows add label used');
    ok(c.html.includes('&quot;close_firefox&quot;'), 'rows value rides the hidden input as escaped JSON');
    ok(!c.html.includes('"close_firefox"'), 'no raw quotes inside the value attribute');
}
{
    const c = box();
    renderSettingsForm(c, SCHEMA, { auth: 'auto' });
    ok(c.html.includes('Paste here'), 'an unset secret textarea invites a paste');
    ok(!c.html.includes('✓ Set'), 'nothing claims to be set');
}
{
    // untouched plugins: no show_if, no rows, no secret textarea = old markup
    const c = box();
    renderSettingsForm(c, [{ key: 'url', type: 'string', label: 'URL' }], { url: 'http://x' });
    ok(!c.html.includes('data-show-if') && !c.html.includes('ps-rows'), 'plain schema renders as before');
}

// -- read --------------------------------------------------------------------
const read = values => readSettingsForm(box(Object.fromEntries(
    Object.entries(values).map(([k, v]) => ['#ps-' + k, { value: v, checked: !!v }]))), SCHEMA);
{
    const r = read({ host: 'tower', auth: 'password', password: '', private_key: '', notes: '',
                     commands: JSON.stringify([{ name: 'a', command: 'b' }, 'junk', null, { name: 'c', command: '' }]) });
    ok(!('password' in r), 'empty password = keep the stored one');
    ok(!('private_key' in r), 'empty secret textarea = keep the stored one');
    ok(r.notes === '', 'an ordinary empty textarea is saved as empty');
    ok(Array.isArray(r.commands) && r.commands.length === 2, 'rows: objects kept, junk dropped');
    ok(r.commands[0].name === 'a' && r.commands[1].name === 'c', 'rows: order kept');
}
{
    const r = read({ host: 'x', auth: 'auto', password: '__CLEAR__', private_key: '__CLEAR__',
                     notes: 'n', commands: 'not json' });
    ok(r.password === '' && r.private_key === '', 'clear sentinel = forget the secret');
    ok(Array.isArray(r.commands) && r.commands.length === 0, 'rows: bad JSON reads as empty');
}
{
    const r = read({ host: 'x', auth: 'key_paste', password: '',
                     private_key: '-----BEGIN KEY-----\nAAAA\n-----END KEY-----', notes: '', commands: '[]' });
    ok(r.private_key.includes('\nAAAA\n'), 'a pasted key keeps its line breaks');
}

console.log(`${passed} passed`);
