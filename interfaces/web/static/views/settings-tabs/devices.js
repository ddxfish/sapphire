// settings-tabs/devices.js - Settings > Devices (tmp/device-manager-plan.md)
//
// The list, the add form, and the device window. Every form is drawn by the
// shared settings renderer from the driver's own schema; this file holds no
// driver knowledge. The window's first tab is Status, then one tab per
// capability. The page saves with its own buttons (selfSaving).

import { renderSettingsForm, readSettingsForm } from '../../shared/plugin-settings-renderer.js';
import { showModal, escapeHtml as esc } from '../../shared/modal.js';
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';

const API = '/api/devices';
const ICON = String.fromCodePoint(0x1F39B, 0xFE0F);
const POLL_MS = 10000;
const LOCATION_HELP = 'The room or place it is in. Sapphire sees it, so she knows where a voice came from.';

let page = null;        // the tab's container
let drivers = [];
let timer = null;

const call = (method, path = '', body) => fetchWithTimeout(API + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
}, 30000);

const dot = on => `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:8px;background:${
    on === true ? 'var(--success,#4caf50)' : on === false ? 'var(--error,#e53935)' : 'var(--text-muted,#888)'}"></span>`;

const stateWord = d => !d.enabled ? 'off' : !d.status ? 'checking' : d.status.online ? 'online' : 'offline';

// ---- the page --------------------------------------------------------------

function drawPage(container) {
    page = container;
    container.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
            <div class="setting-help">Machines and gadgets Sapphire can use by name. Only this page can add or change them.</div>
            <button class="btn btn-sm btn-primary" id="dev-add-btn">+ Add Device</button>
        </div>
        <div id="dev-add" hidden></div>
        <div id="dev-list"><p class="text-muted">Loading...</p></div>`;
    container.querySelector('#dev-add-btn').addEventListener('click', () => drawAddForm());
    container.querySelector('#dev-list').addEventListener('click', e => {
        const row = e.target.closest('[data-device]');
        if (row) openDevice(row.dataset.device);
    });
    loadList();
    clearInterval(timer);
    timer = setInterval(() => {
        if (!page || !page.isConnected) { clearInterval(timer); timer = null; return; }
        if (!document.hidden) loadList();
    }, POLL_MS);
}

async function loadList() {
    const list = page?.querySelector('#dev-list');
    if (!list) return;
    try {
        const data = await call('GET');
        drivers = data.drivers || [];
        const devices = data.devices || [];
        if (!devices.length) {
            list.innerHTML = `<p class="text-muted" style="font-size:0.9em">No devices yet. Add one to get started.</p>`;
            return;
        }
        list.innerHTML = devices.map(d => {
            const caps = (d.capabilities || []).join(', ') || 'nothing yet';
            const missing = (d.missing || []).length
                ? ` <span style="color:var(--error,#e53935)">driver off: ${esc(d.missing.join(', '))}</span>` : '';
            return `
            <div class="setting-row" data-device="${esc(d.id)}" style="padding:10px 0;border-bottom:1px solid var(--border);cursor:pointer">
                <div class="setting-label">
                    <label style="cursor:pointer">${dot(d.enabled ? d.status?.online : null)}${esc(d.id)}</label>
                    <div class="setting-help">${esc(d.label)}${d.location ? ' - ' + esc(d.location) : ''} - ${esc(caps)}${missing}</div>
                </div>
                <div class="setting-input" style="text-align:right"><span class="text-muted">${stateWord(d)}</span></div>
            </div>`;
        }).join('');
    } catch (e) {
        list.innerHTML = `<p style="color:var(--error)">Could not load devices: ${esc(e.message)}</p>`;
    }
}

// ---- add a device ----------------------------------------------------------

function drawAddForm() {
    const box = page.querySelector('#dev-add');
    const usable = drivers.filter(d => d.available);
    box.hidden = false;
    box.innerHTML = `
        <div style="border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:14px">
            <h4 style="margin:0 0 10px">Add a device</h4>
            <div class="settings-grid">
                <div class="setting-row"><div class="setting-label"><label>Name</label>
                    <div class="setting-help">What Sapphire calls it. Lowercase, no spaces. Example: desktop</div></div>
                    <div class="setting-input"><input type="text" id="dev-new-id" placeholder="desktop"></div></div>
                <div class="setting-row"><div class="setting-label"><label>Display name</label></div>
                    <div class="setting-input"><input type="text" id="dev-new-label" placeholder="My desktop"></div></div>
                <div class="setting-row"><div class="setting-label"><label>Location</label>
                    <div class="setting-help">${LOCATION_HELP}</div></div>
                    <div class="setting-input"><input type="text" id="dev-new-location" placeholder="Living Room" maxlength="60"></div></div>
                <div class="setting-row"><div class="setting-label"><label>Type</label></div>
                    <div class="setting-input"><select id="dev-new-driver">
                        ${drivers.map(d => `<option value="${esc(d.driver)}" ${d.available ? '' : 'disabled'}>${
                            esc(d.label)}${d.available ? '' : ' - ' + esc(d.note)}</option>`).join('')}
                    </select></div></div>
            </div>
            <div id="dev-new-fields" style="margin-top:8px"></div>
            <div style="display:flex;gap:8px;margin-top:12px">
                <button class="btn btn-sm btn-primary" id="dev-new-save">Add</button>
                <button class="btn btn-sm" id="dev-new-cancel">Cancel</button>
            </div>
        </div>`;
    const pick = box.querySelector('#dev-new-driver');
    const fields = box.querySelector('#dev-new-fields');
    const current = () => drivers.find(d => d.driver === pick.value && d.available);
    const drawFields = () => {
        const d = current();
        if (!d) {
            fields.innerHTML = `<p class="text-muted" style="font-size:0.9em">${
                drivers.length ? 'Pick a type that is available.'
                               : 'No device types yet. Enable a plugin that provides one, such as SSH.'}</p>`;
            return;
        }
        if (!d.config_schema.length) {
            fields.innerHTML = '<p class="text-muted" style="font-size:0.9em">Nothing else to set up for this type.</p>';
            return;
        }
        renderSettingsForm(fields, d.config_schema.map(f => ({ ...withLook(f, d.driver), tab: undefined })), {});
    };
    if (usable.length) pick.value = usable[0].driver;
    pick.addEventListener('change', drawFields);
    drawFields();

    box.querySelector('#dev-new-cancel').addEventListener('click', () => { box.hidden = true; box.innerHTML = ''; });
    box.querySelector('#dev-new-save').addEventListener('click', async ev => {
        const d = current();
        if (!d) return showToast('Pick a device type first', 'error');
        const id = box.querySelector('#dev-new-id').value.trim().toLowerCase();
        if (!id) return showToast('Give the device a name', 'error');
        ev.currentTarget.disabled = true;
        try {
            const res = await call('POST', '', {
                id, label: box.querySelector('#dev-new-label').value.trim(),
                location: box.querySelector('#dev-new-location').value.trim(),
                driver: d.driver,
                config: d.config_schema.length ? readSettingsForm(fields, d.config_schema) : {},
            });
            showToast(res.warning || `Added ${id}`, res.warning ? 'warning' : 'success');
            box.hidden = true; box.innerHTML = '';
            await loadList();
            openDevice(id);
        } catch (e) {
            showToast(e.message, 'error');
            ev.currentTarget.disabled = false;
        }
    });
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
    for (const part of device.parts) {
        for (const f of part.schema) {
            const cap = f.capability || part.capabilities[0];
            const show_if = f.show_if
                ? Object.fromEntries(Object.entries(f.show_if).map(([k, v]) => [fieldKey(part.driver, k), v]))
                : undefined;
            // a field may ask for the Status tab: how to reach a device belongs with its health
            schema.push({ ...withLook(f, part.driver, device.id), key: fieldKey(part.driver, f.key),
                          tab: f.tab || tabOf[cap] || cap, show_if });
            values[fieldKey(part.driver, f.key)] = part.values[f.key];
        }
    }
    return { schema, values };
}

function statusHTML(device) {
    const st = device.status;
    if (!device.enabled) return `<div class="setting-help">${dot(null)}This device is turned off.</div>`;
    if (!st) return `<div class="setting-help">${dot(null)}Not checked yet.</div>`;
    const age = Math.max(0, Math.round(Date.now() / 1000 - st.ts));
    const parts = st.parts.map(p => `
        <div style="margin:4px 0 0 17px" class="setting-help">${esc(p.driver)}: ${p.online ? 'ok' : 'down'}${
            p.detail ? ' - ' + esc(p.detail) : ''}${
            Object.entries(p.readings || {}).map(([k, v]) => `<br>${esc(k)}: ${esc(v)}`).join('')}</div>`).join('');
    return `<div>${dot(st.online)}<b>${st.online ? 'Online' : 'Offline'}</b>
        <span class="text-muted" style="margin-left:6px">checked ${age}s ago</span></div>${parts}`;
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
            device.status = (await call('POST', `/${encodeURIComponent(device.id)}/test`)).status;
            el.querySelector('#dev-status').innerHTML = statusHTML(device);
            loadList();
        } catch (e) { showToast(e.message, 'error'); }
        btn.disabled = false; btn.textContent = 'Test now';
    });
    el.querySelector('#dev-delete').addEventListener('click', async () => {
        if (!confirm(`Delete device "${device.id}"? Its stored secrets are deleted too. This cannot be undone.`)) return;
        try {
            await call('DELETE', `/${encodeURIComponent(device.id)}`);
            showToast(`Deleted ${device.id}`, 'success');
            redraw(null);
        } catch (e) { showToast(e.message, 'error'); }
    });
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
                ? 'What Sapphire can do here. Try runs the saved version, so save first.'
                : 'Nothing to run yet.'}</div>
            ${names.map(n => `
                <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px" data-action="${esc(n)}">
                    <code style="min-width:120px">${esc(n)}</code>
                    <input type="text" class="dev-try-value" value="${esc(cap.actions[n].example || '')}"
                        placeholder="value (optional)" style="flex:1;min-width:0">
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
        btn.disabled = true;
        out.hidden = false; out.textContent = 'Running...';
        showPics([]);
        try {
            const res = await call('POST', `/${encodeURIComponent(device.id)}/run`, {
                capability: cap.capability, action: row.dataset.action,
                value: row.querySelector('.dev-try-value').value,
            });
            out.textContent = (res.ok ? '' : 'FAILED\n') + res.text;
            showPics(res.images);
        } catch (err) { out.textContent = 'FAILED\n' + err.message; }
        btn.disabled = false;
    });
}

async function openDevice(id, tab) {
    let device;
    try {
        device = (await call('GET', `/${encodeURIComponent(id)}`)).device;
    } catch (e) { return showToast(e.message, 'error'); }

    const modal = showModal(`${ICON} ${device.id}`,
        [{ type: 'html', value: '<div id="dev-modal"></div>' }], null, { wide: true });
    const body = modal.element.querySelector('#dev-modal');
    let schema = [];
    let root = null;      // a fresh element per draw, so old listeners die with it

    const activeTab = () => root?.querySelector('.ps-tab.active')?.dataset.psTab;
    const draw = (dev, want) => {
        device = dev;
        const built = modalSchema(device);
        schema = built.schema;
        const slots = [{ tab: 'Status', mount: el => mountStatus(el, device, after) }];
        for (const cap of device.capabilities || [])
            slots.push({ tab: cap.label || cap.capability, mount: el => mountActions(el, device, cap) });
        root = document.createElement('div');
        body.replaceChildren(root);
        renderSettingsForm(root, schema, built.values, { slots });
        if (want) [...root.querySelectorAll('.ps-tab')].find(b => b.dataset.psTab === want)?.click();
    };
    const after = dev => {            // null = the device is gone
        loadList();
        if (!dev) modal.close();
    };
    draw(device, tab);

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
