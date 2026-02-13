// UI construction functions
export function buildMainUI() {
  const wrapper = document.createElement('div');
  wrapper.className = 'prompt-manager-plugin';
  wrapper.innerHTML = `
    <div class="pm-controls">
      <select id="pm-preset-select">
        <option value="">Loading...</option>
      </select>
      <div class="pm-control-buttons">
        <button id="pm-new-btn" class="plugin-btn" title="New">+</button>
        <button id="pm-refresh-btn" class="plugin-btn" title="Refresh">&#x1F504;</button>
        <button id="pm-delete-btn" class="plugin-btn" title="Delete">&#x1F5D1;</button>
        <button id="pm-preview-btn" class="plugin-btn" title="Preview">&#x1F50D;</button>
        <button id="pm-export-btn" class="plugin-btn" title="Import/Export">&#x1F4E4;</button>
      </div>
    </div>
    <div id="pm-editor" class="pm-editor">
      <div class="pm-placeholder">Select a prompt to edit</div>
    </div>
  `;
  return wrapper;
}

export function buildEditor(data, components) {
  const type = data.type || 'monolith';
  
  if (type === 'monolith') {
    return buildMonolithEditor(data);
  } else if (type === 'assembled') {
    return buildAssembledEditor(data, components);
  } else {
    return `<div class="pm-error">Unknown prompt type: ${type}</div>`;
  }
}

function buildMonolithEditor(data) {
  const privacyChecked = data.privacy_required ? 'checked' : '';
  return `
    <textarea id="pm-content">${data.content || ''}</textarea>
    <div class="pm-privacy-row">
      <label class="pm-privacy-label">
        <input type="checkbox" id="pm-privacy-required" ${privacyChecked}>
        <span class="pm-privacy-icon">🔒</span> Private Only
        <span class="pm-privacy-hint">(requires Privacy Mode)</span>
      </label>
    </div>
  `;
}

function buildAssembledEditor(data, components) {
  const comp = data.components || {};

  // Single-select components: dropdown + pencil
  const singleSelectTypes = ['character', 'location', 'goals', 'relationship', 'format', 'scenario'];
  const singleSelectHTML = singleSelectTypes.map(type => `
    <div class="pm-component">
      <label>${type.charAt(0).toUpperCase() + type.slice(1)}</label>
      <div class="pm-component-row">
        <select id="pm-${type}">${buildOptions(components[type], comp[type])}</select>
        <button class="inline-btn edit pm-component-edit" data-type="${type}" title="Edit ${type}s">&#x270E;</button>
      </div>
    </div>
  `).join('');

  // Multi-select components: display + pencil
  const multiSelectHTML = `
    <div class="pm-component">
      <label>Extras</label>
      <div class="pm-component-row">
        <div class="pm-selected-items" id="pm-extras-display">${(comp.extras || []).join(', ') || 'none'}</div>
        <button class="inline-btn edit pm-component-edit" data-type="extras" title="Edit extras">&#x270E;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Emotions</label>
      <div class="pm-component-row">
        <div class="pm-selected-items" id="pm-emotions-display">${(comp.emotions || []).join(', ') || 'none'}</div>
        <button class="inline-btn edit pm-component-edit" data-type="emotions" title="Edit emotions">&#x270E;</button>
      </div>
    </div>
  `;

  // Privacy checkbox
  const privacyChecked = data.privacy_required ? 'checked' : '';
  const privacyHTML = `
    <div class="pm-privacy-row">
      <label class="pm-privacy-label">
        <input type="checkbox" id="pm-privacy-required" ${privacyChecked}>
        <span class="pm-privacy-icon">🔒</span> Private Only
        <span class="pm-privacy-hint">(requires Privacy Mode)</span>
      </label>
    </div>
  `;

  return singleSelectHTML + multiSelectHTML + privacyHTML;
}

function buildOptions(componentOptions, selected) {
  const options = componentOptions || {};
  let html = '';
  
  for (const [key, value] of Object.entries(options).sort(([a], [b]) => a.localeCompare(b))) {
    const isSelected = key === selected ? 'selected' : '';
    html += `<option value="${key}" ${isSelected}>${key}</option>`;
  }
  
  return html || `<option value="${selected || 'default'}">${selected || 'default'}</option>`;
}

export function buildComponentSelectOptions(components) {
  const options = [];
  const types = ['character', 'location', 'relationship', 'goals', 'format', 'scenario', 'extras', 'emotions'];
  
  types.forEach(type => {
    if (components[type]) {
      Object.keys(components[type]).forEach(key => {
        options.push({
          value: `${type}:${key}`,
          label: `${type} -> ${key}`,
          type: type,
          key: key,
          text: components[type][key]
        });
      });
    }
  });
  
  return options;
}