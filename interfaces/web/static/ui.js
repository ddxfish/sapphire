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

// Avatar display setting (fetched on load)
let avatarsInChat = true;

// Fetch avatar setting on module init
(async () => {
    try {
        const res = await fetch('/api/settings/AVATARS_IN_CHAT');
        if (res.ok) {
            const data = await res.json();
            avatarsInChat = data.value !== false;
        }
    } catch (e) {
        console.log('[UI] Could not fetch avatar setting, defaulting to enabled');
    }
})();

// Export setter for immediate updates from settings modal
export const setAvatarsInChat = (val) => { avatarsInChat = val; };

// Avatar paths: user overrides first, then static fallbacks (try png then jpg in each)
const AVATARS = {
    user: ['/user-assets/avatars/user.png', '/user-assets/avatars/user.jpg', '/static/users/user.png', '/static/users/user.jpg'],
    assistant: ['/user-assets/avatars/assistant.png', '/user-assets/avatars/assistant.jpg', '/static/users/assistant.png', '/static/users/assistant.jpg']
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

const setAvatarWithFallback = (img, role) => {
    if (!avatarsInChat) {
        img.style.display = 'none';
        return;
    }
    
    const paths = AVATARS[role] || AVATARS.user;
    let idx = 0;
    
    const tryNext = () => {
        if (idx < paths.length) {
            img.src = paths[idx++];
        } else {
            img.style.display = 'none';
        }
    };
    
    img.onerror = tryNext;
    tryNext();
};

const createToolbar = (idx, total) => {
    const tb = createElem('div', { class: 'toolbar' });
    [
        ['trash-btn', 'trash', '\u{1F5D1}\uFE0F', 'Delete'], 
        ['regen-btn', 'regenerate', '\u{1F504}', 'Regenerate'], 
        ['continue-btn', 'continue', '\u{25B6}\uFE0F', 'Continue'],
        ['edit-btn', 'edit', '\u{270F}\uFE0F', 'Edit']
    ].forEach(([cls, act, icon, title]) => {
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
        
        const btns = toolbar.querySelectorAll('button');
        
        if (btns.length === 0) {
            const newToolbar = createToolbar(i, msgs.length);
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
    setAvatarWithFallback(avatar, role);
    
    if (idx !== null) {
        const toolbar = createToolbar(idx, total);
        tb.replaceWith(toolbar);
    }
    
    Parsing.parseContent(contentDiv, msg, isHistoryRender, scrollToBottomIfSticky);
    
    return { clone, contentDiv, msg: msgEl };
};

// =============================================================================
// PUBLIC API - MESSAGE OPERATIONS
// =============================================================================

export const addUserMessage = (txt) => {
    const cnt = chat.querySelectorAll('.message').length;
    const { clone } = createMessage({ role: 'user', content: txt }, cnt, cnt + 1, false);
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
    const { clone, contentDiv, msg } = createMessage({ role: 'assistant', content: '' }, null, null, false);
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

export const endTool = (toolId, toolName, result, isError) => {
    Streaming.endTool(toolId, toolName, result, isError, scrollToBottomIfSticky);
};

export const finishStreaming = async (ephemeral = false) => {
    console.log('[FINISH TIMING] finishStreaming called, ephemeral=', ephemeral, performance.now());
    const streamingMsg = document.getElementById('streaming-message');
    
    Streaming.finishStreaming(updateToolbars);
    
    // Ephemeral: just remove the message, no swap with history
    if (ephemeral) {
        if (streamingMsg) {
            streamingMsg.remove();
            console.log('[SWAP] Ephemeral - removed streaming message without swap');
        }
        scrollToBottomIfSticky(true);
        return;
    }
    
    if (streamingMsg) {
        console.log('[FINISH TIMING] Starting 500ms wait', performance.now());
        await new Promise(resolve => setTimeout(resolve, 500));
        console.log('[FINISH TIMING] 500ms wait done, fetching history', performance.now());
        
        try {
            const hist = await api.fetchHistory();
            console.log('[FINISH TIMING] history fetched', performance.now());
            if (hist && hist.length > 0) {
                const lastMsg = hist[hist.length - 1];
                const { clone } = createMessage(lastMsg, hist.length - 1, hist.length, true);
                streamingMsg.replaceWith(clone);
                console.log('[SWAP] Swapped to history render');
            }
        } catch (e) {
            console.error('[SWAP] Failed:', e);
        }
    }
    
    console.log('[FINISH TIMING] finishStreaming complete', performance.now());
    scrollToBottomIfSticky(true);
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

export const renderChatDropdown = (chats, activeChat) => {
    const select = document.getElementById('chat-select');
    if (!select) return;
    
    select.innerHTML = '';
    chats.forEach(chat => {
        const opt = document.createElement('option');
        opt.value = chat.name;
        opt.textContent = chat.display_name;
        if (chat.name === activeChat) opt.selected = true;
        select.appendChild(opt);
    });
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
    
    const toast = createElem('div', { class: `toast ${type}` }, msg);
    container.appendChild(toast);
    setTimeout(() => toast.remove(), duration);
};