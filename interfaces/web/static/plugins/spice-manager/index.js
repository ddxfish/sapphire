// Spice Manager Plugin - index.js
import { injectStyles } from './spice-styles.js';
import { showToast } from '../../shared/toast.js';
import { showModal, escapeHtml } from '../../shared/modal.js';
import { updateScene } from '../../features/scene.js';
import * as API from './spice-api.js';

export default {
  helpText: `What are Spices:
- Random context injected into system prompt
- Adds variety and personality to responses
- Triggered every N turns (configurable per chat)

Managing Categories:
- Click + to create a new category
- Click × on category header to delete it
- Categories organize related spices

Managing Spices:
- Click + on category to add a spice
- Click ✎ to edit, × to delete
- Spices are picked randomly from all categories

Tips:
- Keep spices short and evocative
- Use reload button after manual file edits`,

  async init(container) {
    injectStyles();
    
    const wrapper = this.buildMainUI();
    container.appendChild(wrapper);
    
    this.elements = {
      addCategoryBtn: wrapper.querySelector('#sm-add-category-btn'),
      reloadBtn: wrapper.querySelector('#sm-reload-btn'),
      status: wrapper.querySelector('#sm-status'),
      categoriesContainer: wrapper.querySelector('#sm-categories')
    };
    
    this.spicesData = null;
    
    this.bindEvents();
    await this.refresh();
  },
  
  buildMainUI() {
    const wrapper = document.createElement('div');
    wrapper.className = 'spice-manager-plugin';
    wrapper.innerHTML = `
      <div class="sm-controls">
        <button id="sm-add-category-btn" class="plugin-btn" title="Add Category">+</button>
        <button id="sm-reload-btn" class="plugin-btn" title="Reload">&#x1F504;</button>
      </div>
      <div id="sm-status" class="sm-status">Loading...</div>
      <div id="sm-categories" class="sm-categories">
        <div class="sm-placeholder">Loading spices...</div>
      </div>
    `;
    return wrapper;
  },
  
  bindEvents() {
    this.elements.addCategoryBtn.addEventListener('click', () => this.handleAddCategory());
    this.elements.reloadBtn.addEventListener('click', () => this.handleReload());
  },
  
  async refresh() {
    try {
      this.spicesData = await API.getSpices();
      this.updateStatus();
      this.renderCategories();
    } catch (e) {
      console.error('Failed to load spices:', e);
      showToast('Failed to load spices', 'error');
    }
  },
  
  updateStatus() {
    const catCount = this.spicesData?.category_count || 0;
    const spiceCount = this.spicesData?.total_spices || 0;
    this.elements.status.innerHTML = `<strong>${spiceCount}</strong> spice${spiceCount === 1 ? '' : 's'} in <strong>${catCount}</strong> categor${catCount === 1 ? 'y' : 'ies'}`;
  },
  
  renderCategories() {
    if (!this.spicesData?.categories) {
      this.elements.categoriesContainer.innerHTML = '<div class="sm-placeholder">No spices found</div>';
      return;
    }
    
    const categories = this.spicesData.categories;
    const sortedCategories = Object.keys(categories).sort();
    
    if (sortedCategories.length === 0) {
      this.elements.categoriesContainer.innerHTML = '<div class="sm-placeholder">No categories yet</div>';
      return;
    }
    
    let html = '';
    sortedCategories.forEach(categoryName => {
      const category = categories[categoryName];
      const spiceCount = category.count;
      const isEnabled = category.enabled !== false;  // Default to enabled
      
      html += `
        <div class="accordion sm-category" data-category="${escapeHtml(categoryName)}">
          <div class="accordion-header">
            <span class="accordion-toggle collapsed"></span>
            <input type="checkbox" class="sm-category-checkbox" ${isEnabled ? 'checked' : ''} title="${isEnabled ? 'Enabled - click to disable' : 'Disabled - click to enable'}">
            <span class="accordion-title">${escapeHtml(categoryName)}</span>
            <span class="accordion-count">(${spiceCount})</span>
            <div class="accordion-actions">
              <button class="inline-btn add sm-add-spice-btn" title="Add Spice">+</button>
              <button class="inline-btn delete sm-delete-cat-btn" title="Delete Category">×</button>
            </div>
          </div>
          <div class="accordion-content collapsed">
      `;
      
      category.spices.forEach((spice, index) => {
        html += `
          <div class="sm-spice" data-index="${index}">
            <span class="sm-spice-text">${escapeHtml(spice)}</span>
            <div class="sm-spice-actions">
              <button class="inline-btn edit sm-edit-spice-btn" title="Edit">✎</button>
              <button class="inline-btn delete sm-delete-spice-btn" title="Delete">×</button>
            </div>
          </div>
        `;
      });
      
      if (spiceCount === 0) {
        html += '<div class="sm-empty-category">Empty</div>';
      }
      
      html += `</div></div>`;
    });
    
    this.elements.categoriesContainer.innerHTML = html;
    this.attachCategoryHandlers();
  },
  
  attachCategoryHandlers() {
    this.elements.categoriesContainer.querySelectorAll('.accordion-header').forEach(header => {
      header.addEventListener('click', (e) => {
        if (e.target.closest('.accordion-actions') || e.target.classList.contains('sm-category-checkbox')) return;
        
        const content = header.nextElementSibling;
        const toggle = header.querySelector('.accordion-toggle');
        
        if (content.classList.contains('collapsed')) {
          content.classList.remove('collapsed');
          toggle.classList.remove('collapsed');
        } else {
          content.classList.add('collapsed');
          toggle.classList.add('collapsed');
        }
      });
    });
    
    this.elements.categoriesContainer.querySelectorAll('.sm-category-checkbox').forEach(checkbox => {
      checkbox.addEventListener('click', async (e) => {
        e.stopPropagation();
        const category = checkbox.closest('.accordion').dataset.category;
        try {
          const result = await API.toggleCategory(category);
          checkbox.checked = result.enabled;
          checkbox.title = result.enabled ? 'Enabled - click to disable' : 'Disabled - click to enable';
          showToast(`${category}: ${result.enabled ? 'enabled' : 'disabled'}`, 'success');
          // Refresh spice indicator tooltip with new spice
          await updateScene();
        } catch (err) {
          checkbox.checked = !checkbox.checked;  // Revert on error
          showToast(`Failed: ${err.message}`, 'error');
        }
      });
    });
    
    this.elements.categoriesContainer.querySelectorAll('.sm-add-spice-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const category = btn.closest('.accordion').dataset.category;
        this.showSpiceModal('add', category);
      });
    });
    
    this.elements.categoriesContainer.querySelectorAll('.sm-delete-cat-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const category = btn.closest('.accordion').dataset.category;
        this.handleDeleteCategory(category);
      });
    });
    
    this.elements.categoriesContainer.querySelectorAll('.sm-edit-spice-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const spiceEl = btn.closest('.sm-spice');
        const category = btn.closest('.accordion').dataset.category;
        const index = parseInt(spiceEl.dataset.index);
        const currentText = spiceEl.querySelector('.sm-spice-text').textContent;
        this.showSpiceModal('edit', category, index, currentText);
      });
    });
    
    this.elements.categoriesContainer.querySelectorAll('.sm-delete-spice-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const spiceEl = btn.closest('.sm-spice');
        const category = btn.closest('.accordion').dataset.category;
        const index = parseInt(spiceEl.dataset.index);
        this.handleDeleteSpice(category, index);
      });
    });
  },
  
  showSpiceModal(mode, category, index = null, currentText = '') {
    const title = mode === 'add' ? `Add Spice to "${category}"` : 'Edit Spice';
    
    showModal(title, [
      { id: 'spice-text', label: 'Spice Text', type: 'textarea', value: currentText, rows: 4 }
    ], async (data) => {
      const text = data['spice-text'].trim();
      if (!text) {
        showToast('Spice text required', 'error');
        return;
      }
      
      try {
        if (mode === 'add') {
          await API.addSpice(category, text);
          showToast('Spice added', 'success');
        } else {
          await API.updateSpice(category, index, text);
          showToast('Spice updated', 'success');
        }
        await this.refresh();
        
        const categoryEl = this.elements.categoriesContainer.querySelector(`[data-category="${category}"]`);
        if (categoryEl) {
          const content = categoryEl.querySelector('.accordion-content');
          const toggle = categoryEl.querySelector('.accordion-toggle');
          content.classList.remove('collapsed');
          toggle.classList.remove('collapsed');
        }
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    });
  },
  
  handleAddCategory() {
    showModal('New Category', [
      { id: 'cat-name', label: 'Category Name', type: 'text' }
    ], async (data) => {
      const name = data['cat-name'].trim();
      if (!name) {
        showToast('Category name required', 'error');
        return;
      }
      
      try {
        await API.addCategory(name);
        showToast(`Created: ${name}`, 'success');
        await this.refresh();
      } catch (e) {
        showToast(`Failed: ${e.message}`, 'error');
      }
    });
  },
  
  async handleDeleteCategory(name) {
    const category = this.spicesData.categories[name];
    const count = category?.count || 0;
    
    const msg = count > 0 
      ? `Delete "${name}" and its ${count} spice(s)?`
      : `Delete empty category "${name}"?`;
    
    if (!confirm(msg)) return;
    
    try {
      await API.deleteCategory(name);
      showToast(`Deleted: ${name}`, 'success');
      await this.refresh();
    } catch (e) {
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async handleDeleteSpice(category, index) {
    if (!confirm('Delete this spice?')) return;
    
    try {
      await API.deleteSpice(category, index);
      showToast('Deleted', 'success');
      await this.refresh();
    } catch (e) {
      showToast(`Failed: ${e.message}`, 'error');
    }
  },
  
  async handleReload() {
    try {
      await API.reloadSpices();
      showToast('Reloaded', 'success');
      await this.refresh();
    } catch (e) {
      showToast(`Failed: ${e.message}`, 'error');
    }
  }
};