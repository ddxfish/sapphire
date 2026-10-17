// Avatar plugin settings — model management, track mapping, idle pool
import { registerPluginSettings } from '/static/shared/plugin-registry.js';

const API = '/api/plugin/avatar';

function csrf() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

async function fetchJSON(url, opts = {}) {
    opts.headers = { 'X-CSRF-Token': csrf(), ...(opts.headers || {}) };
    const r = await fetch(url, opts);
    return r.ok ? r.json() : null;
}

// Avatar states that need track mapping. Each key must correspond to a state
// that some entry in sidebar.js's TRANSITIONS dict actually targets — keep
// these aligned. Adding a state here without wiring it (or vice versa) leaves
// a dead dropdown or an unconfigurable event.
const AVATAR_STATES = [
    { key: 'idle',        label: 'Idle' },
    { key: 'processing',  label: 'Transcribing' },
    { key: 'typing',      label: 'Composing' },
    { key: 'listening',   label: 'Listening' },
    { key: 'speaking',    label: 'Speaking' },
    { key: 'toolcall',    label: 'Tool Use' },
    { key: 'happy',       label: 'Happy' },
    { key: 'wakeword',    label: 'Alert' },
    { key: 'agent',       label: 'Agent Working' },
    { key: 'cron',        label: 'Scheduled Task' },
    { key: 'user_typing', label: 'User Typing' },
    { key: 'reading',     label: 'Reading' },
];

let _renderContainer = null;

registerPluginSettings({
    id: 'avatar',
    name: 'Avatar',
    icon: '\uD83D\uDC8E',
    helpText: '3D avatar model management and animation mapping',

    load: () => fetchJSON(`${API}/config`),

    getSettings: () => {
        if (!_renderContainer) return {};
        return collectConfig(_renderContainer);
    },

    save: async (settings) => {
        const resp = await fetch(`${API}/config`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrf(),
            },
            body: JSON.stringify(settings),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    },

    render: (container) => {
        _renderContainer = container;
        injectStyles();

        container.innerHTML = `
            <div class="avatar-settings">
                <div class="avatar-section">
                    <h3>Behavior</h3>
                    <label class="avatar-checkbox">
                        <input type="checkbox" id="avatar-inject-prompt" checked>
                        <span>Include animation instructions in AI prompt</span>
                    </label>
                    <p class="avatar-help" style="margin:2px 0 6px 22px">When enabled, the AI knows about animations and can trigger them with &lt;&lt;avatar: trackname&gt;&gt; tags in its responses.</p>
                    <label class="avatar-checkbox">
                        <input type="checkbox" id="avatar-strip-tags">
                        <span>Hide animation tags from chat</span>
                    </label>
                    <p class="avatar-help" style="margin:2px 0 6px 22px">Strip &lt;&lt;avatar: ...&gt;&gt; tags so they don't appear in chat messages. Animations still play.</p>
                    <label class="avatar-checkbox">
                        <input type="checkbox" id="avatar-user-tags">
                        <span>Let my typed tags trigger animations</span>
                    </label>
                    <p class="avatar-help" style="margin:2px 0 0 22px">When on, &lt;&lt;avatar: trackname&gt;&gt; tags YOU type in chat also play. Off by default — normally only the AI drives her avatar.</p>
                </div>

                <div class="avatar-section">
                    <h3>Models</h3>
                    <div id="avatar-model-list" class="avatar-model-list"></div>
                    <label class="avatar-upload-btn">
                        Upload Model (.glb)
                        <input type="file" id="avatar-upload" accept=".glb" hidden>
                    </label>
                </div>

                <div class="avatar-section" id="avatar-mapping-section" style="display:none">
                    <h3>Track Mapping</h3>
                    <p class="avatar-help">Map avatar states to animation tracks from your model.</p>
                    <div id="avatar-track-grid" class="avatar-track-grid"></div>
                </div>

                <div class="avatar-section" id="avatar-behavior-section" style="display:none">
                    <h3>Idle Behavior</h3>
                    <p class="avatar-help">Base = resting pose, chosen by time of day (she rotates among the checked tracks). Variety = occasional overlays that play every few minutes, then she returns to base.</p>
                    <div id="avatar-time-settings" class="avatar-time-settings"></div>
                    <div class="avatar-pool-actions">
                        <button type="button" class="avatar-btn-secondary" id="avatar-pool-variety-all">Variety: all</button>
                        <button type="button" class="avatar-btn-secondary" id="avatar-pool-variety-none">Variety: none</button>
                    </div>
                    <div id="avatar-behavior-pool"></div>
                </div>

                <div class="avatar-section" id="avatar-greeting-section" style="display:none">
                    <h3>Greeting &amp; Display</h3>
                    <div class="avatar-field">
                        <label>Play on load</label>
                        <select id="avatar-greeting-track"></select>
                    </div>
                    <div class="avatar-field" style="margin-top:6px">
                        <label>Scale</label>
                        <input type="number" id="avatar-scale" min="0.01" max="100" step="0.1" value="1.0" class="avatar-weight" style="width:70px">
                        <span class="avatar-help" style="margin:0">(1.0 = auto-fit, adjust if too big/small)</span>
                    </div>
                </div>

            </div>
        `;

        // Wire upload (use event delegation so it survives DOM rebuilds)
        container.addEventListener('change', async (e) => {
            const input = e.target.closest('#avatar-upload');
            if (!input) return;
            const file = input.files[0];
            if (!file) return;
            const form = new FormData();
            form.append('file', file);
            const label = container.querySelector('.avatar-upload-btn');
            if (label) label.textContent = 'Uploading...';
            try {
                const result = await fetch(`${API}/upload`, {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': csrf() },
                    body: form,
                });
                const data = await result.json();
                if (data.error) {
                    alert(data.error);
                } else {
                    await loadModels(container);
                }
            } catch (err) {
                alert('Upload failed');
            }
            if (label) {
                label.innerHTML = 'Upload Model (.glb)<input type="file" id="avatar-upload" accept=".glb" hidden>';
            }
        });

        // Load behavior checkboxes from config
        fetchJSON(`${API}/config`).then(cfg => {
            if (!cfg) return;
            const injectCb = container.querySelector('#avatar-inject-prompt');
            const stripCb = container.querySelector('#avatar-strip-tags');
            const userCb = container.querySelector('#avatar-user-tags');
            if (injectCb) injectCb.checked = cfg.inject_prompt !== false;
            if (stripCb) stripCb.checked = cfg.strip_tags === true;
            if (userCb) userCb.checked = cfg.user_tags === true;
        });

        // Wire behavior checkboxes — save immediately on change
        container.addEventListener('change', (e) => {
            if (e.target.id === 'avatar-inject-prompt') {
                fetch(`${API}/config`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                    body: JSON.stringify({ inject_prompt: e.target.checked }),
                });
            }
            if (e.target.id === 'avatar-strip-tags') {
                fetch(`${API}/config`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                    body: JSON.stringify({ strip_tags: e.target.checked }),
                });
                // Update the live scanner
                window._avatarStripTags = e.target.checked;
            }
            if (e.target.id === 'avatar-user-tags') {
                fetch(`${API}/config`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
                    body: JSON.stringify({ user_tags: e.target.checked }),
                });
                // Update the live scanner
                window._avatarUserTags = e.target.checked;
            }
        });

        // Initial load
        loadModels(container).catch(e => console.error('[Avatar Settings] loadModels failed:', e));
    }
});

let _currentModels = [];
let _activeModel = '';
let _activeTracks = [];
let _activeConfig = {};

async function loadModels(container) {
    const data = await fetchJSON(`${API}/models`);
    if (!data) return;

    _currentModels = data.models || [];
    _activeModel = data.active_model || '';

    const list = container.querySelector('#avatar-model-list');
    if (!_currentModels.length) {
        list.innerHTML = '<div class="avatar-help">No models uploaded yet.</div>';
        return;
    }

    list.innerHTML = _currentModels.map(m => `
        <div class="avatar-model-card ${m.active ? 'active' : ''}" data-filename="${m.filename}">
            <div class="avatar-model-info">
                <span class="avatar-model-name">${m.filename}</span>
                <span class="avatar-model-meta">${(m.size / 1024 / 1024).toFixed(1)}MB${m.track_count ? ' · ' + m.track_count + ' mapped' : ''}</span>
            </div>
            <div class="avatar-model-actions">
                ${m.active ? '<span class="avatar-badge">Active</span>' : `<button class="avatar-btn avatar-btn-sm" data-action="activate">Use</button>`}
                <button class="avatar-btn avatar-btn-sm avatar-btn-danger" data-action="delete">Delete</button>
            </div>
        </div>
    `).join('');

    // Wire card actions
    list.querySelectorAll('[data-action="activate"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const filename = btn.closest('[data-filename]').dataset.filename;
            await fetchJSON(`${API}/config`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active_model: filename }),
            });
            await loadModels(container);
        });
    });

    list.querySelectorAll('[data-action="delete"]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const filename = btn.closest('[data-filename]').dataset.filename;
            if (!confirm(`Delete ${filename}?`)) return;
            await fetchJSON(`${API}/models/${filename}`, { method: 'DELETE' });
            await loadModels(container);
        });
    });

    // Load track mapping for active model
    if (_activeModel) {
        await loadTrackMapping(container);
    }
}

async function loadTrackMapping(container) {
    // Get tracks + config in one call where possible
    const [trackData, cfg] = await Promise.all([
        fetchJSON(`${API}/tracks/${_activeModel}`),
        fetchJSON(`${API}/config`),
    ]);
    if (!trackData) return;
    _activeTracks = trackData.tracks || [];
    _activeConfig = (cfg?.models || {})[_activeModel] || {};

    const trackMap = _activeConfig.track_map || {};
    const idlePool = _activeConfig.idle_pool || [];
    const greetingTrack = _activeConfig.greeting_track || '';
    const timeBuckets = _activeConfig.time_buckets?.length
        ? _activeConfig.time_buckets
        : [{ name: 'day', start: 7 }, { name: 'night', start: 21 }];
    const varietyIntervalMin = _activeConfig.variety_interval_min || 2;

    // Show sections
    container.querySelector('#avatar-mapping-section').style.display = '';
    container.querySelector('#avatar-behavior-section').style.display = '';
    container.querySelector('#avatar-greeting-section').style.display = '';

    // Track mapping grid
    const grid = container.querySelector('#avatar-track-grid');
    const trackOptions = _activeTracks.map(t =>
        `<option value="${t.name}">${t.name} (${t.duration}s)</option>`
    ).join('');

    grid.innerHTML = AVATAR_STATES.map(s => `
        <div class="avatar-field">
            <label>${s.label}</label>
            <select data-state="${s.key}">
                <option value="">(none)</option>
                ${trackOptions}
            </select>
        </div>
    `).join('');

    // Set current values
    grid.querySelectorAll('select').forEach(sel => {
        const state = sel.dataset.state;
        if (trackMap[state]) sel.value = trackMap[state];
    });

    // Time settings — one start-hour input per bucket + variety interval.
    const timeEl = container.querySelector('#avatar-time-settings');
    timeEl.innerHTML = timeBuckets.map(b => {
        const quiet = b.quiet ?? (b.name === 'night');
        return `
        <div class="avatar-field">
            <label>${b.name[0].toUpperCase() + b.name.slice(1)} starts (hour, 0–23)</label>
            <input type="number" data-bucket-start="${b.name}" min="0" max="23" value="${b.start}" class="avatar-weight">
            <label class="avatar-quiet-label" title="No variety overlays fire — she rests undisturbed">
                <input type="checkbox" data-bucket-quiet="${b.name}" ${quiet ? 'checked' : ''}> quiet
            </label>
        </div>
    `;
    }).join('') + `
        <div class="avatar-field">
            <label>Variety every (minutes)</label>
            <input type="number" id="avatar-variety-interval" min="1" max="120" value="${varietyIntervalMin}" class="avatar-weight">
        </div>
    `;

    // Behavior pool — one row per track, with a base checkbox per time bucket,
    // a variety checkbox, and a weight (used for variety random selection).
    const poolEl = container.querySelector('#avatar-behavior-pool');
    const headerCols = timeBuckets.map(b =>
        `<span class="avatar-pool-col">${b.name[0].toUpperCase() + b.name.slice(1)}</span>`
    ).join('');
    poolEl.innerHTML = `
        <div class="avatar-pool-header">
            <span class="avatar-pool-name">Track</span>
            ${headerCols}
            <span class="avatar-pool-col">Variety</span>
            <span class="avatar-pool-col">Weight</span>
        </div>
    ` + _activeTracks.map(t => {
        const entry = idlePool.find(p => p.track === t.name);
        const base = entry?.base || [];
        const variety = entry ? (entry.variety ?? true) : false;
        const weight = entry?.weight || 10;
        const baseCells = timeBuckets.map(b => `
            <span class="avatar-pool-col">
                <input type="checkbox" data-base="${t.name}" data-bucket="${b.name}" ${base.includes(b.name) ? 'checked' : ''}>
            </span>
        `).join('');
        return `
            <div class="avatar-pool-row">
                <span class="avatar-pool-name" title="${t.name} (${t.duration}s)">${t.name}</span>
                ${baseCells}
                <span class="avatar-pool-col">
                    <input type="checkbox" data-variety="${t.name}" ${variety ? 'checked' : ''}>
                </span>
                <span class="avatar-pool-col">
                    <input type="number" data-weight="${t.name}" min="1" max="100" value="${weight}" class="avatar-weight">
                </span>
            </div>
        `;
    }).join('');

    // Variety bulk buttons.
    const setAllVariety = (checked) => {
        poolEl.querySelectorAll('input[type="checkbox"][data-variety]').forEach(cb => { cb.checked = checked; });
    };
    container.querySelector('#avatar-pool-variety-all')?.addEventListener('click', () => setAllVariety(true));
    container.querySelector('#avatar-pool-variety-none')?.addEventListener('click', () => setAllVariety(false));

    // Greeting dropdown
    const greetSel = container.querySelector('#avatar-greeting-track');
    greetSel.innerHTML = `<option value="">(none)</option>` + trackOptions;
    if (greetingTrack) greetSel.value = greetingTrack;

    // Scale
    const scaleInput = container.querySelector('#avatar-scale');
    if (scaleInput) scaleInput.value = _activeConfig.scale || 1.0;
}

function collectConfig(container) {
    // Track map
    const track_map = {};
    container.querySelectorAll('#avatar-track-grid select').forEach(sel => {
        if (sel.value) track_map[sel.dataset.state] = sel.value;
    });

    // Behavior pool — one entry per track that has ANY role (base or variety).
    const idle_pool = [];
    container.querySelectorAll('#avatar-behavior-pool .avatar-pool-row').forEach(row => {
        const varietyCb = row.querySelector('input[data-variety]');
        const track = varietyCb?.dataset.variety;
        if (!track) return;
        const base = [];
        row.querySelectorAll('input[data-base]').forEach(cb => {
            if (cb.checked) base.push(cb.dataset.bucket);
        });
        const variety = !!varietyCb.checked;
        if (!variety && base.length === 0) return;  // no role → omit
        const weight = parseInt(row.querySelector(`[data-weight="${track}"]`)?.value || '10');
        idle_pool.push({ track, weight, variety, base });
    });

    // Time buckets (preserve names/order, update start hours + quiet flag) + interval.
    const time_buckets = [];
    container.querySelectorAll('#avatar-time-settings input[data-bucket-start]').forEach(inp => {
        const name = inp.dataset.bucketStart;
        const start = Math.max(0, Math.min(23, parseInt(inp.value || '0')));
        const quiet = !!container.querySelector(`[data-bucket-quiet="${name}"]`)?.checked;
        time_buckets.push({ name, start, quiet });
    });
    const variety_interval_min = Math.max(1, parseInt(container.querySelector('#avatar-variety-interval')?.value || '2'));

    // Greeting + scale
    const greeting_track = container.querySelector('#avatar-greeting-track')?.value || null;
    const scale = parseFloat(container.querySelector('#avatar-scale')?.value || '1.0') || 1.0;

    return {
        active_model: _activeModel,
        models: {
            [_activeModel]: {
                ..._activeConfig,
                track_map,
                idle_pool,
                time_buckets,
                variety_interval_min,
                greeting_track,
                scale,
            }
        }
    };
}

function injectStyles() {
    if (document.getElementById('avatar-settings-styles')) return;
    const style = document.createElement('style');
    style.id = 'avatar-settings-styles';
    style.textContent = `
        .avatar-settings { display: flex !important; flex-direction: column !important; gap: 20px; }
        .avatar-section { display: block !important; }
        .avatar-section h3 { margin: 0 0 8px; font-size: 14px; color: var(--text); }
        .avatar-help { font-size: 12px; color: var(--text-muted); margin: 0 0 8px; }
        .avatar-checkbox { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; margin-bottom: 4px; }
        .avatar-checkbox input { accent-color: #4a9eff; margin: 0; }

        .avatar-model-list { display: flex !important; flex-direction: column !important; gap: 6px; margin-bottom: 10px; }
        .avatar-model-card {
            display: flex !important; justify-content: space-between; align-items: center;
            padding: 8px 12px; border-radius: 6px;
            background: var(--bg-primary); border: 1px solid var(--border);
        }
        .avatar-model-card.active { border-color: #4a9eff; }
        .avatar-model-name { font-size: 13px; font-weight: 500; display: block; }
        .avatar-model-meta { font-size: 11px; color: var(--text-muted); display: block; margin-top: 2px; }
        .avatar-model-actions { display: flex !important; gap: 6px; align-items: center; }
        .avatar-badge { font-size: 11px; color: #4a9eff; border: 1px solid #4a9eff; border-radius: 4px; padding: 2px 6px; }

        .avatar-track-grid { display: flex !important; flex-direction: column !important; gap: 6px; }
        .avatar-field { display: flex !important; align-items: center; gap: 8px; }
        .avatar-field label { min-width: 80px; font-size: 13px; color: var(--text); flex-shrink: 0; }
        .avatar-field select {
            flex: 1; padding: 5px 8px; border: 1px solid var(--border);
            border-radius: 5px; background: var(--bg-primary); color: var(--text); font-size: 12px;
        }

        .avatar-weight { width: 50px; padding: 3px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-primary); color: var(--text); font-size: 12px; text-align: center; }
        .avatar-time-settings { display: flex !important; flex-direction: column !important; gap: 6px; margin-bottom: 10px; }
        .avatar-quiet-label { font-size: 11px; color: var(--text-muted); display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
        .avatar-quiet-label input { accent-color: #4a9eff; margin: 0; }
        .avatar-pool-header, .avatar-pool-row {
            display: flex !important; align-items: center; gap: 4px; padding: 3px 0;
        }
        .avatar-pool-header { border-bottom: 1px solid var(--border); font-size: 11px; color: var(--text-muted); font-weight: 600; }
        .avatar-pool-name { flex: 1; min-width: 90px; font-size: 12px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .avatar-pool-col { width: 52px; flex-shrink: 0; text-align: center; }
        .avatar-pool-col input[type="checkbox"] { accent-color: #4a9eff; margin: 0; }
        .avatar-pool-row .avatar-weight { width: 44px; }

        .avatar-upload-btn {
            display: inline-block; padding: 6px 14px; border-radius: 6px; cursor: pointer;
            background: var(--bg-primary); border: 1px dashed var(--border); color: var(--text);
            font-size: 12px; text-align: center; transition: border-color 0.15s;
        }
        .avatar-upload-btn:hover { border-color: #4a9eff; }

        .avatar-btn {
            padding: 5px 12px; border: 1px solid var(--border); border-radius: 5px;
            background: var(--bg-primary); color: var(--text); cursor: pointer; font-size: 12px;
        }
        .avatar-btn:hover { background: var(--bg-secondary); }
        .avatar-btn-primary { background: #4a9eff; color: white; border-color: #4a9eff; }
        .avatar-btn-primary:hover { background: #3a8eef; }
        .avatar-btn-sm { padding: 3px 8px; font-size: 11px; }
        .avatar-btn-danger { color: var(--error, #ff4444); }
        .avatar-btn-danger:hover { background: var(--error, #ff4444); color: white; }
        .avatar-pool-actions { display: flex; gap: 6px; margin-bottom: 8px; }
        .avatar-btn-secondary {
            padding: 4px 10px; border: 1px solid var(--border); border-radius: 4px;
            background: var(--bg-primary); color: var(--text); cursor: pointer; font-size: 11px;
        }
        .avatar-btn-secondary:hover { background: var(--bg-secondary); }
    `;
    document.head.appendChild(style);
}
