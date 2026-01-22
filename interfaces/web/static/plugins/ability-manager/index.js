// Ability Manager Plugin - index.js
// 
// Editable Default Setup:
// Built-in toolsets (none, default, all) are read-only. To give users an
// editable starting point: create a user toolset with desired tools (or none),
// then set default_ability in user/config.yaml to that toolset name.
// Users can then customize by checking/unchecking tools directly.

import { injectStyles } from './ability-styles.js';
import { showToast } from '../../shared/toast.js';
import * as API from './ability-api.js';

// Import updateScene to sync pill after changes
async function updateScene() {
  const { updateScene: doUpdate } = await import('../../features/scene.js');
  return doUpdate();
}

// Direct pill update for instant feedback
function updatePillDirect(name, count) {
  const pill = document.getElementById('ability-pill');
  if (!pill) return;
  const textEl = pill.querySelector('.pill-text');
  if (textEl) {
    textEl.textContent = `${name} (${count})`;
  }
}

export default {
  helpText: `What are Toolsets:
- Named sets of enabled functions/tools
- Control what actions the AI can perform
- Switch quickly between capability profiles

Toolset Types:
- 🔧 Built-in: Core system abilities (none, default, all)
- 📦 Module: Auto-generated from loaded modules
- 👤 User: Custom sets you create and save

Using Toolsets:
- Select from dropdown = activates immediately
- Check/uncheck functions = auto-saves for user toolsets
- Use module checkbox to toggle entire categories
- + New: Always prompts for new toolset name
- 💾 Save: System toolset → Save As new, User toolset → overwrite
- 🔄 Refresh: Reload from server
- 🗑 Delete: Remove user-defined toolsets only`,

  async init(container) {
    injectStyles();
    
    const wrapper = this.buildMainUI();
    container.appendChild(wrapper);
    
    this.elements = {
      select: wrapper.querySelector('#am-ability-select'),
      newBtn: wrapper.querySelector('#am-new-btn'),
      saveBtn: wrapper.querySelector('#am-save-btn'),
      refreshBtn: wrapper.querySelector('#am-refresh-btn'),
      deleteBtn: wrapper.querySelector('#am-delete-btn'),
      status: wrapper.querySelector('#am-status'),
      functionsContainer: wrapper.querySelector('#am-functions')
    };
    
    this.abilities = [];
    this.currentAbility = null;
    this.functionsData = null;
    this._saveTimeout = null;
    
    this.bindEvents();
    await this.refresh();
  },
  
  buildMainUI() {
    const wrapper = document.createElement('div');
    wrapper.className = 'ability-manager-plugin';
    wrapper.innerHTML = `
      <div class="am-controls">
        <select id="am-ability-select">
          <option value="">Loading...</option>
        </select>
        <div class="am-control-buttons">
          <button id="am-new-btn" class="plugin-btn" title="Save As New">+</button>
          <button id="am-save-btn" class="plugin-btn" title="Save">&#x1F4BE;</button>
          <button id="am-refresh-btn" class="plugin-btn" title="Refresh">&#x1F504;</button>
          <button id="am-delete-btn" class="plugin-btn" title="Delete">&#x1F5D1;</button>
        </div>
      </div>
      <div id="am-status" class="am-status">Loading...</div>
      <div id="am-functions" class="am-functions">
        <div class="am-placeholder">Loading functions...</div>
      </div>
    `;
    return wrapper;
  },
  
  bindEvents() {
    this.elements.newBtn.addEventListener('click', () => this.handleSaveAs());
    this.elements.saveBtn.addEventListener('click', () => this.handleSave());
    this.elements.refreshBtn.addEventListener('click', () => this.handleRefresh());
    this.elements.deleteBtn.addEventListener('click', () => this.handleDelete());
    this.elements.select.addEventListener('change', () => this.handleSelectChange());
  },
  
  async refresh() {
    try {
      const [abilitiesData, currentAbility, functionsData] = await Promise.all([
        API.getAbilities(),
        API.getCurrentAbility(),
        API.getFunctions()
      ]);
      
      this.abilities = abilitiesData.abilities || [];
      this.currentAbility = currentAbility;
      this.functionsData = functionsData;
      
      // Get type from abilities list
      const match = this.abilities.find(a => a.name === this.currentAbility?.name);
      if (match) this.currentAbility.type = match.type;
      
      this.updateAbilityDropdown();
      this.updateStatus();
      this.renderFunctions();
      this.setReadonlyState(this.currentAbility?.type !== 'user');
    } catch (e) {
      console.error('Failed to load ability data:', e);
      showToast('Failed to load data', 'error');
    }
  },
  
  async handleRefresh() {
    await this.refresh();
    await updateScene();
    showToast('Refreshed', 'success');
  },
  
  updateAbilityDropdown() {
    this.elements.select.innerHTML = '';
    
    this.abilities.forEach(ability => {
      const opt = document.createElement('option');
      opt.value = ability.name;
      opt.dataset.type = ability.type;
      
      let prefix = '';
      if (ability.type === 'builtin') prefix = '🔧 ';
      else if (ability.type === 'module') prefix = '📦 ';
      else if (ability.type === 'user') prefix = '👤 ';
      
      opt.textContent = `${prefix}${ability.name} (${ability.function_count})`;
      this.elements.select.appendChild(opt);
    });
    
    if (this.currentAbility?.name) {
      this.elements.select.value = this.currentAbility.name;
    }
  },
  
  updateStatus() {
    const name = this.currentAbility?.name || 'unknown';
    const count = this.currentAbility?.function_count || 0;
    const type = this.currentAbility?.type;
    
    if (type === 'user') {
      this.elements.status.innerHTML = `Active: <strong>${name}</strong> (${count} function${count === 1 ? '' : 's'})`;
    } else {
      // Read-only system/module toolset - guide user to create their own
      this.elements.status.innerHTML = `Active: <strong>${name}</strong> (${count})<br><span class="am-readonly-hint">Read-only · + creates editable copy</span>`;
    }
  },
  
  renderFunctions() {
    if (!this.functionsData?.modules) {
      this.elements.functionsContainer.innerHTML = '<div class="am-placeholder">No functions available</div>';
      return;
    }
    
    const modules = this.functionsData.modules;
    const sortedModules = Object.keys(modules).sort();
    
    let html = '';
    sortedModules.forEach(moduleName => {
      const module = modules[moduleName];
      const enabledCount = module.functions.filter(f => f.enabled).length;
      const allChecked = enabledCount === module.count;
      const someChecked = enabledCount > 0 && enabledCount < module.count;
      
      html += `
        <div class="accordion am-module" data-module="${moduleName}">
          <div class="accordion-header">
            <input type="checkbox" class="am-module-checkbox" data-module="${moduleName}" 
              ${allChecked ? 'checked' : ''} title="Toggle all ${moduleName} functions">
            <span class="accordion-toggle collapsed"></span>
            <span class="accordion-title">📦 ${moduleName}</span>
            <span class="accordion-count">(${enabledCount}/${module.count})</span>
          </div>
          <div class="accordion-content collapsed"><div class="accordion-inner">
      `;
      
      module.functions.forEach(func => {
        const checked = func.enabled ? 'checked' : '';
        const desc = func.description ? func.description.substring(0, 100) : '';
        
        html += `
          <div class="am-function">
            <label class="am-function-label">
              <input type="checkbox" data-function="${func.name}" ${checked}>
              <span class="am-function-name">${func.name}</span>
            </label>
            ${desc ? `<div class="am-function-desc">${desc}</div>` : ''}
          </div>
        `;
      });
      
      html += `</div></div></div>`;
    });
    
    this.elements.functionsContainer.innerHTML = html;
    
    // Set indeterminate state for partially checked modules
    sortedModules.forEach(moduleName => {
      const module = modules[moduleName];
      const enabledCount = module.functions.filter(f => f.enabled).length;
      if (enabledCount > 0 && enabledCount < module.count) {
        const cb = this.elements.functionsContainer.querySelector(`.am-module-checkbox[data-module="${moduleName}"]`);
        if (cb) cb.indeterminate = true;
      }
    });
    
    // Accordion toggle (exclude checkbox clicks)
    this.elements.functionsContainer.querySelectorAll('.accordion-header').forEach(header => {
      header.addEventListener('click', (e) => {
        // Don't toggle accordion when clicking checkbox
        if (e.target.classList.contains('am-module-checkbox')) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        const content = header.nextElementSibling;
        const toggle = header.querySelector('.accordion-toggle');
        
        if (content && toggle) {
          const isCollapsed = content.classList.contains('collapsed');
          content.classList.toggle('collapsed', !isCollapsed);
          toggle.classList.toggle('collapsed', !isCollapsed);
        }
      });
    });
    
    // Module checkbox - toggle all functions in module
    this.elements.functionsContainer.querySelectorAll('.am-module-checkbox').forEach(cb => {
      cb.addEventListener('click', (e) => e.stopPropagation());
      cb.addEventListener('change', (e) => {
        const moduleName = e.target.dataset.module;
        const moduleEl = e.target.closest('.am-module');
        const checked = e.target.checked;
        
        // Toggle all function checkboxes in this module
        moduleEl.querySelectorAll('.am-function input[type="checkbox"]').forEach(funcCb => {
          funcCb.checked = checked;
        });
        
        e.target.indeterminate = false;
        this.handleCheckboxChange();
      });
    });
    
    // Function checkbox change listeners
    this.elements.functionsContainer.querySelectorAll('.am-function input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => this.handleCheckboxChange());
    });
  },
  
  async handleCheckboxChange() {
    // Debounce rapid changes
    if (this._saveTimeout) clearTimeout(this._saveTimeout);
    
    this._saveTimeout = setTimeout(async () => {
      await this.applyCurrentCheckboxes();
    }, 150);
  },
  
  async applyCurrentCheckboxes() {
    const checkedFunctions = this.getCheckedFunctions();
    const count = checkedFunctions.length;
    
    // Instant UI feedback
    this.updateModuleCounts();
    
    // Get currently selected toolset
    const selectedName = this.elements.select.value;
    const selectedAbility = this.abilities.find(a => a.name === selectedName);
    
    try {
      if (count === 0) {
        await API.activateAbility('none');
        this.currentAbility = { name: 'none', function_count: 0, type: 'builtin' };
        updatePillDirect('none', 0);
        showToast('Toolset: none (0)', 'success');
      } else if (selectedAbility?.type === 'user') {
        // Save over the user toolset
        await API.saveCustomAbility(selectedName, checkedFunctions);
        await API.activateAbility(selectedName);
        this.currentAbility = { name: selectedName, function_count: count, type: 'user' };
        updatePillDirect(selectedName, count);
        
        // Update dropdown option text
        const opt = this.elements.select.querySelector(`option[value="${selectedName}"]`);
        if (opt) opt.textContent = `👤 ${selectedName} (${count})`;
        
        // Update abilities cache
        const cached = this.abilities.find(a => a.name === selectedName);
        if (cached) {
          cached.function_count = count;
          cached.functions = checkedFunctions;
        }
        
        showToast(`Saved: ${selectedName} (${count})`, 'success');
      } else {
        // System/module toolset - checkboxes should be disabled, but handle as fallback
        await API.enableFunctions(checkedFunctions);
        this.currentAbility = { name: 'custom', function_count: count };
        updatePillDirect('custom', count);
        showToast(`Custom (${count}) - save to keep`, 'info');
      }
      
      this.updateStatus();
      
      // Full sync in background
      updateScene();
    } catch (e) {
      console.error('Failed to apply functions:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async handleSelectChange() {
    const selected = this.elements.select.value;
    if (!selected) return;
    
    const ability = this.abilities.find(a => a.name === selected);
    if (!ability) return;
    
    // Instant pill update
    updatePillDirect(ability.name, ability.function_count);
    
    try {
      // Activate on server
      const result = await API.activateAbility(selected);
      
      // Update checkboxes to match
      if (ability.functions) {
        const abilityFunctions = new Set(ability.functions);
        this.elements.functionsContainer.querySelectorAll('.am-function input[type="checkbox"]').forEach(cb => {
          cb.checked = abilityFunctions.has(cb.dataset.function);
        });
      }
      
      // Update local state
      this.currentAbility = result;
      this.currentAbility.type = ability.type;
      this.updateStatus();
      this.updateModuleCounts();
      this.setReadonlyState(ability.type !== 'user');
      
      // Full sync
      await updateScene();
      
      showToast(`Toolset: ${result.name} (${result.function_count})`, 'success');
    } catch (e) {
      console.error('Failed to activate ability:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  setReadonlyState(readonly) {
    // Toggle readonly class on container
    this.elements.functionsContainer.classList.toggle('am-readonly', readonly);
    
    // Disable/enable all checkboxes
    this.elements.functionsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.disabled = readonly;
    });
  },
  
  updateModuleCounts() {
    this.elements.functionsContainer.querySelectorAll('.am-module').forEach(moduleEl => {
      const checkboxes = moduleEl.querySelectorAll('.am-function input[type="checkbox"]');
      const enabledCount = Array.from(checkboxes).filter(cb => cb.checked).length;
      const totalCount = checkboxes.length;
      const countEl = moduleEl.querySelector('.accordion-count');
      if (countEl) {
        countEl.textContent = `(${enabledCount}/${totalCount})`;
      }
      
      // Update module checkbox state
      const moduleCheckbox = moduleEl.querySelector('.am-module-checkbox');
      if (moduleCheckbox) {
        moduleCheckbox.checked = enabledCount === totalCount && totalCount > 0;
        moduleCheckbox.indeterminate = enabledCount > 0 && enabledCount < totalCount;
      }
    });
  },
  
  async handleSave() {
    const selectedName = this.elements.select.value;
    if (!selectedName) {
      showToast('Select a toolset first', 'info');
      return;
    }
    
    const selectedAbility = this.abilities.find(a => a.name === selectedName);
    const checkedFunctions = this.getCheckedFunctions();
    // Allow saving empty toolsets for user toolsets
    
    if (selectedAbility?.type === 'user') {
      // User toolset - silent overwrite
      try {
        await API.saveCustomAbility(selectedName, checkedFunctions);
        await API.activateAbility(selectedName);
        
        // Update cache
        const cached = this.abilities.find(a => a.name === selectedName);
        if (cached) {
          cached.function_count = checkedFunctions.length;
          cached.functions = checkedFunctions;
        }
        
        // Update dropdown
        const opt = this.elements.select.querySelector(`option[value="${selectedName}"]`);
        if (opt) opt.textContent = `👤 ${selectedName} (${checkedFunctions.length})`;
        
        this.currentAbility = { name: selectedName, function_count: checkedFunctions.length, type: 'user' };
        updatePillDirect(selectedName, checkedFunctions.length);
        this.updateStatus();
        await updateScene();
        
        showToast(`Saved: ${selectedName} (${checkedFunctions.length})`, 'success');
      } catch (e) {
        console.error('Failed to save toolset:', e);
        showToast(`Failed: ${e.message}`, 'error');
      }
    } else {
      // System/module toolset - prompt for new name (default to selected name)
      const name = prompt('Save as new toolset:', selectedName);
      if (!name?.trim()) return;
      
      const trimmedName = name.trim();
      const existing = this.abilities.find(a => a.name === trimmedName);
      if (existing && (existing.type === 'module' || existing.type === 'builtin')) {
        showToast(`Cannot overwrite ${existing.type} toolset '${trimmedName}'`, 'error');
        return;
      }
      
      try {
        await API.saveCustomAbility(trimmedName, checkedFunctions);
        showToast(`Saved: ${trimmedName}`, 'success');
        await this.refresh();
        this.elements.select.value = trimmedName;
        
        await API.activateAbility(trimmedName);
        this.currentAbility = { name: trimmedName, function_count: checkedFunctions.length, type: 'user' };
        updatePillDirect(trimmedName, checkedFunctions.length);
        this.updateStatus();
        this.setReadonlyState(false);
        await updateScene();
      } catch (e) {
        console.error('Failed to save toolset:', e);
        showToast(`Failed: ${e.message}`, 'error');
      }
    }
  },
  
  async handleSaveAs() {
    const name = prompt('Name for this custom toolset:');
    if (!name?.trim()) return;
    
    const trimmedName = name.trim();
    const existing = this.abilities.find(a => a.name === trimmedName);
    if (existing && (existing.type === 'module' || existing.type === 'builtin')) {
      showToast(`Cannot overwrite ${existing.type} toolset '${trimmedName}'`, 'error');
      return;
    }
    
    const checkedFunctions = this.getCheckedFunctions();
    // Allow empty toolsets - user may want editable "none" as starting point
    // Tip: Create a user toolset with 0 tools and set as default_ability in config
    // for an editable default that users can customize by checking tools
    
    try {
      const result = await API.saveCustomAbility(trimmedName, checkedFunctions);
      showToast(`Saved: ${result.name} (${checkedFunctions.length})`, 'success');
      await this.refresh();
      this.elements.select.value = trimmedName;
      
      // Activate the newly saved toolset
      await API.activateAbility(trimmedName);
      this.currentAbility = { name: trimmedName, function_count: checkedFunctions.length, type: 'user' };
      updatePillDirect(trimmedName, checkedFunctions.length);
      this.updateStatus();
      this.setReadonlyState(false);
      await updateScene();
    } catch (e) {
      console.error('Failed to save toolset:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async handleDelete() {
    const selected = this.elements.select.value;
    if (!selected) {
      showToast('Select a toolset first', 'info');
      return;
    }
    
    const ability = this.abilities.find(a => a.name === selected);
    if (!ability) {
      showToast('Toolset not found', 'error');
      return;
    }
    
    if (ability.type !== 'user') {
      showToast(`Cannot delete ${ability.type} toolset '${selected}'`, 'error');
      return;
    }
    
    if (!confirm(`Delete user toolset "${selected}"?`)) return;
    
    try {
      await API.deleteAbility(selected);
      showToast(`Deleted: ${selected}`, 'success');
      await this.refresh();
      
      // Select and activate current ability
      if (this.currentAbility?.name) {
        this.elements.select.value = this.currentAbility.name;
      }
      await updateScene();
    } catch (e) {
      console.error('Failed to delete toolset:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  getCheckedFunctions() {
    const checkboxes = this.elements.functionsContainer.querySelectorAll('.am-function input[type="checkbox"]:checked');
    return Array.from(checkboxes).map(cb => cb.dataset.function);
  }
};