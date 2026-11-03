// views/apps.js — Plugin apps grid + app host. Each tile's 📌 pins the app on
// the Apps menu (the rail's Apps flyout, core/plugin-apps.js); the server
// keeps the choice (PUT /api/apps/pins) and answers with the fresh list.
import { fetchWithTimeout } from '../shared/fetch.js';
import { registerView, switchView } from '../core/router.js';
import { syncAppsMenu } from '../core/plugin-apps.js';

let appsData = [];
let activeApp = null;
let activeCleanup = null;
// Visibility epoch — same contract as main.js's app host: a click-away can
// land between openApp starting and the module resolving; without this the
// render finished into a stale view and a late cleanup fired against the
// next app's state (post-fix review 2026-08-05, finding 1.3's sibling).
let openEpoch = 0;

function _esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

async function loadApps() {
    try {
        const data = await fetchWithTimeout('/api/apps');
        appsData = (data.apps || []).filter(a => (a.places || ['apps']).includes('apps'));   // a stage-only app has no tile
    } catch (e) {
        console.warn('[Apps] Failed to load apps:', e);
        appsData = [];
    }
}

function renderGrid(container) {
    const ghostTile = `
        <button class="app-tile app-tile-ghost" data-action="get-more">
            <span class="app-tile-icon">+</span>
            <span class="app-tile-label">Get More Apps</span>
        </button>`;

    container.innerHTML = `
        <div class="apps-page">
            <div class="apps-header">
                <h2>Apps</h2>
            </div>
            <div class="apps-grid">
                ${appsData.map(app => `
                    <div class="app-tile-wrap">
                        <button class="app-tile" data-app="${_esc(app.name)}">
                            <span class="app-tile-icon">${_esc(app.icon || '📦')}</span>
                            <span class="app-tile-label">${_esc(app.label)}</span>
                            ${app.description ? `<span class="app-tile-desc">${_esc(app.description)}</span>` : ''}
                        </button>
                        ${app.nav ? '' : `<button class="app-tile-pin${app.pinned ? ' pinned' : ''}" data-pin="${_esc(app.name)}" aria-pressed="${app.pinned ? 'true' : 'false'}" title="${app.pinned ? 'Unpin from the Apps menu' : 'Pin to the Apps menu'}">📌</button>`}
                    </div>
                `).join('')}
                ${ghostTile}
            </div>
        </div>`;

    container.querySelectorAll('.app-tile[data-app]').forEach(tile => {
        tile.addEventListener('click', () => openApp(tile.dataset.app, container));
    });

    container.querySelector('[data-action="get-more"]')?.addEventListener('click', () => switchView('store'));

    // An app with a rail or flyout home of its own has no pin (the server says so too).
    container.querySelectorAll('.app-tile-pin[data-pin]').forEach(btn => {
        btn.addEventListener('click', async () => {
            const name = btn.dataset.pin;
            const app = appsData.find(a => a.name === name);
            btn.disabled = true;
            try {
                const r = await fetch('/api/apps/pins', {
                    method: 'PUT', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' },
                    body: JSON.stringify({ name, pinned: !app?.pinned }),
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                appsData = (data.apps || []).filter(a => (a.places || ['apps']).includes('apps'));
                syncAppsMenu(appsData);
                renderGrid(container);
            } catch (e) {
                console.warn('[Apps] Pin failed:', e);
                btn.disabled = false;
            }
        });
    });
}

async function openApp(appName, container) {
    const app = appsData.find(a => a.name === appName);
    if (!app) return;

    // Clean up previous app
    if (activeCleanup) {
        try { activeCleanup(); } catch (e) { console.warn('[Apps] Cleanup error:', e); }
        activeCleanup = null;
    }

    const mine = ++openEpoch;
    activeApp = appName;
    const v = document.querySelector('meta[name="boot-version"]')?.content || '';

    container.innerHTML = `
        <div class="app-host">
            <div class="app-host-header">
                <button class="app-back-btn" title="Back to Apps">&larr;</button>
                <span class="app-host-title">${_esc(app.icon || '📦')} ${_esc(app.label)}</span>
            </div>
            <div class="app-host-content" id="app-content-${appName}"></div>
        </div>`;

    container.querySelector('.app-back-btn').addEventListener('click', () => {
        closeApp(container);
    });

    // Load the app's JS module
    const appContent = container.querySelector(`#app-content-${appName}`);
    let imported = null;
    try {
        imported = await import(`/plugin-web/${appName}/app/index.js?v=${v}`);
        if (mine !== openEpoch) return;          // navigated away while importing
        // Per-render cleanup closure preferred over the module-level export —
        // same reasoning as main.js: the module is memoized, so a superseded
        // render's module cleanup would act on the newer render's state.
        let ret = null;
        if (imported.render) {
            ret = await imported.render(appContent);
        }
        const fin = (typeof ret === 'function') ? ret : (imported.cleanup || null);
        if (mine !== openEpoch) {
            // Superseded mid-render: tear THIS render down, don't strand it.
            try { fin?.(); } catch (e) { console.warn('[Apps] Cleanup error:', e); }
            return;
        }
        if (fin) activeCleanup = fin;
    } catch (e) {
        if (mine !== openEpoch) return;
        // Render threw after possibly claiming shared DOM — release it.
        try { imported?.cleanup?.(); } catch {}
        console.error(`[Apps] Failed to load app '${appName}':`, e);
        appContent.innerHTML = `
            <div class="view-placeholder">
                <h2>Failed to load ${_esc(app.label)}</h2>
                <p style="color:var(--text-muted)">${_esc(e.message)}</p>
            </div>`;
    }

    history.replaceState(null, '', `#apps/${appName}`);
}

function closeApp(container) {
    openEpoch++;                       // cancels any in-flight openApp
    if (activeCleanup) {
        try { activeCleanup(); } catch (e) { console.warn('[Apps] Cleanup error:', e); }
        activeCleanup = null;
    }
    activeApp = null;
    renderGrid(container);
    history.replaceState(null, '', '#apps');
}

export default {
    init(el) {
        // Listen for nav clicks on the Apps item while already on Apps view
        // (switchView returns early when currentView === viewId, so show() doesn't fire)
        document.querySelector('[data-view="apps"]')?.addEventListener('click', () => {
            if (activeApp) {
                closeApp(document.getElementById('view-apps'));
            }
        });
    },

    async show() {
        const el = document.getElementById('view-apps');
        if (!el) return;
        await loadApps();

        // Check if URL has a specific app to open
        const hash = location.hash;
        const appMatch = hash.match(/^#apps\/(.+)$/);
        if (appMatch && appsData.find(a => a.name === appMatch[1])) {
            renderGrid(el);
            await openApp(appMatch[1], el);
        } else {
            renderGrid(el);
        }
    },

    hide() {
        openEpoch++;                   // cancels any in-flight openApp
        if (activeCleanup) {
            try { activeCleanup(); } catch (e) { console.warn('[Apps] Cleanup error:', e); }
            activeCleanup = null;
        }
        activeApp = null;
    }
};
