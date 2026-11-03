// core/plugin-apps.js - Plugin apps in the rail. A manifest `nav` buys an app
// a rail slot (`true`, capped) or a seat in a group's flyout (`"mind"`…). The
// Apps item's own flyout is the Apps menu: the apps the person pinned on the
// Apps page, plus the shipped ones that start pinned — /api/apps decides who
// is `pinned` (core/routes/plugins.py), this file only draws it.
import { registerView, getCurrentView } from './router.js';
import { escapeHtml } from '../shared/modal.js';

const MAX_NAV_APPS = 3;
const promoted = new Map();   // app name -> view module; a view is registered once

// nav: "<group>" → that group's flyout (no rail cost, uncapped). "apps" is
// never a flyout group here: the Apps menu goes by `pinned`, so a user plugin
// can't seat itself there by name. Unknown/malformed group → a rail slot.
const flyoutFor = a => (typeof a.nav === 'string' && a.nav !== 'apps' && /^[a-z][a-z0-9-]*$/.test(a.nav))
    ? document.querySelector(`.nav-item.nav-group-parent[data-view="${a.nav}"] .nav-flyout`)
    : null;

// View container + router registration — shared by every path. Returns the
// view module so nav items can "reset to app home": clicking the app's nav
// entry while already inside one of its sub-routes (#app-x/...) re-renders
// at the base.
function promoteAppView(app, group) {
    if (promoted.has(app.name)) return promoted.get(app.name);
    const appName = app.name;
    // Visibility epoch: an app's render is async, so a click-away can land
    // between show() starting and the module resolving. Without this, hide()
    // found no _appCleanup yet and no-oped, then render finished into a
    // display:none view — and apps that claim shared DOM (the Game Room's
    // rail + composer) left Chat with no rail, no composer, and no error.
    // Every app inherits this contract.
    let epoch = 0;
    const mod = {
        init(el) {},
        async show() {
            const el = document.getElementById(`view-app-${appName}`);
            if (!el) return;
            if (el.dataset.loaded) return;
            const mine = ++epoch;
            const v = document.querySelector('meta[name="boot-version"]')?.content || '';
            let imported = null;
            try {
                imported = await import(`/plugin-web/${appName}/app/index.js?v=${v}`);
                if (mine !== epoch) return;         // hidden while importing
                // Per-RENDER cleanup: render() may return its own teardown
                // closure. The module-level cleanup export is only a fallback —
                // the module is memoized, so on a superseded render that export
                // acts on state the NEWER render now owns (post-fix review
                // 2026-08-05: the loser's teardown killed the winner).
                let ret = null;
                if (imported.render) ret = await imported.render(el);
                const fin = (typeof ret === 'function') ? ret : (imported.cleanup || null);
                if (mine !== epoch) {
                    // Hidden mid-render: the app may have claimed shared DOM by
                    // now. Tear THIS render down instead of stranding it.
                    try { fin?.(); } catch {}
                    el.dataset.loaded = '';
                    return;
                }
                if (fin) el._appCleanup = fin;
                el.dataset.loaded = 'true';
            } catch (e) {
                if (mine !== epoch) return;
                // Render threw after possibly claiming shared DOM
                // (rail/composer/#chatbg) — release it before painting the
                // error, or Chat is left with no rail.
                try { imported?.cleanup?.(); } catch {}
                el.innerHTML = `<div class="view-placeholder"><h2>Failed to load ${appName}</h2><p style="color:var(--text-muted)">${escapeHtml(e.message)}</p></div>`;
            }
        },
        hide() {
            epoch++;                                 // cancels any in-flight show()
            const el = document.getElementById(`view-app-${appName}`);
            if (el?._appCleanup) {
                try { el._appCleanup(); } catch {}
                el._appCleanup = null;
                el.dataset.loaded = '';
            }
        }
    };
    registerView(`app-${appName}`, mod, group ? { group } : {});
    promoted.set(appName, mod);
    return mod;
}

// Clicking an app's nav entry while already deep inside it (#app-x/<sub>)
// returns to the app's home. switchView no-ops on same-view clicks, so this
// is the only way back to an app's landing page from the rail.
function resetToAppHome(appName, mod) {
    if (getCurrentView() !== `app-${appName}`) return;
    if (!location.hash.startsWith(`#app-${appName}/`)) return;
    mod.hide();
    history.replaceState(null, '', `#app-${appName}`);
    mod.show();
}

// One flyout entry. DOM API for icon/label: they come from manifest data,
// which is user-supplied and must not reach innerHTML (day-ruiner 2026-05-07 #A).
function flyoutItem(app, group) {
    const item = document.createElement('div');
    item.className = 'nav-flyout-item';
    item.dataset.view = `app-${app.name}`;
    item.dataset.app = app.name;
    item.setAttribute('role', 'button');
    item.setAttribute('tabindex', '0');
    item.textContent = `${app.icon || '📦'} ${app.label || app.name}`;
    const mod = promoteAppView(app, group);
    item.addEventListener('click', () => resetToAppHome(app.name, mod));
    return item;
}

// The Apps menu follows `pinned`: drawn at boot and again after every pin
// click on the Apps page (views/apps.js hands us the fresh list).
export function syncAppsMenu(apps) {
    const menu = document.querySelector('#nav-apps .nav-flyout');
    if (!menu) return;
    const want = (apps || []).filter(a => a.pinned && !a.nav && (a.places || ['apps']).includes('apps'));
    const names = new Set(want.map(a => a.name));
    menu.querySelectorAll('.nav-flyout-item').forEach(el => { if (!names.has(el.dataset.app)) el.remove(); });
    for (const app of want) {
        if (menu.querySelector(`.nav-flyout-item[data-app="${CSS.escape(app.name)}"]`)) continue;
        menu.appendChild(flyoutItem(app, 'apps'));
    }
}

// Boot: read /api/apps once, seat every app, show the Apps item when there is
// anything to see on its page.
export async function discoverPluginApps() {
    const appsRes = await fetch('/api/apps', { signal: AbortSignal.timeout(15000) });
    if (!appsRes.ok) return;
    const appsData = await appsRes.json();
    const allApps = (appsData.apps || []).filter(a => (a.places || ['apps']).includes('apps'));   // stage-only: no rail slot, no tile
    const navApps = allApps.filter(a => a.nav);
    const gridApps = allApps.filter(a => !a.nav);
    const flyoutApps = navApps.filter(a => flyoutFor(a));
    const railApps = navApps.filter(a => !flyoutFor(a));

    // Rail-promoted apps: own slot, capped
    const rail = document.getElementById('nav-rail');
    const navAppsBtn = document.getElementById('nav-apps');
    for (const app of railApps.slice(0, MAX_NAV_APPS)) {
        const btn = document.createElement('button');
        btn.className = 'nav-item';
        btn.dataset.view = `app-${app.name}`;
        const iconSpan = document.createElement('span');
        iconSpan.className = 'nav-icon';
        iconSpan.textContent = app.icon || '📦';
        const labelSpan = document.createElement('span');
        labelSpan.className = 'nav-label';
        labelSpan.textContent = app.label || '';
        btn.appendChild(iconSpan);
        btn.appendChild(labelSpan);
        if (navAppsBtn) rail.insertBefore(btn, navAppsBtn);
        else {
            const spacer = rail.querySelector('.nav-spacer');
            if (spacer) rail.insertBefore(btn, spacer);
            else rail.appendChild(btn);
        }
        const mod = promoteAppView(app);
        btn.addEventListener('click', () => resetToAppHome(app.name, mod));
    }

    // Flyout-promoted apps: item inside the named group's flyout
    for (const app of flyoutApps) flyoutFor(app).appendChild(flyoutItem(app, app.nav));

    // The Apps menu
    syncAppsMenu(allApps);

    // Show the Apps item if its page has tiles (or rail apps overflowed onto it)
    const hasGridApps = gridApps.length > 0 || railApps.length > MAX_NAV_APPS;
    if (navAppsBtn && hasGridApps) navAppsBtn.style.display = '';
}
