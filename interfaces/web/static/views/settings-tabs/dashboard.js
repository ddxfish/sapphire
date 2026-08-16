// settings-tabs/dashboard.js — Hero-style command center.
// Identity row: orb (mood ring) + Sapphire name + Store/Help + status pills.
// Action panels: System / Updates / Backups / Maintenance, each with a
// dropdown of actions. Below the hero: Plugin Spotlight + Token Metrics.
// Design source: tmp/dashboard-hero.html (Variant B, Dancing Script).
import * as ui from '../../ui.js';
import { listStorePlugins } from '../../shared/store-api.js';
import { isSafeHref } from '../../shared/url-safety.js';

let updateStatus = null;

function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const MOOD_LABELS = {
    healthy: 'Online',
    busy:    'Working',
    warn:    'Issues',
    error:   'Error',
    idle:    'Idle',
};
// Status word color in Maintenance — splash of mood color, matched to the orb.
const MOOD_COLORS = {
    healthy: '#22c97a',
    busy:    '#4a9eff',
    warn:    '#f5a623',
    error:   '#ff4f4f',
    idle:    '#6e8e7d',
};

export default {
    id: 'dashboard',
    name: 'Dashboard',
    icon: '🏠',
    description: 'System status, updates, and controls',

    render(ctx) {
        const displayName = (() => {
            try { return localStorage.getItem('sapphireDisplayName') || 'Sapphire'; }
            catch (e) { return 'Sapphire'; }
        })();
        const version = window.__appVersion || '?';
        return `
            <div class="dash-root">
                <div class="dash-hero" id="dash-hero">
                    <div class="dash-npc-star" id="dash-npc-star"></div>
                    <div class="dash-hero-top">
                        <div class="dash-orb-block">
                            <div class="dash-orb" data-mood="healthy" id="dash-orb">
                                <div class="dash-orb-core"></div>
                            </div>
                        </div>

                        <div class="dash-hero-title">
                            <div class="dash-hero-name"
                                 id="dash-hero-name"
                                 contenteditable="plaintext-only"
                                 spellcheck="false"
                                 title="Click to rename">${_esc(displayName)}</div>
                            <div class="dash-hero-meta"><strong>v${_esc(version)}</strong> <span id="dash-branch"></span></div>
                        </div>

                        <div class="dash-hero-right">
                            <div class="dash-quick-links">
                                <button class="dash-hero-link" id="dash-open-store">\u{1F6CD}\u{FE0F} Store</button>
                                <button class="dash-hero-link" id="dash-open-help">\u{1F4D6} Help</button>
                            </div>
                            <div class="dash-component-status" id="dash-component-status">
                                ${['emb', 'tts', 'stt', 'ww'].map(k =>
                                    `<span class="dash-cs-pill" data-cs="${k}" title="${k.toUpperCase()}: loading"><span>${k}</span><span class="dash-cs-dot idle"></span></span>`
                                ).join('')}
                            </div>
                        </div>
                    </div>

                    <div class="dash-widget-controls" id="dash-widget-controls">
                        <button id="dash-add-widget" title="Add widget">+ Add</button>
                        <button id="dash-edit-widgets" title="Edit dashboard">✎ Edit</button>
                    </div>
                    <div class="dash-action-panels" id="dash-panels">
                        <span class="dim" style="font-size:0.6875rem;padding:8px">Loading widgets...</span>
                    </div>
                </div>

                <div class="dash-deps-card" id="dash-deps-card" style="display:none">
                    <h4 style="margin:0 0 8px;font-size:var(--font-sm);color:var(--text-muted);text-transform:uppercase;letter-spacing:0.05em">Missing Dependencies</h4>
                    <div id="dash-deps-list" style="font-size:var(--font-sm)">
                        <span class="text-muted">Checking...</span>
                    </div>
                </div>

                <div class="dash-content">
                    <div class="dash-card">
                        <div class="dash-card-header">
                            <h4>Token Metrics <span class="text-muted" style="font-size:var(--font-xs);font-weight:normal">(30 days)</span></h4>
                            <label class="metrics-toggle" id="metrics-toggle">
                                <input type="checkbox" id="metrics-enabled-cb">
                                <span class="toggle-track"></span>
                                <span class="toggle-label">Track</span>
                            </label>
                        </div>
                        <div id="dash-metrics" class="dash-metrics">
                            <span class="text-muted">Loading...</span>
                        </div>
                    </div>

                    <div class="dash-card dash-spotlight" id="dash-spotlight-card" style="display:none">
                        <h4>\u{1F6CD}\u{FE0F} Plugin Spotlight</h4>
                        <div class="dash-recommended-list" id="dash-recommended-list">
                            <span class="text-muted" style="font-size:var(--font-sm)">Loading...</span>
                        </div>
                        <a href="#store" class="dash-rec-see-all">See all in Store →</a>
                    </div>
                </div>
            </div>
        `;
    },

    attachListeners(ctx, el) {
        // ── Identity row ────────────────────────────────────────────
        _wireEditableName(el);
        _wireOrb(el);
        _startNpcStar(el);

        el.querySelector('#dash-open-store')?.addEventListener('click', () => {
            import('../../core/router.js').then(r => r.switchView('store'));
        });
        el.querySelector('#dash-open-help')?.addEventListener('click', () => {
            import('../../core/router.js').then(r => r.switchView('help'));
        });

        // ── Spotlight tile click → store deep-link (lower content row) ──
        // Don't intercept if the user clicked an actual link (e.g. author URL)
        // inside the tile — let the browser navigate to that link instead.
        el.querySelector('#dash-spotlight-card')?.addEventListener('click', e => {
            if (e.target.closest('a')) return;
            const tile = e.target.closest('.dash-rec-tile');
            if (tile) {
                window.location.hash = `#store/plugins/${encodeURIComponent(tile.dataset.slug)}`;
            }
        });

        // ── Mount panels from /api/dashboard/widgets ────────────────
        // Each registered widget renders through its own module. The host
        // owns the panel chrome (title + actions dropdown) and calls
        // module.render() to populate the body.
        mountPanels(el).catch(e => console.warn('mountPanels failed', e));

        // ── Controls row — `+` opens the picker, `✎` toggles edit mode
        el.querySelector('#dash-add-widget')?.addEventListener('click', () => openPicker(el));
        el.querySelector('#dash-edit-widgets')?.addEventListener('click', () => toggleEditMode(el));

        // ── Initial mood paint (status word picks up its color when
        //    the Maintenance widget's #mnt-status is in the DOM) ──
        _setMood(el, el.querySelector('#dash-orb')?.getAttribute('data-mood') || 'healthy');

        // ── Hero-level data fetches ─────────────────────────────────
        // These keep the orb mood + component pills + lower content row
        // populated. Per-panel data fetches live inside each widget now.
        loadSystemInfo(el);
        checkForUpdate(el);
        loadComponentStatus(el);
        loadPluginSpotlight(el);
        loadMetrics(el);
        loadMissingDeps(el, ctx);
        checkLastUpdateResult();
    }
};


// =============================================================================
// HERO HELPERS
// =============================================================================

function _setMood(el, mood) {
    const orb = el.querySelector('#dash-orb');
    if (orb) orb.setAttribute('data-mood', mood);
    // The Maintenance widget renders #mnt-status async — may not be in
    // the DOM yet at first call. Subsequent mood-signal updates re-paint
    // it once the widget has mounted.
    const status = el.querySelector('#mnt-status');
    if (status) {
        const label = MOOD_LABELS[mood] || 'Online';
        const color = MOOD_COLORS[mood] || MOOD_COLORS.healthy;
        status.innerHTML = `status <strong style="color:${color}">${_esc(label)}</strong>`;
    }
}

function _wireEditableName(el) {
    const node = el.querySelector('#dash-hero-name');
    if (!node) return;
    node.addEventListener('blur', async e => {
        const v = (e.target.textContent || '').trim() || 'Sapphire';
        // Cap to reasonable length so a runaway paste can't deform the hero.
        const trimmed = v.slice(0, 64);
        e.target.textContent = trimmed;
        // localStorage cache for instant render on next load.
        try { localStorage.setItem('sapphireDisplayName', trimmed); } catch (e2) { /* ignore */ }
        // Persist to backend setting so it lives with the install, not the
        // browser. Falls back silently if the network call fails — the local
        // cache still carries the change.
        try {
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            await fetch('/api/settings/batch', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ settings: { DASHBOARD_DISPLAY_NAME: trimmed }, persist: true }),
            });
        } catch { /* offline / network — local cache still has it */ }
    });
    node.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); }
        if (e.key === 'Escape') {
            try { e.target.textContent = localStorage.getItem('sapphireDisplayName') || 'Sapphire'; }
            catch (e2) { e.target.textContent = 'Sapphire'; }
            e.target.blur();
        }
    });
}

function _wireOrb(el) {
    const orb = el.querySelector('#dash-orb');
    if (!orb) return;
    orb.addEventListener('click', e => {
        const rect = orb.getBoundingClientRect();
        const cx = e.clientX - rect.left;
        const cy = e.clientY - rect.top;
        const ripple = document.createElement('div');
        ripple.className = 'dash-orb-ripple';
        ripple.style.left = cx + 'px';
        ripple.style.top  = cy + 'px';
        orb.appendChild(ripple);
        ripple.addEventListener('animationend', () => ripple.remove());
        orb.classList.add('inflated');
        setTimeout(() => orb.classList.remove('inflated'), 550);
    });
}

// NPC star — wanders, visits [data-attention] markers, hides in the orb,
// returns. State machine with linger phase + per-state fidget, driven by
// rAF (no React, plain DOM). Cleanup is best-effort: if the user leaves
// the dashboard tab the rAF keeps running until the node is gone — cheap.
function _startNpcStar(el) {
    const hero = el.querySelector('#dash-hero');
    const star = el.querySelector('#dash-npc-star');
    if (!hero || !star) return;

    let x = 80, y = 60, tx = 80, ty = 60;
    let state = 'wander';
    let lingering = false;
    let lingerUntil = 0;
    let anchorX = 80, anchorY = 60;
    let opacity = 1;

    const lingerDuration = (s) => {
        if (s === 'visit')  return 2000 + Math.random() * 1500;
        if (s === 'orbit')  return 2800 + Math.random() * 2400;
        if (s === 'wander') return 1300 + Math.random() * 1500;
        if (s === 'home')   return 1900 + Math.random() * 1700;
        return 1500;
    };
    const arrivalThreshold = (s) => (s === 'home' ? 22 : 4);

    const pickState = () => {
        const r = Math.random();
        if (r < 0.42)      state = 'wander';
        else if (r < 0.72) state = 'visit';
        else if (r < 0.88) state = 'orbit';
        else               state = 'home';
        lingering = false;

        const rect = hero.getBoundingClientRect();
        if (state === 'wander') {
            tx = 60 + Math.random() * (rect.width - 120);
            ty = 25 + Math.random() * (rect.height - 50);
        } else if (state === 'visit') {
            const targets = hero.querySelectorAll('[data-attention]');
            if (targets.length) {
                const t = targets[Math.floor(Math.random() * targets.length)].getBoundingClientRect();
                tx = t.left + t.width / 2 - rect.left;
                ty = t.top + t.height / 2 - rect.top;
            } else {
                state = 'wander';
                tx = Math.random() * rect.width;
                ty = Math.random() * rect.height;
            }
        } else if (state === 'orbit') {
            tx = x + (Math.random() - 0.5) * 90;
            ty = y + (Math.random() - 0.5) * 50;
        } else if (state === 'home') {
            const orbEl = hero.querySelector('.dash-orb');
            if (orbEl) {
                const o = orbEl.getBoundingClientRect();
                tx = o.left + o.width / 2 - rect.left;
                ty = o.top + o.height / 2 - rect.top;
            }
        }
    };

    pickState();

    const tick = () => {
        if (!star.isConnected) return; // node gone, stop the loop
        const now = performance.now();
        if (!lingering) {
            const ease = state === 'visit' ? 0.028 : 0.014;
            x += (tx - x) * ease;
            y += (ty - y) * ease;
            const dist = Math.hypot(tx - x, ty - y);
            if (dist < arrivalThreshold(state)) {
                lingering = true;
                lingerUntil = now + lingerDuration(state);
                anchorX = x; anchorY = y;
            }
        } else {
            const t = now / 1000;
            if (state === 'wander') {
                x += (Math.random() - 0.5) * 0.7;
                y += (Math.random() - 0.5) * 0.7;
                x += (anchorX - x) * 0.02;
                y += (anchorY - y) * 0.02;
            } else if (state === 'visit') {
                const phase = t * 1.6;
                x = anchorX + Math.cos(phase) * 5;
                y = anchorY + Math.sin(phase) * 5;
            } else if (state === 'orbit') {
                const phase = t * 0.55;
                x = anchorX + Math.cos(phase) * 14;
                y = anchorY + Math.sin(phase) * 8;
            }
            if (now > lingerUntil) pickState();
        }

        if (state === 'home' && lingering) {
            opacity = Math.max(0, opacity - 0.05);
        } else {
            opacity = Math.min(1, opacity + 0.02);
        }

        star.style.transform = `translate(${x}px, ${y}px)`;
        star.style.opacity = opacity;
        requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}


// =============================================================================
// PANEL MOUNTING — widgets load from /api/dashboard/widgets
// =============================================================================
//
// User's panel list lives in user/webui/dashboard.json (auto-seeded
// with built-ins on first read). Each entry references a widget by
// (plugin, widget_id) and carries instance_id + size + per-instance
// settings. Backend annotates each entry with `available` + `render_url`
// so the host can render placeholders for orphaned panels (plugin
// uninstalled but still in the user's list).

// Cleanup callbacks returned by each widget's render(). Run when the
// dashboard tab leaves or panels remount.
let _panelRegistry = [];

async function _fetchUserPanels() {
    try {
        const res = await fetch('/api/dashboard/widgets');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        return data.panels || [];
    } catch (e) {
        console.warn('failed to load dashboard panels', e);
        return [];
    }
}

async function _saveUserPanels(panels) {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const body = JSON.stringify({
        version: 1,
        panels: panels.map(p => ({
            instance_id: p.instance_id,
            plugin: p.plugin,
            widget_id: p.widget_id,
            size: p.size,
            settings: p.settings || {},
        })),
    });
    const res = await fetch('/api/dashboard/widgets', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body,
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
}

// Debounced save — drag/resize cascades coalesce into one PUT.
let _saveTimer = null;
let _savePending = null;
function _scheduleSave(panels) {
    _savePending = panels;
    if (_saveTimer) clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => {
        const toSave = _savePending;
        _saveTimer = null; _savePending = null;
        _saveUserPanels(toSave).catch(e =>
            ui.showToast(`Save failed: ${e.message}`, 'error'));
    }, 250);
}

function _buildPanelChrome(panel, supportedSizes) {
    const div = document.createElement('div');
    div.className = `dash-action-panel size-${panel.size}`;
    div.dataset.panel = panel.widget_id;
    div.dataset.instance = panel.instance_id;
    div.dataset.plugin = panel.plugin;
    div.dataset.widget = panel.widget_id;
    div.dataset.size = panel.size;

    // Drag handle — 6-dot SVG (clean at any size, font-independent).
    // Always present; only visible/draggable in edit mode.
    const handle = document.createElement('div');
    handle.className = 'dash-action-panel-edit-handle';
    handle.title = 'Drag to reorder';
    handle.innerHTML = `
        <svg viewBox="0 0 8 14" width="8" height="14" xmlns="http://www.w3.org/2000/svg">
            <circle cx="2" cy="2"  r="1.3" fill="currentColor"/>
            <circle cx="6" cy="2"  r="1.3" fill="currentColor"/>
            <circle cx="2" cy="7"  r="1.3" fill="currentColor"/>
            <circle cx="6" cy="7"  r="1.3" fill="currentColor"/>
            <circle cx="2" cy="12" r="1.3" fill="currentColor"/>
            <circle cx="6" cy="12" r="1.3" fill="currentColor"/>
        </svg>
    `;
    div.appendChild(handle);

    // Delete button.
    const del = document.createElement('button');
    del.className = 'dash-action-panel-edit-delete';
    del.title = 'Remove widget';
    del.textContent = '×';
    del.addEventListener('click', (e) => {
        e.stopPropagation();
        _deletePanel(panel.instance_id);
    });
    div.appendChild(del);

    // Title and info-line wrapper.
    const titleEl = document.createElement('div');
    titleEl.className = 'dash-action-panel-title';
    div.appendChild(titleEl);

    const infoEl = document.createElement('div');
    infoEl.className = 'dash-action-panel-info';
    div.appendChild(infoEl);

    // Resize pills (only the sizes the widget allows; hidden if just one).
    if (supportedSizes && supportedSizes.length > 1) {
        const sizes = document.createElement('div');
        sizes.className = 'dash-action-panel-edit-sizes';
        for (const s of supportedSizes) {
            const btn = document.createElement('button');
            btn.textContent = s;
            if (s === panel.size) btn.classList.add('active');
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                _resizePanel(panel.instance_id, s);
            });
            sizes.appendChild(btn);
        }
        div.appendChild(sizes);
    }

    // Actions dropdown.
    const drop = document.createElement('details');
    drop.className = 'dash-action-dropdown';
    drop.name = 'dash-hero-actions';
    drop.innerHTML = `
        <summary><span>Actions</span><span class="chev">▾</span></summary>
        <div class="dash-action-dropdown-menu"></div>
    `;
    div.appendChild(drop);

    return div;
}

// Edit-mode state. The dashboard root div gets `dashboard-editing` and
// SortableJS attaches/detaches on the panels container.
let _sortableInstance = null;
let _editEl = null;

function toggleEditMode(el) {
    const root = el.querySelector('.dash-root');
    const button = el.querySelector('#dash-edit-widgets');
    if (!root) return;
    const editing = root.classList.toggle('dashboard-editing');
    button?.classList.toggle('editing', editing);
    if (editing) {
        button.textContent = '✓ Done';
        _editEl = el;
        _initSortable(el);
    } else {
        button.textContent = '✎ Edit';
        _editEl = null;
        _destroySortable();
    }
}

function _initSortable(el) {
    if (!window.Sortable) {
        console.warn('Sortable not loaded');
        return;
    }
    const container = el.querySelector('#dash-panels');
    if (!container) return;
    _destroySortable();
    _sortableInstance = window.Sortable.create(container, {
        animation: 180,
        handle: '.dash-action-panel-edit-handle',
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        onEnd: () => _persistOrder(el),
    });
}

function _destroySortable() {
    if (_sortableInstance) {
        try { _sortableInstance.destroy(); } catch {}
        _sortableInstance = null;
    }
}

// Read panel order from DOM (Sortable just rearranged it) and save.
async function _persistOrder(el) {
    const panels = await _fetchUserPanels();
    const byId = new Map(panels.map(p => [p.instance_id, p]));
    const ordered = [];
    el.querySelectorAll('#dash-panels .dash-action-panel').forEach(node => {
        const inst = node.dataset.instance;
        const p = byId.get(inst);
        if (p) ordered.push(p);
    });
    if (ordered.length !== panels.length) {
        // Something out of sync — refetch and remount.
        mountPanels(el);
        return;
    }
    _scheduleSave(ordered);
}

async function _deletePanel(instance_id) {
    const el = _editEl || document.querySelector('[data-view="settings"]') || document;
    const panels = await _fetchUserPanels();
    const next = panels.filter(p => p.instance_id !== instance_id);
    if (next.length === panels.length) return;
    try {
        await _saveUserPanels(next);  // immediate save, not debounced — user-visible action
        await mountPanels(el);
        // After remount, restore edit-mode visuals (panels are fresh DOM).
        if (el.querySelector('.dash-root')?.classList.contains('dashboard-editing')) {
            _initSortable(el);
        }
    } catch (e) {
        ui.showToast(`Delete failed: ${e.message}`, 'error');
    }
}

// =============================================================================
// WIDGET SETTINGS MODAL — builds a form from the widget's settings_schema,
// saves to the panel's per-instance settings via PUT /api/dashboard/widgets.
// Auto-attached to the Actions dropdown when a widget declares a schema.
// =============================================================================

async function openWidgetSettings(el, instance_id) {
    const panels = await _fetchUserPanels();
    const panel = panels.find(p => p.instance_id === instance_id);
    if (!panel) {
        ui.showToast('Widget not found', 'error');
        return;
    }
    const schema = panel.settings_schema || [];
    if (schema.length === 0) {
        ui.showToast('This widget has no settings', 'error');
        return;
    }

    const existing = panel.settings || {};
    let backdrop = document.querySelector('.dash-picker-backdrop');
    if (backdrop) backdrop.remove();

    backdrop = document.createElement('div');
    backdrop.className = 'dash-picker-backdrop';
    backdrop.innerHTML = `
        <div class="dash-picker" role="dialog" aria-modal="true">
            <div class="dash-picker-header">
                <h3>${_esc(panel.name || panel.widget_id)} — settings</h3>
                <button class="dash-picker-close" title="Close">×</button>
            </div>
            <div class="dash-picker-body" style="padding:16px"></div>
            <div class="dash-picker-footer">
                <a class="dash-picker-cancel">Cancel</a>
                <button class="dash-picker-add" data-act="save">Save</button>
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);

    const close = () => backdrop.remove();
    backdrop.querySelector('.dash-picker-close').addEventListener('click', close);
    backdrop.querySelector('.dash-picker-cancel').addEventListener('click', close);
    backdrop.addEventListener('click', e => { if (e.target === backdrop) close(); });
    document.addEventListener('keydown', function escClose(ev) {
        if (ev.key === 'Escape') { close(); document.removeEventListener('keydown', escClose); }
    });

    const body = backdrop.querySelector('.dash-picker-body');
    const fieldNodes = {};

    for (const field of schema) {
        // Per-field try/catch so a single malformed schema entry doesn't
        // tank the whole modal. Plugin authors WILL ship bad schemas;
        // we render what we can and skip the broken entries.
        try {
        const wrap = document.createElement('label');
        wrap.style.cssText = 'display:flex;flex-direction:column;gap:4px;margin-bottom:14px';
        const label = document.createElement('span');
        label.style.cssText = 'font-size:var(--font-sm);color:var(--text-muted)';
        label.textContent = field.label || field.key;
        wrap.appendChild(label);

        const cur = existing[field.key] !== undefined ? existing[field.key] : field.default;
        let input;
        switch (field.type) {
            case 'textarea': {
                input = document.createElement('textarea');
                input.rows = field.rows || 3;
                input.style.cssText = 'width:100%;padding:8px 10px;background:var(--bg-tertiary,#2c2c2c);border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:inherit;font-size:0.8125rem;resize:vertical';
                input.value = (cur ?? '');
                break;
            }
            case 'select': {
                input = document.createElement('select');
                input.style.cssText = 'width:100%;padding:6px 10px;background:var(--bg-tertiary,#2c2c2c);border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:inherit;font-size:0.8125rem';
                for (const opt of (field.options || [])) {
                    const o = document.createElement('option');
                    o.value = opt.value;
                    o.textContent = opt.label || opt.value;
                    if (String(cur) === String(opt.value)) o.selected = true;
                    input.appendChild(o);
                }
                break;
            }
            case 'number': {
                input = document.createElement('input');
                input.type = 'number';
                input.style.cssText = 'width:100%;padding:6px 10px;background:var(--bg-tertiary,#2c2c2c);border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:inherit;font-size:0.8125rem';
                if (typeof cur === 'number') input.value = cur;
                if (field.min !== undefined) input.min = field.min;
                if (field.max !== undefined) input.max = field.max;
                if (field.step !== undefined) input.step = field.step;
                break;
            }
            case 'boolean': {
                input = document.createElement('input');
                input.type = 'checkbox';
                input.checked = !!cur;
                input.style.cssText = 'width:auto;margin-top:4px';
                break;
            }
            case 'color': {
                input = document.createElement('input');
                input.type = 'color';
                input.value = cur || '#4a9eff';
                input.style.cssText = 'width:60px;height:30px;padding:0;border:1px solid var(--border);border-radius:6px;background:transparent';
                break;
            }
            case 'text':
            default: {
                input = document.createElement('input');
                input.type = 'text';
                input.style.cssText = 'width:100%;padding:6px 10px;background:var(--bg-tertiary,#2c2c2c);border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:inherit;font-size:0.8125rem';
                input.value = (cur ?? '');
                break;
            }
        }
        wrap.appendChild(input);

        if (field.help) {
            const help = document.createElement('span');
            help.style.cssText = 'font-size:var(--font-xs);color:var(--text-dim);margin-top:2px';
            help.textContent = field.help;
            wrap.appendChild(help);
        }

        body.appendChild(wrap);
        // Skip fields with no key — they'd write to fieldNodes[undefined]
        // and clobber each other on save. Logged so plugin authors notice.
        if (!field.key) {
            console.warn('[widget settings] field has no `key`, skipping:', field);
            continue;
        }
        if (fieldNodes[field.key]) {
            console.warn(`[widget settings] duplicate field key '${field.key}', last wins`);
        }
        fieldNodes[field.key] = { input, type: field.type };
        } catch (e) {
            console.warn('[widget settings] field render failed, skipping:', field, e);
        }
    }

    backdrop.querySelector('[data-act="save"]').addEventListener('click', async () => {
        const newSettings = { ...existing };
        for (const [key, { input, type }] of Object.entries(fieldNodes)) {
            if (type === 'boolean') {
                newSettings[key] = input.checked;
            } else if (type === 'number') {
                newSettings[key] = input.value === '' ? null : Number(input.value);
            } else {
                newSettings[key] = input.value;
            }
        }
        // Replace the panel's settings in the user's list.
        const next = panels.map(p =>
            p.instance_id === instance_id ? { ...p, settings: newSettings } : p);
        try {
            await _saveUserPanels(next);
            ui.showToast('Settings saved', 'success');
            close();
            await mountPanels(el);
            if (el.querySelector('.dash-root')?.classList.contains('dashboard-editing')) {
                _initSortable(el);
            }
        } catch (e) {
            ui.showToast(`Save failed: ${e.message}`, 'error');
        }
    });
}


async function _resizePanel(instance_id, size) {
    const el = _editEl || document.querySelector('[data-view="settings"]') || document;
    const panels = await _fetchUserPanels();
    const target = panels.find(p => p.instance_id === instance_id);
    if (!target || target.size === size) return;
    target.size = size;
    try {
        await _saveUserPanels(panels);
        await mountPanels(el);
        if (el.querySelector('.dash-root')?.classList.contains('dashboard-editing')) {
            _initSortable(el);
        }
    } catch (e) {
        ui.showToast(`Resize failed: ${e.message}`, 'error');
    }
}

async function mountPanels(el) {
    const container = el.querySelector('#dash-panels');
    if (!container) return;
    // Tear down any previously-mounted panels first.
    for (const p of _panelRegistry) {
        try { p.cleanup?.(); } catch (e) { console.warn('panel cleanup', p.instance_id, e); }
    }
    _panelRegistry = [];
    container.innerHTML = '';

    const panels = await _fetchUserPanels();
    if (panels.length === 0) {
        container.innerHTML = `<span class="dim" style="font-size:0.8125rem;padding:14px">Your command center is empty. Click <strong>+ Add</strong> above to get started.</span>`;
        return;
    }

    // Shared API surface passed to each widget via ctx.api. Plugin widgets
    // receive the same shape so they only need to learn one contract.
    const api = {
        fetch: (url, init) => window.fetch(url, init),
        toast: (msg, kind) => ui.showToast(msg, kind),
        listStorePlugins,
        pollForRestart: () => setTimeout(() => pollForRestart(), 2000),
        navigateSettingsTab: (tab) => {
            const settingsView = el.closest('.settings-view') || el.closest('[data-view="settings"]');
            if (settingsView) {
                settingsView.dispatchEvent(new CustomEvent('settings-navigate', { detail: { tab }, bubbles: true }));
            }
        },
        openWidgetSettings: (instance_id) => openWidgetSettings(el, instance_id),
    };

    const v = (window.__appVersion || 'dev');

    for (const panel of panels) {
        const wrapper = _buildPanelChrome(panel, panel.sizes || ['1x1']);
        container.appendChild(wrapper);
        const bodyEl = wrapper.querySelector('.dash-action-panel-info');
        const titleEl = wrapper.querySelector('.dash-action-panel-title');
        const menu = wrapper.querySelector('.dash-action-dropdown-menu');

        // Plugin uninstalled but still in user's list — render placeholder.
        if (!panel.available) {
            titleEl.textContent = panel.name || panel.widget_id;
            bodyEl.innerHTML = `<div class="dash-action-panel-info-line"><span class="dim">(${_esc(panel.plugin)} unavailable)</span></div>`;
            wrapper.querySelector('.dash-action-dropdown')?.remove();
            continue;
        }

        // Per-panel cleanup list. Widgets can call ctx.api.registerCleanup(fn)
        // BEFORE scheduling work — that way cleanup runs even if render()
        // throws after starting timers/listeners. Also captures the
        // `cleanup` returned at the end of a successful render. 2026-05-07.
        const cleanups = [];
        const ctx = {
            plugin: panel.plugin,
            widget_id: panel.widget_id,
            instance_id: panel.instance_id,
            size: panel.size,
            settings: panel.settings || {},
            pluginWebPath: panel.plugin === 'core' ? '/core-widgets/' : `/plugin-web/${panel.plugin}/`,
            api: {
                ...api,
                registerCleanup: (fn) => {
                    if (typeof fn === 'function') cleanups.push(fn);
                },
            },
        };

        try {
            const module = await import(`${panel.render_url}?v=${encodeURIComponent(v)}`);
            const result = await module.render(bodyEl, ctx);
            if (typeof result?.cleanup === 'function') cleanups.push(result.cleanup);
            titleEl.textContent = result?.title || panel.name || panel.widget_id;

            const allActions = [...(result?.actions || [])];
            // Auto-append "⚙ Settings..." when the widget declares a schema.
            // Plugin authors don't have to wire this themselves.
            if ((panel.settings_schema || []).length > 0) {
                allActions.push({
                    icon: '⚙',
                    label: 'Settings...',
                    onClick: () => openWidgetSettings(el, panel.instance_id),
                });
            }
            allActions.forEach(a => {
                const btn = document.createElement('button');
                if (a.kind) btn.className = a.kind;
                if (a.icon) {
                    const ic = document.createElement('span');
                    ic.className = 'action-icon';
                    ic.textContent = a.icon;
                    btn.appendChild(ic);
                    btn.appendChild(document.createTextNode(' '));
                }
                btn.appendChild(document.createTextNode(a.label || ''));
                if (typeof a.onClick === 'function') btn.addEventListener('click', a.onClick);
                menu.appendChild(btn);
            });

            _panelRegistry.push({
                instance_id: panel.instance_id,
                cleanup: () => {
                    for (const fn of cleanups) {
                        try { fn(); } catch (err) { console.warn('panel cleanup', panel.instance_id, err); }
                    }
                },
            });
        } catch (e) {
            console.warn(`[panel ${panel.plugin}.${panel.widget_id}] render failed`, e);
            // Even though render threw, run any cleanups it managed to
            // register before the throw. Otherwise leaked intervals /
            // listeners accumulate across remount cycles.
            for (const fn of cleanups) {
                try { fn(); } catch {}
            }
            titleEl.textContent = panel.name || panel.widget_id;
            bodyEl.innerHTML = `<div class="dash-action-panel-info-line"><span class="dim">render failed: ${_esc(e?.message || String(e))}</span></div>`;
        }
    }

    // After Maintenance widget mounts (which creates #mnt-status), make
    // sure the status word picks up the current mood color.
    _setMood(el, el.querySelector('#dash-orb')?.getAttribute('data-mood') || 'healthy');
}


// =============================================================================
// PICKER MODAL — choose a widget to add
// =============================================================================

async function openPicker(el) {
    let backdrop = document.querySelector('.dash-picker-backdrop');
    if (backdrop) backdrop.remove();

    backdrop = document.createElement('div');
    backdrop.className = 'dash-picker-backdrop';
    backdrop.innerHTML = `
        <div class="dash-picker" role="dialog" aria-modal="true">
            <div class="dash-picker-header">
                <h3>Add a widget</h3>
                <button class="dash-picker-close" title="Close">×</button>
            </div>
            <div class="dash-picker-body">
                <span class="dim" style="display:block;padding:14px;font-size:var(--font-sm)">Loading...</span>
            </div>
            <div class="dash-picker-footer">
                <a class="dash-picker-restore">Restore defaults</a>
                <span class="dim" style="font-size:var(--font-xs)">click Add on any row</span>
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);

    const close = () => backdrop.remove();
    backdrop.querySelector('.dash-picker-close').addEventListener('click', close);
    backdrop.addEventListener('click', e => { if (e.target === backdrop) close(); });
    document.addEventListener('keydown', function escClose(ev) {
        if (ev.key === 'Escape') { close(); document.removeEventListener('keydown', escClose); }
    });

    const body = backdrop.querySelector('.dash-picker-body');
    let available = [];
    let installed = [];
    try {
        const [availRes, instRes] = await Promise.all([
            fetch('/api/dashboard/widgets/available'),
            fetch('/api/dashboard/widgets'),
        ]);
        available = (await availRes.json()).widgets || [];
        installed = (await instRes.json()).panels || [];
    } catch {
        body.innerHTML = '<span class="dim" style="display:block;padding:14px">Could not load widget catalog.</span>';
        return;
    }

    const installedKeys = new Set(installed.map(p => `${p.plugin}.${p.widget_id}`));

    // Group by plugin: core first, then alphabetical.
    const grouped = {};
    for (const w of available) {
        (grouped[w.plugin] = grouped[w.plugin] || []).push(w);
    }
    const pluginOrder = Object.keys(grouped).sort((a, b) => {
        if (a === 'core') return -1;
        if (b === 'core') return 1;
        return a.localeCompare(b);
    });

    body.innerHTML = '';
    for (const plug of pluginOrder) {
        const groupTitle = document.createElement('div');
        groupTitle.className = 'dash-picker-group-title';
        groupTitle.textContent = plug === 'core' ? 'Built-in' : plug;
        body.appendChild(groupTitle);
        for (const w of grouped[plug]) {
            const key = `${w.plugin}.${w.widget_id}`;
            const row = document.createElement('div');
            row.className = 'dash-picker-row';
            const sizes = (w.sizes || ['1x1']).map(s => `<span class="dash-picker-size-pill">${_esc(s)}</span>`).join('');
            const isInstalled = installedKeys.has(key);
            const btnLabel = isInstalled
                ? (w.multi_instance ? 'Add another' : 'Added')
                : 'Add';
            const btnDisabled = isInstalled && !w.multi_instance;
            row.innerHTML = `
                <div class="dash-picker-icon">${_esc(w.icon || '\u{25A2}')}</div>
                <div class="dash-picker-meta">
                    <div class="dash-picker-name">${_esc(w.name)}</div>
                    ${w.description ? `<div class="dash-picker-desc">${_esc(w.description)}</div>` : ''}
                    <div class="dash-picker-sizes">${sizes}</div>
                </div>
                <button class="dash-picker-add" ${btnDisabled ? 'disabled' : ''}>${_esc(btnLabel)}</button>
            `;
            row.querySelector('.dash-picker-add').addEventListener('click', async () => {
                if (btnDisabled) return;
                // Refetch the current panel list rather than using the stale
                // closure-captured `installed`. Defends against rapid Add
                // clicks (multi_instance) and multi-tab edits.
                let current;
                try { current = await _fetchUserPanels(); }
                catch { current = installed; }
                const updated = current.concat([{
                    instance_id: 'i' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                    plugin: w.plugin,
                    widget_id: w.widget_id,
                    size: w.default_size || '1x1',
                    settings: {},
                }]);
                try {
                    await _saveUserPanels(updated);
                    ui.showToast(`Added ${w.name}`, 'success');
                    close();
                    await mountPanels(el);
                    // Re-init drag if edit mode is active — mountPanels
                    // rebuilds DOM so Sortable's binding is gone.
                    if (el.querySelector('.dash-root')?.classList.contains('dashboard-editing')) {
                        _initSortable(el);
                    }
                } catch (e) {
                    ui.showToast(`Failed to add: ${e.message}`, 'error');
                }
            });
            body.appendChild(row);
        }
    }

    backdrop.querySelector('.dash-picker-restore').addEventListener('click', async () => {
        const defaults = [
            { plugin: 'core', widget_id: 'system', size: '1x1' },
            { plugin: 'core', widget_id: 'updates', size: '1x1' },
            { plugin: 'core', widget_id: 'backups', size: '1x1' },
            { plugin: 'core', widget_id: 'maintenance', size: '1x1' },
            { plugin: 'core', widget_id: 'mini-spotlight', size: '1x1' },
        ];
        // Refetch current state — `installed` may be stale.
        let current;
        try { current = await _fetchUserPanels(); }
        catch { current = installed; }
        const next = current.slice();
        for (const d of defaults) {
            if (!next.some(p => p.plugin === d.plugin && p.widget_id === d.widget_id)) {
                next.push({
                    instance_id: 'i' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
                    ...d, settings: {},
                });
            }
        }
        try {
            await _saveUserPanels(next);
            ui.showToast('Defaults restored', 'success');
            close();
            await mountPanels(el);
            if (el.querySelector('.dash-root')?.classList.contains('dashboard-editing')) {
                _initSortable(el);
            }
        } catch (e) {
            ui.showToast(`Failed: ${e.message}`, 'error');
        }
    });
}


// =============================================================================
// DATA LOADERS
// =============================================================================

// Hero-level system-info fetch — used for mood derivation (disk %) and
// display name sync. Per-panel data fetches now live in widget render
// modules. There's a small duplicate-fetch cost (System and Maintenance
// widgets call this same endpoint) but in V2 we'll add a shared signals
// API so widgets contribute without re-querying.
async function loadSystemInfo(el) {
    const nameEl = el.querySelector('#dash-hero-name');
    try {
        const res = await fetch('/api/dashboard/system-info');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        if (typeof d.disk_pct === 'number') {
            _moodSignals.diskPct = d.disk_pct;
            _refreshMood(el);
        }
        // Only sync from backend if the user isn't actively editing —
        // otherwise the late-arriving system-info clobbers their typed text.
        if (d.display_name && nameEl && nameEl.textContent.trim() !== d.display_name
            && document.activeElement !== nameEl) {
            nameEl.textContent = d.display_name;
            try { localStorage.setItem('sapphireDisplayName', d.display_name); } catch (e2) { /* ignore */ }
        }
    } catch { /* widgets show their own errors; mood stays at last known */ }
}

// Component status cache — lets the mood derivation read the latest snapshot.
let _componentStatus = { emb: 'idle', tts: 'idle', stt: 'idle', ww: 'idle' };

async function loadComponentStatus(el) {
    try {
        const res = await fetch('/api/dashboard/component-status');
        if (!res.ok) throw new Error('component-status failed');
        const d = await res.json();
        _componentStatus = {
            emb: d.emb || 'idle',
            tts: d.tts || 'idle',
            stt: d.stt || 'idle',
            ww:  d.ww  || 'idle',
        };
        Object.entries(_componentStatus).forEach(([k, v]) => _setComponentDot(el, k, v));
    } catch {
        // Endpoint failed — paint all warn so the user sees something's off.
        ['emb', 'tts', 'stt', 'ww'].forEach(k => _setComponentDot(el, k, 'warn'));
        _componentStatus = { emb: 'warn', tts: 'warn', stt: 'warn', ww: 'warn' };
    }
    _refreshMood(el);
}

// Derive an aggregate mood from component status + update availability +
// disk usage. Called after each signal updates so the orb reflects the
// freshest picture. Idle components are benign — they just mean the
// subsystem is configured off, not broken.
let _moodSignals = {
    updateAvailable: false,
    pluginUpdatesCount: 0,
    diskPct: 0,
};
function _refreshMood(el) {
    const statuses = Object.values(_componentStatus);
    let mood = 'healthy';
    if (statuses.includes('error')) {
        mood = 'error';
    } else if (
        statuses.includes('warn') ||
        _moodSignals.updateAvailable ||
        _moodSignals.pluginUpdatesCount > 0 ||
        _moodSignals.diskPct > 92
    ) {
        mood = 'warn';
    }
    _setMood(el, mood);
}

function _setComponentDot(el, key, status) {
    const pill = el.querySelector(`.dash-cs-pill[data-cs="${key}"]`);
    if (!pill) return;
    const dot = pill.querySelector('.dash-cs-dot');
    if (dot) dot.className = `dash-cs-dot ${status}`;
    pill.title = `${key.toUpperCase()}: ${status}`;
}


// =============================================================================
// UPDATES
// =============================================================================

// Hero-level update check — for mood signals + branch label only. The
// Updates panel widget renders the "current/available · vX · Xh ago"
// status line itself.
async function checkForUpdate(el, retry = 0) {
    try {
        const res = await fetch('/api/system/update-check');
        if (!res.ok) throw new Error('Check failed');
        updateStatus = await res.json();
        if (!updateStatus.last_check && retry < 3) {
            setTimeout(() => checkForUpdate(el, retry + 1), 2000);
            return;
        }
        // Branch label appears in the meta line under Sapphire's name.
        const branchEl = el.querySelector('#dash-branch');
        if (branchEl && updateStatus.branch) {
            const tag = updateStatus.is_fork ? `${updateStatus.branch} · fork` : updateStatus.branch;
            // textContent needs the RAW string — escaping here rendered
            // literal &amp;/&quot; for branch names (scout, 2026-07-20).
            branchEl.textContent = `· ${tag}`;
        }
        if (updateStatus.available) {
            window.dispatchEvent(new CustomEvent('update-available', { detail: updateStatus }));
            _moodSignals.updateAvailable = true;
        } else {
            _moodSignals.updateAvailable = false;
        }
        _refreshMood(el);
    } catch { /* widget shows the error in its own line */ }
}

function pollForRestart() {
    let attempts = 0;
    const maxAttempts = 300;
    const poll = async () => {
        attempts++;
        try {
            const res = await fetch('/api/health');
            if (res.ok) { window.location.reload(); return; }
        } catch {}
        if (attempts < maxAttempts) setTimeout(poll, 1000);
    };
    poll();
}

async function checkLastUpdateResult() {
    try {
        const res = await fetch('/api/system/last-update-result');
        if (!res.ok) return;
        const data = await res.json();
        const r = data.result;
        if (!r) return;
        if (r.success) {
            ui.showToast(r.message || 'Update applied', 'success');
        } else {
            ui.showToast(`Update did NOT apply: ${r.message}`, 'error');
        }
    } catch {}
}


// =============================================================================
// PLUGIN SPOTLIGHT — community shoutouts from sapphireblue.dev (lower content row)
// =============================================================================

async function loadPluginSpotlight(el) {
    const card = el.querySelector('#dash-spotlight-card');
    if (!card) return;
    let data;
    try {
        data = await listStorePlugins({ featured: true, perPage: 5 });
    } catch (e) {
        card.style.display = 'none';
        return;
    }
    const items = (data && data.items) || [];
    if (!items.length || data.unreachable) {
        card.style.display = 'none';
        return;
    }

    // Mood signal — partial (only covers featured plugins). The Updates
    // widget surfaces the same number visually; this just feeds the orb.
    const updateCount = items.filter(i => i.installed_state === 'update_available').length;
    _moodSignals.pluginUpdatesCount = updateCount;
    _refreshMood(el);

    const list = card.querySelector('#dash-recommended-list');
    if (!list) return;
    list.innerHTML = items.map(item => {
        const author = (item.author_url && isSafeHref(item.author_url))
            ? `<a href="${_esc(item.author_url)}" target="_blank" rel="noopener noreferrer">${_esc(item.author || 'Unknown')}</a>`
            : _esc(item.author || 'Unknown');
        const installed = item.installed_state === 'current'
            ? '<span class="dash-rec-installed">installed</span>'
            : item.installed_state === 'update_available'
                ? '<span class="dash-rec-update">update available</span>'
                : '';
        return `
            <button class="dash-rec-tile" data-slug="${_esc(item.slug)}" title="Open ${_esc(item.name)} in the Store">
                <div class="dash-rec-name">${_esc(item.name)} ${installed}</div>
                <div class="dash-rec-author">by ${author}</div>
                <div class="dash-rec-desc">${_esc(item.description || '')}</div>
            </button>`;
    }).join('');
    card.style.display = '';
}


// =============================================================================
// MISSING DEPENDENCIES
// =============================================================================

async function loadMissingDeps(el, ctx) {
    const card = el.querySelector('#dash-deps-card');
    const list = el.querySelector('#dash-deps-list');
    if (!card || !list) return;

    try {
        const res = await fetch('/api/webui/plugins');
        if (!res.ok) return;
        const data = await res.json();
        const withDeps = (data.plugins || []).filter(p => p.missing_deps?.length);
        if (!withDeps.length) {
            card.style.display = 'none';
            return;
        }
        card.style.display = '';
        list.innerHTML = withDeps.map(p => `
            <div style="display:flex;align-items:center;gap:8px;padding:4px 0;">
                <span>${_esc(p.icon || '🔌')}</span>
                <span style="flex:1"><strong>${_esc(p.title || p.name)}</strong> needs: ${_esc(p.missing_deps.join(', '))}</span>
                <button class="btn btn-sm dash-deps-fix" data-plugin="${_esc(p.name)}"
                    style="font-size:0.75em;padding:2px 10px;background:rgba(255,165,0,0.2);border:1px solid rgba(255,165,0,0.4);color:#e0a030;cursor:pointer;border-radius:var(--radius-sm)">
                    Fix
                </button>
            </div>
        `).join('');
        list.querySelectorAll('.dash-deps-fix').forEach(btn => {
            btn.addEventListener('click', () => {
                const settingsView = el.closest('.settings-view') || el.closest('[data-view="settings"]');
                if (settingsView) {
                    settingsView.dispatchEvent(new CustomEvent('settings-navigate', { detail: { tab: 'plugins' }, bubbles: true }));
                }
            });
        });
    } catch { card.style.display = 'none'; }
}


// =============================================================================
// TOKEN METRICS
// =============================================================================

const fmt = n => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
};

async function loadMetrics(el) {
    const metricsEl = el.querySelector('#dash-metrics');
    const cb = el.querySelector('#metrics-enabled-cb');
    if (!metricsEl) return;

    try {
        const toggleRes = await fetch('/api/metrics/enabled');
        if (toggleRes.ok) {
            const { enabled } = await toggleRes.json();
            if (cb) cb.checked = enabled;
        }
    } catch {}

    if (cb) {
        cb.addEventListener('change', async () => {
            try {
                await fetch('/api/metrics/enabled', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: cb.checked })
                });
                loadMetricsData(metricsEl, cb.checked);
            } catch { cb.checked = !cb.checked; }
        });
    }
    loadMetricsData(metricsEl, cb?.checked !== false);
}

async function loadMetricsData(el, enabled) {
    if (!enabled) {
        el.innerHTML = '<span class="text-muted">Metrics tracking is off. Per-message stats still show in chat.</span>';
        return;
    }
    try {
        const [sumRes, brkRes, dailyRes] = await Promise.all([
            fetch('/api/metrics/summary?days=30'),
            fetch('/api/metrics/breakdown?days=30'),
            fetch('/api/metrics/daily?days=30')
        ]);
        if (!sumRes.ok || !brkRes.ok || !dailyRes.ok) throw new Error('Metrics fetch failed');
        const summary = await sumRes.json();
        const breakdown = await brkRes.json();
        const daily = await dailyRes.json();
        renderMetrics(el, summary, breakdown.models || [], daily.daily || []);
    } catch (e) {
        el.innerHTML = '<span class="text-muted">No metrics data yet — send some messages to start collecting</span>';
    }
}

function renderMetrics(el, s, models, daily) {
    if (!s.total_calls) {
        el.innerHTML = '<span class="text-muted">No data yet — metrics start recording from this version</span>';
        return;
    }
    const totalInput = (s.total_prompt || 0) + (s.total_cache_read || 0);
    const cacheRate = totalInput > 0 && s.total_cache_read > 0
        ? Math.round((s.total_cache_read / totalInput) * 100) : null;

    el.innerHTML = `
        <div class="metrics-stats">
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_calls)}</div>
                <div class="metric-label">LLM Calls</div>
            </div>
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_tokens)}</div>
                <div class="metric-label">Total Tokens</div>
            </div>
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_prompt)}</div>
                <div class="metric-label">Input</div>
            </div>
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_completion)}</div>
                <div class="metric-label">Output</div>
            </div>
            ${s.total_thinking > 0 ? `
            <div class="metric-item">
                <div class="metric-value">${fmt(s.total_thinking)}</div>
                <div class="metric-label">Thinking</div>
            </div>` : ''}
            ${cacheRate !== null ? `
            <div class="metric-item">
                <div class="metric-value">${cacheRate}%</div>
                <div class="metric-label">Cache Hit</div>
            </div>` : ''}
        </div>
        <div class="metrics-charts">
            <div class="metrics-chart-container">
                <div class="chart-title">Daily Usage</div>
                <div id="chart-daily" class="chart-area"></div>
            </div>
            <div class="metrics-chart-container">
                <div class="chart-title">Models</div>
                <div id="chart-models" class="chart-area"></div>
            </div>
        </div>
    `;
    renderDailyChart(el.querySelector('#chart-daily'), daily);
    renderModelChart(el.querySelector('#chart-models'), models);
}


// =============================================================================
// SVG CHARTS (unchanged from prior version)
// =============================================================================

function renderDailyChart(el, daily) {
    if (!el || daily.length < 2) {
        if (el) el.innerHTML = '<span class="text-muted" style="font-size:var(--font-xs)">Need 2+ days of data</span>';
        return;
    }
    const W = 540, H = 120, PAD_L = 40, PAD_R = 8, PAD_T = 8, PAD_B = 20;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;
    const maxTokens = Math.max(...daily.map(d => d.tokens)) || 1;
    const points = daily.map((d, i) => {
        const x = PAD_L + (i / (daily.length - 1)) * chartW;
        const y = PAD_T + chartH - (d.tokens / maxTokens) * chartH;
        return { x, y, ...d };
    });
    const polyline = points.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
    const areaPoints = `${PAD_L},${PAD_T + chartH} ${polyline} ${points[points.length - 1].x.toFixed(1)},${PAD_T + chartH}`;
    const yMid = fmt(Math.round(maxTokens / 2));
    const yMax = fmt(maxTokens);
    const firstDate = daily[0].date.slice(5);
    const lastDate = daily[daily.length - 1].date.slice(5);
    const dots = points.map(p =>
        `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" class="chart-dot">
            <title>${p.date}: ${fmt(p.tokens)} tokens, ${p.calls} calls</title>
        </circle>`
    ).join('');
    el.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" class="chart-svg">
            <line x1="${PAD_L}" y1="${PAD_T}" x2="${PAD_L + chartW}" y2="${PAD_T}" class="chart-grid"/>
            <line x1="${PAD_L}" y1="${PAD_T + chartH / 2}" x2="${PAD_L + chartW}" y2="${PAD_T + chartH / 2}" class="chart-grid"/>
            <line x1="${PAD_L}" y1="${PAD_T + chartH}" x2="${PAD_L + chartW}" y2="${PAD_T + chartH}" class="chart-grid"/>
            <text x="${PAD_L - 4}" y="${PAD_T + 4}" class="chart-label" text-anchor="end">${yMax}</text>
            <text x="${PAD_L - 4}" y="${PAD_T + chartH / 2 + 3}" class="chart-label" text-anchor="end">${yMid}</text>
            <text x="${PAD_L - 4}" y="${PAD_T + chartH + 3}" class="chart-label" text-anchor="end">0</text>
            <text x="${PAD_L}" y="${H - 2}" class="chart-label">${firstDate}</text>
            <text x="${PAD_L + chartW}" y="${H - 2}" class="chart-label" text-anchor="end">${lastDate}</text>
            <polygon points="${areaPoints}" class="chart-area-fill"/>
            <polyline points="${polyline}" class="chart-line"/>
            ${dots}
        </svg>
    `;
}

function renderModelChart(el, models) {
    if (!el || !models.length) {
        if (el) el.innerHTML = '<span class="text-muted" style="font-size:var(--font-xs)">No model data yet</span>';
        return;
    }
    const top = models.slice(0, 5);
    const maxTotal = Math.max(...top.map(m => m.total)) || 1;
    const BAR_H = 18, GAP = 6, LABEL_W = 100, BAR_AREA = 370, PAD_R = 70;
    const W = LABEL_W + BAR_AREA + PAD_R;
    const H = top.length * (BAR_H + GAP) + GAP;
    const bars = top.map((m, i) => {
        const y = GAP + i * (BAR_H + GAP);
        const barW = Math.max(2, (m.total / maxTotal) * BAR_AREA);
        const label = m.model.length > 14 ? m.model.slice(0, 13) + '…' : m.model;
        const totalPrompt = (m.prompt || 0) + (m.cache_read || 0);
        const cacheInfo = m.cache_read > 0 && totalPrompt > 0
            ? ` · cache ${Math.round((m.cache_read / totalPrompt) * 100)}%` : '';
        return `
            <text x="${LABEL_W - 4}" y="${y + BAR_H / 2 + 4}" class="chart-label" text-anchor="end">${label}</text>
            <rect x="${LABEL_W}" y="${y}" width="${barW.toFixed(1)}" height="${BAR_H}" class="chart-bar" rx="2">
                <title>${m.model}: ${fmt(m.total)} tokens, ${m.calls} calls${cacheInfo}</title>
            </rect>
            <text x="${LABEL_W + barW + 4}" y="${y + BAR_H / 2 + 4}" class="chart-label">${fmt(m.total)}${cacheInfo}</text>
        `;
    }).join('');
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="chart-svg">${bars}</svg>`;
}
