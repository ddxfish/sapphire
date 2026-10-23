// settings-tabs/ir.js - the IR code library, a pane of the Device Manager
// (Devices > IR). One bordered card per appliance (a TV), each with its named
// commands (power, vol up) and a code a satellite's IR blaster sends. A board
// blasts by name ("turn off the TV"); Test fires one through a chosen device.
// Editing is tucked away: "+ Add appliance" opens a modal, each card hides its
// add-command form behind a button, and a kebab (⋮) holds Import / Export /
// Remove. Codes are NEC hex today; learning from a remote needs an IR receiver.
// Doors: /api/ir/* (core/routes/ir.py, core/ir/library.py). mount(el) draws it.
import { fetchWithTimeout } from '../../shared/fetch.js';
import { showToast } from '../../shared/toast.js';
import { showModal, escapeHtml as esc } from '../../shared/modal.js';

const API = '/api/ir';
const post = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const cssId = s => String(s).replace(/[^a-z0-9]+/gi, '-');
const hex = n => (Number(n) & 0xff).toString(16).padStart(2, '0');

let brands = [];       // the built-in brands to import from, from the last fetch

const SHELL = `
    <div class="net-section" id="ir-library">
        <h4>IR library</h4>
        <div class="setting-help" style="margin-bottom:10px">Appliances in a room and the remote codes to blast them. A satellite with an
            IR blaster sends a code by name — say or ask "turn off the TV". Add codes as NEC hex (an LG TV's power is <b>04 08</b>),
            or import a brand's known codes. Learning codes from a remote needs an IR receiver.</div>
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
            <button class="btn btn-sm btn-primary" id="ir-add-appliance">+ Add appliance</button>
            <label style="margin-left:auto;display:flex;gap:6px;align-items:center;font-size:var(--font-sm)">Test through
                <select id="ir-device"></select></label>
        </div>
        <div id="ir-lib-body" style="font-size:var(--font-sm)">Loading…</div>
    </div>`;

export function mount(el) {
    el.innerHTML = SHELL;
    el.querySelector('#ir-add-appliance')?.addEventListener('click', () => openAddAppliance(el));
    fillDevices(el);            // once: a mutation reload keeps the chosen Test device
    reload(el);
}

async function reload(el) {
    const body = el.querySelector('#ir-lib-body');
    if (!body) return;
    let data = { appliances: [], brands: [] };
    try { data = await fetchWithTimeout(`${API}/library`); }
    catch (e) { body.innerHTML = `<span style="color:var(--error)">The IR library could not be read: ${esc(e.message)}</span>`; return; }
    brands = data.brands || [];
    paint(body, data.appliances || [], el);
}

async function fillDevices(el) {
    const sel = el.querySelector('#ir-device');
    if (!sel) return;
    try {
        const d = await fetchWithTimeout('/api/devices');
        const list = Array.isArray(d) ? d : (d.devices || []);
        const has = x => {
            const caps = x.capabilities || x.has || x.caps || [];
            return Array.isArray(caps) ? caps.includes('ir') : String(caps).includes('ir');
        };
        const ir = list.filter(has);
        const use = ir.length ? ir : list;
        sel.innerHTML = use.map(x => `<option value="${esc(x.id || x.name)}">${esc(x.name || x.id)}</option>`).join('')
            || '<option value="">(no IR device)</option>';
    } catch { sel.innerHTML = '<option value="">(devices unavailable)</option>'; }
}

function paint(body, apps, el) {
    if (!apps.length) {
        body.innerHTML = '<span class="setting-help">No appliances yet — click “+ Add appliance”.</span>';
        return;
    }
    body.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;align-items:start">${apps.map(appBlock).join('')}</div>`;
    apps.forEach(a => {
        const id = cssId(a.name);
        el.querySelector(`#ir-kebab-${id}`)?.addEventListener('click', ev => appMenu(ev.currentTarget, el, a));
        const form = el.querySelector(`#ir-cmd-form-${id}`);
        const show = el.querySelector(`#ir-cmd-show-${id}`);
        show?.addEventListener('click', () => { form.style.display = 'flex'; show.style.display = 'none'; form.querySelector('input')?.focus(); });
        el.querySelector(`#ir-cmd-cancel-${id}`)?.addEventListener('click', () => { form.style.display = 'none'; show.style.display = ''; });
        el.querySelector(`#ir-cmd-add-${id}`)?.addEventListener('click', () => addCommand(el, a.name));
        (a.commands || []).forEach(c => {
            const cid = `${id}-${cssId(c.name)}`;
            el.querySelector(`#ir-test-${cid}`)?.addEventListener('click', () => testBlast(el, a.name, c.name));
            el.querySelector(`#ir-cmd-del-${cid}`)?.addEventListener('click', () => removeCommand(el, a.name, c.name));
        });
    });
}

function appBlock(a) {
    const id = cssId(a.name);
    const rows = (a.commands || []).map(c => {
        const cid = `${id}-${cssId(c.name)}`;
        const code = c.protocol === 'nec' ? `NEC ${(Number(c.addr) & 0xffff).toString(16)} ${hex(c.cmd)}` : esc(c.protocol || '?');
        return `<tr>
            <td style="padding:2px 12px 2px 0"><b>${esc(c.name)}</b></td>
            <td style="padding:2px 12px 2px 0;color:var(--text-muted)">${code}</td>
            <td style="padding:2px 0;text-align:right"><button class="btn-sm" id="ir-test-${cid}">Test</button>
                <button class="btn-sm danger" id="ir-cmd-del-${cid}" title="remove">×</button></td>
        </tr>`;
    }).join('');
    return `<div class="sched-task-card" style="display:block;cursor:default;position:relative;padding:12px">
        <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-weight:600">${esc(a.name)}${a.brand ? ` <span style="font-weight:400;color:var(--text-muted)">${esc(a.brand)}</span>` : ''}</div>
            <button type="button" id="ir-kebab-${id}" title="More" aria-label="More for ${esc(a.name)}"
                style="background:none;border:0;color:var(--text-muted,#888);font-size:1.3em;line-height:1;cursor:pointer;padding:2px 8px;border-radius:6px">&#8942;</button>
        </div>
        <table style="border-collapse:collapse;margin-top:8px;width:100%"><tbody>${rows || '<tr><td style="color:var(--text-muted)">no codes yet</td></tr>'}</tbody></table>
        <div style="margin-top:8px">
            <button class="btn-sm" id="ir-cmd-show-${id}">+ Add command</button>
            <div id="ir-cmd-form-${id}" style="display:none;gap:6px;align-items:center;flex-wrap:wrap">
                <input type="text" id="ir-cmd-name-${id}" placeholder="power" style="width:90px">
                <input type="text" id="ir-cmd-addr-${id}" placeholder="addr" style="width:54px">
                <input type="text" id="ir-cmd-cmd-${id}" placeholder="cmd" style="width:54px">
                <button class="btn-sm" id="ir-cmd-add-${id}">Add</button>
                <button class="btn-sm" id="ir-cmd-cancel-${id}">Cancel</button>
            </div>
        </div>
    </div>`;
}

// --- the card's kebab menu (mirrors the Devices card menu) -----------------
function appMenu(anchor, el, a) {
    openMenu(anchor, [
        { text: 'Import from brand…', run: () => openImport(el, a.name) },
        { text: 'Export', run: () => exportAppliance(a) },
        { text: 'Remove appliance', danger: true, run: () => removeAppliance(el, a.name) },
    ]);
}

function openMenu(anchor, items) {
    document.querySelector('#ir-menu')?.remove();
    const at = anchor.getBoundingClientRect();
    const menu = document.createElement('div');
    menu.id = 'ir-menu';
    menu.style.cssText = `position:fixed;z-index:1000;top:${at.bottom + 4}px;right:${Math.max(8, innerWidth - at.right)}px;min-width:170px;padding:4px;` +
        'background:var(--bg-secondary,#222);border:1px solid var(--border,#444);border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.35)';
    menu.innerHTML = items.map((it, i) => `<button type="button" data-i="${i}" style="display:block;width:100%;text-align:left;background:none;border:0;` +
        `padding:8px 10px;border-radius:6px;font:inherit;cursor:pointer;color:${it.danger ? 'var(--error,#e53935)' : 'inherit'}">${esc(it.text)}</button>`).join('');
    document.body.appendChild(menu);
    menu.addEventListener('mouseover', e => { for (const b of menu.children) b.style.background = b === e.target ? 'var(--bg-tertiary,#333)' : 'none'; });
    const shut = e => {
        if (e.type === 'keydown' && e.key !== 'Escape') return;
        const line = e.type === 'click' ? e.target.closest?.('#ir-menu [data-i]') : null;
        menu.remove();
        for (const ev of ['click', 'keydown', 'scroll', 'resize']) window.removeEventListener(ev, shut, true);
        if (line) items[+line.dataset.i].run();
    };
    setTimeout(() => { for (const ev of ['click', 'keydown', 'scroll', 'resize']) window.addEventListener(ev, shut, true); });
}

function exportAppliance(a) {
    const blob = new Blob([JSON.stringify(a, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `ir-${cssId(a.name)}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// --- modals ----------------------------------------------------------------
function openAddAppliance(el) {
    const modal = showModal('📺 Add appliance', [{ type: 'html', value: '<div id="ir-add-body"></div>' }], null, {});
    const body = modal.element.querySelector('#ir-add-body');
    body.innerHTML = `<div class="settings-grid">
        <div class="setting-row"><div class="setting-label"><label>Name</label>
            <div class="setting-help">What Sapphire calls it, e.g. "tv" or "speaker".</div></div>
            <div class="setting-input"><input type="text" id="ir-new-name" placeholder="tv"></div></div>
        <div class="setting-row"><div class="setting-label"><label>Brand</label>
            <div class="setting-help">Optional, just a label.</div></div>
            <div class="setting-input"><input type="text" id="ir-new-brand" placeholder="LG"></div></div>
        ${brands.length ? `<div class="setting-row"><div class="setting-label"><label>Start from a brand</label>
            <div class="setting-help">Optional — import a known code set to begin with.</div></div>
            <div class="setting-input"><select id="ir-new-import"><option value="">— none —</option>${brands.map(b => `<option value="${esc(b)}">${esc(b)}</option>`).join('')}</select></div></div>` : ''}
    </div>`;
    const add = document.createElement('button');
    add.className = 'btn btn-primary';
    add.textContent = 'Add';
    add.addEventListener('click', async () => {
        const name = body.querySelector('#ir-new-name').value.trim();
        const brand = body.querySelector('#ir-new-brand').value.trim();
        const imp = body.querySelector('#ir-new-import')?.value || '';
        if (!name && !imp) { showToast('Name the appliance', 'error'); return; }
        try {
            if (imp) await fetchWithTimeout(`${API}/import`, post({ brand: imp, appliance: name }));
            else await fetchWithTimeout(`${API}/appliance`, post({ name, brand }));
            modal.close();
            showToast('Appliance added', 'success');
            await reload(el);
        } catch (e) { showToast(e.message, 'error'); }
    });
    modal.element.querySelector('.modal-footer')?.appendChild(add);
}

function openImport(el, appliance) {
    if (!brands.length) { showToast('No built-in brands to import from yet', 'error'); return; }
    const modal = showModal(`📺 Import codes into ${appliance}`, [{ type: 'html', value: '<div id="ir-imp-body"></div>' }], null, {});
    const body = modal.element.querySelector('#ir-imp-body');
    body.innerHTML = `<div class="settings-grid"><div class="setting-row">
        <div class="setting-label"><label>Brand</label><div class="setting-help">Adds this brand's known codes to "${esc(appliance)}".</div></div>
        <div class="setting-input"><select id="ir-imp-brand">${brands.map(b => `<option value="${esc(b)}">${esc(b)}</option>`).join('')}</select></div></div></div>`;
    const btn = document.createElement('button');
    btn.className = 'btn btn-primary';
    btn.textContent = 'Import';
    btn.addEventListener('click', async () => {
        try {
            await fetchWithTimeout(`${API}/import`, post({ brand: body.querySelector('#ir-imp-brand').value, appliance }));
            modal.close();
            showToast('Codes imported', 'success');
            await reload(el);
        } catch (e) { showToast(e.message, 'error'); }
    });
    modal.element.querySelector('.modal-footer')?.appendChild(btn);
}

// --- mutations -------------------------------------------------------------
async function addCommand(el, appliance) {
    const id = cssId(appliance);
    const command = el.querySelector(`#ir-cmd-name-${id}`).value.trim();
    const addr = el.querySelector(`#ir-cmd-addr-${id}`).value.trim();
    const cmd = el.querySelector(`#ir-cmd-cmd-${id}`).value.trim();
    if (!command || !addr || !cmd) { showToast('Give the code a name, addr and cmd (hex)', 'error'); return; }
    try { await fetchWithTimeout(`${API}/command`, post({ appliance, command, protocol: 'nec', addr, cmd })); showToast(`Added ${appliance} ${command}`, 'success'); await reload(el); }
    catch (e) { showToast(e.message, 'error'); }
}

async function removeAppliance(el, name) {
    if (!confirm(`Remove the appliance "${name}" and all its codes?`)) return;
    try { await fetchWithTimeout(`${API}/library/${encodeURIComponent(name)}`, { method: 'DELETE' }); showToast(`Removed ${name}`, 'success'); await reload(el); }
    catch (e) { showToast(e.message, 'error'); }
}

async function removeCommand(el, appliance, command) {
    try { await fetchWithTimeout(`${API}/library/${encodeURIComponent(appliance)}/${encodeURIComponent(command)}`, { method: 'DELETE' }); showToast(`Removed ${command}`, 'success'); await reload(el); }
    catch (e) { showToast(e.message, 'error'); }
}

async function testBlast(el, appliance, command) {
    const dev = el.querySelector('#ir-device')?.value;
    if (!dev) { showToast('No IR device to blast through', 'error'); return; }
    try {
        const r = await fetchWithTimeout(`/api/devices/${encodeURIComponent(dev)}/run`,
            post({ capability: 'ir', action: 'blast', value: `${appliance} ${command}` }), 30000);
        if (r && r.ok === false) showToast(r.text || 'blast failed', 'error');
        else showToast((r && r.text) || `Sent ${appliance} ${command}`, 'success');
    } catch (e) { showToast(e.message, 'error'); }
}
