// trigger-editor/trigger-cron.js - Cron schedule picker (simple/advanced) + helpers

/**
 * Render the cron trigger section HTML
 * @param {Object} t - Existing task data (or {})
 * @param {Object} opts - { isHeartbeat: bool }
 * @returns {string} HTML string
 */
export function renderCronTrigger(t, opts = {}) {
    const { isHeartbeat } = opts;
    const parsed = t.schedule ? parseCron(t.schedule) : (isHeartbeat ? { mode: 'interval', value: 15, unit: 'minutes' } : { mode: 'daily', time: '09:00' });
    const isEdit = !!t.id;

    const simpleOk = isHeartbeat ? parsed.mode === 'interval' : (parsed.mode === 'daily' || parsed.mode === 'weekly');
    const initTab = simpleOk || !isEdit ? 'simple' : 'advanced';

    if (!isEdit && isHeartbeat && parsed.mode !== 'interval') {
        parsed.mode = 'interval';
        parsed.value = 15;
        parsed.unit = 'minutes';
    }

    const currentTime = parsed.time || '09:00';
    const intervalValue = parsed.mode === 'interval' ? parsed.value : 15;
    const intervalUnit = parsed.mode === 'interval' ? parsed.unit : 'minutes';
    const cronRaw = t.schedule || '0 9 * * *';

    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    const selectedDays = parsed.mode === 'weekly' ? parsed.days : [1, 2, 3, 4, 5];
    const dayChecks = dayNames.map((name, i) =>
        `<label class="sched-day-label"><input type="checkbox" value="${i}" ${selectedDays.includes(i) ? 'checked' : ''}> ${name}</label>`
    ).join('');

    return `
        <div class="sched-section-title" style="margin-top:16px">\uD83D\uDCC5 When</div>
        <div class="sched-picker">
            <div class="sched-picker-tabs">
                <button type="button" class="sched-picker-tab${initTab === 'simple' ? ' active' : ''}" data-tab="simple">Simple</button>
                <button type="button" class="sched-picker-tab${initTab === 'advanced' ? ' active' : ''}" data-tab="advanced">Advanced</button>
            </div>
            <div class="sched-pick-panel" data-tab-panel="simple" ${initTab !== 'simple' ? 'style="display:none"' : ''}>
                ${isHeartbeat ? `
                <div class="sched-sentence">
                    Beats every
                    <input type="number" id="ed-interval-val" value="${intervalValue}" min="1" style="width:60px">
                    <select id="ed-interval-unit">
                        <option value="minutes" ${intervalUnit === 'minutes' ? 'selected' : ''}>minutes</option>
                        <option value="hours" ${intervalUnit === 'hours' ? 'selected' : ''}>hours</option>
                    </select>
                </div>
                ` : `
                <div class="sched-sentence">
                    Runs at <input type="time" id="ed-time" value="${currentTime}"> every
                    <select id="ed-frequency">
                        <option value="day" ${parsed.mode === 'daily' ? 'selected' : ''}>Day</option>
                        <option value="days" ${parsed.mode === 'weekly' ? 'selected' : ''}>On these days</option>
                    </select>
                </div>
                <div class="sched-days" id="ed-days-row" ${parsed.mode !== 'weekly' ? 'style="display:none"' : ''}>${dayChecks}</div>
                `}
            </div>
            <div class="sched-pick-panel" data-tab-panel="advanced" ${initTab !== 'advanced' ? 'style="display:none"' : ''}>
                <div class="sched-field" style="margin-bottom:4px">
                    <label>Cron expression</label>
                    <input type="text" id="ed-cron-raw" value="${_esc(cronRaw)}" placeholder="0 9 * * *">
                </div>
            </div>
            <div class="sched-preview-line" id="sched-preview-line"></div>
        </div>

        <div class="sched-modifiers">
            <label class="sched-modifier">
                <input type="checkbox" id="ed-active-hours-on" ${t.active_hours_start != null ? 'checked' : ''}>
                Active hours
                <span class="help-tip" data-tip="Restrict to a time window. Outside these hours, cron matches are skipped. Supports overnight (e.g. 8PM-4AM).">?</span>
                <span class="sched-modifier-inputs" id="ed-active-hours-row" ${t.active_hours_start == null ? 'style="display:none"' : ''}>
                    <select id="ed-active-start">${_hourOptions(t.active_hours_start ?? 20)}</select>
                    to
                    <select id="ed-active-end">${_hourOptions(t.active_hours_end ?? 4)}</select>
                </span>
            </label>
            <label class="sched-modifier">
                <input type="checkbox" id="ed-chance-on" ${(t.chance ?? 100) < 100 ? 'checked' : ''}>
                Chance
                <span class="help-tip" data-tip="Roll the dice each time this fires. 50% = runs half the time. 100% = always.">?</span>
                <span class="sched-modifier-inputs" id="ed-chance-row" ${(t.chance ?? 100) >= 100 ? 'style="display:none"' : ''}>
                    <input type="number" id="ed-chance" value="${t.chance ?? 100}" min="1" max="100" style="width:60px">%
                </span>
            </label>
        </div>`;
}

/**
 * Wire cron trigger event listeners, returns getCurrentCron() function
 * @param {HTMLElement} modal - The editor modal element
 * @param {Object} opts - { isHeartbeat: bool }
 * @returns {Function} getCurrentCron() - call to get current cron expression
 */
export function wireCronTrigger(modal, opts = {}) {
    const { isHeartbeat } = opts;

    // Determine initial tab from DOM state
    const activeTab = modal.querySelector('.sched-picker-tab.active');
    let currentTab = activeTab?.dataset.tab || 'simple';

    // Tab switching
    modal.querySelector('.sched-picker-tabs')?.addEventListener('click', e => {
        const tab = e.target.closest('.sched-picker-tab');
        if (!tab) return;
        currentTab = tab.dataset.tab;
        modal.querySelectorAll('.sched-picker-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === currentTab));
        modal.querySelectorAll('[data-tab-panel]').forEach(p => p.style.display = p.dataset.tabPanel === currentTab ? '' : 'none');
        updatePreview();
    });

    // Frequency dropdown (tasks only)
    modal.querySelector('#ed-frequency')?.addEventListener('change', () => {
        const daysRow = modal.querySelector('#ed-days-row');
        if (daysRow) daysRow.style.display = modal.querySelector('#ed-frequency').value === 'days' ? '' : 'none';
        updatePreview();
    });

    const getCurrentCron = () => {
        if (currentTab === 'advanced') {
            return modal.querySelector('#ed-cron-raw')?.value || '0 9 * * *';
        }
        if (isHeartbeat) {
            return buildCron('interval', {
                value: parseInt(modal.querySelector('#ed-interval-val')?.value) || 15,
                unit: modal.querySelector('#ed-interval-unit')?.value || 'minutes'
            });
        }
        const time = modal.querySelector('#ed-time')?.value || '09:00';
        const freq = modal.querySelector('#ed-frequency')?.value || 'day';
        if (freq === 'days') {
            const days = [...modal.querySelectorAll('.sched-days input:checked')].map(c => parseInt(c.value));
            if (days.length === 0) return buildCron('daily', { time });
            return buildCron('weekly', { time, days });
        }
        return buildCron('daily', { time });
    };

    const updatePreview = () => {
        const cronText = describeCron(getCurrentCron());
        const chanceOn = modal.querySelector('#ed-chance-on')?.checked;
        const chance = chanceOn ? (parseInt(modal.querySelector('#ed-chance')?.value) || 100) : 100;
        const text = chance < 100 ? `${chance}% chance to run ${cronText.toLowerCase()}` : cronText;
        const el = modal.querySelector('#sched-preview-line');
        if (el) el.textContent = text;
    };

    // Live preview updates
    modal.querySelector('#ed-chance')?.addEventListener('input', updatePreview);
    modal.querySelectorAll('#ed-time, #ed-interval-val, #ed-interval-unit, #ed-cron-raw')
        .forEach(el => el.addEventListener('input', updatePreview));
    modal.querySelectorAll('.sched-days input')
        .forEach(el => el.addEventListener('change', updatePreview));
    updatePreview();

    // Active hours toggle
    modal.querySelector('#ed-active-hours-on')?.addEventListener('change', () => {
        const row = modal.querySelector('#ed-active-hours-row');
        if (row) row.style.display = modal.querySelector('#ed-active-hours-on').checked ? '' : 'none';
    });

    // Chance toggle
    modal.querySelector('#ed-chance-on')?.addEventListener('change', () => {
        const row = modal.querySelector('#ed-chance-row');
        if (row) row.style.display = modal.querySelector('#ed-chance-on').checked ? '' : 'none';
        updatePreview();
    });

    return getCurrentCron;
}

/**
 * Read cron trigger values from the modal
 * @param {HTMLElement} modal - The editor modal element
 * @param {Function} getCurrentCron - From wireCronTrigger
 * @returns {Object} Cron trigger fields for the task data
 */
export function readCronTrigger(modal, getCurrentCron) {
    const chanceOn = modal.querySelector('#ed-chance-on')?.checked;
    return {
        schedule: getCurrentCron(),
        chance: chanceOn ? (parseInt(modal.querySelector('#ed-chance')?.value) || 100) : 100,
        active_hours_start: modal.querySelector('#ed-active-hours-on')?.checked ? parseInt(modal.querySelector('#ed-active-start')?.value) : null,
        active_hours_end: modal.querySelector('#ed-active-hours-on')?.checked ? parseInt(modal.querySelector('#ed-active-end')?.value) : null,
    };
}

// ── Cron helpers (exported for use by schedule view) ──

export function parseCron(cron) {
    if (!cron) return { mode: 'daily', time: '09:00' };
    const parts = cron.trim().split(/\s+/);
    if (parts.length !== 5) return { mode: 'cron', raw: cron };
    const [min, hour, dom, mon, dow] = parts;

    if (min.startsWith('*/') && hour === '*' && dom === '*' && mon === '*' && dow === '*')
        return { mode: 'interval', value: parseInt(min.slice(2)), unit: 'minutes' };
    if (min === '0' && hour.startsWith('*/') && dom === '*' && mon === '*' && dow === '*')
        return { mode: 'interval', value: parseInt(hour.slice(2)), unit: 'hours' };
    if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*') {
        const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`;
        if (dow === '*') return { mode: 'daily', time };
        const days = dow.split(',').map(Number).filter(d => !isNaN(d));
        if (days.length > 0) return { mode: 'weekly', time, days };
    }
    return { mode: 'cron', raw: cron };
}

export function buildCron(mode, config) {
    switch (mode) {
        case 'daily': {
            const [h, m] = (config.time || '09:00').split(':');
            return `${parseInt(m)} ${parseInt(h)} * * *`;
        }
        case 'weekly': {
            const [h, m] = (config.time || '09:00').split(':');
            return `${parseInt(m)} ${parseInt(h)} * * ${config.days.sort((a,b) => a-b).join(',')}`;
        }
        case 'interval':
            if (config.unit === 'minutes') return `*/${config.value} * * * *`;
            return `0 */${config.value} * * *`;
        case 'cron': return config.raw || '0 9 * * *';
    }
}

export function describeCron(cron) {
    if (!cron) return '';
    const parsed = parseCron(cron);
    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    switch (parsed.mode) {
        case 'daily': return `Daily at ${_formatTime12(parsed.time)}`;
        case 'weekly': return `${parsed.days.map(d => dayNames[d]).join(', ')} at ${_formatTime12(parsed.time)}`;
        case 'interval': return `Every ${parsed.value} ${parsed.unit}`;
        default: return cron;
    }
}

// ── Private helpers ──

function _formatTime12(time24) {
    if (!time24) return '';
    const [h, m] = time24.split(':').map(Number);
    const ampm = h >= 12 ? 'PM' : 'AM';
    return `${h % 12 || 12}:${m.toString().padStart(2, '0')} ${ampm}`;
}

function _hourOptions(selected) {
    return Array.from({ length: 24 }, (_, i) => {
        const label = i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`;
        return `<option value="${i}" ${i === selected ? 'selected' : ''}>${label}</option>`;
    }).join('');
}

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
