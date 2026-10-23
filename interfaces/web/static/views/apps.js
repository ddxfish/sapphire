// views/apps.js — Plugin apps grid + app host
import { fetchWithTimeout } from '../shared/fetch.js';
import { registerView, switchView } from '../core/router.js';

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
        appsData = data.apps || [];
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
                    <button class="app-tile" data-app="${_esc(app.name)}">
                        <span class="app-tile-icon">${_esc(app.icon || '📦')}</span>
                        <span class="app-tile-label">${_esc(app.label)}</span>
                        ${app.description ? `<span class="app-tile-desc">${_esc(app.description)}</span>` : ''}
                    </button>
                `).join('')}
                ${ghostTile}
            </div>
        </div>`;

    container.querySelectorAll('.app-tile[data-app]').forEach(tile => {
        tile.addEventListener('click', () => openApp(tile.dataset.app, container));
    });

    container.querySelector('[data-action="get-more"]')?.addEventListener('click', () => switchView('store'));
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
