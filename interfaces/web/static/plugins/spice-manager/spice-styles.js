// styles.js - CSS injection for Spice Manager plugin
// Modal styles now in shared.css

export function injectStyles() {
  if (document.getElementById('sm-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'sm-styles';
  style.textContent = `
    .spice-manager-plugin {
      padding: 8px;
    }
    
    .sm-controls {
      display: flex;
      gap: 4px;
      margin-bottom: 8px;
    }
    
    .sm-status {
      color: var(--text-muted);
      font-size: var(--font-sm);
      text-align: center;
      padding: 4px;
      margin-bottom: 6px;
    }
    
    .sm-status strong {
      color: var(--text-tertiary);
    }
    
    .sm-categories {
      max-height: 300px;
      overflow-y: auto;
    }
    
    /* Spice items */
    .sm-spice {
      display: flex;
      align-items: flex-start;
      gap: 6px;
      padding: 4px 6px 4px 22px;
      color: var(--text-muted);
      font-size: var(--font-sm);
      border-bottom: 1px solid var(--bg-dark);
      transition: background var(--transition-normal);
    }
    
    .sm-spice:hover {
      background: var(--bg-dark);
    }
    
    .sm-spice:last-child {
      border-bottom: none;
    }
    
    .sm-spice-text {
      flex: 1;
      line-height: 1.3;
      word-break: break-word;
    }
    
    .sm-spice-actions {
      display: flex;
      gap: 2px;
      opacity: 0;
      flex-shrink: 0;
      transition: opacity var(--transition-normal);
    }
    
    .sm-spice:hover .sm-spice-actions {
      opacity: 1;
    }
    
    /* Category header - warm tint */
    .sm-category .accordion-header {
      background: var(--plugin-spice-bg);
    }
    
    .sm-category .accordion-header:hover {
      background: var(--plugin-spice-bg-hover);
    }
    
    /* Category enable/disable checkbox */
    .sm-category-checkbox {
      width: 14px;
      height: 14px;
      margin: 0 4px 0 0;
      cursor: pointer;
      accent-color: var(--trim, var(--text-secondary));
    }
    
    .sm-category-checkbox:not(:checked) + .accordion-title {
      opacity: 0.5;
      text-decoration: line-through;
    }
    
    .sm-empty-category {
      color: var(--border-hover);
      font-style: italic;
      font-size: var(--font-xs);
      padding: 6px 6px 6px 22px;
    }
    
    .sm-placeholder {
      color: var(--text-dim);
      font-style: italic;
      text-align: center;
      padding: 15px;
      font-size: var(--font-sm);
    }
  `;
  document.head.appendChild(style);
}