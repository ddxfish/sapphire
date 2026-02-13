// core/router.js - Hash-based view router
// Switches views by toggling display on #view-{id} containers

const views = {};
let currentView = null;

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

    // Update nav rail active state
    document.querySelectorAll('.nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === viewId);
    });

    // Update hash without triggering hashchange
    if (location.hash !== `#${viewId}`) {
        history.replaceState(null, '', `#${viewId}`);
    }
}

export function getCurrentView() {
    return currentView;
}

export function initRouter(defaultView = 'chat') {
    // Listen for hash changes (back/forward)
    window.addEventListener('hashchange', () => {
        const hash = location.hash.slice(1);
        if (hash && views[hash]) switchView(hash);
    });

    // Initial route
    const hash = location.hash.slice(1);
    switchView((hash && views[hash]) ? hash : defaultView);
}
