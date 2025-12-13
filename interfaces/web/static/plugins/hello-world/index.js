// Hello World sample plugin
export default {
  name: 'hello-world',
  version: '1.0.0',
  
  init: (container) => {
    // Load CSS if it exists
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/plugins/hello-world/style.css';
    document.head.appendChild(link);
    
    // Create plugin content
    const pluginDiv = document.createElement('div');
    pluginDiv.className = 'plugin-hello-world';
    pluginDiv.innerHTML = `
      <div style="padding: 20px; text-align: center;">
        <h3 style="margin: 0 0 10px 0; font-size: 14px;">Hello World</h3>
        <p style="margin: 0; font-size: 12px; color: #888;">
          Plugin system active
        </p>
      </div>
    `;
    
    container.appendChild(pluginDiv);
    console.log('Hello World plugin initialized');
  },
  
  destroy: () => {
    const element = document.querySelector('.plugin-hello-world');
    if (element) element.remove();
    console.log('Hello World plugin destroyed');
  }
};