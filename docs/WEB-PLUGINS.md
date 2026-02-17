# Web UI Plugins

Web UI plugins are JavaScript extensions that add features to the Sapphire web interface. They can add widgets to the sidebar, items to the gear menu, or settings tabs in the Plugins popup.

**Web UI Plugins vs Backend Plugins:**
- **Web UI Plugins**: JavaScript, runs in browser, extends the interface
- **Backend Plugins**: Python, runs server-side, keyword-triggered responses (see [PLUGINS.md](PLUGINS.md))

## What Can Plugins Do?

Plugins can hook into three places:

| Location | Example Use |
|----------|-------------|
| **Sidebar** | Prompt editor, spice manager, toolset editor |
| **Gear Menu** | Settings, backup manager |
| **Plugins Popup** | Image generation settings, plugin-specific config |

Think weather widgets, API integrations, custom controls, or anything that extends the UI.

## Included Plugins

| Plugin | Location | Purpose |
|--------|----------|---------|
| backup | Gear menu | Backup management |
| plugins-modal | Gear menu | Enable/disable plugins |
| image-gen | Plugins popup | SDXL image generation settings |
| continuity | Gear menu | Scheduled task management |
| homeassistant | Plugins popup | Home Assistant integration |
| email | Plugins popup | Gmail/email configuration |
| ssh | Plugins popup | SSH server management |
| setup-wizard | Auto-show | First-run configuration |

## Creating Plugins with AI

Copy this prompt to Claude or any AI:

> Create a Sapphire web UI plugin that [describe what you want].
>
> **Plugin requirements:**
> - Folder: `interfaces/web/static/plugins/{plugin-name}/`
> - Entry point: `index.js` with default export
> - Must have `init(container)` function
> - Optional: `destroy()` for cleanup, `helpText` for help button
>
> **For sidebar plugins**, the container is inside a collapsible accordion.
> **For settings-only plugins**, use `registerPluginSettings()` to add a tab to the Plugins popup.
>
> **Example index.js (sidebar widget):**
> ```javascript
> export default {
>   name: 'my-plugin',
>   version: '1.0.0',
>   helpText: 'What this plugin does',
>   
>   init(container) {
>     container.innerHTML = `
>       <div style="padding: 12px;">
>         <p>Hello from my plugin!</p>
>         <button id="my-btn">Click me</button>
>       </div>
>     `;
>     
>     container.querySelector('#my-btn').addEventListener('click', () => {
>       alert('Button clicked!');
>     });
>   },
>   
>   destroy() {
>     // Cleanup if needed
>   }
> };
> ```
>
> **To register in plugins.json:**
> ```json
> {
>   "enabled": ["my-plugin"],
>   "plugins": {
>     "my-plugin": {
>       "title": "My Plugin",
>       "collapsible": true,
>       "showInSidebar": true
>     }
>   }
> }
> ```
>
> Give me the complete files.

After the AI gives you the files:
1. Create folder: `interfaces/web/static/plugins/your-plugin/`
2. Save `index.js` inside
3. Add to `plugins.json` enabled array and plugins object
4. Reload the page

## Technical Reference

### File Structure

```
interfaces/web/static/plugins/
├── plugins.json           # Registry (enabled list + metadata)
├── plugin-loader.js       # Loads and initializes plugins
└── my-plugin/
    ├── index.js           # Entry point (required)
    └── style.css          # Optional styles
```

### plugins.json Format

```json
{
  "enabled": ["my-plugin"],
  "plugins": {
    "my-plugin": {
      "title": "My Plugin",
      "collapsible": true,
      "defaultOpen": false,
      "showInSidebar": true
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| enabled | array | Plugin names to load |
| title | string | Display name |
| collapsible | bool | Wrap in accordion (sidebar only) |
| defaultOpen | bool | Start expanded |
| showInSidebar | bool | false = gear menu only |

### index.js Requirements

```javascript
export default {
  name: 'my-plugin',           // Unique identifier
  version: '1.0.0',            // Semver
  helpText: 'Optional help',   // Shows ? button if present
  
  init(container) {
    // Build your UI here
    // container is a div you own
  },
  
  destroy() {
    // Optional cleanup
  }
};
```

### Adding Settings Tabs

For plugins that only need a settings panel in the Plugins popup:

```javascript
import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';

export default {
  name: 'my-settings-plugin',
  
  init(container) {
    registerPluginSettings({
      id: 'my-settings-plugin',
      name: 'My Settings',
      icon: '⚙️',
      helpText: 'Configure my plugin',
      
      render(container, settings) {
        container.innerHTML = `
          <input type="text" id="my-input" value="${settings.value || ''}">
        `;
      },
      
      load: async () => {
        const res = await fetch('/api/webui/plugins/my-settings-plugin/settings');
        const data = await res.json();
        return data.settings || {};
      },
      
      save: async (settings) => {
        await fetch('/api/webui/plugins/my-settings-plugin/settings', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ settings })
        });
      },
      
      getSettings(container) {
        return {
          value: container.querySelector('#my-input').value
        };
      }
    });
  }
};
```

Set `showInSidebar: false` in plugins.json for settings-only plugins.

### Available Imports

```javascript
// Toast notifications
import { showToast } from '../../shared/toast.js';
showToast('Message', 'success');  // or 'error'

// Modal dialogs
import { showHelpModal } from '../../shared/modal.js';
showHelpModal('Title', 'Content');

// Plugin settings registry
import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';

// Plugin settings API
import pluginsAPI from '../plugins-modal/plugins-api.js';
await pluginsAPI.getSettings('plugin-name');
await pluginsAPI.saveSettings('plugin-name', { key: 'value' });
```

### CSS Guidelines

Use CSS variables for theme compatibility:

```css
.my-plugin {
  background: var(--bg-secondary);
  color: var(--text);
  border: 1px solid var(--border);
}

.my-plugin:hover {
  background: var(--bg-hover);
}
```

### Files Reference

| Path | Purpose |
|------|---------|
| `interfaces/web/static/plugins/` | All web UI plugins |
| `interfaces/web/static/plugins/plugins.json` | Plugin registry |
| `interfaces/web/static/plugin-loader.js` | Plugin loading system |
| `core/api_fastapi.py` | Backend API (includes plugin settings endpoints) |
| `user/webui/plugins/` | User plugin settings storage |

## Reference for AI

Web plugins extend the browser-based UI (JavaScript, not Python).

WEB PLUGIN VS BACKEND PLUGIN:
- Web plugin: Runs in browser, modifies UI (JavaScript)
- Backend plugin: Runs on server, keyword-triggered (Python)

BUILT-IN WEB PLUGINS:
- backup: Backup management UI
- plugins-modal: Plugin management
- image-gen: Image generation settings
- continuity: Scheduled task management
- homeassistant: Home Assistant integration
- email: Email configuration
- ssh: SSH server management
- setup-wizard: First-run configuration

PLUGIN LOCATIONS:
- interfaces/web/static/plugins/ - Built-in plugins
- user/webui/plugins/ - Plugin settings storage

CREATING WEB PLUGINS:
- Create folder in interfaces/web/static/plugins/
- Add index.js with default export (name, init, destroy)
- Register in plugins.json
- Feed WEB-PLUGINS.md to AI to generate

TROUBLESHOOTING:
- Plugin not showing: Check plugins.json, verify enabled: true
- JavaScript errors: Check browser console (F12)
- Settings not saving: Verify API endpoint in plugin code