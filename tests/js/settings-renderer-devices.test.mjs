// Corpus for the three form features the Devices page added to
// shared/plugin-settings-renderer.js - run under node by
// tests/test_settings_renderer_devices_js.py. A tiny fake DOM proves:
//   rows      a list of objects round-trips through the hidden JSON input
//   show_if   the row carries its condition
//   secret    a secret textarea never echoes its value, and an empty one
//             means "keep what is stored"
//   found     a live pick list: what is here, what was picked and is away
// 2026-09-27 (tmp/device-manager-plan.md, C5). found: 2026-09-28, section 14.

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

const { renderSettingsForm, readSettingsForm, foundPicks, foundRows, foundHTML } =
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

// -- found: the pick list ------------------------------------------------------
const FOUND = { key: 'keys.sources', type: 'found', label: 'Plays through it', all_label: 'Every MIDI source' };
const AKM = { id: 'usb-akm', name: 'AKM320', kind: 'USB' };
const FM1 = { id: 'name:FM-1_BLE', name: 'FM-1_BLE', kind: 'Bluetooth' };
{
    ok(JSON.stringify(foundPicks(undefined)) === '{"all":true,"only":[]}', 'never set = everything counts');
    ok(JSON.stringify(foundPicks('not json')) === '{"all":true,"only":[]}', 'junk = everything counts');
    const p = foundPicks(JSON.stringify({ all: false, only: [{ id: 'usb-akm', name: 'AKM320' }, { name: 'no id' }, null] }));
    ok(p.all === false && p.only.length === 1 && p.only[0].id === 'usb-akm', 'picks: kept, junk dropped');
    ok(foundPicks({ all: false, only: [{ id: 7 }] }).only[0].name === '7', 'a pick without a name goes by its id');

    const rows = foundRows({ all: false, only: [{ id: 'usb-old', name: 'Old keys' }, { id: 'usb-akm', name: 'AKM320' }] }, [AKM, FM1]);
    ok(rows.map(r => r.name).join() === 'AKM320,FM-1_BLE,Old keys', 'what is here comes first, then what is away');
    ok(rows[2].away === true && !rows[0].away, 'a pick that is not here is marked away');
    ok(foundRows(foundPicks(null), null).length === 0, 'still looking = no rows');
}
{
    const c = box();
    renderSettingsForm(c, [FOUND], { 'keys.sources': { all: false, only: [{ id: 'usb-"akm', name: 'A<b>KM' }] } });
    ok(c.html.includes('data-found-key="keys.sources"') && c.html.includes('id="ps-keys.sources"'), 'found widget rendered');
    ok(c.html.includes('&quot;usb-\\&quot;akm&quot;'), 'the value rides the hidden input as escaped JSON');
    ok(!c.html.includes('<b>'), 'a name is never markup');
}
{
    const all = foundHTML(FOUND, foundPicks(null), [AKM, FM1]);
    ok(all.includes('Every MIDI source') && all.includes('Only these'), 'both choices are named');
    ok(/value="all" checked/.test(all) && !/value="only" checked/.test(all), '"every" is chosen');
    ok((all.match(/checked disabled|checked\s+disabled/g) || []).length === 2, 'every one that is here is shown as counting, and cannot be unticked');
    ok(all.includes('USB') && all.includes('Bluetooth') && all.includes('Look again'), 'kinds and the look-again button');

    const only = foundHTML(FOUND, { all: false, only: [{ id: 'usb-old', name: 'Old keys' }, { id: 'usb-akm', name: 'AKM320' }] }, [AKM, FM1]);
    ok(/value="only" checked/.test(only), '"only these" is chosen');
    ok(!only.includes('disabled'), 'the ticks can be changed');
    ok(/data-id="usb-akm"[^>]*\n?[^>]*checked/.test(only), 'a pick that is here is ticked');
    ok(!/data-id="name:FM-1_BLE"[^>]*checked/.test(only), 'one that was not picked is not');
    ok(/data-id="usb-old"[^>]*checked/.test(only) && only.includes('not here now'), 'a pick that is away stays, and says so');

    ok(foundHTML(FOUND, foundPicks(null), null).includes('Looking...'), 'says it is looking');
    ok(foundHTML(FOUND, foundPicks(null), []).includes('Nothing is here right now.'), 'says when nothing is here');
    ok(foundHTML(FOUND, foundPicks(null), [], 'The driver is off <x>').includes('The driver is off &lt;x&gt;'), 'a problem is shown, escaped');
    ok(!foundHTML(FOUND, foundPicks(null), [{ id: 'x', name: '<img src=x>', kind: '<i>' }]).includes('<img'), 'found names are never markup');

    const one = foundHTML({ ...FOUND, many: false }, { all: false, only: [{ id: 'name:FM-1_BLE', name: 'FM-1_BLE' }] }, [AKM, FM1]);
    ok(one.includes('<select') && !one.includes('type="checkbox"'), 'pick one is a dropdown');
    ok(one.includes('Every MIDI source') && /value="name:FM-1_BLE" selected/.test(one), 'its pick is selected');
    ok(!/value="usb-akm" selected/.test(one), 'and only that one');
}
{
    const field = [FOUND];
    const readOne = v => readSettingsForm(box({ '#ps-keys.sources': { value: v } }), field)['keys.sources'];
    const r = readOne(JSON.stringify({ all: false, only: [AKM] }));
    ok(r.all === false && r.only.length === 1 && r.only[0].id === 'usb-akm' && !('kind' in r.only[0]), 'reads back as picks');
    ok(readOne('not json').all === true, 'bad JSON reads as "everything"');
}

// -- found: the wiring, run for real against stub elements -----------------------
{
    const listeners = {};
    const sent = [];
    const hidden = { value: JSON.stringify({ all: true, only: [] }), dispatchEvent: e => sent.push(e.type) };
    const body = { innerHTML: '', addEventListener: (type, fn) => { listeners[type] = fn; } };
    const wrap = { querySelector: sel => sel === '.ps-found-body' ? body : sel === '#ps-keys.sources' ? hidden : null };
    const c = box({ '.ps-found[data-found-key="keys.sources"]': wrap });
    const asked = [];
    globalThis.fetch = async url => { asked.push(url); return { ok: true, json: async () => ({ found: [AKM, FM1] }) }; };
    renderSettingsForm(c, [{ ...FOUND, found_url: '/api/devices/found/keys?device=keyboard' }], {});
    ok(body.innerHTML.includes('Looking...'), 'it says it is looking while the driver is asked');
    await new Promise(r => setTimeout(r, 20));
    ok(asked.length === 1 && asked[0] === '/api/devices/found/keys?device=keyboard', 'the driver is asked once, at its own address');
    ok(body.innerHTML.includes('AKM320') && body.innerHTML.includes('FM-1_BLE'), 'what is here is listed');

    const event = (target) => ({ target, stopPropagation() { this.stopped = true; } });
    const el = (kind, more) => ({ matches: sel => sel === kind, closest: () => null, ...more });
    let e = event(el('input[type="radio"]', { value: 'only' }));
    listeners.change(e);
    ok(e.stopped && JSON.parse(hidden.value).all === false, 'choosing "only these" is stored, and the inner event goes no further');
    ok(sent.length === 1 && sent[0] === 'change', 'the hidden input speaks for the field');
    listeners.change(event(el('.ps-found-tick', { checked: true, dataset: { id: 'usb-akm', name: 'AKM320' } })));
    listeners.change(event(el('.ps-found-tick', { checked: true, dataset: { id: 'name:FM-1_BLE', name: 'FM-1_BLE' } })));
    listeners.change(event(el('.ps-found-tick', { checked: false, dataset: { id: 'usb-akm', name: 'AKM320' } })));
    ok(hidden.value === '{"all":false,"only":[{"id":"name:FM-1_BLE","name":"FM-1_BLE"}]}', 'ticks are added and taken away');
    ok(/data-id="name:FM-1_BLE"[^>]*checked/.test(body.innerHTML) && !/data-id="usb-akm"[^>]*checked/.test(body.innerHTML), 'and drawn');
    listeners.change(event(el('input[type="radio"]', { value: 'all' })));
    ok(JSON.parse(hidden.value).all === true && JSON.parse(hidden.value).only.length === 1, 'back to "every": the ticks are kept');

    globalThis.fetch = async () => ({ ok: false, status: 400, json: async () => ({ detail: 'The fm1 plugin is off.' }) });
    listeners.click({ target: { closest: sel => sel === '.ps-found-look' ? {} : null } });
    await new Promise(r => setTimeout(r, 20));
    ok(body.innerHTML.includes('The fm1 plugin is off.'), 'a look that fails says why');
    ok(body.innerHTML.includes('FM-1_BLE') && body.innerHTML.includes('not here now'), 'and the picks are still shown');

    // pick one
    const hidden1 = { value: '', dispatchEvent() {} };
    const body1 = { innerHTML: '', addEventListener: (type, fn) => { listeners['one-' + type] = fn; } };
    const wrap1 = { querySelector: sel => sel === '.ps-found-body' ? body1 : hidden1 };
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ found: [AKM, FM1] }) });
    renderSettingsForm(box({ '.ps-found[data-found-key="keys.sources"]': wrap1 }),
        [{ ...FOUND, many: false, found_url: '/x' }], {});
    await new Promise(r => setTimeout(r, 20));
    listeners['one-change'](event(el('.ps-found-one', { value: 'usb-akm' })));
    ok(hidden1.value === '{"all":false,"only":[{"id":"usb-akm","name":"AKM320"}]}', 'pick one: the one is stored');
    listeners['one-change'](event(el('.ps-found-one', { value: '' })));
    ok(JSON.parse(hidden1.value).all === true, 'pick one: "whichever" is stored');
}

console.log(`${passed} passed`);
