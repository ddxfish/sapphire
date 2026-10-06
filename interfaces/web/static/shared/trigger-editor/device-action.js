// trigger-editor/device-action.js - the "Device" side of a Task (2026-10-06).
// A Device task runs one device action on its cron with no LLM in the loop:
// device → capability → action → value, from what the device describes
// (the same list the Devices page's Try buttons come from). Actions that
// ask for I UNDERSTAND (danger) are not offered: nothing wipes a card on a
// schedule.

export async function fetchDevices() {
    try {
        const r = await fetch('/api/devices');
        if (!r.ok) return [];
        const data = await r.json();
        return (data.devices || []).filter(d => d.enabled !== false);
    } catch { return []; }
}

async function fetchCapabilities(deviceId) {
    try {
        const r = await fetch(`/api/devices/${encodeURIComponent(deviceId)}`);
        if (!r.ok) return [];
        const data = await r.json();
        return (data.device?.capabilities || []).filter(c => !c.error);
    } catch { return []; }
}

/**
 * @param {Object} t - task (t.device_action = {device, capability, action, value} or empty)
 * @param {Array} devices - from fetchDevices()
 */
export function renderDeviceAction(t, devices) {
    const da = t.device_action || {};
    const known = devices.map(d => d.id);
    const opts = devices.map(d =>
        `<option value="${_esc(d.id)}" ${da.device === d.id ? 'selected' : ''}>${_esc(d.label || d.id)}${d.type ? ' — ' + _esc(d.type) : ''}</option>`
    ).join('');
    const missing = da.device && !known.includes(da.device)
        ? `<option value="${_esc(da.device)}" selected>${_esc(da.device)} (missing)</option>` : '';
    return `
        <div class="sched-field-row" style="margin-top:12px">
            <div class="sched-field">
                <label>Device</label>
                <select id="ed-dev-device">
                    <option value="">Pick a device</option>
                    ${missing}${opts}
                </select>
            </div>
            <div class="sched-field">
                <label>Capability</label>
                <select id="ed-dev-cap" ${da.device ? '' : 'disabled'}><option value="">—</option></select>
            </div>
            <div class="sched-field">
                <label>Action</label>
                <select id="ed-dev-action" ${da.device ? '' : 'disabled'}><option value="">—</option></select>
            </div>
        </div>
        <div class="sched-field" id="ed-dev-value-field" style="display:none">
            <label>Value <span class="help-tip" data-tip="What the action takes, if anything. The hint shows the shape.">?</span></label>
            <input type="text" id="ed-dev-value" value="${_esc(da.value || '')}" placeholder="">
        </div>
        <p class="sched-editor-blurb" id="ed-dev-help" style="margin-top:8px">${devices.length ? '' : 'No devices yet. Add one in Settings > Devices first.'}</p>`;
}

/**
 * Wire the three selects. Capabilities and actions load from the device
 * when it is picked (and once at open, for an edit).
 */
export function wireDeviceAction(modal, t) {
    const da = t.device_action || {};
    const devSel = modal.querySelector('#ed-dev-device');
    const capSel = modal.querySelector('#ed-dev-cap');
    const actSel = modal.querySelector('#ed-dev-action');
    const valField = modal.querySelector('#ed-dev-value-field');
    const valInput = modal.querySelector('#ed-dev-value');
    const help = modal.querySelector('#ed-dev-help');
    let caps = [];

    const fillActions = (want) => {
        const cap = caps.find(c => c.capability === capSel.value);
        const actions = Object.entries(cap?.actions || {}).filter(([, a]) => !a.danger);
        actSel.innerHTML = '<option value="">—</option>' + actions.map(([name]) =>
            `<option value="${_esc(name)}" ${want === name ? 'selected' : ''}>${_esc(name)}</option>`).join('');
        actSel.disabled = !actions.length;
        showAction();
    };
    const showAction = () => {
        const cap = caps.find(c => c.capability === capSel.value);
        const a = cap?.actions?.[actSel.value];
        if (!a) { help.textContent = cap?.help || ''; valField.style.display = 'none'; return; }
        help.textContent = a.help || '';
        const hint = a.values || a.example || '';
        valInput.placeholder = hint;
        valField.style.display = hint ? '' : 'none';
    };
    const fillCaps = async (wantCap, wantAct) => {
        caps = devSel.value ? await fetchCapabilities(devSel.value) : [];
        capSel.innerHTML = '<option value="">—</option>' + caps.map(c =>
            `<option value="${_esc(c.capability)}" ${wantCap === c.capability ? 'selected' : ''}>${_esc(c.label || c.capability)}</option>`).join('');
        capSel.disabled = !caps.length;
        if (devSel.value && !caps.length) help.textContent = 'This device describes nothing to run yet.';
        fillActions(wantAct);
    };

    devSel?.addEventListener('change', () => fillCaps('', ''));
    capSel?.addEventListener('change', () => fillActions(''));
    actSel?.addEventListener('change', showAction);
    if (da.device) fillCaps(da.capability, da.action);
}

/** {device, capability, action, value} as typed; the server checks it. */
export function readDeviceAction(modal) {
    return {
        device: modal.querySelector('#ed-dev-device')?.value || '',
        capability: modal.querySelector('#ed-dev-cap')?.value || '',
        action: modal.querySelector('#ed-dev-action')?.value || '',
        value: modal.querySelector('#ed-dev-value')?.value?.trim() || '',
    };
}

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
