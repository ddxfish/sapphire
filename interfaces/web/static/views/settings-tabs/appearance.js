// settings-tabs/appearance.js - Visual settings: color sets, type, background, options
// Appearance settings use localStorage (client-side only), except data-key rows.
// P1 of themes-v2 (plan: tmp/themes-v2-plan.md): true miniature theme cards via
// [data-theme] scoping, font preset cards, scene library surfaced here.

import { mountScenePicker } from '../../shared/scene-picker.js';
import { applyBackground, setDefaultBackground } from '../../features/chat-settings.js';
import { updateSettingsBatch } from '../../shared/settings-api.js';

let _allThemes = [];

// Font presets — stacks mirror the [data-font] blocks in shared.css.
// P2 adds downloadable webfont presets to this same grid.
const FONT_PRESETS = [
    { id: 'system',  label: 'System',    stack: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` },
    { id: 'mono',    label: 'Monospace', stack: `'Monaco', 'Menlo', 'Consolas', 'Ubuntu Mono', monospace` },
    { id: 'serif',   label: 'Serif',     stack: `'Georgia', 'Cambria', 'Times New Roman', serif` },
    { id: 'rounded', label: 'Rounded',   stack: `'Nunito', 'Varela Round', -apple-system, sans-serif` },
];

export default {
    id: 'appearance',
    name: 'Visual',
    icon: '\uD83C\uDFA8',
    description: 'Theme, spacing, and font settings',

    render(ctx) {
        const currentTheme = localStorage.getItem('sapphire-theme') || 'dark';
        const density = localStorage.getItem('sapphire-density') || 'default';
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
                    <div class="font-card ${font === f.id ? 'active' : ''}" data-font-id="${f.id}" style="font-family:${f.stack}">
                        <div class="font-sample">Aa</div>
                        <div class="font-quick">The quick brown fox jumps</div>
                        <div class="font-card-name">${f.label}</div>
                        <div class="theme-check">✓</div>
                    </div>`).join('')}
            </div>

            <div class="setting-section-title" style="margin-top:20px">Background</div>
            <div class="setting-help" style="margin-bottom:8px">Global underlay &mdash; shown whenever a chat has no scene of its own. A chat's scene (set from the chat sidebar) always wins.</div>
            <div id="visual-scene-mount"></div>

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
            .appearance-page { max-width: 900px; }
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
            .theme-card-badge { font-size: 9px; color: var(--text-muted); }
            .theme-check { display: none; font-size: 10px; color: var(--trim); }

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
            .font-sample { font-size: 26px; color: var(--text); line-height: 1.1; }
            .font-quick { font-size: var(--font-xs); color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
            .font-card-name { font-size: var(--font-xs); font-weight: 600; color: var(--text); margin-top: 4px; font-family: var(--font-body); }
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

        // Font preset cards (Type section)
        el.querySelector('#font-grid')?.addEventListener('click', e => {
            const card = e.target.closest('.font-card');
            if (!card) return;
            const v = card.dataset.fontId;
            if (v === 'system') {
                document.documentElement.removeAttribute('data-font');
                localStorage.removeItem('sapphire-font');
            } else {
                document.documentElement.setAttribute('data-font', v);
                localStorage.setItem('sapphire-font', v);
            }
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


// ── Theme Grid ──────────────────────────────────────────────

async function _loadThemeGrid(el) {
    const grid = el.querySelector('#theme-grid');
    if (!grid) return;

    const currentTheme = localStorage.getItem('sapphire-theme') || 'dark';

    // Gather themes from all sources
    _allThemes = [];

    // 1. API themes (core + manifest plugins)
    try {
        const res = await fetch('/api/themes');
        if (res.ok) {
            const data = await res.json();
            _allThemes.push(...(data.themes || []));
        }
    } catch {}

    // 2. Legacy plugin themes (window.sapphireThemes global)
    if (window.sapphireThemes) {
        try {
            const legacy = window.sapphireThemes.getAll();
            for (const [id, t] of Object.entries(legacy || {})) {
                if (_allThemes.find(x => x.id === id)) continue; // skip dupes
                // Check for settings: declared on theme object, or via getSettings()
                let settings = t.settings || [];
                if (!settings.length && window.sapphireThemes.getSettings) {
                    try { settings = window.sapphireThemes.getSettings(id) || []; } catch {}
                }
                _allThemes.push({
                    id, name: t.name || id, icon: t.icon || '',
                    description: t.description || '',
                    source: 'plugin-legacy',
                    css: t.css || '',
                    scripts: t.scripts || [],
                    preview: t.preview || {},
                    settings,
                });
            }
        } catch {}
    }

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
        const themeId = card.dataset.themeId;
        const theme = _allThemes.find(t => t.id === themeId);
        if (!theme) return;
        _applyTheme(theme);
        // Update active state
        grid.querySelectorAll('.theme-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        // Show/hide theme settings
        _renderThemeSettings(settingsPanel, theme);
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
        const current = localStorage.getItem(key) ?? s.default ?? '';
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
            localStorage.setItem(key, val);
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


function _applyTheme(theme) {
    // 0. Clear old chat-style data attributes (prevents bleed between themes)
    Array.from(document.documentElement.attributes)
        .filter(a => a.name.startsWith('data-') && a.name.endsWith('-chat'))
        .forEach(a => document.documentElement.removeAttribute(a.name));

    // 1. Remove old theme scripts
    document.querySelectorAll('script[data-theme-script]').forEach(s => s.remove());

    // 2. Apply CSS
    if (theme.source === 'core') {
        // Core themes use data-theme attribute + stylesheet link
        document.documentElement.setAttribute('data-theme', theme.id);
        localStorage.setItem('sapphire-theme', theme.id);
        const link = document.getElementById('theme-stylesheet');
        if (link) { link.href = theme.css; link.disabled = false; }
        else {
            const l = document.createElement('link');
            l.id = 'theme-stylesheet'; l.rel = 'stylesheet';
            l.href = theme.css;
            document.head.appendChild(l);
        }
        // Remove plugin theme CSS if any
        const pluginCSS = document.getElementById('plugin-theme-css');
        if (pluginCSS) pluginCSS.remove();
        // Clear plugin theme data from localStorage
        localStorage.removeItem('sapphire-theme-data');
    } else {
        // Plugin themes use their own CSS file
        document.documentElement.setAttribute('data-theme', theme.id);
        localStorage.setItem('sapphire-theme', theme.id);
        // Store full theme info for reload
        localStorage.setItem('sapphire-theme-data', JSON.stringify({
            id: theme.id, source: theme.source, css: theme.css, scripts: theme.scripts || []
        }));
        // Load plugin CSS
        let pluginCSS = document.getElementById('plugin-theme-css');
        if (pluginCSS) {
            pluginCSS.href = theme.css;
        } else {
            pluginCSS = document.createElement('link');
            pluginCSS.id = 'plugin-theme-css';
            pluginCSS.rel = 'stylesheet';
            pluginCSS.href = theme.css;
            document.head.appendChild(pluginCSS);
        }
        // Disable core theme stylesheet to avoid conflicts
        const coreLink = document.getElementById('theme-stylesheet');
        if (coreLink) coreLink.disabled = true;
    }

    // 3. Load scripts (animated backgrounds)
    if (theme.scripts?.length) {
        for (const src of theme.scripts) {
            const s = document.createElement('script');
            s.src = src;
            s.dataset.themeScript = theme.id;
            document.body.appendChild(s);
        }
    }
}


function _themeMatchesCurrent(theme, currentId) {
    return theme.id === currentId;
}

function _esc(s) { return String(s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
