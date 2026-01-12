// tabs/appearance.js - Theme, density, font, and visual settings tab

const DENSITY_OPTIONS = [
  { value: 'compact', label: 'Compact', desc: 'Tighter spacing, smaller elements' },
  { value: 'default', label: 'Default', desc: 'Balanced spacing' },
  { value: 'comfortable', label: 'Comfortable', desc: 'More breathing room' }
];

const FONT_OPTIONS = [
  { value: 'system', label: 'System', desc: 'Native system fonts' },
  { value: 'mono', label: 'Monospace', desc: 'Code-style throughout' },
  { value: 'serif', label: 'Serif', desc: 'Classic reading style' },
  { value: 'rounded', label: 'Rounded', desc: 'Friendly geometric sans' }
];

const TRIM_PRESETS = [
  { value: '#4a9eff', label: 'Blue', class: 'trim-blue' },
  { value: '#00cccc', label: 'Cyan', class: 'trim-cyan' },
  { value: '#2ecc71', label: 'Green', class: 'trim-green' },
  { value: '#f39c12', label: 'Orange', class: 'trim-orange' },
  { value: '#e74c3c', label: 'Red', class: 'trim-red' },
  { value: '#9b59b6', label: 'Purple', class: 'trim-purple' },
  { value: '#ff66b2', label: 'Pink', class: 'trim-pink' },
  { value: '#888888', label: 'Gray', class: 'trim-gray' }
];

export default {
  id: 'appearance',
  name: 'Visual',
  icon: '🎨',
  description: 'Theme, spacing, and font settings',
  keys: [], // These are localStorage, not backend config

  render(modal) {
    const themeOptions = modal.availableThemes.map(theme => {
      const selected = theme === modal.currentTheme ? 'selected' : '';
      const displayName = theme.charAt(0).toUpperCase() + theme.slice(1);
      return `<option value="${theme}" ${selected}>${displayName}</option>`;
    }).join('');

    const currentDensity = localStorage.getItem('sapphire-density') || 'default';
    const densityOptions = DENSITY_OPTIONS.map(opt => {
      const selected = opt.value === currentDensity ? 'selected' : '';
      return `<option value="${opt.value}" ${selected}>${opt.label}</option>`;
    }).join('');

    const currentFont = localStorage.getItem('sapphire-font') || 'system';
    const fontOptions = FONT_OPTIONS.map(opt => {
      const selected = opt.value === currentFont ? 'selected' : '';
      return `<option value="${opt.value}" ${selected}>${opt.label}</option>`;
    }).join('');

    const currentTrim = localStorage.getItem('sapphire-trim') || '#4a9eff';
    const trimSwatches = TRIM_PRESETS.map(preset => {
      const active = preset.value === currentTrim ? 'active' : '';
      return `<button type="button" class="trim-swatch ${active}" data-trim="${preset.value}" style="background: ${preset.value}" title="${preset.label}"></button>`;
    }).join('');

    return `
      <div class="appearance-container">
        <div class="appearance-controls">
          <div class="setting-row">
            <div class="setting-label">
              <label for="theme-select">Theme</label>
              <div class="help-text-short">Color scheme</div>
            </div>
            <div class="setting-input">
              <select id="theme-select">${themeOptions}</select>
            </div>
          </div>

          <div class="setting-row">
            <div class="setting-label">
              <label for="density-select">Spacing</label>
              <div class="help-text-short">UI density level</div>
            </div>
            <div class="setting-input">
              <select id="density-select">${densityOptions}</select>
            </div>
          </div>

          <div class="setting-row">
            <div class="setting-label">
              <label for="font-select">Font</label>
              <div class="help-text-short">Text style</div>
            </div>
            <div class="setting-input">
              <select id="font-select">${fontOptions}</select>
            </div>
          </div>

          <div class="setting-row">
            <div class="setting-label">
              <label>Trim Color</label>
              <div class="help-text-short">Accent for active states</div>
            </div>
            <div class="setting-input">
              <div class="trim-swatches">${trimSwatches}</div>
            </div>
          </div>
        </div>
        
        <div class="theme-preview">
          <div class="preview-titlebar">
            <span class="preview-dots">
              <span class="dot red"></span>
              <span class="dot yellow"></span>
              <span class="dot green"></span>
            </span>
            <span class="preview-title">Preview</span>
          </div>
          <div class="preview-content">
            <p class="preview-text">Sample text showing how content appears with current settings.</p>
            
            <div class="preview-accordion">
              <div class="preview-accordion-header">
                <span class="preview-toggle">▼</span>
                <span>Accordion Section</span>
              </div>
              <div class="preview-accordion-body">Collapsed content area</div>
            </div>
            
            <div class="preview-buttons">
              <span class="preview-btn primary">Primary</span>
              <span class="preview-btn secondary">Secondary</span>
              <span class="preview-btn trim">Trim</span>
            </div>
            
            <div class="preview-messages">
              <div class="preview-bubble user">User message bubble</div>
              <div class="preview-bubble assistant">Assistant response bubble</div>
            </div>
            
            <div class="preview-input">
              <span class="preview-input-box">Type message...</span>
              <span class="preview-send">Send</span>
            </div>
          </div>
        </div>
      </div>
      
      <style>
        .appearance-container {
          display: flex;
          gap: var(--space-lg);
        }
        .appearance-controls {
          flex: 1;
          min-width: 0;
        }
        .theme-preview {
          width: 240px;
          flex-shrink: 0;
          border: 1px solid var(--border);
          border-radius: var(--radius-lg);
          overflow: hidden;
          background: var(--bg);
        }
        .preview-titlebar {
          display: flex;
          align-items: center;
          gap: var(--space-sm);
          padding: var(--space-sm) var(--space-md);
          background: var(--bg-tertiary);
          border-bottom: 1px solid var(--border);
        }
        .preview-dots {
          display: flex;
          gap: 4px;
        }
        .preview-dots .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
        }
        .preview-dots .dot.red { background: #ff5f56; }
        .preview-dots .dot.yellow { background: #ffbd2e; }
        .preview-dots .dot.green { background: #27c93f; }
        .preview-title {
          font-size: var(--font-xs);
          color: var(--text-muted);
        }
        .preview-content {
          padding: var(--space-md);
          font-size: var(--font-sm);
        }
        .preview-text {
          margin: 0 0 var(--space-md) 0;
          color: var(--text);
          line-height: 1.4;
        }
        .preview-accordion {
          margin-bottom: var(--space-md);
          border: 1px solid var(--border);
          border-radius: var(--radius-sm);
          overflow: hidden;
        }
        .preview-accordion-header {
          display: flex;
          align-items: center;
          gap: var(--space-xs);
          padding: var(--space-xs) var(--space-sm);
          background: var(--bg-tertiary);
          color: var(--text-secondary);
          font-size: var(--font-xs);
        }
        .preview-toggle {
          font-size: 8px;
          color: var(--text-dim);
        }
        .preview-accordion-body {
          padding: var(--space-xs) var(--space-sm);
          background: var(--bg-secondary);
          color: var(--text-muted);
          font-size: var(--font-xs);
        }
        .preview-buttons {
          display: flex;
          gap: var(--space-xs);
          margin-bottom: var(--space-md);
        }
        .preview-btn {
          padding: var(--space-xs) var(--space-sm);
          border-radius: var(--radius-sm);
          font-size: var(--font-xs);
          color: var(--text-bright);
        }
        .preview-btn.primary { background: var(--primary); }
        .preview-btn.secondary { background: var(--bg-tertiary); border: 1px solid var(--border); color: var(--text); }
        .preview-btn.trim { background: var(--trim, var(--accent-blue)); }
        .preview-messages {
          display: flex;
          flex-direction: column;
          gap: var(--space-xs);
          margin-bottom: var(--space-md);
        }
        .preview-bubble {
          padding: var(--space-xs) var(--space-sm);
          border-radius: var(--radius-md);
          font-size: var(--font-xs);
          max-width: 80%;
        }
        .preview-bubble.user {
          background: var(--user-bg);
          align-self: flex-end;
        }
        .preview-bubble.assistant {
          background: var(--assistant-bg);
          align-self: flex-start;
        }
        .preview-input {
          display: flex;
          gap: var(--space-xs);
          align-items: center;
        }
        .preview-input-box {
          flex: 1;
          padding: var(--space-xs) var(--space-sm);
          background: var(--input-bg);
          border: 1px solid var(--border);
          border-radius: var(--radius-sm);
          color: var(--text-muted);
          font-size: var(--font-xs);
        }
        .preview-send {
          padding: var(--space-xs) var(--space-sm);
          background: var(--primary);
          border-radius: var(--radius-sm);
          color: var(--text-bright);
          font-size: var(--font-xs);
        }
        
        /* Trim swatches */
        .trim-swatches {
          display: flex;
          gap: var(--space-xs);
          flex-wrap: wrap;
        }
        .trim-swatch {
          width: 24px;
          height: 24px;
          border-radius: var(--radius-sm);
          border: 2px solid transparent;
          cursor: pointer;
          transition: all var(--transition-fast);
        }
        .trim-swatch:hover {
          transform: scale(1.1);
        }
        .trim-swatch.active {
          border-color: var(--text-bright);
          box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--text-bright);
        }
        
        @media (max-width: 600px) {
          .appearance-container {
            flex-direction: column;
          }
          .theme-preview {
            width: 100%;
          }
        }
      </style>
    `;
  },

  attachListeners(modal, contentEl) {
    const themeSelect = contentEl.querySelector('#theme-select');
    if (themeSelect) {
      themeSelect.addEventListener('change', (e) => this.handleThemeChange(modal, e.target.value));
    }

    const densitySelect = contentEl.querySelector('#density-select');
    if (densitySelect) {
      densitySelect.addEventListener('change', (e) => this.handleDensityChange(e.target.value));
    }

    const fontSelect = contentEl.querySelector('#font-select');
    if (fontSelect) {
      fontSelect.addEventListener('change', (e) => this.handleFontChange(e.target.value));
    }

    const trimSwatches = contentEl.querySelectorAll('.trim-swatch');
    trimSwatches.forEach(swatch => {
      swatch.addEventListener('click', (e) => {
        const color = e.target.dataset.trim;
        this.handleTrimChange(color, contentEl);
      });
    });
  },

  handleThemeChange(modal, themeName) {
    modal.currentTheme = themeName;
    localStorage.setItem('sapphire-theme', themeName);
    document.documentElement.setAttribute('data-theme', themeName);
    this.loadThemeCSS(themeName);
  },

  handleDensityChange(density) {
    if (density === 'default') {
      document.documentElement.removeAttribute('data-density');
      localStorage.removeItem('sapphire-density');
    } else {
      document.documentElement.setAttribute('data-density', density);
      localStorage.setItem('sapphire-density', density);
    }
  },

  handleFontChange(font) {
    if (font === 'system') {
      document.documentElement.removeAttribute('data-font');
      localStorage.removeItem('sapphire-font');
    } else {
      document.documentElement.setAttribute('data-font', font);
      localStorage.setItem('sapphire-font', font);
    }
  },

  handleTrimChange(color, contentEl) {
    document.documentElement.style.setProperty('--trim', color);
    // Generate matching glow color
    const glowColor = this.hexToRgba(color, 0.3);
    document.documentElement.style.setProperty('--trim-glow', glowColor);
    localStorage.setItem('sapphire-trim', color);

    // Update active state in UI
    const swatches = contentEl.querySelectorAll('.trim-swatch');
    swatches.forEach(s => s.classList.remove('active'));
    const active = contentEl.querySelector(`[data-trim="${color}"]`);
    if (active) active.classList.add('active');
  },

  hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  },

  loadThemeCSS(themeName) {
    const existingLink = document.getElementById('theme-stylesheet');
    const href = `/static/themes/${themeName}.css`;
    
    if (existingLink) {
      existingLink.href = href;
    } else {
      const link = document.createElement('link');
      link.id = 'theme-stylesheet';
      link.rel = 'stylesheet';
      link.href = href;
      document.head.appendChild(link);
    }
  }
};