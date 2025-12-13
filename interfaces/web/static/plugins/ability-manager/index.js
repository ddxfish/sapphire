// Ability Manager Plugin - index.js
import { injectStyles } from './ability-styles.js';
import { showToast } from '../../shared/toast.js';
import * as API from './ability-api.js';

export default {
  helpText: `What are Abilities:
- Named sets of enabled functions/tools
- Control what actions the AI can perform
- Switch quickly between capability profiles

Ability Types:
- 🔧 Built-in: Core system abilities (none, default, all)
- 📦 Module: Auto-generated from loaded modules
- 👤 User: Custom sets you create and save

Using Abilities:
- Select from dropdown to preview functions
- ⚡ Activate: Apply selected ability to current chat
- 💾 Save As: Save current checkboxes as new ability
- 🗑 Delete: Remove user-defined abilities only

Function Checkboxes:
- Check/uncheck to customize active functions
- Grouped by module for easy browsing
- Changes don't apply until you click Activate`,

  async init(container) {
    injectStyles();
    
    const wrapper = this.buildMainUI();
    container.appendChild(wrapper);
    
    this.elements = {
      select: wrapper.querySelector('#am-ability-select'),
      activateBtn: wrapper.querySelector('#am-activate-btn'),
      saveBtn: wrapper.querySelector('#am-save-btn'),
      deleteBtn: wrapper.querySelector('#am-delete-btn'),
      status: wrapper.querySelector('#am-status'),
      functionsContainer: wrapper.querySelector('#am-functions')
    };
    
    this.abilities = [];
    this.currentAbility = null;
    this.functionsData = null;
    
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
          <button id="am-activate-btn" class="plugin-btn" title="Activate">&#x26A1;</button>
          <button id="am-save-btn" class="plugin-btn" title="Save As">&#x1F4BE;</button>
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
    this.elements.activateBtn.addEventListener('click', () => this.handleActivate());
    this.elements.saveBtn.addEventListener('click', () => this.handleSaveAs());
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
      
      this.updateAbilityDropdown();
      this.updateStatus();
      this.renderFunctions();
    } catch (e) {
      console.error('Failed to load ability data:', e);
      showToast('Failed to load data', 'error');
    }
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
    const name = this.currentAbility.name;
    const count = this.currentAbility.function_count;
    this.elements.status.innerHTML = `Active: <strong>${name}</strong> (${count} function${count === 1 ? '' : 's'})`;
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
      
      html += `
        <div class="accordion am-module" data-module="${moduleName}">
          <div class="accordion-header">
            <span class="accordion-toggle collapsed"></span>
            <span class="accordion-title">📦 ${moduleName}</span>
            <span class="accordion-count">(${enabledCount}/${module.count})</span>
          </div>
          <div class="accordion-content collapsed">
      `;
      
      module.functions.forEach(func => {
        const checked = func.enabled ? 'checked' : '';
        const desc = func.description ? func.description.substring(0, 100) : '';
        
        html += `
          <div class="am-function">
            <label class="am-function-label">
              <input type="checkbox" id="func-${func.name}" data-function="${func.name}" ${checked}>
              <span class="am-function-name">${func.name}</span>
            </label>
            ${desc ? `<div class="am-function-desc">${desc}</div>` : ''}
          </div>
        `;
      });
      
      html += `</div></div>`;
    });
    
    this.elements.functionsContainer.innerHTML = html;
    
    this.elements.functionsContainer.querySelectorAll('.accordion-header').forEach(header => {
      header.addEventListener('click', (e) => {
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
  },
  
  async handleSelectChange() {
    const selected = this.elements.select.value;
    if (!selected) return;
    
    const ability = this.abilities.find(a => a.name === selected);
    if (!ability?.functions) return;
    
    const abilityFunctions = new Set(ability.functions);
    this.elements.functionsContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.checked = abilityFunctions.has(cb.dataset.function);
    });
    
    const typeLabel = ability.type === 'builtin' ? 'built-in' :
                      ability.type === 'module' ? 'module' :
                      ability.type === 'user' ? 'user-defined' : 'unknown';
    
    showToast(`Preview: ${ability.name} (${typeLabel})`, 'info');
  },
  
  async handleActivate() {
    const checkedFunctions = this.getCheckedFunctions();
    
    if (checkedFunctions.length === 0) {
      if (!confirm('No functions selected. This will disable all functions. Continue?')) return;
      try {
        await API.activateAbility('none');
        showToast('Activated: none (all functions disabled)', 'success');
        await this.refresh();
        return;
      } catch (e) {
        console.error('Failed to activate:', e);
        showToast(`Failed: ${e.message}`, 'error');
        return;
      }
    }
    
    const selectedAbilityName = this.elements.select.value;
    const selectedAbility = this.abilities.find(a => a.name === selectedAbilityName);
    
    let useAbilityName = null;
    
    if (selectedAbility?.functions) {
      const abilityFuncs = new Set(selectedAbility.functions);
      const checkedSet = new Set(checkedFunctions);
      
      if (abilityFuncs.size === checkedSet.size && 
          [...abilityFuncs].every(f => checkedSet.has(f))) {
        useAbilityName = selectedAbilityName;
      }
    }
    
    try {
      if (useAbilityName) {
        const result = await API.activateAbility(useAbilityName);
        showToast(`Activated: ${result.name} (${result.function_count} functions)`, 'success');
      } else {
        const result = await API.enableFunctions(checkedFunctions);
        showToast(`Activated: custom (${result.enabled_functions.length} functions)`, 'success');
      }
      
      await this.refresh();
      if (selectedAbilityName) this.elements.select.value = selectedAbilityName;
    } catch (e) {
      console.error('Failed to activate functions:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async handleSaveAs() {
    const name = prompt('Name for this custom ability set:');
    if (!name?.trim()) return;
    
    const trimmedName = name.trim();
    const existing = this.abilities.find(a => a.name === trimmedName);
    if (existing && (existing.type === 'module' || existing.type === 'builtin')) {
      showToast(`Cannot overwrite ${existing.type} ability '${trimmedName}'`, 'error');
      return;
    }
    
    const checkedFunctions = this.getCheckedFunctions();
    if (checkedFunctions.length === 0) {
      showToast('No functions selected', 'error');
      return;
    }
    
    try {
      const result = await API.saveCustomAbility(trimmedName, checkedFunctions);
      showToast(`Saved: ${result.name}`, 'success');
      await this.refresh();
      this.elements.select.value = trimmedName;
    } catch (e) {
      console.error('Failed to save ability:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async handleDelete() {
    const selected = this.elements.select.value;
    if (!selected) {
      showToast('Select an ability first', 'info');
      return;
    }
    
    const ability = this.abilities.find(a => a.name === selected);
    if (!ability) {
      showToast('Ability not found', 'error');
      return;
    }
    
    if (ability.type !== 'user') {
      showToast(`Cannot delete ${ability.type} ability '${selected}'`, 'error');
      return;
    }
    
    if (!confirm(`Delete user ability "${selected}"?`)) return;
    
    try {
      await API.deleteAbility(selected);
      showToast(`Deleted: ${selected}`, 'success');
      await this.refresh();
      if (this.currentAbility?.name) {
        this.elements.select.value = this.currentAbility.name;
      }
    } catch (e) {
      console.error('Failed to delete ability:', e);
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  getCheckedFunctions() {
    const checkboxes = this.elements.functionsContainer.querySelectorAll('input[type="checkbox"]:checked');
    return Array.from(checkboxes).map(cb => cb.dataset.function);
  }
};