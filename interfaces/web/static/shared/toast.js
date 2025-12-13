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
  
  setTimeout(() => toast.remove(), duration);
}