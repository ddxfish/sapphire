// settings-tabs/appearance.js - Theme picker, density, font settings
// Appearance settings use localStorage (client-side only)

let _allThemes = [];

export default {
    id: 'appearance',
    name: 'Visual',
    icon: '\uD83C\uDFA8',
    description: 'Theme, spacing, and font settings',

    render(ctx) {
        const currentTheme = localStorage.getItem('sapphire-theme') || 'dark';
        const density = localStorage.getItem('sapphire-density') || 'default';
        const font = localStorage.getItem('sapphire-font') || 'system';
        const avatars = ctx.settings.AVATARS_IN_CHAT ?? true;

        return `
        <div class="appearance-page">
            <div class="setting-section-title">Theme</div>
            <div class="theme-grid" id="theme-grid">
                <div class="text-muted" style="font-size:var(--font-sm);padding:12px">Loading themes...</div>
            </div>
            <div id="theme-settings-panel" style="display:none"></div>

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
                    <div class="setting-label"><label>Font</label><div class="setting-help">Text style</div></div>
                    <div class="setting-input">
                        <select id="app-font">
                            <option value="system" ${font === 'system' ? 'selected' : ''}>System</option>
                            <option value="mono" ${font === 'mono' ? 'selected' : ''}>Monospace</option>
                            <option value="serif" ${font === 'serif' ? 'selected' : ''}>Serif</option>
                            <option value="rounded" ${font === 'rounded' ? 'selected' : ''}>Rounded</option>
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
                <div class="setting-row" data-key="AVATARS_IN_CHAT">
                    <div class="setting-label"><label>Avatars In Chat</label><div class="setting-help">Show avatars next to messages</div></div>
                    <div class="setting-input">
                        <label class="setting-toggle">
                            <input type="checkbox" id="setting-AVATARS_IN_CHAT" data-key="AVATARS_IN_CHAT" ${avatars ? 'checked' : ''}>
                            <span>${avatars ? 'Enabled' : 'Disabled'}</span>
                        </label>
                    </div>
                </div>
            </div>
        </div>

        <style>
            .appearance-page { max-width: 900px; }
            .setting-section-title { font-weight: 600; font-size: var(--font-sm); color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
            .theme-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; }
            .theme-card {
                display: flex; flex-direction: column; align-items: center; gap: 6px;
                padding: 10px 8px; border-radius: 10px; cursor: pointer;
                background: var(--bg-secondary); border: 2px solid transparent;
                transition: border-color 0.15s, transform 0.1s;
            }
            .theme-card:hover { transform: translateY(-1px); border-color: var(--border-hover, #555); }
            .theme-card.active { border-color: var(--accent, #4a9eff); }
            .theme-card.active .theme-check { display: block; }
            .theme-swatch { display: flex; gap: 3px; width: 100%; height: 28px; border-radius: 6px; overflow: hidden; }
            .theme-swatch-bar { flex: 1; }
            .theme-card-name { font-size: var(--font-xs); font-weight: 600; color: var(--text); text-align: center; }
            .theme-card-badge { font-size: 9px; color: var(--text-muted); }
            .theme-check { display: none; font-size: 10px; color: var(--accent, #4a9eff); }
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

        // Font
        el.querySelector('#app-font')?.addEventListener('change', e => {
            const v = e.target.value;
            if (v === 'system') {
                document.documentElement.removeAttribute('data-font');
                localStorage.removeItem('sapphire-font');
            } else {
                document.documentElement.setAttribute('data-font', v);
                localStorage.setItem('sapphire-font', v);
            }
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

    const cards = [...core, ...plugin].map(t => {
        const p = t.preview || {};
        const isActive = _themeMatchesCurrent(t, currentTheme);
        const hasScripts = t.scripts?.length > 0;
        const bg = p.bg || '#1a1a2e';
        const bg2 = p.bg2 || _darken(bg);
        const text = p.text || '#ccc';
        const accent = p.accent || p.trim || '#4a9eff';
        const border = p.border || '#333';

        return `
            <div class="theme-card ${isActive ? 'active' : ''}" data-theme-id="${_esc(t.id)}" title="${_esc(t.description || t.name)}">
                <div class="theme-swatch">
                    <div class="theme-swatch-bar" style="background:${_esc(bg)}"></div>
                    <div class="theme-swatch-bar" style="background:${_esc(bg2)}"></div>
                    <div class="theme-swatch-bar" style="background:${_esc(text)}"></div>
                    <div class="theme-swatch-bar" style="background:${_esc(accent)}"></div>
                    <div class="theme-swatch-bar" style="background:${_esc(border)}"></div>
                </div>
                <div class="theme-card-name">${t.icon ? t.icon + ' ' : ''}${_esc(t.name)}</div>
                ${hasScripts ? '<div class="theme-card-badge">animated</div>' : ''}
                <div class="theme-check">\u2713</div>
            </div>`;
    }).join('');

    grid.innerHTML = cards || '<div class="text-muted" style="font-size:var(--font-sm)">No themes found</div>';

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
        const current = localStorage.getItem(key) || s.default || '';
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
            input = `<input type="range" data-setting-key="${_esc(key)}"
                min="${s.min || 0}" max="${s.max || 100}" step="${s.step || 1}" value="${_esc(current)}">
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
            <div class="theme-settings-title">${theme.icon || ''} ${_esc(theme.name)} Settings</div>
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

function _darken(hex) {
    // Simple darken for bg2 when not provided
    try {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgb(${Math.max(0, r + 15)}, ${Math.max(0, g + 15)}, ${Math.max(0, b + 15)})`;
    } catch { return '#222'; }
}

function _esc(s) { return String(s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
