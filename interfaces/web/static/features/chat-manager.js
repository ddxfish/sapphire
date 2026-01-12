// features/chat-manager.js - Chat list management, import/export, kebab menus
import * as api from '../api.js';
import * as audio from '../audio.js';
import * as ui from '../ui.js';
import { getElements, getIsProc, setHistLen, refresh } from '../core/state.js';
import { updateScene, updateSendButtonLLM } from './scene.js';

export async function populateChatDropdown() {
    const { chatSelect } = getElements();
    try {
        const data = await api.fetchChatList();
        ui.renderChatDropdown(data.chats, data.active_chat);
    } catch (e) {
        console.error('Failed to load chat list:', e);
        if (chatSelect && chatSelect.options.length === 0) {
            console.log('Backend may still be starting up, will retry...');
        }
    }
}

export async function handleChatChange() {
    const { chatSelect } = getElements();
    if (getIsProc()) {
        console.log('Cannot switch chats while processing');
        return;
    }
    
    const selectedChat = chatSelect.value;
    if (!selectedChat) return;
    
    try {
        audio.stop();
        await api.activateChat(selectedChat);
        const len = await refresh(false);
        setHistLen(len);
        await updateScene();
        
        // Update send button based on this chat's LLM settings
        try {
            const response = await api.getChatSettings(selectedChat);
            const settings = response?.settings || {};
            updateSendButtonLLM(settings.llm_primary || 'auto', settings.llm_model || '');
        } catch (e) {
            updateSendButtonLLM('auto');
        }
    } catch (e) {
        console.error('Failed to switch chat:', e);
        ui.showToast(`Failed to switch chat: ${e.message}`, 'error');
        await populateChatDropdown();
    }
}

export async function handleNewChat() {
    closeAllKebabs();
    const name = prompt('Enter name for new chat:');
    if (!name || !name.trim()) return;
    
    const { chatSelect } = getElements();
    
    try {
        await api.createChat(name);
        await populateChatDropdown();
        
        const normalizedName = name.toLowerCase().replace(/\s+/g, '_');
        chatSelect.value = normalizedName;
        await handleChatChange();
    } catch (e) {
        console.error('Failed to create chat:', e);
        if (e.message.includes('already exists')) {
            alert(`Chat already exists! Try a different name.`);
        } else {
            alert(`Failed to create chat: ${e.message}`);
        }
    }
}

export async function handleDeleteChat() {
    closeAllKebabs();
    const { chatSelect } = getElements();
    const selectedChat = chatSelect.value;
    
    if (!selectedChat) {
        alert('No chat selected');
        return;
    }
    
    const displayName = chatSelect.options[chatSelect.selectedIndex].text;
    if (!confirm(`Delete "${displayName}" permanently?`)) return;
    
    try {
        await api.activateChat('default');
        await api.deleteChat(selectedChat);
        await populateChatDropdown();
        chatSelect.value = 'default';
        const len = await refresh(false);
        setHistLen(len);
    } catch (e) {
        console.error('Failed to delete chat:', e);
        alert(`Failed to delete chat: ${e.message}`);
    }
}

export async function handleClearChat() {
    const { chatSelect } = getElements();
    const displayName = chatSelect.options[chatSelect.selectedIndex]?.text || 'this chat';
    if (!confirm(`Clear all messages in "${displayName}"?`)) return;
    
    closeAllKebabs();
    try {
        await api.clearChat();
        const len = await refresh(false);
        setHistLen(len);
        ui.showToast('Chat cleared', 'success');
    } catch (e) {
        console.error('Failed to clear chat:', e);
        ui.showToast('Failed to clear chat', 'error');
    }
}

export async function handleExportChat() {
    closeAllKebabs();
    const { chatSelect } = getElements();
    
    try {
        const data = await api.fetchRawHistory();
        const chatName = chatSelect.value || 'chat';
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${chatName}_export.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        ui.showToast('Chat exported', 'success');
    } catch (e) {
        console.error('Failed to export chat:', e);
        ui.showToast('Failed to export chat', 'error');
    }
}

export function handleImportChat() {
    closeAllKebabs();
    const { importFileInput } = getElements();
    importFileInput.click();
}

export async function handleImportFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    
    try {
        const text = await file.text();
        const data = JSON.parse(text);
        
        // Support both raw array and {messages: []} format
        const messages = Array.isArray(data) ? data : data.messages;
        if (!messages || !Array.isArray(messages)) {
            ui.showToast('Invalid chat format', 'error');
            return;
        }
        
        await api.importChat(messages);
        const len = await refresh(false);
        setHistLen(len);
        ui.showToast(`Imported ${messages.length} messages`, 'success');
    } catch (e) {
        console.error('Failed to import chat:', e);
        ui.showToast('Failed to import chat', 'error');
    } finally {
        e.target.value = '';
    }
}

// Kebab menu utilities
export function toggleKebab(menu) {
    const wasOpen = menu.classList.contains('open');
    closeAllKebabs();
    if (!wasOpen) menu.classList.add('open');
}

export function closeAllKebabs() {
    document.querySelectorAll('.kebab-menu.open').forEach(m => m.classList.remove('open'));
}

export async function handleLogout() {
    closeAllKebabs();
    try {
        await fetch('/logout', { method: 'POST' });
        window.location.href = '/login';
    } catch (e) {
        console.error('Logout failed:', e);
        window.location.href = '/login';
    }
}

export async function handleRestart() {
    closeAllKebabs();
    if (!confirm('Restart Sapphire? The page will reload when the server is back.')) {
        return;
    }
    try {
        await fetch('/api/system/restart', { method: 'POST' });
        showRestartingScreen();
    } catch (e) {
        console.error('Restart failed:', e);
        alert('Restart request failed: ' + e.message);
    }
}

function showRestartingScreen() {
    document.body.innerHTML = `
        <div style="position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;font-family:system-ui,sans-serif;background:#1a1a2e;">
            <div style="font-size:1.5rem;color:#888;">Restarting Sapphire...</div>
            <div id="restart-status" style="font-size:1rem;color:#666;">Waiting for server...</div>
            <button id="manual-refresh-btn" style="display:none;padding:12px 24px;font-size:1rem;cursor:pointer;background:#4a9eff;color:white;border:none;border-radius:6px;">
                Click to Refresh
            </button>
        </div>
    `;
    
    // Show manual button after 5 seconds regardless
    setTimeout(() => {
        const btn = document.getElementById('manual-refresh-btn');
        if (btn) {
            btn.style.display = 'block';
            btn.addEventListener('click', () => window.location.reload());
        }
    }, 5000);
    
    // Start polling after 2 second delay
    setTimeout(() => pollForServer(), 2000);
}

function pollForServer(attempts = 0) {
    const statusEl = document.getElementById('restart-status');
    const maxAttempts = 30;
    
    if (attempts >= maxAttempts) {
        if (statusEl) statusEl.textContent = 'Server may be ready. Click button to refresh.';
        return;
    }
    
    if (statusEl) statusEl.textContent = `Checking server... (${attempts + 1}/${maxAttempts})`;
    
    fetch('/api/settings', { method: 'GET' })
        .then(r => {
            if (r.ok) {
                if (statusEl) statusEl.textContent = 'Server is back! Refreshing...';
                setTimeout(() => window.location.reload(), 500);
            } else {
                setTimeout(() => pollForServer(attempts + 1), 1000);
            }
        })
        .catch(() => {
            setTimeout(() => pollForServer(attempts + 1), 1000);
        });
}