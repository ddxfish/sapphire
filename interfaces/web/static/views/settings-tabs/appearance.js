// settings-tabs/appearance.js - Visual settings: color sets, type, background, options
// Appearance settings use localStorage (client-side only), except data-key rows.
// P1 of themes-v2 (plan: tmp/themes-v2-plan.md): true miniature theme cards via
// [data-theme] scoping, font preset cards, scene library surfaced here.

import { mountScenePicker } from '../../shared/scene-picker.js';
import { applyBackground, setDefaultBackground } from '../../features/chat-settings.js';
import { updateSettingsBatch } from '../../shared/settings-api.js';
import { applyTheme, fetchThemes, currentThemeId, settingKey } from '../../core/theme.js';
import { FONT_PRESETS, fontStatus, ensureFont } from '../../shared/fonts.js';

let _allThemes = [];

export default {
    id: 'appearance',
    name: 'Visual',
    icon: '\uD83C\uDFA8',
    description: 'Theme, spacing, and font settings',

    render(ctx) {
        const density = localStorage.getItem('sapphire-density') || 'default';
        const fontsize = localStorage.getItem('sapphire-fontsize') || 'default';
        const font = localStorage.getItem('sapphire-font') || 'system';
        const avatars = ctx.getValue('AVATARS_IN_CHAT') ?? true;
        const iconColor = ctx.getValue('ICON_COLOR') || '';

        return `
        <div class="appearance-page">
            <div class="setting-section-title">Colors</div>
            <div class="theme-grid" id="theme-grid">
                <div class="text-muted" style="font-size:var(--font-sm);padding:12px">Loading themes...</div>
            </div>
            <div id="theme-settings-panel" style="display:none"></div>

            <div class="setting-section-title" style="margin-top:20px">Type</div>
            <div class="font-grid" id="font-grid">
                ${FONT_PRESETS.map(f => `
                    <div class="font-card ${font === f.id ? 'active' : ''}" data-font-id="${f.id}" data-family="${f.family || ''}" style="font-family:${f.stack}">
                        <div class="font-sample">Aa</div>
                        <div class="font-quick">The quick brown fox jumps</div>
                        <div class="font-card-name">${f.label}</div>
                        <span class="font-dl" hidden title="Downloads on first use (~300KB, OFL)">&#x2913;</span>
                        <div class="theme-check">✓</div>
                    </div>`).join('')}
            </div>

            <div class="setting-section-title" style="margin-top:20px">Background &amp; Motion</div>
            <div class="setting-help" style="margin-bottom:8px">Global underlay &mdash; shown whenever a chat has no scene of its own. A chat's scene (set from the chat sidebar) always wins.</div>
            <div id="visual-scene-mount"></div>
            <div class="setting-help" style="margin:14px 0 8px">Ambient motion behind chat &mdash; runs over scenes too, pauses while the tab is hidden.</div>
            <div class="motion-row" id="motion-row"><div class="text-muted" style="font-size:var(--font-sm)">Loading&hellip;</div></div>
            <div class="motion-speed-row">
                <span class="setting-help">Speed</span>
                <select id="motion-speed">
                    <option value="0.5">Slow</option>
                    <option value="1" selected>Normal</option>
                    <option value="1.75">Fast</option>
                </select>
                <span class="setting-help" style="margin-left:12px">Intensity</span>
                <select id="motion-intensity">
                    <option value="0.5">Low</option>
                    <option value="1" selected>Normal</option>
                    <option value="1.75">High</option>
                </select>
            </div>

            <div class="setting-section-title" style="margin-top:20px">Options</div>
            <div class="settings-grid">
                <div class="setting-row">
                    <div class="setting-label"><label>Spacing</label><div class="setting-help">UI density</div></div>
                    <div class="setting-input">
                        <select id="app-density">
                            <option value="compact" ${density === 'compact' ? 'selected' : ''}>Compact</option>
                            <option value="default" ${density === 'default' ? 'selected' : ''}>Default</option>
                            <option value="comfortable" ${density === 'comfortable' ? 'selected' : ''}>Comfortable</option>
                        </select>
                    </div>
                </div>
                <div class="setting-row">
                    <div class="setting-label"><label>Font Size</label><div class="setting-help">Scales all text (stacks with the font's own sizing)</div></div>
                    <div class="setting-input">
                        <select id="app-fontsize">
                            <option value="small" ${fontsize === 'small' ? 'selected' : ''}>Small</option>
                            <option value="default" ${fontsize === 'default' ? 'selected' : ''}>Default</option>
                            <option value="large" ${fontsize === 'large' ? 'selected' : ''}>Large</option>
                            <option value="xlarge" ${fontsize === 'xlarge' ? 'selected' : ''}>Extra Large</option>
                        </select>
                    </div>
                </div>
                <div class="setting-row">
                    <div class="setting-label"><label>Send Button</label><div class="setting-help">Use trim color vs provider indicator</div></div>
                    <div class="setting-input">
                        <label class="setting-toggle">
                            <input type="checkbox" id="app-send-trim" ${localStorage.getItem('sapphire-send-btn-trim') === 'true' ? 'checked' : ''}>
                            <span>Use trim color</span>
                        </label>
                    </div>
                </div>
                <div class="setting-row" data-key="ICON_COLOR">
                    <div class="setting-label"><label>Icon Color</label><div class="setting-help">Tint the gem logo + favicon for this Sapphire (persona trim overrides it)</div></div>
                    <div class="setting-input" style="display:flex;gap:6px;align-items:center">
                        <input type="color" id="icon-color-picker" value="${iconColor || '#036ec3'}" title="Icon color">
                        <input type="hidden" id="setting-ICON_COLOR" data-key="ICON_COLOR" value="${iconColor}">
                        <button class="btn-sm" id="icon-color-clear" title="Reset to sapphire blue">&#x21BA;</button>
                    </div>
                </div>
                <div class="setting-row" data-key="AVATARS_IN_CHAT">
                    <div class="setting-label"><label>Avatars In Chat</label><div class="setting-help">Show avatars next to messages</div></div>
                    <div class="setting-input">
                        <label class="setting-toggle">
                            <input type="checkbox" id="setting-AVATARS_IN_CHAT" data-key="AVATARS_IN_CHAT" ${avatars ? 'checked' : ''}>
                        </label>
                    </div>
                </div>
            </div>
        </div>

        <style>
            /* full-width like the other settings tabs (Krem 2026-08-30);
               the auto-fill grids below add columns on wide screens */
            .setting-section-title { font-weight: 600; font-size: var(--font-sm); color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
            .theme-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
            .theme-card {
                display: flex; flex-direction: column; align-items: center; gap: 6px;
                padding: 8px; border-radius: 10px; cursor: pointer;
                background: var(--bg-secondary); border: 2px solid transparent;
                transition: border-color 0.15s, transform 0.1s;
            }
            .theme-card:hover { transform: translateY(-1px); border-color: var(--border-hover); }
            .theme-card.active { border-color: var(--trim); }
            .theme-card.active .theme-check { display: block; }
            .theme-card-name { font-size: var(--font-xs); font-weight: 600; color: var(--text); text-align: center; }
            .theme-card-badge { font-size: 0.5625rem; color: var(--text-muted); }
            .theme-check { display: none; font-size: 0.625rem; color: var(--trim); }

            /* Miniature app preview — every color is a live var() resolved
               through the card's own data-theme (core) or inline props
               (plugin previews), so cards ARE the theme, not swatches of it. */
            .tc-mini {
                display: flex; width: 100%; height: 78px;
                border-radius: 8px; overflow: hidden;
                background: var(--bg); border: 1px solid var(--border);
            }
            .tc-rail { width: 16px; background: var(--bg-secondary); border-right: 1px solid var(--border);
                display: flex; flex-direction: column; align-items: center; gap: 3px; padding-top: 4px; flex-shrink: 0; }
            .tc-logo { width: 7px; height: 7px; border-radius: 2px; background: var(--trim); }
            .tc-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--text); opacity: .22; }
            .tc-dot.on { background: var(--trim); opacity: 1; }
            .tc-body { flex: 1; display: flex; flex-direction: column; gap: 3px; padding: 5px; min-width: 0; }
            .tc-bubble { border-radius: 4px; height: 14px; }
            .tc-user { background: var(--user-bg, var(--bg-secondary)); width: 62%; align-self: flex-end; }
            .tc-assistant { background: var(--assistant-bg, var(--bg-secondary)); width: 78%; }
            .tc-composer { margin-top: auto; height: 12px; border: 1px solid var(--border); border-radius: 4px;
                background: var(--bg-secondary); display: flex; justify-content: flex-end; align-items: center; padding: 1px 2px; }
            .tc-send { width: 8px; height: 8px; border-radius: 2px; background: var(--primary); }

            /* Font preset cards */
            .font-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
            .font-card {
                position: relative;
                display: flex; flex-direction: column; align-items: center; gap: 2px;
                padding: 12px 8px 8px; border-radius: 10px; cursor: pointer;
                background: var(--bg-secondary); border: 2px solid transparent;
                transition: border-color 0.15s, transform 0.1s;
            }
            .font-card:hover { transform: translateY(-1px); border-color: var(--border-hover); }
            .font-card.active { border-color: var(--trim); }
            .font-card.active .theme-check { display: block; position: absolute; top: 6px; right: 8px; }
            .font-sample { font-size: 1.625rem; color: var(--text); line-height: 1.1; }
            .font-quick { font-size: var(--font-xs); color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
            .font-card-name { font-size: var(--font-xs); font-weight: 600; color: var(--text); margin-top: 4px; }
            .font-dl { position: absolute; top: 6px; left: 8px; font-size: 0.75rem; color: var(--text-muted); }

            /* Ambient motion picker (P3) — card styles live in style.css
               (shared with the chat scene modal) */
            .motion-speed-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
            .motion-speed-row select { min-width: 100px; }
            .theme-settings-panel {
                margin-top: 12px; padding: 14px; border-radius: 10px;
                background: var(--bg-secondary); border: 1px solid var(--border);
            }
            .theme-settings-title { font-weight: 600; font-size: var(--font-sm); margin-bottom: 10px; color: var(--text); }
            .theme-setting-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 0; }
            .theme-setting-row + .theme-setting-row { border-top: 1px solid var(--border); }
            .theme-setting-label { font-size: var(--font-sm); color: var(--text); }
            .theme-setting-help { font-size: var(--font-xs); color: var(--text-muted); }
            .theme-setting-input select, .theme-setting-input input[type="range"] { min-width: 120px; }
            .theme-setting-input input[type="checkbox"] { width: 16px; height: 16px; }
        </style>`;
    },

    async attachListeners(ctx, el) {
        // Load and render theme grid
        await _loadThemeGrid(el);

        // Density
        el.querySelector('#app-fontsize')?.addEventListener('change', e => {
            const v = e.target.value;
            if (v === 'default') {
                document.documentElement.removeAttribute('data-fontsize');
                localStorage.removeItem('sapphire-fontsize');
            } else {
                document.documentElement.setAttribute('data-fontsize', v);
                localStorage.setItem('sapphire-fontsize', v);
            }
        });

        el.querySelector('#app-density')?.addEventListener('change', e => {
            const v = e.target.value;
            if (v === 'default') {
                document.documentElement.removeAttribute('data-density');
                localStorage.removeItem('sapphire-density');
            } else {
                document.documentElement.setAttribute('data-density', v);
                localStorage.setItem('sapphire-density', v);
            }
        });

        // Font preset cards (Type section). Webfont-backed presets show a ⤓
        // badge until their font is downloaded (pinned + sha-verified server
        // side); first click downloads, then applies.
        fontStatus().then(status => {
            el.querySelectorAll('.font-card[data-family]').forEach(c => {
                const fam = c.dataset.family;
                if (fam && !status[fam]?.downloaded) c.querySelector('.font-dl')?.removeAttribute('hidden');
            });
        }).catch(() => {});

        el.querySelector('#font-grid')?.addEventListener('click', async e => {
            const card = e.target.closest('.font-card');
            if (!card) return;
            const v = card.dataset.fontId;
            const fam = card.dataset.family;
            if (fam) {
                const badge = card.querySelector('.font-dl');
                const needs = badge && !badge.hidden;
                if (needs) badge.textContent = '⏳';
                try {
                    await ensureFont(fam);
                    badge?.setAttribute('hidden', '');
                } catch {
                    if (badge) badge.textContent = '⚠';
                    return;
                }
            }
            // Always store the pick, 'system' included — an EXPLICIT choice
            // must outrank a theme bundle's font default (themes-v2 P2).
            localStorage.setItem('sapphire-font', v);
            if (v === 'system') document.documentElement.removeAttribute('data-font');
            else document.documentElement.setAttribute('data-font', v);
            el.querySelectorAll('.font-card').forEach(c => c.classList.toggle('active', c === card));
        });

        // Scene library (Background section) — the GLOBAL underlay, not the
        // current chat's scene (that stays in the chat sidebar's Scene modal).
        // Saves immediately, like theme cards; repaints live only when the
        // current chat is actually riding the underlay.
        const sceneMount = el.querySelector('#visual-scene-mount');
        if (sceneMount) {
            mountScenePicker(sceneMount, {
                current: ctx.getValue('DEFAULT_BACKGROUND') || '',
                onSelect: (name) => {
                    updateSettingsBatch({ DEFAULT_BACKGROUND: name }).catch(() => {});
                    // Mirror into the page's loaded settings (already persisted
                    // above — markChanged would flag a phantom unsaved state).
                    ctx.settings.DEFAULT_BACKGROUND = name;
                    setDefaultBackground(name);
                    const chatScene = document.getElementById('chatbg')?.dataset.scene || '';
                    applyBackground(chatScene);
                }
            }).catch(() => {});
        }

        // Ambient motion picker (P3) — registry is server-fetched; initMotions
        // is idempotent (observers wire once) so re-entering the tab is safe.
        import('../../core/motions.js').then(async m => {
            await m.initMotions();
            _renderMotionRow(el, m);
            const wire = (sel, key, setter) => {
                if (!sel) return;
                try { sel.value = localStorage.getItem(key) || '1'; } catch {}
                if (!sel.value) sel.value = '1';   // stored value not among options
                sel.addEventListener('change', () => setter(parseFloat(sel.value)));
            };
            wire(el.querySelector('#motion-speed'), 'sapphire-motion-speed', m.setMotionSpeed);
            wire(el.querySelector('#motion-intensity'), 'sapphire-motion-intensity', m.setMotionIntensity);
        }).catch(() => {});

        // Icon color (instance-level gem/favicon tint)
        const iconPicker = el.querySelector('#icon-color-picker');
        const iconHidden = el.querySelector('#setting-ICON_COLOR');
        const previewIcon = v => import('../../features/logo.js')
            .then(m => { m.setInstanceColor(v); m.applyLogoTint(''); })
            .catch(() => {});
        iconPicker?.addEventListener('input', () => {
            iconHidden.value = iconPicker.value;
            ctx.markChanged('ICON_COLOR', iconPicker.value);
            previewIcon(iconPicker.value);
        });
        el.querySelector('#icon-color-clear')?.addEventListener('click', () => {
            iconHidden.value = '';
            iconPicker.value = '#036ec3';
            ctx.markChanged('ICON_COLOR', '');
            previewIcon('');
        });

        // Send button trim
        el.querySelector('#app-send-trim')?.addEventListener('change', e => {
            localStorage.setItem('sapphire-send-btn-trim', e.target.checked);
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.classList.toggle('use-trim', e.target.checked);
        });
    }
};


// ── Motion Row ──────────────────────────────────────────────
// Shows and sets the GLOBAL default (Krem's ruling 2026-08-17) — a chat's
// own override lives in the chat scene modal. Painting the effective value
// here made every click look dead whenever the open chat overrode it: the
// click wrote the global pick, the highlight snapped back to the chat's.
// When it comes from the theme bundle (no explicit pick yet) the card
// carries a "theme" badge; a chat override gets a note, not the highlight.

function _renderMotionRow(el, m) {
    const row = el.querySelector('#motion-row');
    if (!row) return;
    const motions = m.getMotions();
    let pick = '';
    try { pick = localStorage.getItem('sapphire-motion') || ''; } catch {}
    const valid = id => motions.some(x => x.id === id);
    // Global-only resolution: explicit pick > theme bundle (orphan picks
    // fall through to the theme, mirroring currentMotionId).
    let globalEff = '';
    if (pick !== 'none') {
        globalEff = (pick && valid(pick)) ? pick
            : (valid(m.themeMotionId()) ? m.themeMotionId() : '');
    }
    const fromTheme = !!globalEff && !(pick && valid(pick));
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const cards = [{ id: 'none', name: 'None' }, ...motions].map(mo => {
        const isNone = mo.id === 'none';
        const active = isNone ? !globalEff : mo.id === globalEff;
        const badge = (fromTheme && !isNone && mo.id === globalEff)
            ? '<span class="motion-badge">theme</span>' : '';
        return `<div class="motion-card${active ? ' active' : ''}" data-motion="${_esc(mo.id)}" title="${_esc(mo.description || '')}">${_esc(mo.name)}${badge}</div>`;
    }).join('');

    const chatOv = m.chatMotionId();
    const chatNote = (chatOv && valid(chatOv))
        ? `<div class="setting-help" style="flex-basis:100%">The open chat overrides this with &ldquo;${_esc((motions.find(x => x.id === chatOv) || {}).name || chatOv)}&rdquo; &mdash; change that from the chat&rsquo;s scene menu.</div>`
        : '';

    // Reduced-motion never locks the picker — an explicit pick is consent
    // (the OS flag only stops THEME-DEFAULT motions from auto-starting).
    row.innerHTML = cards + chatNote + (reduced
        ? '<div class="setting-help" style="flex-basis:100%">Your system prefers reduced motion, so themes won’t auto-start one &mdash; a pick you make here still applies.</div>'
        : '');

    row.querySelectorAll('.motion-card').forEach(c => c.addEventListener('click', () => {
        m.applyMotion(c.dataset.motion === 'none' ? '' : c.dataset.motion);
        _renderMotionRow(el, m);
    }));
}


// ── Theme Grid ──────────────────────────────────────────────

async function _loadThemeGrid(el) {
    const grid = el.querySelector('#theme-grid');
    if (!grid) return;

    const currentTheme = currentThemeId();

    // One source of truth: the server list via core/theme.js. The old
    // window.sapphireThemes legacy merge is gone — it had zero producers
    // anywhere in the tree and fed unvalidated css/script values straight
    // into boot persistence (lifecycle #14 / chaos T2).
    _allThemes = await fetchThemes();

    // Group: core first, then plugin
    const core = _allThemes.filter(t => t.source === 'core');
    const plugin = _allThemes.filter(t => t.source !== 'core');

    // Load every core theme's stylesheet once. All theme rules are
    // [data-theme="x"]-scoped (verified across all 14, P0 recon), so the
    // sheets are inert everywhere except an element carrying the attribute \u2014
    // which is exactly what each card's miniature does. This is what makes
    // the previews REAL instead of extracted swatches.
    for (const t of core) {
        if (!document.querySelector(`link[data-theme-preview="${CSS.escape(t.id)}"]`)) {
            const l = document.createElement('link');
            l.rel = 'stylesheet';
            l.href = t.css;
            l.dataset.themePreview = t.id;
            document.head.appendChild(l);
        }
    }

    const MINI = `
        <div class="tc-rail"><span class="tc-logo"></span><span class="tc-dot on"></span><span class="tc-dot"></span><span class="tc-dot"></span></div>
        <div class="tc-body">
            <div class="tc-bubble tc-user"></div>
            <div class="tc-bubble tc-assistant"></div>
            <div class="tc-composer"><span class="tc-send"></span></div>
        </div>`;

    const cards = [...core, ...plugin].map(t => {
        const isActive = _themeMatchesCurrent(t, currentTheme);
        const hasScripts = t.scripts?.length > 0;
        const scope = t.source === 'core'
            ? ` data-theme="${_esc(t.id)}"`
            : ` data-plugin-mini="${_esc(t.id)}"`;
        return `
            <div class="theme-card ${isActive ? 'active' : ''}" data-theme-id="${_esc(t.id)}" title="${_esc(t.description || t.name)}">
                <div class="tc-mini"${scope}>${MINI}</div>
                <div class="theme-card-name">${t.icon ? _esc(t.icon) + ' ' : ''}${_esc(t.name)}</div>
                ${hasScripts ? '<div class="theme-card-badge">animated</div>' : ''}
                <div class="theme-check">\u2713</div>
            </div>`;
    }).join('');

    grid.innerHTML = cards || '<div class="text-muted" style="font-size:var(--font-sm)">No themes found</div>';

    // Plugin minis can't use data-theme scoping (their CSS isn't loaded here,
    // and loading arbitrary plugin CSS globally would be a leak risk) \u2014 paint
    // their declared preview colors as inline custom properties instead.
    // setProperty with a value-shape gate, never string-templated into style
    // attributes (chaos hunt T4).
    const COLOR_RE = /^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\))$/;
    grid.querySelectorAll('[data-plugin-mini]').forEach(miniEl => {
        const t = _allThemes.find(x => x.id === miniEl.dataset.pluginMini);
        const p = (t && t.preview) || {};
        const accent = p.accent || p.trim;
        const map = {
            '--bg': p.bg, '--bg-secondary': p.bg2, '--text': p.text,
            '--trim': accent, '--primary': accent, '--border': p.border,
            '--user-bg': p.bg2, '--assistant-bg': p.bg2,
        };
        for (const [k, v] of Object.entries(map)) {
            if (typeof v === 'string' && COLOR_RE.test(v.trim())) miniEl.style.setProperty(k, v.trim());
        }
    });

    // Click handler
    const settingsPanel = el.querySelector('#theme-settings-panel');
    grid.addEventListener('click', e => {
        const card = e.target.closest('.theme-card');
        if (!card) return;
        // Re-clicking the active card used to re-run the whole apply and
        // double-inject theme scripts (lifecycle #5).
        if (card.classList.contains('active')) return;
        const themeId = card.dataset.themeId;
        const theme = _allThemes.find(t => t.id === themeId);
        if (!theme) return;
        applyTheme(theme);
        // Update active state
        grid.querySelectorAll('.theme-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        // Show/hide theme settings
        _renderThemeSettings(settingsPanel, theme);
        // The bundle may have swapped the running motion and font — repaint
        // both pickers so they tell the truth (hunt 2026-08-17: Abyss
        // started fireflies while the row still showed None; Paper switched
        // the font while Type still highlighted System).
        import('../../core/motions.js').then(m => _renderMotionRow(el, m)).catch(() => {});
        let fPick = null;
        try { fPick = localStorage.getItem('sapphire-font'); } catch {}
        const effFont = fPick || theme.font || 'system';
        el.querySelectorAll('#font-grid .font-card').forEach(c =>
            c.classList.toggle('active', c.dataset.fontId === effFont));
    });

    // Show settings for currently active theme on load
    const activeTheme = _allThemes.find(t => _themeMatchesCurrent(t, currentTheme));
    if (activeTheme) _renderThemeSettings(settingsPanel, activeTheme);
}


function _renderThemeSettings(panel, theme) {
    if (!panel) return;
    const settings = theme.settings || [];
    if (!settings.length) {
        panel.style.display = 'none';
        panel.innerHTML = '';
        return;
    }

    const rows = settings.map(s => {
        const key = s.key || '';
        // Namespaced per-theme storage (theme:{id}:{key}) — bare manifest
        // keys could clobber sapphire-* app state (lifecycle #9 / chaos T7).
        const current = localStorage.getItem(settingKey(theme.id, key)) ?? s.default ?? '';
        let input = '';

        if (s.type === 'select' && s.options) {
            input = `<select data-setting-key="${_esc(key)}">
                ${s.options.map(o => {
                    const val = typeof o === 'string' ? o : o.value;
                    const label = typeof o === 'string' ? o : (o.label || o.value);
                    return `<option value="${_esc(val)}" ${val === current ? 'selected' : ''}>${_esc(label)}</option>`;
                }).join('')}
            </select>`;
        } else if (s.type === 'boolean' || s.type === 'checkbox') {
            const checked = current === 'true' || current === true;
            input = `<input type="checkbox" data-setting-key="${_esc(key)}" ${checked ? 'checked' : ''}>`;
        } else if (s.type === 'range') {
            // Number() coercion: min/max/step come from third-party manifests
            // and were interpolated raw into attributes (chaos hunt T3).
            const nMin = Number(s.min) || 0, nMax = Number(s.max) || 100, nStep = Number(s.step) || 1;
            input = `<input type="range" data-setting-key="${_esc(key)}"
                min="${nMin}" max="${nMax}" step="${nStep}" value="${_esc(current)}">
                <span class="text-muted" style="font-size:var(--font-xs);min-width:30px;text-align:right">${_esc(current)}</span>`;
        } else {
            input = `<input type="text" data-setting-key="${_esc(key)}" value="${_esc(current)}" style="width:120px">`;
        }

        return `
            <div class="theme-setting-row">
                <div>
                    <div class="theme-setting-label">${_esc(s.label || s.key)}</div>
                    ${s.help ? `<div class="theme-setting-help">${_esc(s.help)}</div>` : ''}
                </div>
                <div class="theme-setting-input">${input}</div>
            </div>`;
    }).join('');

    panel.innerHTML = `
        <div class="theme-settings-panel">
            <div class="theme-settings-title">${_esc(theme.icon || '')} ${_esc(theme.name)} Settings</div>
            ${rows}
        </div>`;
    panel.style.display = '';

    // Wire change handlers — write to localStorage + dispatch event for live themes
    panel.querySelectorAll('[data-setting-key]').forEach(input => {
        const handler = () => {
            const key = input.dataset.settingKey;
            const val = input.type === 'checkbox' ? String(input.checked) : input.value;
            try { localStorage.setItem(settingKey(theme.id, key), val); } catch {}
            // Update range display
            if (input.type === 'range') {
                const span = input.nextElementSibling;
                if (span) span.textContent = val;
            }
            // Apply data-attribute for chat-style settings (frosted glass etc.)
            const chatStyleMatch = key.match(/^(.+)-chat-style$/);
            if (chatStyleMatch) {
                document.documentElement.setAttribute(`data-${chatStyleMatch[1]}-chat`, val);
            }
            // Notify live theme JS (they can listen for storage events or custom events)
            window.dispatchEvent(new CustomEvent('sapphire-theme-setting', { detail: { key, value: val } }));
        };
        input.addEventListener('change', handler);
        if (input.type === 'range') input.addEventListener('input', handler);
    });
}


function _themeMatchesCurrent(theme, currentId) {
    return theme.id === currentId;
}

function _esc(s) { return String(s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
