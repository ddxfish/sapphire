// main.js - Application orchestrator
import * as audio from './audio.js';
import * as ui from './ui.js';
import { initElements, refresh, setHistLen, getElements, getIsProc } from './core/state.js';
import { bindAllEvents, bindCleanupEvents } from './core/events.js';
import { initVolumeControls } from './features/volume.js';
import { startMicIconPolling, stopMicIconPolling, updateMicButtonState } from './features/mic.js';
import { populateChatDropdown } from './features/chat-manager.js';
import { hasPendingActivate } from './api.js';
import { updateScene, updateSendButtonLLM } from './features/scene.js';
import { applyTrimColor } from './features/chat-settings.js';
import { refreshInitData } from './shared/init-data.js';
import { initUserProfile } from './features/user-profile.js';
import { handleAutoRefresh } from './handlers/message-handlers.js';
import { setupImageHandlers } from './handlers/send-handlers.js';
import { setupImageModal } from './ui-images.js';
import * as eventBus from './core/event-bus.js';
import { getInitData } from './shared/init-data.js';

// New architecture
import { registerView, initRouter, getCurrentView } from './core/router.js';
import { initNavRail, setChatHeaderName } from './core/nav-rail.js';
import { renderSurface } from './surface/surface.js';
import { chatMode } from './surface/chat-mode.js';

// View modules loaded dynamically — a broken view cannot kill the app
const _v = window.__v ? `?v=${window.__v}` : '';
const VIEW_MODULES = {
    chat:     `./views/chat.js${_v}`,
    'chat-manage': `./views/chat-manage.js${_v}`,
    personas: `./views/personas.js${_v}`,
    prompts:  `./views/prompts.js${_v}`,
    toolsets: `./views/toolsets.js${_v}`,
    spices:   `./views/spices.js${_v}`,
    heartbeat: `./views/heartbeat.js${_v}`,
    scheduled: `./views/scheduled.js${_v}`,
    daemons:  `./views/daemons.js${_v}`,
    realtime: `./views/realtime.js${_v}`,
    webhooks: `./views/webhooks.js${_v}`,
    // Mind views route through a dispatcher: palace views when the mindpalace
    // plugin is active, classic views otherwise (probed live on each show).
    // 'self' is palace-only (L0) — classic falls back to a pointer placeholder.
    self:     `./views/mind-dispatch.js?view=self${_v ? '&v=' + window.__v : ''}`,
    memories: `./views/mind-dispatch.js?view=memories${_v ? '&v=' + window.__v : ''}`,
    people:   `./views/mind-dispatch.js?view=people${_v ? '&v=' + window.__v : ''}`,
    knowledge: `./views/mind-dispatch.js?view=knowledge${_v ? '&v=' + window.__v : ''}`,
    // No navrail item (v2 flyout) — reached via the CLASSIC tab bar only.
    // v1 and v2 memory systems coexist (Krem 2026-07-17): both fully work,
    // users can switch back; v1 retires no earlier than ~2 months out.
    'ai-knowledge': `./views/ai-knowledge.js${_v}`,
    goals:    `./views/mind-dispatch.js?view=goals${_v ? '&v=' + window.__v : ''}`,
    admin:    `./views/mind-dispatch.js?view=admin${_v ? '&v=' + window.__v : ''}`,
    settings: `./views/settings.js${_v}`,
    help:     `./views/help.js${_v}`,
    apps:     `./views/apps.js${_v}`,
    store:    `./views/store.js${_v}`,
    'video-guide': `./views/video-guide.js${_v}`,
};

async function loadViews() {
    await Promise.allSettled(
        Object.entries(VIEW_MODULES).map(async ([id, path]) => {
            try {
                const mod = await import(path);
                registerView(id, mod.default);
            } catch (e) {
                console.error(`[Views] Failed to load '${id}' from ${path}:`, e);
                registerView(id, {
                    init(el) {
                        el.innerHTML = `<div class="view-placeholder">
                            <h2>Failed to load ${id}</h2>
                            <p style="color:var(--text-muted);font-size:var(--font-sm)">${e.message}</p>
                            <p style="color:var(--text-muted);font-size:var(--font-sm)">Try a hard refresh (Ctrl+Shift+R)</p>
                        </div>`;
                    },
                    show() {},
                    hide() {}
                });
            }
        })
    );
}

// Plugin memory layers (v1.1): one generic view module per registered layer
// (?layer= on the import URL = distinct instance), plus a Mind flyout item
// after Goals. Registration + injection only happen for layers that exist,
// so there's nothing to click before this resolves.
async function loadPluginLayerViews() {
    let layers = [];
    try {
        const r = await fetch('/api/plugin/mindpalace/layers', { credentials: 'same-origin' });
        if (!r.ok) return;
        layers = (await r.json()).layers || [];
    } catch { return; }
    if (!layers.length) return;
    const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    let anchor = document.querySelector('.nav-flyout-item[data-view="goals"]');
    for (const l of layers) {
        const id = `layer-${l.key}`;
        try {
            const mod = await import(`./views/palace/layer.js?layer=${encodeURIComponent(l.key)}${_v ? '&v=' + window.__v : ''}`);
            registerView(id, mod.default, { group: 'mind' });
            if (anchor && !document.querySelector(`.nav-flyout-item[data-view="${id}"]`)) {
                const item = document.createElement('div');
                item.className = 'nav-flyout-item';
                item.dataset.view = id;
                item.setAttribute('role', 'button');
                item.setAttribute('tabindex', '0');
                item.innerHTML = `${esc(l.icon || '\u{1F9E9}')} ${esc(l.label)}`;
                anchor.after(item);
                anchor = item;   // keep declaration order for multiple layers
            }
        } catch (e) {
            console.error(`[Views] Plugin layer '${l.key}' failed to load:`, e);
        }
    }
}

// Initialize appearance settings from localStorage (theme, density, font)
// Trim color is per-persona now — default cyan set in CSS body
function initAppearance() {
    const root = document.documentElement;

    // Density
    const density = localStorage.getItem('sapphire-density');
    if (density && density !== 'default') {
        root.setAttribute('data-density', density);
    }

    // Font
    const font = localStorage.getItem('sapphire-font');
    if (font && font !== 'system') {
        root.setAttribute('data-font', font);
    }

    // Clean up stale trim localStorage (now per-persona)
    localStorage.removeItem('sapphire-trim');

    // Send button trim preference
    const sendBtnTrim = localStorage.getItem('sapphire-send-btn-trim');
    if (sendBtnTrim === 'true') {
        requestAnimationFrame(() => {
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.classList.add('use-trim');
        });
    }
}

async function init() {
    const t0 = performance.now();

    try {
        // Render the chat Surface skeleton FIRST — ui.bindChatDom() and
        // initElements() cache refs from inside it. The skeleton lived in
        // index.html until Phase 1 of tmp/chat-surface-plan.md.
        renderSurface(document.getElementById('view-chat'), chatMode);
        ui.bindChatDom();
        initAppearance();
        initElements();

        const { form, sendBtn, micBtn, input } = getElements();

        // Prevent form submission immediately
        form.addEventListener('submit', e => e.preventDefault());

        // Disable input until loaded
        sendBtn.disabled = true;
        sendBtn.textContent = '\u23F3';
        if (micBtn) {
            micBtn.disabled = true;
            micBtn.style.opacity = '0.5';
        }
        input.placeholder = 'Loading Web UI...';
        input.classList.add('loading');

        ui.showStatus();
        ui.updateStatus('Loading...');

        // Load views dynamically (isolated — one broken view won't kill the app)
        await loadViews();

        // Plugin memory layers (v1.1): registered layers get a generic palace
        // view + a Mind flyout entry. Fire-and-forget — silent no-op when the
        // mindpalace plugin is off (live-probe precedent: mind-dispatch.js).
        loadPluginLayerViews();

        // === DATA FETCH (can fail without killing the app) ===
        // Must run BEFORE initRouter so chat dropdown has real data when chat.show() fires
        let initData = null;
        try {
            initData = await getInitData();
            ui.initFromInitData(initData);
            // One-time notice: global privacy mode was removed in v2.8.4 —
            // users still carrying the old setting are otherwise silently
            // unprotected after upgrade.
            if (initData?.privacy_v2_notice && !localStorage.getItem('privacyV2NoticeShown')) {
                localStorage.setItem('privacyV2NoticeShown', '1');
                ui.showToast('Heads up: global privacy mode was removed and your old setting no longer applies. '
                    + 'Mark individual chats private with the eye icon in the sidebar.', 'warning', 0);
            }
            // Show any plugin load errors from startup (before SSE was connected)
            if (initData?.load_errors?.length) {
                for (const err of initData.load_errors) {
                    const hint = err.hint ? ` — ${err.hint}` : '';
                    const isDeps = err.missing_deps?.length > 0;
                    ui.showToast(`Plugin '${err.plugin}': ${err.error}${hint}`, isDeps ? 'warning' : 'error', isDeps ? 0 : 10000);
                }
            }
            // Core integrity: yell once per browser session if the install doesn't
            // match its manifest. Check-and-toast ONLY — repair is a human-only
            // button on Settings > System, never automatic.
            if (!sessionStorage.getItem('integrity-warned')) {
                fetch('/api/system/integrity', { credentials: 'same-origin' })
                    .then(r => r.ok ? r.json() : null)
                    .then(rep => {
                        if (rep?.available && rep.ok === false) {
                            sessionStorage.setItem('integrity-warned', '1');
                            ui.showToast(
                                `Core files don't match v${rep.version}: ${rep.mismatched.length} modified, ${rep.missing.length} missing. See Settings → System.`,
                                'warning', 12000);
                        }
                    })
                    .catch(() => {});
            }
            // Discover plugin apps — promote nav apps, show Apps grid if others exist
            try {
                const appsRes = await fetch('/api/apps');
                if (appsRes.ok) {
                    const appsData = await appsRes.json();
                    const allApps = appsData.apps || [];
                    const navApps = allApps.filter(a => a.nav);
                    const gridApps = allApps.filter(a => !a.nav);

                    // nav: true → own rail slot (capped). nav: "<group>" → item
                    // in that group's flyout (no rail cost, uncapped) — same
                    // mechanism the Mind flyout uses for plugin memory layers.
                    // Unknown/malformed group falls back to a rail slot.
                    const flyoutFor = a => (typeof a.nav === 'string' && /^[a-z][a-z0-9-]*$/.test(a.nav))
                        ? document.querySelector(`.nav-item.nav-group-parent[data-view="${a.nav}"] .nav-flyout`)
                        : null;
                    const flyoutApps = navApps.filter(a => flyoutFor(a));
                    const railApps = navApps.filter(a => !flyoutFor(a));

                    // View container + router registration — shared by both paths.
                    // Returns the view module so nav items can "reset to app
                    // home": clicking the app's nav entry while already inside
                    // one of its sub-routes (#app-x/...) re-renders at the base.
                    const promoteAppView = (app, group) => {
                        const appContent = document.getElementById('app-content');
                        if (appContent) {
                            const viewDiv = document.createElement('div');
                            viewDiv.id = `view-app-${app.name}`;
                            viewDiv.className = 'view';
                            viewDiv.style.display = 'none';
                            appContent.appendChild(viewDiv);
                        }
                        const appName = app.name;
                        // Visibility epoch: an app's render is async, so a
                        // click-away can land between show() starting and the
                        // module resolving. Without this, hide() found no
                        // _appCleanup yet and no-oped, then render finished
                        // into a display:none view — and apps that claim
                        // shared DOM (the Game Room's rail + composer) left
                        // Chat with no rail, no composer, and no error.
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
                                    // Per-RENDER cleanup: render() may return its own
                                    // teardown closure. The module-level cleanup export
                                    // is only a fallback — the module is memoized, so on
                                    // a superseded render that export acts on state the
                                    // NEWER render now owns (post-fix review 2026-08-05:
                                    // the loser's teardown killed the winner).
                                    let ret = null;
                                    if (imported.render) ret = await imported.render(el);
                                    const fin = (typeof ret === 'function') ? ret : (imported.cleanup || null);
                                    if (mine !== epoch) {
                                        // Hidden mid-render: the app may have
                                        // claimed shared DOM by now. Tear THIS
                                        // render down instead of stranding it.
                                        try { fin?.(); } catch {}
                                        el.dataset.loaded = '';
                                        return;
                                    }
                                    if (fin) el._appCleanup = fin;
                                    el.dataset.loaded = 'true';
                                } catch (e) {
                                    if (mine !== epoch) return;
                                    // Render threw after possibly claiming shared DOM
                                    // (rail/composer/#chatbg) — release it before
                                    // painting the error, or Chat is left with no rail.
                                    try { imported?.cleanup?.(); } catch {}
                                    el.innerHTML = `<div class="view-placeholder"><h2>Failed to load ${appName}</h2><p style="color:var(--text-muted)">${e.message}</p></div>`;
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
                        return mod;
                    };

                    // Clicking an app's nav entry while already deep inside it
                    // (#app-x/<sub>) returns to the app's home. switchView
                    // no-ops on same-view clicks, so this is the only way back
                    // to an app's landing page from the rail.
                    const resetToAppHome = (appName, mod) => {
                        if (getCurrentView() !== `app-${appName}`) return;
                        if (!location.hash.startsWith(`#app-${appName}/`)) return;
                        mod.hide();
                        history.replaceState(null, '', `#app-${appName}`);
                        mod.show();
                    };

                    // Inject nav-promoted plugin apps into the navrail
                    const MAX_NAV_APPS = 3;
                    const rail = document.getElementById('nav-rail');
                    const navAppsBtn = document.getElementById('nav-apps');
                    for (const app of railApps.slice(0, MAX_NAV_APPS)) {
                        // Create nav item — use DOM API for icon/label since
                        // they originate from plugin manifest data which is
                        // user-supplied and shouldn't reach innerHTML.
                        // Day-ruiner scout 2026-05-07 #A.
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

                    // Flyout-promoted apps: item inside the group's flyout
                    for (const app of flyoutApps) {
                        const item = document.createElement('div');
                        item.className = 'nav-flyout-item';
                        item.dataset.view = `app-${app.name}`;
                        item.setAttribute('role', 'button');
                        item.setAttribute('tabindex', '0');
                        item.textContent = `${app.icon || '📦'} ${app.label || app.name}`;
                        flyoutFor(app).appendChild(item);
                        const mod = promoteAppView(app, app.nav);
                        item.addEventListener('click', () => resetToAppHome(app.name, mod));
                    }

                    // Show Apps grid nav if there are non-nav apps (or overflow nav apps)
                    const appsNavBtn = document.getElementById('nav-apps');
                    const hasGridApps = gridApps.length > 0 || railApps.length > MAX_NAV_APPS;
                    if (appsNavBtn && hasGridApps) appsNavBtn.style.display = '';
                }
            } catch {}
        } catch (e) {
            console.warn('[Init] Could not fetch init data:', e);
        }

        // Use allSettled so one failure doesn't kill the other
        const [sceneResult, refreshResult] = await Promise.allSettled([
            updateScene(),
            refresh(false)
        ]);

        const status = sceneResult.status === 'fulfilled' ? sceneResult.value : null;
        const historyLen = refreshResult.status === 'fulfilled' ? refreshResult.value : 0;

        if (sceneResult.status === 'rejected') console.warn('[Init] updateScene failed:', sceneResult.reason);
        if (refreshResult.status === 'rejected') console.warn('[Init] refresh failed:', refreshResult.reason);

        setHistLen(historyLen);

        // Populate chat dropdown + picker (before router so chat.show() has real chat name).
        // Always via populateChatDropdown — the old status.chats fast-path skipped its
        // archived/private filters, leaking both into the picker on every boot.
        try { await populateChatDropdown(); } catch (e) { console.warn('[Init] Chat dropdown failed:', e); }

        // Apply chat settings
        const settings = status?.chat_settings || {};
        updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
        applyTrimColor(settings.trim_color || '');

        // Init nav rail + router (after data so chat sidebar loads with correct chat)
        initNavRail();
        initRouter('chat');
        initVersionBadge();

        // Hide nav items for disabled plugins (non-blocking)
        syncNavWithPlugins();

        // === UI WIRING (must always run) ===
        initVolumeControls();
        startMicIconPolling();
        bindAllEvents();
        setupImageHandlers();
        setupImageModal();
        initUserProfile();

        initEventBus();

        // Re-enable input
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        if (micBtn) {
            micBtn.disabled = false;
            micBtn.style.opacity = '1';
        }
        input.placeholder = 'Type message... (paste or drop images)';
        input.classList.remove('loading');
        ui.hideStatus();

        // Scroll to bottom after render
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                ui.forceScrollToBottom();
            });
        });

        // Auto-refresh interval (fallback - events handle most updates)
        setInterval(handleAutoRefresh, 30000);

        // Setup wizard auto-show on first launch
        const wizardStep = initData?.wizard_step;
        if (typeof wizardStep === 'number' && wizardStep < 3) {
            setTimeout(async () => {
                try {
                    const mod = await import(`./core-ui/setup-wizard/index.js${_v}`);
                    if (mod.default?.init) await mod.default.init();
                } catch (e) { console.warn('[Init] Setup wizard failed:', e); }
            }, 500);
        }

        console.log(`[Init] Complete in ${(performance.now() - t0).toFixed(0)}ms`);

    } catch (e) {
        console.error('Init error:', e);
        const { sendBtn, micBtn, input } = getElements();
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.textContent = 'Send';
        }
        if (micBtn) {
            micBtn.disabled = false;
            micBtn.style.opacity = '1';
        }
        if (input) {
            input.placeholder = 'Type message... (paste or drop images)';
            input.classList.remove('loading');
        }
        ui.hideStatus();

        // Still wire up core UI even on error
        try {
            initVolumeControls();
            startMicIconPolling();
            bindAllEvents();
            setupImageHandlers();
            setupImageModal();
            initUserProfile();
            initEventBus();
        } catch (e2) {
            console.error('UI wiring failed:', e2);
        }
    }
}

// Plugins that own a nav-rail view — hide nav if plugin disabled
const PLUGIN_NAV_MAP = { continuity: 'triggers' };

function syncNavWithPlugins() {
    fetch('/api/webui/plugins').then(r => r.ok ? r.json() : null).then(d => {
        if (!d?.plugins) return;
        for (const [plugin, view] of Object.entries(PLUGIN_NAV_MAP)) {
            const p = d.plugins.find(x => x.name === plugin);
            if (p && !p.enabled) {
                const btn = document.querySelector(`.nav-item[data-view="${view}"]`);
                if (btn) btn.style.display = 'none';
            }
        }
        // Auto-load plugin scripts (web/main.js) for enabled plugins
        loadPluginScripts(d.plugins);
    }).catch(() => {});
}

// Per-plugin load tracker. Previously a Set keyed by name only — once added,
// never forgotten. This broke two flows in 2.6.4:
//   1. Store-installed plugin with default_enabled:true never got main.js loaded
//      until full page reload (store didn't dispatch plugin_toggled).
//   2. Uninstall→reinstall same plugin → Set.has(name) was true → script skipped,
//      and even if forgotten, ES module URL cache returned the OLD module.
// Map now tracks (name → loadId). Reloads when the plugin drops out of the
// current enabled+has_script set (e.g., uninstalled), and uses loadId as a
// cache-bust query param so reinstalled plugins get a fresh module instance.
// 2026-05-14.
const _loadedPluginScripts = new Map();
let _pluginLoadCounter = 0;

function loadPluginScripts(plugins) {
    const currentNames = new Set();
    for (const p of plugins) {
        if (!p.enabled || !p.has_script) continue;
        currentNames.add(p.name);
        if (_loadedPluginScripts.has(p.name)) continue;
        const loadId = ++_pluginLoadCounter;
        _loadedPluginScripts.set(p.name, loadId);
        const cacheBust = _v ? `${_v}&t=${loadId}` : `?t=${loadId}`;
        const url = `/plugin-web/${p.name}/main.js${cacheBust}`;
        import(url).then(mod => {
            if (mod.default?.init) mod.default.init();
            console.log(`[Plugins] Loaded script: ${p.name}`);
        }).catch(() => {}); // Plugin has no main.js or it failed — that's fine
    }
    // Forget any plugins that are no longer enabled+has_script (uninstall /
    // toggle off). Lets a later re-enable / reinstall reload cleanly.
    for (const tracked of [..._loadedPluginScripts.keys()]) {
        if (!currentNames.has(tracked)) _loadedPluginScripts.delete(tracked);
    }
}

// Re-check for new plugin scripts (called after plugin toggle/reload)
function reloadPluginScripts() {
    fetch('/api/webui/plugins').then(r => r.ok ? r.json() : null).then(d => {
        if (d?.plugins) loadPluginScripts(d.plugins);
    }).catch(() => {});
}

function initEventBus() {
    // Debounced refresh - prevents multiple /api/history calls from racing
    let refreshTimer = null;
    const debouncedRefresh = () => {
        if (refreshTimer) clearTimeout(refreshTimer);
        refreshTimer = setTimeout(() => {
            refreshTimer = null;
            if (!getIsProc()) refresh(false);
        }, 100);
    };

    // AI typing events. D4: ignore a foreign stream's typing (a live phone call /
    // background conversation) — it isn't the operator's own turn, and refreshing
    // their active chat on it caused spurious churn.
    eventBus.on(eventBus.Events.AI_TYPING_START, (data) => {
        if (data?.foreign) return;
        console.log('[EventBus] AI typing started');
    });

    eventBus.on(eventBus.Events.AI_TYPING_END, (data) => {
        if (data?.foreign) return;
        console.log('[EventBus] AI typing ended');
        debouncedRefresh();
    });

    // TTS events. Phone-call playback (surface 'phone') is NOT local audio —
    // it must never light the Stop button or drive audio.stop() (Phase I).
    eventBus.on(eventBus.Events.TTS_PLAYING, (data) => {
        if (data?.surface === 'phone') return;
        audio.setLocalTtsPlaying(true);
        updateMicButtonState();
    });

    eventBus.on(eventBus.Events.TTS_STOPPED, (data) => {
        if (data?.surface === 'phone') return;
        audio.stop(true);
        audio.setLocalTtsPlaying(false);
        updateMicButtonState();
    });

    // Browser TTS from heartbeat/scheduled tasks — one tab claims and plays
    eventBus.on(eventBus.Events.TTS_SPEAK, (data) => {
        if (!data?.text) return;
        const claimId = `${Date.now()}-${Math.random()}`;
        const claimKey = 'sapphire_tts_claim';
        // Try to claim — first writer wins
        const existing = localStorage.getItem(claimKey);
        if (existing && Date.now() - parseInt(existing.split(':')[0]) < 30000) return; // another tab claimed recently
        localStorage.setItem(claimKey, `${Date.now()}:${claimId}`);
        // Brief yield to let other tabs race, then verify we won
        setTimeout(() => {
            const winner = localStorage.getItem(claimKey);
            if (!winner || !winner.endsWith(claimId)) return; // lost the race
            console.log(`[BrowserTTS] Playing: "${data.text.substring(0, 60)}..." from task "${data.task || '?'}"`);
            audio.playText(data.text).finally(() => {
                localStorage.removeItem(claimKey);
            });
        }, 50);
    });

    // Generic plugin toasts — any plugin can publish PLUGIN_NOTICE to surface one
    eventBus.on(eventBus.Events.PLUGIN_NOTICE, (data) => {
        if (!data?.message) return;
        const sev = ['info', 'success', 'warning', 'error'].includes(data.severity) ? data.severity : 'info';
        ui.showToast(data.message, sev);
    });

    // Voice-turn live streaming: render the local-mic reply into the chat AS it streams,
    // instead of letting the saved message blob in at the end. While a voice turn is live we
    // SUPPRESS the MESSAGE_ADDED refresh (it would wipe the live bubble mid-stream);
    // finishStreaming reconciles with the saved message on END — same as the web-typed path.
    let _voiceTurnActive = false;
    eventBus.on('voice_turn_start', (data) => {
        // A turn that belongs to another chat (a phone call's side chat) must not
        // render into the chat being viewed — it used to stream in, then vanish on
        // the end-of-turn reconcile (2026-07-04 outbound-call bug). Chunks/end are
        // gated on _voiceTurnActive, so skipping start skips the whole turn.
        if (data?.foreign) return;
        try {
            if (data?.user_text) ui.addUserMessage(data.user_text);
            ui.startStreaming();
            _voiceTurnActive = true;
        } catch (e) { console.warn('[VOICE_TURN] start failed', e); }
    });
    eventBus.on('voice_turn_chunk', (data) => {
        if (data?.foreign || !_voiceTurnActive) return;
        try { ui.appendStream(data?.text || ''); } catch (e) { /* ignore */ }
    });
    eventBus.on('voice_turn_end', async (data) => {
        if (data?.foreign || !_voiceTurnActive) return;
        _voiceTurnActive = false;
        try { await ui.finishStreaming(); } catch (e) { console.warn('[VOICE_TURN] end failed', e); }
    });

    // Message events
    eventBus.on(eventBus.Events.MESSAGE_ADDED, () => { if (_voiceTurnActive) return; debouncedRefresh(); });
    eventBus.on(eventBus.Events.MESSAGE_REMOVED, () => debouncedRefresh());
    eventBus.on(eventBus.Events.CHAT_CLEARED, () => debouncedRefresh());
    // Trim/compress rewrite a chat's history wholesale — if it's the open
    // chat, the transcript on screen is stale until refreshed.
    eventBus.on(eventBus.Events.CHAT_TRIMMED, () => debouncedRefresh());
    eventBus.on(eventBus.Events.CHAT_COMPRESSED, () => debouncedRefresh());

    // Debounced updateScene
    let sceneTimer = null;
    const debouncedUpdateScene = () => {
        if (sceneTimer) clearTimeout(sceneTimer);
        sceneTimer = setTimeout(() => {
            sceneTimer = null;
            updateScene();
        }, 100);
    };

    // System state events — invalidate init cache so views get fresh data on show()
    const refreshAndUpdateScene = () => { refreshInitData(); debouncedUpdateScene(); };
    eventBus.on(eventBus.Events.PROMPT_CHANGED, (data) => {
        // Vault lock/unlock changes WHICH CHATS EXIST (vaulted chats Phase 1:
        // private chats vanish sealed, reappear unlocked) — repaint the chat
        // list too. Without this wire, nothing repainted on vault_changed and
        // hidden chats lingered until an unrelated event. Eviction's own
        // CHAT_SWITCHED handles the transcript; the Phase 0 paint-seq guard
        // dedups the overlapping repaints.
        // forceAdopt (vault hunt R7): a lock/unlock repaint must adopt the
        // server's real active — the adopt=false path could re-synthesize a
        // sealed chat's RAW name into the select/header in a two-tab storm.
        if (data?.action === 'vault_changed') populateChatDropdown({ forceAdopt: true });
        refreshAndUpdateScene();
    });
    eventBus.on(eventBus.Events.TOOLSET_CHANGED, refreshAndUpdateScene);
    eventBus.on(eventBus.Events.SPICE_CHANGED, refreshAndUpdateScene);
    eventBus.on(eventBus.Events.COMPONENTS_CHANGED, refreshAndUpdateScene);
    eventBus.on(eventBus.Events.PROMPT_DELETED, refreshAndUpdateScene);
    // chat_prompt_fallback fires PER DEGRADED TURN (chat pinned to a prompt
    // that no longer resolves — e.g. asleep in a locked vault). Keyed out of
    // the full init-refresh (a steady-state locked vault would hammer
    // /api/init every turn) and surfaced as a throttled toast instead.
    let lastFallbackToast = 0;
    eventBus.on(eventBus.Events.SETTINGS_CHANGED, (data) => {
        if (data?.key === 'chat_prompt_fallback') {
            if (Date.now() - lastFallbackToast > 60000) {
                lastFallbackToast = Date.now();
                const missing = (data?.reason || '').replace(/^missing:/, '');
                ui.showToast(`Prompt '${missing}' unavailable (vault locked or deleted) — running on the default prompt`, 'error');
            }
            debouncedUpdateScene();
            return;
        }
        refreshAndUpdateScene();
    });
    // Settings changes can move a chat between dropdown sections (the
    // talk-stamp flips private_chat server-side mid-turn) — repaint the
    // list too, not just the scene. Paint-seq guard dedups.
    eventBus.on(eventBus.Events.CHAT_SETTINGS_CHANGED, () => {
        populateChatDropdown();
        debouncedUpdateScene();
    });

    eventBus.on(eventBus.Events.CHAT_SWITCHED, async (data) => {
        // Remote switch (phone / other tab) — this tab's own switches are
        // self-origin-dropped by the bus. Adopt the payload name DIRECTLY:
        // a list refetch may predate the switch, the event cannot. Skipped
        // while a local activate is in flight (last local intent wins — if
        // the remote switch was truly last, its event lands after ours
        // settles and this handler adopts it then).
        const name = data?.name;
        const sel = document.getElementById('chat-select');
        if (name && sel && !hasPendingActivate()) {
            if (![...sel.options].some(o => o.value === name)) {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                sel.appendChild(opt);
            }
            sel.value = name;
            sel.dispatchEvent(new CustomEvent('chat-activated', { detail: { chat: name } }));
            // Transcript + scene never refreshed on remote switches before —
            // the old chat stayed on screen until the next message event.
            await refresh(false);
            await updateScene();
        }
        populateChatDropdown();
    });

    eventBus.on(eventBus.Events.CHAT_CREATED, () => {
        populateChatDropdown();
    });

    eventBus.on(eventBus.Events.CHAT_ARCHIVED, () => {
        populateChatDropdown();
    });

    // Delete/rename from ANOTHER surface (Chat Manager, second tab, a tool) —
    // these events had zero listeners, so this tab's dropdown kept the ghost
    // (or the stale name) forever while SSE was up; clicking the ghost 400'd.
    // Vaulted-chats Phase 0, 2026-08-13.
    eventBus.on(eventBus.Events.CHAT_DELETED, () => {
        populateChatDropdown();
    });

    eventBus.on(eventBus.Events.CHAT_RENAMED, () => {
        populateChatDropdown();
    });

    // Plugin reload/toggle — load new scripts
    eventBus.on(eventBus.Events.PLUGIN_RELOADED, (data) => {
        ui.showToast(`Plugin '${data?.plugin || 'unknown'}' reloaded`, 'success');
        reloadPluginScripts();
    });
    document.addEventListener('sapphire:plugin_toggled', () => { refreshInitData(); reloadPluginScripts(); });

    // In-place plugin update/revert — the plugin stays enabled throughout, so
    // the Map entry never drops and the OLD ES module survives until a hard
    // refresh: Python new, JS stale (2026-08-06 hunt, H8). Force a fresh
    // loadId so the cache-busted import picks up the new main.js.
    document.addEventListener('sapphire:plugin_updated', (e) => {
        const name = e.detail?.plugin;
        if (name) _loadedPluginScripts.delete(name);
        refreshInitData();
        reloadPluginScripts();
    });

    // Plugin load errors — sticky toast for missing deps, timed for other errors
    eventBus.on(eventBus.Events.PLUGIN_LOAD_ERROR, (data) => {
        const hint = data?.hint ? ` — ${data.hint}` : '';
        const isDeps = data?.missing_deps?.length > 0;
        ui.showToast(
            `Plugin '${data?.plugin}': ${data?.error}${hint}`,
            isDeps ? 'warning' : 'error',
            isDeps ? 0 : 10000
        );
    });

    // Continuity task errors — toast so user knows a scheduled task failed
    eventBus.on(eventBus.Events.CONTINUITY_TASK_ERROR, (data) => {
        ui.showToast(`Task "${data?.task || 'Unknown'}": ${data?.error || 'failed'}`, 'error', 10000);
    });

    // DB corruption sentinel (backup housekeeping) — halts ALL backups until
    // cleared. Was journalctl-only; this event had zero subscribers before the
    // 2026-07-06 herring hunt (#14).
    eventBus.on('sapphire_health_alert', (data) => {
        const dbs = (data?.dbs || []).join(', ');
        ui.showToast(
            `Database integrity check FAILED${dbs ? ` (${dbs})` : ''} — ` +
            `backups are halted until the sentinel is cleared in Settings > Backup`,
            'error', 15000);
    });

    // Re-fetch conversation-mode state and route it through the same event the
    // toggle buttons already listen on (chat.js). SSE reconnects use replay=false,
    // so a conversation_mode_changed fired during a gap is otherwise lost forever —
    // the toggle wedges dark while the mode is ON, and a dark button can never
    // send stop (2026-07-06 herring hunt: the phone one-way-trap reproducer).
    const resyncTrueSpeech = async () => {
        try {
            const r = await fetch('/api/runtime/true-speech');
            if (r.ok) {
                const d = await r.json();
                eventBus.dispatch('conversation_mode_changed',
                    { enabled: d.enabled === true, source: d.source || null });
            }
        } catch { /* offline — next resync covers it */ }
    };

    // Server restart detection — full state resync
    eventBus.on(eventBus.Events.SERVER_RESTARTED, async () => {
        console.log('[Main] Server restarted — full resync');
        await refreshInitData();
        await populateChatDropdown();
        await refresh(false);
        await updateScene();
        await resyncTrueSpeech();
    });

    // SSE reconnect — resync state in case events were missed during disconnect
    let _sseConnectedOnce = false;
    eventBus.on(eventBus.Events.BUS_CONNECTED, async () => {
        if (!_sseConnectedOnce) {
            _sseConnectedOnce = true;
            return; // Skip initial connect — state is fresh from page load
        }
        console.log('[Main] SSE reconnected — resyncing state');
        await refreshInitData();
        await populateChatDropdown();
        await refresh(false);
        await updateScene();
        await resyncTrueSpeech();
    });

    // STT events
    eventBus.on(eventBus.Events.STT_RECORDING_START, () => {});
    eventBus.on(eventBus.Events.STT_RECORDING_END, () => {});
    eventBus.on(eventBus.Events.STT_PROCESSING, () => {});
    eventBus.on(eventBus.Events.WAKEWORD_DETECTED, () => {});

    // Tool events
    eventBus.on(eventBus.Events.TOOL_EXECUTING, () => {});
    eventBus.on(eventBus.Events.TOOL_COMPLETE, () => {});

    // Error events
    eventBus.on(eventBus.Events.LLM_ERROR, (data) => {
        console.warn('[EventBus] LLM error:', data);
    });

    eventBus.on(eventBus.Events.STT_ERROR, (data) => {
        console.warn('[EventBus] STT error:', data);
        if (data?.message) ui.showToast(data.message, 'error');
    });

    // Connect to server
    eventBus.connect(false);
    window.eventBus = eventBus;
}

function cleanup() {
    stopMicIconPolling();
    eventBus.disconnect();
    audio.stop();
}

function initVersionBadge() {
    const badge = document.getElementById('nav-version');
    if (!badge) return;

    // Click → navigate to settings dashboard
    badge.addEventListener('click', () => {
        import('./core/router.js').then(r => r.switchView('settings'));
    });

    // Fetch branch info at boot — show non-main branches immediately
    fetch('/api/system/update-check').then(r => r.ok ? r.json() : null).then(data => {
        if (!data) return;
        const branch = data.branch;
        if (branch && branch !== 'main') {
            badge.innerHTML = `v${window.__appVersion || '?'}<br><span class="nav-branch">${branch}</span>`;
        }
        if (data.available) {
            badge.classList.add('update-available');
            badge.title = `Update available: v${data.latest}`;
            window.dispatchEvent(new CustomEvent('update-available', { detail: data }));
        }
    }).catch(() => {});
}

// Boot
document.addEventListener('DOMContentLoaded', init);
bindCleanupEvents(cleanup);
