// /static/shared/toast.js - Shared toast notifications
// Requires shared.css loaded for .toast styles

export function showToast(message, type = 'info', duration = 4000) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  
  if (container) {
    container.appendChild(toast);
  } else {
    // Fallback if no container
    toast.style.cssText = 'position:fixed;top:20px;right:20px;z-index:10001;';
    document.body.appendChild(toast);
  }
  
  // Shake chat area on error
  if (type === 'error') {
    const chatbg = document.getElementById('chatbg');
    if (chatbg) {
      chatbg.classList.add('shake');
      setTimeout(() => chatbg.classList.remove('shake'), 500);
    }
  }
  
  setTimeout(() => toast.remove(), duration);
}

/**
 * Show toast with action button
 * @param {string} message - Toast message
 * @param {string} actionText - Button text
 * @param {Function} actionCallback - Called when button clicked
 * @param {string} type - Toast type: info, success, warning, error
 * @param {number} duration - Auto-dismiss time in ms (0 = persistent until action/dismiss)
 */
export function showActionToast(message, actionText, actionCallback, type = 'info', duration = 0) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-action ${type}`;
  
  const msgSpan = document.createElement('span');
  msgSpan.className = 'toast-message';
  msgSpan.textContent = message;
  
  const btnContainer = document.createElement('div');
  btnContainer.className = 'toast-buttons';
  
  const actionBtn = document.createElement('button');
  actionBtn.className = 'toast-btn toast-btn-action';
  actionBtn.textContent = actionText;
  actionBtn.addEventListener('click', () => {
    toast.remove();
    actionCallback();
  });
  
  const dismissBtn = document.createElement('button');
  dismissBtn.className = 'toast-btn toast-btn-dismiss';
  dismissBtn.textContent = '✕';
  dismissBtn.title = 'Dismiss';
  dismissBtn.addEventListener('click', () => toast.remove());
  
  btnContainer.appendChild(actionBtn);
  btnContainer.appendChild(dismissBtn);
  toast.appendChild(msgSpan);
  toast.appendChild(btnContainer);
  
  if (container) {
    container.appendChild(toast);
  } else {
    toast.style.cssText = 'position:fixed;top:20px;right:20px;z-index:10001;';
    document.body.appendChild(toast);
  }
  
  // Auto-dismiss if duration > 0
  if (duration > 0) {
    setTimeout(() => toast.remove(), duration);
  }
}