// core/router.js - Hash-based view router
// Switches views by toggling display on #view-{id} containers

const views = {};
let currentView = null;

// View groups: views that share a nav parent
const VIEW_GROUPS = {
    chat: ['chat', 'chat-manage'],
    personas: ['personas', 'prompts', 'toolsets', 'spices'],
    triggers: ['heartbeat', 'scheduled', 'daemons', 'realtime', 'webhooks'],
    mind: ['self', 'memories', 'people', 'knowledge', 'ai-knowledge', 'goals'],
    settings: ['settings', 'help', 'video-guide']
};

// Legacy id redirects: old #schedule bookmarks and the Triggers group parent
// (which has no view of its own) both land on Scheduled.
const VIEW_ALIASES = { schedule: 'scheduled', triggers: 'scheduled', mind: 'memories', 'ai-notes': 'ai-knowledge' };

// Reverse lookup: view -> group parent
const VIEW_TO_GROUP = {};
for (const [parent, members] of Object.entries(VIEW_GROUPS)) {
    for (const m of members) VIEW_TO_GROUP[m] = parent;
}

export function registerView(id, module, opts = {}) {
    views[id] = { module, initialized: false };
    // The router works by toggling #view-{id} containers. index.html bakes
    // them for core views only — registerView owns the rest of the contract,
    // so runtime registrations (plugin layers, apps) need exactly one call.
    if (!document.getElementById(`view-${id}`)) {
        const host = document.getElementById('app-content');
        if (host) {
            const el = document.createElement('div');
            el.id = `view-${id}`;
            el.className = 'view';
            el.style.display = 'none';
            host.appendChild(el);
        }
    }
    if (opts.group && VIEW_GROUPS[opts.group]) {
        if (!VIEW_GROUPS[opts.group].includes(id)) VIEW_GROUPS[opts.group].push(id);
        VIEW_TO_GROUP[id] = opts.group;
    }
}

export function switchView(viewId) {
    viewId = VIEW_ALIASES[viewId] || viewId;
    if (viewId === currentView) return;

    // Validate the target BEFORE hiding anything — a missing view must be a
    // loud no-op, never a blank screen.
    const entry = views[viewId];
    const el = document.getElementById(`view-${viewId}`);
    if (!entry || !el) {
        console.warn(`[Router] View '${viewId}' has no ${entry ? 'container' : 'registration'} — staying on '${currentView}'`);
        return;
    }

    // Hide current
    if (currentView && views[currentView]) {
        const oldEl = document.getElementById(`view-${currentView}`);
        if (oldEl) oldEl.style.display = 'none';
        views[currentView].module.hide?.();
    }

    el.style.display = '';

    // Lazy init on first show
    if (!entry.initialized) {
        entry.module.init?.(el);
        entry.initialized = true;
    }
    entry.module.show?.();

    currentView = viewId;

    // Update nav rail active state (group-aware)
    const groupParent = VIEW_TO_GROUP[viewId];
    document.querySelectorAll('.nav-item').forEach(btn => {
        const btnView = btn.dataset.view;
        const isActive = btnView === viewId || (groupParent ? btnView === groupParent : false);
        btn.classList.toggle('active', isActive);
    });

    // Update hash without triggering hashchange. Preserve nested routes
    // (e.g. #store/plugins/lattice-theme) — only force the hash if the
    // current hash's base view doesn't already match the target.
    const currentBase = (location.hash || '#').slice(1).split('/')[0];
    if (currentBase !== viewId) {
        history.replaceState(null, '', `#${viewId}`);
    }
}

export function getCurrentView() {
    return currentView;
}

export { VIEW_GROUPS, VIEW_TO_GROUP };

export function initRouter(defaultView = 'chat') {
    // Hide ALL views before first switch — prevents dual-display when hash
    // restores a non-default view (chat starts visible in HTML, others display:none)
    document.querySelectorAll('.view').forEach(v => { v.style.display = 'none'; });

    // Listen for hash changes (back/forward)
    window.addEventListener('hashchange', () => {
        const hash = location.hash.slice(1);
        // Support nested routes like #apps/mission-control → switch to 'apps' view
        const baseView = VIEW_ALIASES[hash.split('/')[0]] || hash.split('/')[0];
        if (baseView && views[baseView]) switchView(baseView);
    });

    // Initial route
    const hash = location.hash.slice(1);
    const baseView = VIEW_ALIASES[hash.split('/')[0]] || hash.split('/')[0];
    switchView((baseView && views[baseView]) ? baseView : defaultView);
}
