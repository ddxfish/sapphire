// ui.js - UI Coordinator (main interface)

import * as Images from './ui-images.js';
import * as Parsing from './ui-parsing.js';
import * as Streaming from './ui-streaming.js';
import * as api from './api.js';

// DOM references
const chat = document.getElementById('chat-container');
const chatbgOverlay = document.getElementById('chatbg-overlay');
const msgTpl = document.getElementById('message-template');
const statusTpl = document.getElementById('status-template');

// Avatar display setting (loaded from /api/init)
let avatarsInChat = true;

// Export setter for immediate updates from settings modal
export const setAvatarsInChat = (val) => { avatarsInChat = val; };

// Avatar path cache - populated from /api/init, eliminates 404 cascades
let avatarPaths = null;

// Persona avatar URL cache - avoids repeated fetches per message
const personaAvatarCache = new Map();

// Current persona for the active chat (set by chat.js when settings load)
let currentPersona = null;
export const setCurrentPersona = (name) => { currentPersona = name; };

// Initialize from /api/init data (called from main.js after init data loads)
export const initFromInitData = (initData) => {
    if (initData.settings?.AVATARS_IN_CHAT !== undefined) {
        avatarsInChat = initData.settings.AVATARS_IN_CHAT !== false;
    }
    if (initData.avatars) {
        avatarPaths = initData.avatars;
    }
};

// Getter for avatar paths (returns cached or fetches if needed)
const loadAvatarPaths = async () => {
    if (avatarPaths) return avatarPaths;

    // Fallback fetch if init data wasn't loaded yet
    try {
        const res = await fetch('/api/avatars');
        if (res.ok) {
            avatarPaths = await res.json();
        }
    } catch (e) {
        avatarPaths = { user: null, assistant: null };
    }
    return avatarPaths || { user: null, assistant: null };
};

// Export for cache invalidation after avatar upload
export const refreshAvatarPaths = async () => {
    try {
        const res = await fetch('/api/avatars');
        if (res.ok) {
            avatarPaths = await res.json();
        }
    } catch (e) {
        // Keep existing cache on error
    }
};

// =============================================================================
// SCROLL MANAGEMENT
// =============================================================================

const SCROLL_THRESHOLD = 100;

const isNearBottom = () => {
    if (!chatbgOverlay) return true;
    const scrollableHeight = chatbgOverlay.scrollHeight - chatbgOverlay.clientHeight;
    const currentScroll = chatbgOverlay.scrollTop;
    return (scrollableHeight - currentScroll) <= SCROLL_THRESHOLD;
};

const scrollToBottomIfSticky = (force = false) => {
    if (!chatbgOverlay) return;
    if (force || isNearBottom()) {
        chatbgOverlay.scrollTop = chatbgOverlay.scrollHeight;
    }
};

export const forceScrollToBottom = () => scrollToBottomIfSticky(true);

// =============================================================================
// SIMPLE UTILITIES
// =============================================================================

const createElem = (tag, attrs = {}, content = '') => {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => k === 'style' ? el.style.cssText = v : el.setAttribute(k, v));
    if (content) el.textContent = content;
    return el;
};

const setAvatarWithFallback = async (img, role) => {
    if (!avatarsInChat) {
        img.style.display = 'none';
        return;
    }
    
    // Lazy load avatars for performance
    img.loading = 'lazy';
    
    // Get cached path (or wait for it to load)
    const paths = await loadAvatarPaths();
    const src = paths[role];
    
    if (src) {
        img.src = src;
        img.onerror = () => { img.style.display = 'none'; };
    } else {
        img.style.display = 'none';
    }
};

const setPersonaAvatar = (img, personaName) => {
    if (!avatarsInChat) {
        img.style.display = 'none';
        return;
    }
    img.loading = 'lazy';

    // Use cached URL or build it
    const url = `/api/personas/${encodeURIComponent(personaName)}/avatar`;
    img.src = url;
    img.onerror = async () => {
        // Fall back to default assistant avatar
        const paths = await loadAvatarPaths();
        if (paths.assistant) {
            img.src = paths.assistant;
            img.onerror = () => { img.style.display = 'none'; };
        } else {
            img.style.display = 'none';
        }
    };
};

const createToolbar = (idx, total, role = 'user') => {
    const tb = createElem('div', { class: 'toolbar' });
    const buttons = [
        ['trash-btn', 'trash', '\u{1F5D1}\uFE0F', 'Delete'],
        ['regen-btn', 'regenerate', '\u{1F504}', 'Regenerate'],
        ['continue-btn', 'continue', '\u{25B6}\uFE0F', 'Continue'],
        ['edit-btn', 'edit', '\u{270F}\uFE0F', 'Edit'],
        ['replay-btn', 'replay', '\u{1F50A}', 'Replay TTS']
    ];

    buttons.forEach(([cls, act, icon, title]) => {
        const btn = createElem('button', { class: cls, 'data-action': act, 'data-message-index': idx }, icon);
        btn.title = title;
        tb.appendChild(btn);
    });
    return tb;
};

const updateToolbars = () => {
    const msgs = chat.querySelectorAll('.message:not(.status):not(.error)');
    msgs.forEach((msg, i) => {
        const toolbar = msg.querySelector('.toolbar');
        if (!toolbar) return;
        
        const role = msg.classList.contains('assistant') ? 'assistant' : 'user';
        const btns = toolbar.querySelectorAll('button');
        
        if (btns.length === 0) {
            const newToolbar = createToolbar(i, msgs.length, role);
            toolbar.replaceWith(newToolbar);
        } else {
            btns.forEach(btn => {
                btn.dataset.messageIndex = i;
                if (btn.classList.contains('trash-btn')) {
                    const toDel = msgs.length - i;
                    const pairs = Math.ceil(toDel / 2);
                    btn.title = `Delete from here (${toDel} msg, ${pairs} pair${pairs === 1 ? '' : 's'})`;
                }
            });
        }
    });
};

export const forceUpdateToolbars = updateToolbars;

// =============================================================================
// MESSAGE CREATION
// =============================================================================

const createMessage = (msg, idx = null, total = null, isHistoryRender = false) => {
    const clone = msgTpl.content.cloneNode(true);
    const msgEl = clone.querySelector('.message');
    const avatar = clone.querySelector('.msg-avatar');
    const contentDiv = clone.querySelector('.message-content');
    const wrapper = clone.querySelector('.message-wrapper');
    const tb = wrapper.querySelector('.toolbar');
    
    const role = msg.role || 'user';
    msgEl.classList.add(role);
    if (role === 'assistant' && msg.persona) {
        setPersonaAvatar(avatar, msg.persona);
    } else {
        setAvatarWithFallback(avatar, role);
    }
    
    if (idx !== null) {
        const toolbar = createToolbar(idx, total, role);
        tb.replaceWith(toolbar);
    }
    
    Parsing.parseContent(contentDiv, msg, isHistoryRender, scrollToBottomIfSticky);
    
    // Add metadata footer for assistant messages
    if (role === 'assistant' && msg.metadata) {
        const meta = msg.metadata;
        const parts = [];
        
        if (meta.duration_seconds) {
            parts.push(`${meta.duration_seconds}s`);
        }
        if (meta.tokens_per_second) {
            parts.push(`${meta.tokens_per_second} tok/s`);
        }
        if (meta.model) {
            parts.push(meta.model);
        }
        
        if (parts.length > 0) {
            const metaDiv = createElem('div', { class: 'message-metadata' }, parts.join(' • '));
            contentDiv.appendChild(metaDiv);
        }
    }
    
    return { clone, contentDiv, msg: msgEl };
};

// =============================================================================
// PUBLIC API - MESSAGE OPERATIONS
// =============================================================================

export const addUserMessage = (txt, images = null, files = null) => {
    const cnt = chat.querySelectorAll('.message').length;
    const msgData = { role: 'user', content: txt };

    // Add images for display if present
    if (images && images.length > 0) {
        msgData.images = images.map(img => ({
            data: img.data,
            media_type: img.media_type
        }));
    }

    // Add files for display if present
    if (files && files.length > 0) {
        msgData.files = files.map(f => ({
            filename: f.filename,
            text: f.text
        }));
    }

    const { clone } = createMessage(msgData, cnt, cnt + 1, false);
    chat.appendChild(clone);
    scrollToBottomIfSticky(true);
};

export const renderHistory = (hist) => {
    Images.clearPendingImages();
    chat.querySelectorAll('.message:not(.status):not(.error)').forEach(msg => msg.remove());
    
    if (!hist || !Array.isArray(hist)) return;
    
    hist.forEach((msg, i) => {
        if (!msg || typeof msg !== 'object') return;
        const { clone } = createMessage(msg, i, hist.length, true);
        chat.appendChild(clone);
    });
    updateToolbars();
    
    const waitForImages = () => {
        if (!Images.hasPendingImages()) {
            scrollToBottomIfSticky(true);
        } else {
            setTimeout(() => {
                if (Images.hasPendingImages()) {
                    console.log(`Timeout: images still pending, scrolling anyway`);
                    Images.clearPendingImages();
                }
                scrollToBottomIfSticky(true);
            }, 5000);
        }
    };
    
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            waitForImages();
        });
    });
};


// =============================================================================
// STATUS MESSAGES
// =============================================================================

export const showStatus = () => {
    if (!document.getElementById('status-message')) {
        chat.appendChild(statusTpl.content.cloneNode(true));
        scrollToBottomIfSticky();
    }
};

export const hideStatus = () => {
    const st = document.getElementById('status-message');
    if (st) st.remove();
};

export const updateStatus = (txt) => {
    const st = document.getElementById('status-message');
    if (st) {
        const span = st.querySelector('.status-text');
        if (span) span.textContent = txt;
    }
};

// =============================================================================
// STREAMING
// =============================================================================

export const startStreaming = () => {
    const streamMsg = { role: 'assistant', content: '' };
    if (currentPersona) streamMsg.persona = currentPersona;
    const { clone, contentDiv, msg } = createMessage(streamMsg, null, null, false);
    msg.id = 'streaming-message';
    msg.dataset.streaming = 'true';
    return Streaming.startStreaming(chat, clone, scrollToBottomIfSticky);
};

export const appendStream = (chunk) => {
    Streaming.appendStream(chunk, scrollToBottomIfSticky);
};

export const startTool = (toolId, toolName, args) => {
    Streaming.startTool(toolId, toolName, args, scrollToBottomIfSticky);
};

// Tool names that affect scope counts
const GOAL_TOOLS = ['create_goal', 'update_goal', 'delete_goal'];
const MEMORY_TOOLS = ['save_memory', 'delete_memory'];
const KNOWLEDGE_TOOLS = ['save_person', 'save_knowledge'];

const refreshScopeCounts = async (selectId, apiPath) => {
    try {
        const sel = document.querySelector(selectId);
        if (!sel) return;
        const current = sel.value;
        const resp = await fetch(apiPath);
        if (!resp.ok) return;
        const data = await resp.json();
        const scopes = data.scopes || [];
        sel.innerHTML = '<option value="none">None</option>' +
            scopes.map(s => `<option value="${s.name}">${s.name} (${s.count})</option>`).join('');
        sel.value = current;
    } catch (e) { /* silent */ }
};

export const endTool = (toolId, toolName, result, isError) => {
    Streaming.endTool(toolId, toolName, result, isError, scrollToBottomIfSticky);
    if (!isError) {
        if (GOAL_TOOLS.includes(toolName)) refreshScopeCounts('#sb-goal-scope', '/api/goals/scopes');
        if (MEMORY_TOOLS.includes(toolName)) refreshScopeCounts('#sb-memory-scope', '/api/memory/scopes');
        if (KNOWLEDGE_TOOLS.includes(toolName)) refreshScopeCounts('#sb-knowledge-scope', '/api/knowledge/scopes');
        if (toolName === 'save_person') refreshScopeCounts('#sb-people-scope', '/api/knowledge/people/scopes');
    }
};

export const finishStreaming = async (ephemeral = false) => {
    const streamingMsg = document.getElementById('streaming-message');
    
    Streaming.finishStreaming(updateToolbars);
    
    // Ephemeral: just remove the message, no swap with history
    if (ephemeral) {
        if (streamingMsg) {
            streamingMsg.remove();
        }
        scrollToBottomIfSticky(true);
        return;
    }
    
    if (streamingMsg) {
        await new Promise(resolve => setTimeout(resolve, 500));
        
        try {
            const hist = await api.fetchHistory();
            if (hist && hist.length > 0) {
                const lastMsg = hist[hist.length - 1];
                const { clone } = createMessage(lastMsg, hist.length - 1, hist.length, true);
                streamingMsg.replaceWith(clone);
            }
        } catch (e) {
            console.error('[SWAP] Failed:', e);
        }
    }
    
    scrollToBottomIfSticky(true);
    
    // Update scene state (spice tooltip, etc.) after generation completes
    import('./features/scene.js').then(scene => scene.updateScene());
};

export const cancelStreaming = () => {
    Streaming.cancelStreaming();
};

export const hasVisibleContent = () => {
    return Streaming.hasVisibleContent();
};

// =============================================================================
// CHAT MANAGEMENT
// =============================================================================

export const renderChatDropdown = (chats, activeChat, storyChats = [], privateChats = []) => {
    // Combine all chats for the hidden select (needs all chats for switching)
    const allChats = [...chats, ...privateChats, ...storyChats];

    // Update hidden select (state holder used throughout the app)
    const select = document.getElementById('chat-select');
    if (select) {
        select.innerHTML = '';
        allChats.forEach(chat => {
            const opt = document.createElement('option');
            opt.value = chat.name;
            opt.textContent = chat.display_name;
            if (chat.name === activeChat) opt.selected = true;
            select.appendChild(opt);
        });
    }

    // Build picker items — regular chats, then private, then story
    let itemsHtml = chats.map(c => `
        <button class="chat-picker-item ${c.name === activeChat ? 'active' : ''}"
                data-chat="${c.name}">
            <span class="chat-picker-item-check">${c.name === activeChat ? '\u2713' : ''}</span>
            <span class="chat-picker-item-name">${escapeHtml(c.display_name)}</span>
        </button>
    `).join('');

    if (privateChats.length > 0) {
        itemsHtml += '<div class="chat-picker-divider"></div>';
        itemsHtml += privateChats.map(c => `
            <button class="chat-picker-item chat-picker-private ${c.name === activeChat ? 'active' : ''}"
                    data-chat="${c.name}">
                <span class="chat-picker-item-check">${c.name === activeChat ? '\u2713' : ''}</span>
                <span class="chat-picker-item-name">${escapeHtml(c.display_name)}</span>
            </button>
        `).join('');
    }

    if (storyChats.length > 0) {
        itemsHtml += '<div class="chat-picker-divider"></div>';
        itemsHtml += storyChats.map(c => `
            <button class="chat-picker-item chat-picker-story ${c.name === activeChat ? 'active' : ''}"
                    data-chat="${c.name}">
                <span class="chat-picker-item-check">${c.name === activeChat ? '\u2713' : ''}</span>
                <span class="chat-picker-item-name">${escapeHtml(c.display_name)}</span>
            </button>
        `).join('');
    }

    // Action buttons at the bottom
    itemsHtml += '<div class="chat-picker-divider"></div>';
    itemsHtml += '<button class="chat-picker-story-btn" data-action="new-private">&#x1F512; New Private...</button>';
    itemsHtml += '<button class="chat-picker-story-btn" data-action="new-story">&#x1F4D6; New Story...</button>';

    // Update sidebar chat picker dropdown
    const sbDropdown = document.getElementById('sb-chat-picker-dropdown');
    if (sbDropdown) sbDropdown.innerHTML = itemsHtml;

    // Update header names (check all chats)
    const active = allChats.find(c => c.name === activeChat);
    const displayName = active?.display_name || activeChat || 'Chat';

    const headerName = document.getElementById('chat-header-name');
    if (headerName) headerName.textContent = displayName;

    const sbName = document.getElementById('sb-chat-name');
    if (sbName) sbName.textContent = displayName;

    // Notify sidebar to reload with correct chat settings
    if (select) select.dispatchEvent(new Event('chat-list-ready'));
};

const escapeHtml = (str) => {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
};

// =============================================================================
// TEXT EXTRACTION
// =============================================================================

export const extractProseText = (el) => {
    return Parsing.extractProseText(el);
};

export const extractEditableContent = (contentEl, timestamp) => {
    const details = [...contentEl.querySelectorAll('details')];
    const lastThink = details.filter(d => d.querySelector('summary').textContent.includes('Think')).pop();
    
    let text = '';
    if (lastThink) text = `<think>${lastThink.querySelector('div').textContent}</think>\n\n`;
    text += [...contentEl.querySelectorAll('p')].map(p => p.textContent).join('\n\n');
    return { text: text.trim(), timestamp };
};

// =============================================================================
// EDIT MODE
// =============================================================================

export const enterEditMode = (msgEl, idx, timestamp) => {
    const content = msgEl.querySelector('.message-content');
    const toolbar = msgEl.querySelector('.toolbar');
    const { text } = extractEditableContent(content, timestamp);
    
    content.dataset.original = content.innerHTML;
    content.dataset.editTimestamp = timestamp;
    msgEl.dataset.editTimestamp = timestamp;
    
    content.innerHTML = `
        <textarea id="edit-textarea" class="edit-textarea" rows="10">${text}</textarea>
        <div class="edit-actions">
            <button id="save-edit" class="btn btn-primary" data-index="${idx}">Save</button>
            <button id="cancel-edit" class="btn btn-secondary">Cancel</button>
        </div>
    `;
    toolbar.style.display = 'none';
    msgEl.classList.add('editing');
    document.getElementById('edit-textarea').focus();
};

export const exitEditMode = (msgEl, restore = true) => {
    const content = msgEl.querySelector('.message-content');
    const toolbar = msgEl.querySelector('.toolbar');
    if (restore) content.innerHTML = content.dataset.original;
    toolbar.style.display = '';
    msgEl.classList.remove('editing');
    delete content.dataset.original;
};

// =============================================================================
// TOAST NOTIFICATIONS
// =============================================================================

export const showToast = (msg, type = 'error', duration = 4000) => {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span class="toast-text">${msg.replace(/</g, '&lt;')}</span><button class="toast-close">\u00d7</button>`;
    toast.querySelector('.toast-close').addEventListener('click', () => toast.remove());
    container.appendChild(toast);

    // Shake chat area on error
    if (type === 'error') {
        const chatbg = document.getElementById('chatbg');
        if (chatbg) {
            chatbg.classList.add('shake');
            setTimeout(() => chatbg.classList.remove('shake'), 500);
        }
    }

    setTimeout(() => toast.remove(), duration);
};