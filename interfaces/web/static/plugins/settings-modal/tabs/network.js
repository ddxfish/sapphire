// tabs/network.js - Network and proxy settings

export default {
  id: 'network',
  name: 'Network',
  icon: '🌐',
  description: 'Network and proxy settings',
  keys: [
    'SOCKS_ENABLED',
    'SOCKS_HOST',
    'SOCKS_PORT'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};