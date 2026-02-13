// settings-tabs/appearance.js - Theme, density, font, trim color
// All appearance settings use localStorage (client-side only)

const TRIM_PRESETS = [
    { value: '#4a9eff', label: 'Blue' },
    { value: '#00cccc', label: 'Cyan' },
    { value: '#2ecc71', label: 'Green' },
    { value: '#f39c12', label: 'Orange' },
    { value: '#e74c3c', label: 'Red' },
    { value: '#9b59b6', label: 'Purple' },
    { value: '#ff66b2', label: 'Pink' },
];

export default {
    id: 'appearance',
    name: 'Visual',
    icon: '\uD83C\uDFA8',
    description: 'Theme, spacing, and font settings',

    render(ctx) {
        const theme = localStorage.getItem('sapphire-theme') || 'dark';
        const density = localStorage.getItem('sapphire-density') || 'default';
        const font = localStorage.getItem('sapphire-font') || 'system';
        const trim = localStorage.getItem('sapphire-trim') || '#4a9eff';

        return `<div class="settings-grid">
            <div class="setting-row">
                <div class="setting-label"><label>Theme</label><div class="setting-help">Color scheme</div></div>
                <div class="setting-input">
                    <select id="app-theme">
                        ${ctx.availableThemes.map(t => `<option value="${t}" ${t === theme ? 'selected' : ''}>${t.charAt(0).toUpperCase() + t.slice(1)}</option>`).join('')}
                    </select>
                </div>
            </div>
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
                <div class="setting-label"><label>Trim Color</label><div class="setting-help">Accent color for active states</div></div>
                <div class="setting-input">
                    <div class="trim-swatches">
                        ${TRIM_PRESETS.map(p => `<button type="button" class="trim-swatch${p.value === trim ? ' active' : ''}" data-trim="${p.value}" style="background:${p.value}" title="${p.label}"></button>`).join('')}
                    </div>
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
        </div>`;
    },

    attachListeners(ctx, el) {
        el.querySelector('#app-theme')?.addEventListener('change', e => {
            const theme = e.target.value;
            localStorage.setItem('sapphire-theme', theme);
            document.documentElement.setAttribute('data-theme', theme);
            const link = document.getElementById('theme-stylesheet');
            if (link) link.href = `/static/themes/${theme}.css`;
            else {
                const l = document.createElement('link');
                l.id = 'theme-stylesheet'; l.rel = 'stylesheet';
                l.href = `/static/themes/${theme}.css`;
                document.head.appendChild(l);
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

        el.querySelectorAll('.trim-swatch').forEach(s => {
            s.addEventListener('click', () => {
                const color = s.dataset.trim;
                const root = document.documentElement;
                const r = parseInt(color.slice(1, 3), 16);
                const g = parseInt(color.slice(3, 5), 16);
                const b = parseInt(color.slice(5, 7), 16);
                root.style.setProperty('--trim', color);
                root.style.setProperty('--trim-glow', `rgba(${r},${g},${b},0.35)`);
                root.style.setProperty('--trim-light', `rgba(${r},${g},${b},0.15)`);
                root.style.setProperty('--trim-border', `rgba(${r},${g},${b},0.4)`);
                root.style.setProperty('--trim-50', `rgba(${r},${g},${b},0.5)`);
                root.style.setProperty('--accordion-header-bg', `rgba(${r},${g},${b},0.08)`);
                root.style.setProperty('--accordion-header-hover', `rgba(${r},${g},${b},0.12)`);
                localStorage.setItem('sapphire-trim', color);
                el.querySelectorAll('.trim-swatch').forEach(x => x.classList.remove('active'));
                s.classList.add('active');
            });
        });

        el.querySelector('#app-send-trim')?.addEventListener('change', e => {
            localStorage.setItem('sapphire-send-btn-trim', e.target.checked);
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) sendBtn.classList.toggle('use-trim', e.target.checked);
        });
    }
};
