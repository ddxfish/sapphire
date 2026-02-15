// core/router.js - Hash-based view router
// Switches views by toggling display on #view-{id} containers

const views = {};
let currentView = null;

// View groups: views that share a nav parent
const VIEW_GROUPS = {
    personas: ['personas', 'prompts', 'toolsets', 'spices']
};

// Reverse lookup: view -> group parent
const VIEW_TO_GROUP = {};
for (const [parent, members] of Object.entries(VIEW_GROUPS)) {
    for (const m of members) VIEW_TO_GROUP[m] = parent;
}

export function registerView(id, module) {
    views[id] = { module, initialized: false };
}

export function switchView(viewId) {
    if (viewId === currentView) return;

    // Hide current
    if (currentView && views[currentView]) {
        const oldEl = document.getElementById(`view-${currentView}`);
        if (oldEl) oldEl.style.display = 'none';
        views[currentView].module.hide?.();
    }

    // Show target
    const entry = views[viewId];
    const el = document.getElementById(`view-${viewId}`);
    if (!entry || !el) return;

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

    // Update hash without triggering hashchange
    if (location.hash !== `#${viewId}`) {
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
        if (hash && views[hash]) switchView(hash);
    });

    // Initial route
    const hash = location.hash.slice(1);
    switchView((hash && views[hash]) ? hash : defaultView);
}
