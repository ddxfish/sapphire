// settings-tabs/devices.js - Settings > Devices (tmp/device-manager-plan.md)
//
// The list, the add window, and the device window. Every form is drawn by the
// shared settings renderer from the driver's own schema; this file holds no
// driver knowledge. Add asks what is being added (one card per type), then
// only what that type needs to work (the fields its driver marks `setup`),
// then opens the device window. The window's first tab is Status, then one
// tab per capability. The page saves with its own buttons (selfSaving).
//
// A device is online or offline, nothing else (core/devices/health.py
// believes; the page only reads). A probe in flight pulses the dot and never
// changes the words. The list is drawn as cards on the daemon page's CSS, and
// a row of type pills filters it when the devices are of more than one type.

import { renderSettingsForm, readSettingsForm } from '../../shared/plugin-settings-renderer.js';
import { showModal, escapeHtml as esc } from '../../shared/modal.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';
import { showDangerConfirm } from '../../shared/danger-confirm.js';
import { openFlash } from './device-flash.js';
import { renderSectionTabs } from '../../shared/section-tabs.js';

const PANES = [{ id: 'list', label: 'Devices', icon: '\u{1F4E1}' }, { id: 'settings', label: 'Settings', icon: '\u{2699}\u{FE0F}' }];

const API = '/api/devices';
const ICON = String.fromCodePoint(0x1F39B, 0xFE0F);
const POLL_MS = 10000;
const LOCATION_HELP = 'The room or place it is in. Sapphire sees it, so she knows where a voice came from.';

let page = null;        // the tab's container
let drivers = [];
let devices = [];       // the list as it was last loaded
let typeFilter = '';    // a driver label, or '' for every type
let timer = null;
let onPoll = null;      // the open device window reads each poll, so it never has to ask itself

const call = (method, path = '', body, ms = 30000) => fetchWithTimeout(API + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
}, ms);

const ago = ts => { const s = Math.max(0, Math.round(Date.now() / 1000 - ts)); return s < 90 ? `${s}s ago` : `${Math.round(s / 60)}m ago`; };
const stateWord = d => !d.enabled ? 'off' : d.status?.online ? 'online' : 'offline';
const when = st => !st ? '' : st.checking ? 'checking now' : st.ts ? `checked ${ago(st.ts)}` : 'not checked yet';
// Green or red, never a third colour for "checking": a probe in flight pulses instead.
const dot = (d, size = 10) => {
    const st = d.enabled ? d.status : null;
    const cls = 'sched-status-dot' + (!st ? '' : st.online ? ' running' : ' stopped') + (st?.checking ? ' pulse' : '');
    return `<span class="${cls}" style="width:${size}px;height:${size}px;margin:0;flex-shrink:0;${
        st ? '' : 'background:var(--text-muted,#888)'}" title="${stateWord(d)}"></span>`;
};

// ---- the page --------------------------------------------------------------

function drawPage(container) {
    page = container;
    container.innerHTML = `
        <div id="dev-tabs" style="margin-bottom:14px">${renderSectionTabs(PANES, 'list')}</div>
        <div id="dev-pane-settings" style="display:none"></div>
        <div id="dev-pane-list">
        <div class="setting-help" style="margin-bottom:12px">Machines and gadgets Sapphire can use by name. Only this page can add or change them.</div>
        <div id="dev-filter"></div>
        <div class="sched-layout trigger-2col" id="dev-list" style="max-width:none;padding:0">
            <div style="min-width:0">
                <div class="sched-col-header">
                    <h3>Boards</h3>
                    <button class="btn btn-sm btn-primary" id="dev-board-btn" style="display:none" title="Plug an ESP32 board in: it is written and set up from here">+ Add Board</button>
                </div>
                <div id="dev-list-boards"><p class="text-muted">Loading...</p></div>
            </div>
            <div style="min-width:0">
                <div class="sched-col-header">
                    <h3>Devices</h3>
                    <button class="btn btn-sm btn-primary" id="dev-add-btn">+ Add Device</button>
                </div>
                <div id="dev-list-devices"></div>
            </div>
        </div>
        </div>`;
    // the strip every section wears (shared/section-tabs.js); here a tab shows a pane of this page, not another view
    container.querySelector('#dev-tabs').addEventListener('click', e => {
        const tab = e.target.closest('.section-tab');
        if (!tab) return;
        container.querySelectorAll('#dev-tabs .section-tab').forEach(b => b.classList.toggle('active', b === tab));
        const settings = tab.dataset.view === 'settings';
        container.querySelector('#dev-pane-list').style.display = settings ? 'none' : '';
        container.querySelector('#dev-pane-settings').style.display = settings ? '' : 'none';
        if (settings) drawSettings(container.querySelector('#dev-pane-settings'));
    });
    container.querySelector('#dev-add-btn').addEventListener('click', () => openAdd());
    container.querySelector('#dev-board-btn').addEventListener('click', () => openFlash(flashed));
    container.querySelector('#dev-filter').addEventListener('click', e => {
        const pill = e.target.closest('[data-type]');
        if (!pill) return;
        typeFilter = pill.dataset.type;
        drawList();
    });
    container.querySelector('#dev-list').addEventListener('click', e => {
        const more = e.target.closest('[data-more]');
        if (more) return openMenu(more, devices.find(d => d.id === more.dataset.more));
        const row = e.target.closest('[data-device]');
        if (row) openDevice(row.dataset.device);
    });
    loadList();
    clearInterval(timer);
    timer = setInterval(() => {
        if (!page || !page.isConnected) { clearInterval(timer); timer = null; return; }
        if (!document.hidden && page.offsetParent) loadList();     // not while another view is up (the router only hides this one)
    }, POLL_MS);
}

// ---- settings for every device: where board firmware comes from -------------------------

const NOT_OURS = "Firmware from a source you add is someone else's code, not part of Sapphire. It runs on your board, holds your WiFi password and can talk to Sapphire as that device. Add only a source you trust.";

async function drawSettings(el) {
    el.innerHTML = '<p class="text-muted">Reading the firmware sources...</p>';
    let fw;
    try { fw = await call('GET', '/firmware', undefined, 60000); }
    catch (e) { el.innerHTML = `<p style="color:var(--error)">Could not read the firmware sources: ${esc(e.message)}</p>`; return; }
    const all = fw.sources || [];
    const mine = all.filter(s => !s.official).map(s => s.source);
    const off = all.filter(s => !s.on).map(s => s.source);
    const save = async (list, quiet) => {
        try { await call('PUT', '/firmware/sources', { sources: list, off: quiet }, 60000); }
        catch (e) { showToast(e.message, 'error'); }
        drawSettings(el);
    };
    const said = s => !s.on ? 'Switched off: not read, no boards offered from here.'
        : s.error ? `<span style="color:var(--error)">Could not be read: ${esc(s.error)}</span>`
        : `${s.boards} board${s.boards === 1 ? '' : 's'} offered from here.`;
    const row = s => `
        <div class="setting-row" style="align-items:center${s.on ? '' : ';opacity:0.6'}">
            <div class="setting-label"><code style="word-break:break-all">${esc(s.source)}</code>
                <div class="setting-help">${s.official ? "Sapphire's own release, read after any source of yours." : '<span style="color:var(--warning, #c90)">Not Sapphire\'s.</span> Read before her release: a board both have comes from here.'}
                <br>${said(s)}</div></div>
            <div class="setting-input" style="display:flex;gap:10px;align-items:center;justify-content:flex-end">
                <label class="setting-toggle" title="${s.on ? 'On: read' : 'Off: not read'}"><input type="checkbox" data-toggle="${esc(s.source)}" ${s.on ? 'checked' : ''}></label>
                ${s.official ? '' : `<button type="button" class="btn btn-sm" data-drop="${esc(s.source)}">Remove</button>`}</div>
        </div>`;
    el.innerHTML = `
        <b style="display:block;margin-bottom:4px">Board firmware sources</b>
        <div class="setting-help" style="margin-bottom:10px">Where Add Board and a device's Firmware tab find programs for boards. Each is a firmware release URL (the folder its index.json is in) or a folder on Sapphire's computer.</div>
        <div class="settings-grid">${all.map(row).join('')}</div>
        <div class="setting-input" style="display:flex;gap:8px;margin-top:14px;align-items:center">
            <input type="text" id="dev-source-new" style="flex:1;min-width:0" placeholder="https://github.com/someone/their-firmware/releases/latest/download/">
            <button type="button" class="btn btn-sm btn-primary" id="dev-source-add" style="flex-shrink:0">+ Board Repo</button>
        </div>
        <p class="setting-help" style="margin-top:8px">${NOT_OURS}</p>`;
    el.querySelectorAll('[data-drop]').forEach(b => b.addEventListener('click', () =>
        save(mine.filter(s => s !== b.dataset.drop), off.filter(s => s !== b.dataset.drop))));
    el.querySelectorAll('[data-toggle]').forEach(t => t.addEventListener('change', () =>
        save(mine, t.checked ? off.filter(s => s !== t.dataset.toggle) : [...off, t.dataset.toggle])));
    const add = () => {
        const value = el.querySelector('#dev-source-new').value.trim();
        if (value) save([...mine, value], off);
    };
    el.querySelector('#dev-source-add').addEventListener('click', add);
    el.querySelector('#dev-source-new').addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
}

async function loadList() {
    const list = page?.querySelector('#dev-list-boards');
    if (!list) return;
    try {
        const data = await call('GET');
        drivers = data.drivers || [];
        devices = (data.devices || []).sort((a, b) =>
            (a.type || '').localeCompare(b.type || '') || a.id.localeCompare(b.id));   // by type, then name: stable
        const board = page.querySelector('#dev-board-btn');           // boards are satellites: no driver, no button
        if (board) board.style.display = drivers.some(d => d.driver === 'satellite' && d.available) ? '' : 'none';
        drawList();
    } catch (e) {
        list.innerHTML = `<p style="color:var(--error)">Could not load devices: ${esc(e.message)}</p>`;
    }
}

function drawList() {
    const boards = page?.querySelector('#dev-list-boards');
    const others = page?.querySelector('#dev-list-devices');
    const bar = page?.querySelector('#dev-filter');
    if (!boards || !others || !bar) return;
    const counts = {};
    for (const d of devices) if (d.type) counts[d.type] = (counts[d.type] || 0) + 1;
    const types = Object.keys(counts).sort((a, b) => a.localeCompare(b));
    if (typeFilter && !counts[typeFilter]) typeFilter = '';
    // one type = nothing to choose, so no pills
    const pill = (t, label) => `<button type="button" class="ui-pill${typeFilter === t ? ' ui-pill-on' : ''}" data-type="${esc(t)}">${esc(label)}</button>`;
    bar.innerHTML = types.length < 2 ? '' : `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">${
        pill('', `All (${devices.length})`)}${types.map(t => pill(t, `${t} (${counts[t]})`)).join('')}</div>`;
    const shown = typeFilter ? devices.filter(d => d.type === typeFilter) : devices;
    // two columns only so the page is shorter: boards Sapphire's firmware runs on, and everything else
    const none = what => `<p class="text-muted" style="font-size:0.9em">${what}</p>`;
    const fill = (el, some, empty) => { el.innerHTML = some.length ? some.map(card).join('') : none(empty); };
    fill(boards, shown.filter(isBoard), typeFilter ? 'No boards of this type.' : 'No boards yet. Plug one in and add it.');
    fill(others, shown.filter(d => !isBoard(d)), typeFilter ? 'No devices of this type.' : 'No devices yet. Add one to get started.');
    onPoll?.(devices);
}

function card(d) {
    const st = d.enabled ? d.status : null;
    const icon = drivers.find(x => x.driver === d.driver)?.icon || '';
    const caps = (d.capabilities || []).join(' · ') || 'nothing yet';
    const missing = (d.missing || []).length
        ? ` <span style="color:var(--error,#e53935)">driver off: ${esc(d.missing.join(', '))}</span>` : '';
    const all = (st?.parts || []).flatMap(p => Object.entries(p.readings || {}));
    const readings = all.slice(0, 2).map(([k, v]) => `${k} ${v}`);
    const meta = [stateWord(d), when(st), ...readings].filter(Boolean);
    // a newer program in the firmware source: said on the card, not only in the window
    const newer = all.find(([k]) => k === 'update')?.[1];
    const update = newer ? `<span class="sched-plugin-badge" style="background:var(--primary);color:#fff" title="${esc(newer)}">update ${esc(String(newer).split(' ')[0])}</span>` : '';
    return `
    <div class="sched-task-card" data-device="${esc(d.id)}" style="cursor:pointer">
        ${dot(d)}
        <div class="sched-task-info">
            <div class="sched-task-name">${icon ? icon + ' ' : ''}${esc(d.id)}${d.label && d.label !== d.id
                ? ` <span class="text-muted" style="font-weight:400">${esc(d.label)}</span>` : ''}</div>
            <div class="sched-task-schedule">${esc(caps)}${missing}</div>
            <div class="sched-task-meta">${meta.map(esc).join(' · ')}</div>
        </div>
        <div class="sched-task-actions" style="flex-direction:column;align-items:flex-end;gap:4px">
            ${d.location ? `<span class="text-muted" style="font-size:var(--font-xs)">${esc(d.location)}</span>` : ''}
            ${update}${d.type ? `<span class="sched-plugin-badge">${esc(d.type)}</span>` : ''}
        </div>
        <button type="button" data-more="${esc(d.id)}" title="More" aria-label="More for ${esc(d.id)}"
                style="background:none;border:0;color:var(--text-muted,#888);font-size:1.3em;line-height:1;cursor:pointer;padding:6px 8px;border-radius:6px">&#8942;</button>
    </div>`;
}

// ---- the card's menu ---------------------------------------------------------

// after a board was written or given new settings: the list, then its window asking it once
const flashed = async id => { await loadList(); openDevice(id, undefined, { test: true }); };

// A board Sapphire's own firmware runs on: written by the flasher (it has the
// chip's id) or one that says it takes a program. A Pi satellite is neither.
const isBoard = d => d.driver === 'satellite' && !!(d.fingerprint || (d.capabilities || []).includes('firmware'));

function openMenu(anchor, d) {
    document.querySelector('#dev-menu')?.remove();
    if (!d) return;
    const items = [
        ...(isBoard(d) ? [{ text: 'Reflash (USB)', run: () => openFlash(flashed, { device: d, mode: 'install' }) },
                          { text: 'Change WiFi (USB)', run: () => openFlash(flashed, { device: d, mode: 'wifi' }) }] : []),
        { text: 'Delete device', danger: true, run: () => removeDevice(d.id) },
    ];
    const at = anchor.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = 'dev-menu';
    menu.style.cssText = `position:fixed;z-index:1000;top:${at.bottom + 4}px;right:${Math.max(8, innerWidth - at.right)}px;min-width:170px;padding:4px;
        background:var(--bg-secondary,#222);border:1px solid var(--border,#444);border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.35)`;
    menu.innerHTML = items.map((it, i) => `<button type="button" data-i="${i}" style="display:block;width:100%;text-align:left;background:none;border:0;
        padding:8px 10px;border-radius:6px;font:inherit;cursor:pointer;color:${it.danger ? 'var(--error,#e53935)' : 'inherit'}">${esc(it.text)}</button>`).join('');
    document.body.appendChild(menu);
    menu.addEventListener('mouseover', e => { for (const b of menu.children) b.style.background = b === e.target ? 'var(--bg-tertiary,#333)' : 'none'; });
    // any click, Esc, a scroll or a resize puts it away; a click on one of its lines also runs it
    const shut = e => {
        if (e.type === 'keydown' && e.key !== 'Escape') return;
        const line = e.type === 'click' ? e.target.closest?.('#dev-menu [data-i]') : null;
        menu.remove();
        for (const ev of ['click', 'keydown', 'scroll', 'resize']) window.removeEventListener(ev, shut, true);
        if (line) items[+line.dataset.i].run();
    };
    setTimeout(() => { for (const ev of ['click', 'keydown', 'scroll', 'resize']) window.addEventListener(ev, shut, true); });
}

// True when it is gone.
async function removeDevice(id) {
    if (!confirm(`Delete device "${id}"? Its stored secrets are deleted too. This cannot be undone.`)) return false;
    try {
        await call('DELETE', `/${encodeURIComponent(id)}`);
        showToast(`Deleted ${id}`, 'success');
        await loadList();
        return true;
    } catch (e) { showToast(e.message, 'error'); return false; }
}

// ---- add a device ----------------------------------------------------------

// A name no device has yet: the type, then the type with a number.
function freeName(driver) {
    const taken = new Set(devices.map(d => d.id));
    let id = driver, n = 1;
    while (taken.has(id)) id = `${driver}-${++n}`;
    return id;
}

// What a type needs before it can work at all: the fields its driver marks
// `setup`, and any field that shows only because of one of them.
function setupFields(d) {
    const keys = new Set(d.config_schema.filter(f => f.setup).map(f => f.key));
    return d.config_schema
        .filter(f => keys.has(f.key) || (f.show_if && Object.keys(f.show_if).some(k => keys.has(k))))
        .map(f => ({ ...withLook(f, d.driver), tab: undefined }));
}

function typeCard(d) {
    const caps = (d.capabilities || []).join(' · ');
    return `
    <button type="button" class="ui-card" data-driver="${esc(d.driver)}" ${d.available ? '' : 'disabled'}
            style="cursor:${d.available ? 'pointer' : 'not-allowed'};text-align:left;font:inherit;${d.available ? '' : 'opacity:0.55'}">
        <div style="font-size:1.6em;line-height:1.2">${d.icon || ''}</div>
        <div class="ui-card-title" style="padding-right:0">${esc(d.label)}</div>
        <div class="ui-card-body" style="margin-top:2px">${esc(caps)}</div>
        ${d.available ? '' : `<div class="ui-card-body" style="color:var(--text-muted)">${esc(d.note || 'not available')}</div>`}
    </button>`;
}


function openAdd() {
    const modal = showModal(`${ICON} Add a device`,
        [{ type: 'html', value: '<div id="dev-add"></div>' }], null, { wide: true });
    const body = modal.element.querySelector('#dev-add');
    const footer = modal.element.querySelector('.modal-footer');
    let addBtn = null;

    // step 1: what are you adding?
    const pick = () => {
        addBtn?.remove(); addBtn = null;
        body.innerHTML = drivers.length
            ? `<div class="setting-help" style="margin-bottom:10px">What are you adding?</div>
               <div class="ui-grid ui-grid-sm">${drivers.map(typeCard).join('')}</div>`
            : '<p class="text-muted" style="font-size:0.9em">No device types yet. Enable a plugin that provides one, such as SSH.</p>';
        body.querySelectorAll('[data-driver]').forEach(c => c.addEventListener('click', () => {
            const d = drivers.find(x => x.driver === c.dataset.driver && x.available);
            if (d) setup(d);
        }));
    };

    // step 2: only what this type needs
    const setup = d => {
        const schema = setupFields(d);
        body.innerHTML = `
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
                <button type="button" class="btn btn-sm" id="dev-add-back">&larr; Back</button>
                <b>${d.icon || ''} ${esc(d.label)}</b>
            </div>
            <div class="settings-grid">
                <div class="setting-row"><div class="setting-label"><label>Name</label>
                    <div class="setting-help">What Sapphire calls it. Lowercase, no spaces.</div></div>
                    <div class="setting-input"><input type="text" id="dev-new-id" value="${esc(freeName(d.driver))}"></div></div>
            </div>
            <div id="dev-new-fields" style="margin-top:8px"></div>
            <div class="settings-grid" style="margin-top:8px">
                <div class="setting-row"><div class="setting-label"><label>Location</label>
                    <div class="setting-help">${LOCATION_HELP}</div></div>
                    <div class="setting-input"><input type="text" id="dev-new-location" placeholder="Living Room" maxlength="60"></div></div>
            </div>`;
        const fields = body.querySelector('#dev-new-fields');
        if (schema.length) renderSettingsForm(fields, schema, {});
        else fields.innerHTML = '<p class="text-muted" style="font-size:0.9em;margin:4px 0">Nothing else to set up for this type. Everything it has can be changed in its window.</p>';
        body.querySelector('#dev-add-back').addEventListener('click', pick);
        body.querySelector('#dev-new-id').focus();

        addBtn = document.createElement('button');
        addBtn.className = 'btn btn-primary';
        addBtn.textContent = 'Add and test';
        footer.prepend(addBtn);
        addBtn.addEventListener('click', async () => {
            const id = body.querySelector('#dev-new-id').value.trim().toLowerCase();
            if (!id) return showToast('Give the device a name', 'error');
            addBtn.disabled = true;
            try {
                const res = await call('POST', '', {
                    id, label: '', location: body.querySelector('#dev-new-location').value.trim(),
                    driver: d.driver, config: schema.length ? readSettingsForm(fields, schema) : {},
                });
                showToast(res.warning || `Added ${id}`, res.warning ? 'warning' : 'success');
                modal.close();
                await loadList();
                openDevice(id, undefined, { test: true });     // new: ask it once, the window fills in
            } catch (e) {
                showToast(e.message, 'error');
                addBtn.disabled = false;
            }
        });
    };
    pick();
}

// ---- the device modal ------------------------------------------------------

// Driver field keys are prefixed with the driver id so two parts never clash.
const fieldKey = (driver, key) => `${driver}.${key}`;
const lockKey = cap => `lock.${cap}`;

// A pick list asks its driver what is here. A saved device lends its settings.
const isFound = f => f.type === 'found' || f.widget === 'found';
const withLook = (f, driver, device) => !isFound(f) ? f : { ...f,
    found_url: `${API}/found/${encodeURIComponent(driver)}${device ? '?device=' + encodeURIComponent(device) : ''}` };

function modalSchema(device) {
    const tabOf = Object.fromEntries((device.capabilities || []).map(c => [c.capability, c.label || c.capability]));
    const schema = [
        { key: 'device.label', type: 'string', label: 'Display name', tab: 'Status' },
        { key: 'device.location', type: 'string', label: 'Location', tab: 'Status',
          placeholder: 'Living Room', help: LOCATION_HELP },
        { key: 'device.enabled', type: 'boolean', label: 'Enabled', tab: 'Status',
          help: 'Off = Sapphire cannot see or use this device.' },
    ];
    const values = { 'device.label': device.label, 'device.location': device.location || '',
                     'device.enabled': device.enabled };
    for (const cap of device.capabilities || []) {
        if (!cap.lockable) continue;
        schema.push({ key: lockKey(cap.capability), type: 'boolean', label: 'Sapphire may use this',
                      tab: cap.label || cap.capability,
                      help: 'Off = only you can, with the buttons on this tab.' });
        values[lockKey(cap.capability)] = !cap.locked;
    }
    const bindings = [];     // drawn by mountBindings in the tab's slot, not by the form renderer
    for (const part of device.parts) {
        for (const f of part.schema) {
            const cap = f.capability || part.capabilities[0];
            if (f.type === 'bindings') {
                bindings.push({ part, field: f, tab: f.tab || tabOf[cap] || cap });
                continue;
            }
            const show_if = f.show_if
                ? Object.fromEntries(Object.entries(f.show_if).map(([k, v]) => [fieldKey(part.driver, k), v]))
                : undefined;
            // a field may ask for the Status tab: how to reach a device belongs with its health
            schema.push({ ...withLook(f, part.driver, device.id), key: fieldKey(part.driver, f.key),
                          tab: f.tab || tabOf[cap] || cap, show_if });
            values[fieldKey(part.driver, f.key)] = part.values[f.key];
        }
    }
    return { schema, values, bindings };
}

// ---- a bindings field: each slot of the device picks a job from its own menu --------
// (a button's short press -> send a message). The menu comes with the field
// (`menu`, from the driver); a device that has not said what it offers shows
// the note and its stored picks are left as they are.

function mountBindings(el, b) {
    const menu = b.field.menu || {};
    const held = b.part.values[b.field.key] || {};
    const choices = menu.choices || [];
    const note = menu.note ? `<div class="setting-help" style="margin:10px 0 8px">${esc(menu.note)}</div>` : '';
    if (!(menu.slots || []).length) { el.innerHTML = note; return; }
    el.innerHTML = note + menu.slots.map(slot => {
        const pick = held[slot.key] || {};
        const known = choices.some(c => c.value === (pick.do || ''));
        const options = choices.map(c => `<option value="${esc(c.value)}" ${c.value === (pick.do || '') ? 'selected' : ''}>${
            esc(c.value === '' && slot.own ? `${c.label}: ${slot.own}` : c.label)}</option>`).join('')
            + (known ? '' : `<option value="${esc(pick.do)}" selected>${esc(pick.do)} (this board no longer offers it)</option>`);
        return `
        <div data-bind="${esc(slot.key)}" style="display:flex;gap:8px;align-items:center;margin-bottom:6px;flex-wrap:wrap">
            <label style="min-width:150px">${esc(slot.label)}</label>
            <select class="dev-bind-do" style="flex:1;min-width:180px">${options}</select>
            <input type="text" class="dev-bind-text" maxlength="500" value="${esc(pick.text || '')}" style="flex:2;min-width:200px">
        </div>`;
    }).join('');
    const sync = row => {
        const c = choices.find(x => x.value === row.querySelector('.dev-bind-do').value);
        const text = row.querySelector('.dev-bind-text');
        text.hidden = !c?.text;
        text.placeholder = c?.placeholder || '';
    };
    el.querySelectorAll('[data-bind]').forEach(sync);
    el.addEventListener('change', e => { if (e.target.matches('.dev-bind-do')) sync(e.target.closest('[data-bind]')); });
    b.read = () => Object.fromEntries([...el.querySelectorAll('[data-bind]')].map(row => [row.dataset.bind,
        { do: row.querySelector('.dev-bind-do').value, text: row.querySelector('.dev-bind-text').value }])
        .filter(([, pick]) => pick.do));
}

function statusHTML(device) {
    const st = device.status;
    const head = text => `<div style="display:flex;align-items:center;gap:8px">${dot(device, 9)}${text}</div>`;
    if (!device.enabled) return head('<span class="setting-help">This device is turned off.</span>');
    if (!st) return head('<span class="setting-help">Nothing is known about it yet.</span>');
    const parts = (st.parts || []).map(p => `
        <div style="margin:4px 0 0 17px" class="setting-help">${esc(p.driver)}: ${p.online ? 'ok' : 'down'}${
            p.detail ? ' - ' + esc(p.detail) : ''}${
            Object.entries(p.readings || {}).map(([k, v]) => `<br>${esc(k)}: ${esc(v)}`).join('')}</div>`).join('');
    const note = st.online && st.misses ? `<div style="margin:4px 0 0 17px" class="setting-help">It did not answer the last ${
        st.misses === 1 ? 'check' : st.misses + ' checks'}. It counts as offline if it misses again.</div>` : '';
    return head(`<b>${st.online ? 'Online' : 'Offline'}</b><span class="text-muted">${when(st)}</span>`) + parts + note;
}

function mountStatus(el, device, redraw) {
    const warn = device.parts.flatMap(p => [
        ...(p.available ? [] : [`The ${esc(p.driver)} driver is off. Enable the ${esc(p.plugin || p.driver)} plugin in Settings > Plugins.`]),
        ...(p.unreadable || []).map(f => `The stored ${esc(f)} cannot be read on this machine. Enter it again.`),
    ]);
    el.innerHTML = `
        <div style="border-top:1px solid var(--border);margin-top:10px;padding-top:12px">
            <div id="dev-status">${statusHTML(device)}</div>
            ${warn.map(w => `<div style="color:var(--error,#e53935);margin-top:8px">${w}</div>`).join('')}
            <div style="display:flex;gap:8px;margin-top:12px">
                <button type="button" class="btn-action" id="dev-test">Test now</button>
                <button type="button" class="btn-action" id="dev-delete" style="margin-left:auto;color:var(--error,#e53935)">Delete device</button>
            </div>
        </div>`;
    el.querySelector('#dev-test').addEventListener('click', async ev => {
        const btn = ev.currentTarget;
        btn.disabled = true; btn.textContent = 'Testing...';
        try {
            const res = await call('POST', `/${encodeURIComponent(device.id)}/test`);
            device.status = res.status;
            // asked how it is, a device may say it has more or less than this window shows
            const tabs = d => (d.capabilities || []).map(c => c.capability).join();
            if (res.device && tabs(res.device) !== tabs(device)) return redraw(res.device);
            el.querySelector('#dev-status').innerHTML = statusHTML(device);
            loadList();
        } catch (e) { showToast(e.message, 'error'); }
        btn.disabled = false; btn.textContent = 'Test now';
    });
    el.querySelector('#dev-delete').addEventListener('click', async () => { if (await removeDevice(device.id)) redraw(null); });
}

function mountActions(el, device, cap) {
    if (cap.error) {
        el.innerHTML = `<div style="color:var(--error,#e53935);margin-top:10px">${esc(cap.error)}</div>`;
        return;
    }
    const names = Object.keys(cap.actions || {});
    el.innerHTML = `
        <div style="border-top:1px solid var(--border);margin-top:10px;padding-top:12px">
            <div class="setting-help" style="margin-bottom:8px">${names.length
                ? 'What Sapphire can do here. Try sends what you type; the grey text is only an example. Try runs the saved version, so save first.'
                : 'Nothing to run yet.'}</div>
            ${names.map(n => `
                <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px" data-action="${esc(n)}">
                    <code style="min-width:120px" title="${cap.actions[n].owner ? 'Only you, from this page. Sapphire cannot run it.' : ''}">${esc(n)}${cap.actions[n].owner ? ' <span style="color:var(--text-muted);font-size:var(--font-xs)">you only</span>' : ''}</code>
                    <input type="text" class="dev-try-value"
                        placeholder="${esc(cap.actions[n].example ? 'for example ' + cap.actions[n].example : 'value (optional)')}"
                        style="flex:1;min-width:0">
                    <button type="button" class="btn-action dev-try">Try</button>
                </div>`).join('')}
            <pre class="dev-try-out" hidden style="white-space:pre-wrap;max-height:220px;overflow:auto;background:var(--bg-secondary,#1a1b2e);border:1px solid var(--border);border-radius:6px;padding:8px;margin-top:8px;font-size:var(--font-sm,13px)"></pre>
            <div class="dev-try-pics" style="margin-top:8px"></div>
        </div>`;
    const out = el.querySelector('.dev-try-out');
    const pics = el.querySelector('.dev-try-pics');
    const showPics = images => pics.replaceChildren(...(images || []).map(img => {
        const pic = document.createElement('img');
        pic.src = `data:${img.media_type};base64,${img.data}`;
        pic.alt = 'What the device saw';
        pic.style.cssText = 'max-width:100%;border-radius:6px;border:1px solid var(--border)';
        return pic;
    }));
    el.addEventListener('click', async e => {
        const btn = e.target.closest('.dev-try');
        if (!btn) return;
        const row = btn.closest('[data-action]');
        if (cap.capability === 'power'
            && !confirm(`${row.dataset.action} "${device.id}" now?`)) return;
        const danger = cap.actions[row.dataset.action]?.danger;
        if (danger && !(await showDangerConfirm({
            title: `${row.dataset.action} on ${device.id}`,
            warnings: [danger, 'This cannot be undone.'],
            buttonLabel: row.dataset.action,
        }))) return;
        btn.disabled = true;
        out.hidden = false; out.textContent = 'Running...';
        showPics([]);
        try {
            const wait = cap.actions[row.dataset.action]?.wait || 0;        // seconds the driver says it may take
            const res = await call('POST', `/${encodeURIComponent(device.id)}/run`, {
                capability: cap.capability, action: row.dataset.action,
                value: row.querySelector('.dev-try-value').value,
            }, Math.max(30, wait + 15) * 1000);
            out.textContent = (res.ok ? '' : 'FAILED\n') + res.text;
            showPics(res.images);
        } catch (err) { out.textContent = 'FAILED\n' + err.message; }
        btn.disabled = false;
    });
}

async function openDevice(id, tab, opts = {}) {
    let device;
    try {
        device = (await call('GET', `/${encodeURIComponent(id)}`)).device;
    } catch (e) { return showToast(e.message, 'error'); }

    const modal = showModal(`${ICON} ${device.id}`,
        [{ type: 'html', value: '<div id="dev-modal"></div>' }], null, { wide: true });
    const body = modal.element.querySelector('#dev-modal');
    let schema = [];
    let bindings = [];
    let root = null;      // a fresh element per draw, so old listeners die with it

    // Each list poll brings this device's state too: the window shows it without asking.
    onPoll = list => {
        if (!modal.element.isConnected) { onPoll = null; return; }
        const d = list.find(x => x.id === device.id);
        const el = root?.querySelector('#dev-status');
        if (!d || !d.status || !el) return;
        device.status = d.status;
        el.innerHTML = statusHTML(device);
    };

    const activeTab = () => root?.querySelector('.ps-tab.active')?.dataset.psTab;
    const draw = (dev, want) => {
        device = dev;
        const built = modalSchema(device);
        schema = built.schema;
        bindings = built.bindings;
        const slots = [{ tab: 'Status', mount: el => mountStatus(el, device, after) }];
        for (const b of bindings) slots.push({ tab: b.tab, mount: el => mountBindings(el, b) });
        for (const cap of device.capabilities || [])
            slots.push({ tab: cap.label || cap.capability, mount: el => mountActions(el, device, cap) });
        root = document.createElement('div');
        body.replaceChildren(root);
        renderSettingsForm(root, schema, built.values, { slots });
        if (want) [...root.querySelectorAll('.ps-tab')].find(b => b.dataset.psTab === want)?.click();
    };
    const after = dev => {            // null = the device is gone, a device = draw it again
        loadList();
        if (!dev) modal.close();
        else draw(dev, activeTab());
    };
    draw(device, tab);
    if (opts.test) root.querySelector('#dev-test')?.click();

    const save = document.createElement('button');
    save.className = 'btn btn-primary';
    save.textContent = 'Save';
    modal.element.querySelector('.modal-footer').prepend(save);
    save.addEventListener('click', async () => {
        const form = readSettingsForm(root, schema);
        const parts = {};
        for (const part of device.parts) {
            if (!part.available) continue;
            parts[part.driver] = {};
            for (const f of part.schema) {
                const k = fieldKey(part.driver, f.key);
                if (k in form) parts[part.driver][f.key] = form[k];
            }
        }
        for (const b of bindings) if (b.read && b.part.available) parts[b.part.driver][b.field.key] = b.read();
        // only the switches this window showed: one it did not show is never touched
        const locked = Object.fromEntries((device.capabilities || [])
            .filter(c => c.lockable && lockKey(c.capability) in form)
            .map(c => [c.capability, form[lockKey(c.capability)] === false]));
        save.disabled = true;
        try {
            const res = await call('PUT', `/${encodeURIComponent(device.id)}`, {
                label: form['device.label'], location: form['device.location'] || '',
                enabled: form['device.enabled'], parts, locked,
            });
            showToast(res.warning || 'Saved', res.warning ? 'warning' : 'success');
            const fresh = (await call('GET', `/${encodeURIComponent(device.id)}`)).device;
            draw(fresh, activeTab());
            loadList();
        } catch (e) { showToast(e.message, 'error'); }
        save.disabled = false;
    });
}

// ---- the tab ---------------------------------------------------------------

export default {
    id: 'devices',
    name: 'Devices',
    icon: ICON,
    description: 'Machines and gadgets Sapphire can use',
    selfSaving: true,          // own buttons: the global Save hides on this tab

    render() {
        return '<div id="devices-tab"></div>';
    },

    attachListeners(ctx, el) {
        const box = el.querySelector('#devices-tab');
        if (box) drawPage(box);
    }
};
