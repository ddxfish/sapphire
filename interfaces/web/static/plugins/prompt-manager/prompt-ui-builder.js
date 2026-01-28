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
  return `
    <textarea id="pm-content">${data.content || ''}</textarea>
  `;
}

function buildAssembledEditor(data, components) {
  const comp = data.components || {};
  
  return `
    <div class="pm-component">
      <label>Persona</label>
      <div class="pm-component-row">
        <select id="pm-persona">${buildOptions(components.persona, comp.persona)}</select>
        <button class="inline-btn add" data-type="persona" title="Add">+</button>
        <button class="inline-btn edit" data-type="persona" title="Edit">&#x270E;</button>
        <button class="inline-btn delete" data-type="persona" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Location</label>
      <div class="pm-component-row">
        <select id="pm-location">${buildOptions(components.location, comp.location)}</select>
        <button class="inline-btn add" data-type="location" title="Add">+</button>
        <button class="inline-btn edit" data-type="location" title="Edit">&#x270E;</button>
        <button class="inline-btn delete" data-type="location" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Goals</label>
      <div class="pm-component-row">
        <select id="pm-goals">${buildOptions(components.goals, comp.goals)}</select>
        <button class="inline-btn add" data-type="goals" title="Add">+</button>
        <button class="inline-btn edit" data-type="goals" title="Edit">&#x270E;</button>
        <button class="inline-btn delete" data-type="goals" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Relationship</label>
      <div class="pm-component-row">
        <select id="pm-relationship">${buildOptions(components.relationship, comp.relationship)}</select>
        <button class="inline-btn add" data-type="relationship" title="Add">+</button>
        <button class="inline-btn edit" data-type="relationship" title="Edit">&#x270E;</button>
        <button class="inline-btn delete" data-type="relationship" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Format</label>
      <div class="pm-component-row">
        <select id="pm-format">${buildOptions(components.format, comp.format)}</select>
        <button class="inline-btn add" data-type="format" title="Add">+</button>
        <button class="inline-btn edit" data-type="format" title="Edit">&#x270E;</button>
        <button class="inline-btn delete" data-type="format" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Scenario</label>
      <div class="pm-component-row">
        <select id="pm-scenario">${buildOptions(components.scenario, comp.scenario)}</select>
        <button class="inline-btn add" data-type="scenario" title="Add">+</button>
        <button class="inline-btn edit" data-type="scenario" title="Edit">&#x270E;</button>
        <button class="inline-btn delete" data-type="scenario" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Extras</label>
      <div class="pm-component-row">
        <div class="pm-selected-items" id="pm-extras-display">${(comp.extras || []).join(', ') || 'none'}</div>
        <button class="inline-btn pm-extras-select-btn" title="Select Active">&#x2713;</button>
        <button class="inline-btn edit pm-extras-edit-btn" title="Edit Definitions">&#x270E;</button>
        <button class="inline-btn add" data-type="extras" title="Add New">+</button>
        <button class="inline-btn delete pm-extras-delete-btn" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
    <div class="pm-component">
      <label>Emotions</label>
      <div class="pm-component-row">
        <div class="pm-selected-items" id="pm-emotions-display">${(comp.emotions || []).join(', ') || 'none'}</div>
        <button class="inline-btn pm-emotions-select-btn" title="Select Active">&#x2713;</button>
        <button class="inline-btn edit pm-emotions-edit-btn" title="Edit Definitions">&#x270E;</button>
        <button class="inline-btn add" data-type="emotions" title="Add New">+</button>
        <button class="inline-btn delete pm-emotions-delete-btn" title="Delete">&#x1F5D1;</button>
      </div>
    </div>
  `;
}

function buildOptions(componentOptions, selected) {
  const options = componentOptions || {};
  let html = '';
  
  for (const [key, value] of Object.entries(options)) {
    const isSelected = key === selected ? 'selected' : '';
    html += `<option value="${key}" ${isSelected}>${key}</option>`;
  }
  
  return html || `<option value="${selected || 'default'}">${selected || 'default'}</option>`;
}

export function buildComponentSelectOptions(components) {
  const options = [];
  const types = ['persona', 'location', 'relationship', 'goals', 'format', 'scenario', 'extras', 'emotions'];
  
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