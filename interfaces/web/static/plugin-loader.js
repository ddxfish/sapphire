// plugin-loader.js - Plugin system with auto-discovery and collapsible wrappers
// Optimized for parallel plugin loading
import { showHelpModal } from './shared/modal.js';

class PluginLoader {
  constructor(containerSelector) {
    this.container = document.querySelector(containerSelector);
    this.iconContainer = document.querySelector('#plugin-icon-area');
    this.plugins = [];
    this.config = null;
    this.injectGlobalStyles();
  }

  injectGlobalStyles() {
    if (document.getElementById('plugin-global-styles')) return;
    
    const style = document.createElement('style');
    style.id = 'plugin-global-styles';
    style.textContent = `
      #plugin-icon-area:empty { display: none; }
      
      #plugin-icon-area:not(:empty) {
        border-bottom: 1px solid var(--border);
        padding-bottom: 4px;
        margin-bottom: 4px;
      }

      .plugin-menu-item {
        display: block;
        width: 100%;
        padding: 10px 14px;
        border: none;
        background: transparent;
        color: var(--text);
        font-size: 13px;
        text-align: left;
        cursor: pointer;
        transition: background var(--transition-fast);
      }

      .plugin-menu-item:hover { background: var(--bg-hover); }

      .plugin-wrapper { margin-bottom: 0px; }

      .plugin-wrapper > .accordion-header {
        padding: 8px 12px;
        background: var(--bg-tertiary);
        border-top: 1px solid var(--border-light);
        border-bottom: 1px solid var(--border-light);
      }

      .plugin-wrapper > .accordion-header:hover { background: var(--bg-hover); }

      .plugin-wrapper > .accordion-header h3 {
        margin: 0;
        font-size: 14px;
        color: var(--text-light);
        font-weight: 600;
        flex: 1;
      }

      .plugin-help-btn {
        width: 18px;
        height: 18px;
        padding: 0;
        border: 0px solid var(--border-light);
        border-radius: 50%;
        background: transparent;
        color: var(--text-muted);
        font-size: 11px;
        font-weight: bold;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all var(--transition-normal);
        flex-shrink: 0;
      }

      .plugin-help-btn:hover {
        background: var(--accent-blue-light);
        border-color: var(--accent-blue);
        color: var(--accent-blue);
      }
    `;
    document.head.appendChild(style);
  }

  async loadPlugins() {
    const t0 = performance.now();
    
    try {
      // Try API first (returns merged user + static config)
      let response = await fetch('/api/webui/plugins/config');
      
      // Fallback to static file if API fails (e.g., not logged in)
      if (!response.ok) {
        console.log('[PluginLoader] API unavailable, falling back to static plugins.json');
        response = await fetch('/static/plugins/plugins.json');
      }
      
      if (!response.ok) {
        console.log('[PluginLoader] No plugins config found, skipping plugins');
        return;
      }
      
      this.config = await response.json();
      const pluginNames = this.config.enabled || [];
      
      if (pluginNames.length === 0) {
        console.log('[PluginLoader] No plugins enabled');
        return;
      }
      
      console.log(`[PluginLoader] Loading ${pluginNames.length} plugin(s) in parallel: ${pluginNames.join(', ')}`);
      
      // Load all plugins in parallel instead of sequentially
      const results = await Promise.allSettled(
        pluginNames.map(name => this.loadPlugin(name))
      );
      
      // Log any failures
      results.forEach((result, i) => {
        if (result.status === 'rejected') {
          console.error(`[Plugin:${pluginNames[i]}] Failed:`, result.reason);
        }
      });
      
      const elapsed = (performance.now() - t0).toFixed(0);
      console.log(`[PluginLoader] Finished in ${elapsed}ms. ${this.plugins.length}/${pluginNames.length} plugins active`);
      
    } catch (e) {
      console.error('[PluginLoader] System error:', e);
    }
  }

  async loadPlugin(name) {
    let wrapper = null;
    
    try {
      // Phase 1: Import module
      let module;
      try {
        module = await import(`/static/plugins/${name}/index.js`);
      } catch (importErr) {
        console.error(`[Plugin:${name}] Import failed:`, importErr);
        return;
      }
      
      const plugin = module.default;
      
      if (!plugin) {
        console.warn(`[Plugin:${name}] No default export found`);
        return;
      }
      
      if (typeof plugin.init !== 'function') {
        console.warn(`[Plugin:${name}] Missing init function`);
        return;
      }

      // Phase 2: Read config
      const pluginConfig = this.config.plugins?.[name] || {};
      const title = pluginConfig.title || name;
      const collapsible = pluginConfig.collapsible !== false;
      const defaultOpen = pluginConfig.defaultOpen === true;
      const showInSidebar = pluginConfig.showInSidebar !== false;

      // Phase 3: Build DOM structure
      wrapper = document.createElement('div');
      wrapper.className = 'plugin-wrapper';
      wrapper.dataset.plugin = name;

      if (showInSidebar) {
        if (collapsible) {
          const header = document.createElement('div');
          header.className = 'accordion-header';
          
          const toggle = document.createElement('span');
          toggle.className = 'accordion-toggle collapsed';
          
          const h3 = document.createElement('h3');
          h3.textContent = title;
          
          header.appendChild(toggle);
          header.appendChild(h3);
          
          if (plugin.helpText) {
            const helpBtn = document.createElement('button');
            helpBtn.className = 'plugin-help-btn';
            helpBtn.textContent = '?';
            helpBtn.title = 'Help';
            helpBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              showHelpModal(`${title} Help`, plugin.helpText);
            });
            header.appendChild(helpBtn);
          }

          const content = document.createElement('div');
          content.className = 'accordion-content collapsed';

          wrapper.appendChild(header);
          wrapper.appendChild(content);
          this.container.appendChild(wrapper);

          // Phase 4: Initialize plugin (most likely to fail)
          try {
            await plugin.init(content);
          } catch (initErr) {
            console.error(`[Plugin:${name}] init() failed:`, initErr);
            wrapper.remove();
            return;
          }
          
          this.setupCollapse(header, content, name, defaultOpen);
          this.plugins.push({ name, instance: plugin, wrapper, header, content });
        } else {
          this.container.appendChild(wrapper);
          
          try {
            await plugin.init(wrapper);
          } catch (initErr) {
            console.error(`[Plugin:${name}] init() failed:`, initErr);
            wrapper.remove();
            return;
          }
          
          this.plugins.push({ name, instance: plugin, wrapper });
        }
      } else {
        try {
          await plugin.init(wrapper);
        } catch (initErr) {
          console.error(`[Plugin:${name}] init() failed:`, initErr);
          return;
        }
        
        this.plugins.push({ name, instance: plugin, wrapper });
      }

      console.log(`✓ Plugin loaded: ${name}${!showInSidebar ? ' (menu-only)' : ''}`);
      
    } catch (e) {
      console.error(`[Plugin:${name}] Unexpected error:`, e);
      // Clean up partial DOM if wrapper was created
      if (wrapper && wrapper.parentNode) {
        wrapper.remove();
      }
    }
  }

  collapseAllExcept(exceptName) {
    this.plugins.forEach(({ name, header, content }) => {
      try {
        if (name !== exceptName && content && header) {
          const toggle = header.querySelector('.accordion-toggle');
          if (toggle && !content.classList.contains('collapsed')) {
            content.classList.add('collapsed');
            toggle.classList.add('collapsed');
            localStorage.setItem(`plugin-${name}-collapsed`, 'true');
          }
        }
      } catch (e) {
        console.warn(`[Plugin:${name}] collapse failed:`, e);
      }
    });
  }

  setupCollapse(header, content, pluginName, defaultOpen = false) {
    try {
      const savedState = localStorage.getItem(`plugin-${pluginName}-collapsed`);
      
      let isCollapsed;
      if (savedState !== null) {
        isCollapsed = savedState === 'true';
      } else {
        isCollapsed = !defaultOpen;
      }
      
      const toggle = header.querySelector('.accordion-toggle');
      if (!toggle) {
        console.warn(`[Plugin:${pluginName}] No toggle element found`);
        return;
      }
      
      if (isCollapsed) {
        content.classList.add('collapsed');
        toggle.classList.add('collapsed');
      } else {
        content.classList.remove('collapsed');
        toggle.classList.remove('collapsed');
      }

      header.addEventListener('click', (e) => {
        try {
          if (e.target.closest('.plugin-help-btn')) return;
          
          const isCurrentlyCollapsed = content.classList.contains('collapsed');
          
          if (isCurrentlyCollapsed) {
            this.collapseAllExcept(pluginName);
            content.classList.remove('collapsed');
            toggle.classList.remove('collapsed');
            localStorage.setItem(`plugin-${pluginName}-collapsed`, 'false');
          } else {
            content.classList.add('collapsed');
            toggle.classList.add('collapsed');
            localStorage.setItem(`plugin-${pluginName}-collapsed`, 'true');
          }
        } catch (clickErr) {
          console.warn(`[Plugin:${pluginName}] Toggle click error:`, clickErr);
        }
      });
    } catch (e) {
      console.warn(`[Plugin:${pluginName}] setupCollapse failed:`, e);
    }
  }

  registerIcon(plugin) {
    if (!this.iconContainer) {
      console.warn('Plugin menu container not found');
      return null;
    }
    
    const menuButton = document.createElement('button');
    menuButton.className = 'plugin-menu-item';
    menuButton.dataset.plugin = plugin.name;
    this.iconContainer.appendChild(menuButton);
    
    return menuButton;
  }

  destroy() {
    this.plugins.forEach(({ name, instance }) => {
      try {
        if (instance.destroy) instance.destroy();
      } catch (e) {
        console.error(`[Plugin:${name}] destroy() failed:`, e);
      }
    });
    this.plugins = [];
  }
}

export default PluginLoader;