// /static/shared/modal.js - Generic modal dialog system
// Requires shared.css loaded for base modal styles

/**
 * Escape HTML special characters
 */
export function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Show a modal dialog
 * @param {string} title - Modal title
 * @param {Array} fields - Array of field configs
 * @param {Function|null} onSave - Callback with form data, null for close-only mode
 * @returns {Object} - { close: Function, element: HTMLElement }
 * 
 * Field types:
 * - { id, label, type: 'text'|'number', value?, readonly? }
 * - { id, label, type: 'textarea', value?, rows? }
 * - { id, label, type: 'select', options: [], labels?: [], value? }
 * - { id, label, type: 'checkboxes', options: {key: label}, selected: [] }
 * - { type: 'html', value: 'raw html' }
 */
export function showModal(title, fields, onSave = null) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  
  let fieldsHTML = fields.map(field => {
    if (field.type === 'html') {
      return `<div class="modal-field">${field.value}</div>`;
    }
    
    if (field.type === 'textarea') {
      return `
        <div class="modal-field">
          <label for="${field.id}">${field.label}</label>
          <textarea 
            id="${field.id}" 
            rows="${field.rows || 6}"
            ${field.readonly ? 'readonly' : ''}
          >${escapeHtml(field.value || '')}</textarea>
        </div>
      `;
    }
    
    if (field.type === 'select') {
      const labels = field.labels || field.options;
      const options = field.options.map((opt, i) => {
        const selected = opt === field.value ? 'selected' : '';
        return `<option value="${escapeHtml(opt)}" ${selected}>${escapeHtml(labels[i] || opt)}</option>`;
      }).join('');
      return `
        <div class="modal-field">
          <label for="${field.id}">${field.label}</label>
          <select id="${field.id}">${options}</select>
        </div>
      `;
    }
    
    if (field.type === 'checkboxes') {
      const checkboxes = Object.entries(field.options).map(([key, label]) => {
        const checked = field.selected?.includes(key) ? 'checked' : '';
        return `
          <label class="checkbox-wrap">
            <input type="checkbox" name="${field.id}" value="${escapeHtml(key)}" ${checked}>
            <span>${label}</span>
          </label>
        `;
      }).join('');
      return `
        <div class="modal-field">
          <label>${field.label}</label>
          <div class="checkbox-group">${checkboxes}</div>
        </div>
      `;
    }
    
    // Default: text/number input
    return `
      <div class="modal-field">
        <label for="${field.id}">${field.label}</label>
        <input 
          type="${field.type || 'text'}" 
          id="${field.id}" 
          value="${escapeHtml(field.value || '')}"
          ${field.readonly ? 'readonly' : ''}
        >
      </div>
    `;
  }).join('');
  
  // Button layout
  let buttonsHTML;
  if (onSave === null) {
    buttonsHTML = `<button class="btn btn-secondary modal-close">Close</button>`;
  } else {
    buttonsHTML = `
      <button class="btn btn-secondary modal-cancel">Cancel</button>
      <button class="btn btn-primary modal-save">Save</button>
    `;
  }
  
  overlay.innerHTML = `
    <div class="modal-base">
      <div class="modal-header">
        <h3>${escapeHtml(title)}</h3>
        <button class="close-btn modal-x">&times;</button>
      </div>
      <div class="modal-body">${fieldsHTML}</div>
      <div class="modal-footer">${buttonsHTML}</div>
    </div>
  `;
  
  document.body.appendChild(overlay);
  
  // Animate in
  requestAnimationFrame(() => overlay.classList.add('active'));
  
  const close = () => {
    overlay.classList.remove('active');
    setTimeout(() => overlay.remove(), 300);
  };
  
  // Event handlers
  overlay.querySelector('.modal-x')?.addEventListener('click', close);
  overlay.querySelector('.modal-close')?.addEventListener('click', close);
  overlay.querySelector('.modal-cancel')?.addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  
  // ESC key
  const escHandler = e => {
    if (e.key === 'Escape') {
      close();
      document.removeEventListener('keydown', escHandler);
    }
  };
  document.addEventListener('keydown', escHandler);
  
  // Save handler
  overlay.querySelector('.modal-save')?.addEventListener('click', () => {
    const data = {};
    fields.forEach(field => {
      if (field.type === 'html') return;
      
      if (field.type === 'checkboxes') {
        const checked = overlay.querySelectorAll(`input[name="${field.id}"]:checked`);
        data[field.id] = Array.from(checked).map(cb => cb.value);
      } else {
        const el = overlay.querySelector(`#${field.id}`);
        if (el) data[field.id] = el.value;
      }
    });
    onSave(data);
    close();
  });
  
  // Focus first input
  const firstInput = overlay.querySelector('input:not([readonly]), textarea:not([readonly]), select');
  if (firstInput) setTimeout(() => firstInput.focus(), 50);
  
  return { close, element: overlay };
}

/**
 * Simple confirm dialog
 */
export function showConfirm(message, onConfirm) {
  return showModal('Confirm', [
    { type: 'html', value: `<p style="margin:0;color:var(--text-secondary);">${escapeHtml(message)}</p>` }
  ], () => onConfirm());
}

/**
 * Simple prompt dialog
 */
export function showPrompt(title, label, defaultValue = '') {
  return new Promise(resolve => {
    showModal(title, [
      { id: 'value', label, type: 'text', value: defaultValue }
    ], data => resolve(data.value));
  });
}

/**
 * Show help modal with formatted content
 * @param {string} title - Modal title
 * @param {string} content - Help text (supports lines ending with : as headers, • or - as bullets)
 */
export function showHelpModal(title, content) {
  const htmlContent = content.split('\n').map(line => {
    const t = line.trim();
    if (!t) return '';
    if (t.endsWith(':')) {
      return `<div style="margin:10px 0 4px;color:var(--text-light);font-weight:600">${escapeHtml(t)}</div>`;
    }
    if (t.startsWith('•') || t.startsWith('-')) {
      return `<div style="margin:2px 0 2px 14px;color:var(--text-secondary)">${escapeHtml(t)}</div>`;
    }
    return `<div style="margin:2px 0;color:var(--text-secondary)">${escapeHtml(t)}</div>`;
  }).join('');

  return showModal(title, [{ type: 'html', value: htmlContent }], null);
}