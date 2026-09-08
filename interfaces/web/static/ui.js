// ui.js - UI Coordinator (main interface)

import * as Images from './ui-images.js';
import * as Parsing from './ui-parsing.js';
import * as Streaming from './ui-streaming.js';
import * as api from './api.js';
import { dismissLater } from './shared/toast.js';

// DOM references. The chat surface is JS-rendered at boot (surface/surface.js)
// so #chat-container / #chatbg-overlay don't exist at module-eval time —
// main.js calls bindChatDom() right after renderSurface(), before any paint.
// The <template> elements live in static HTML and are safe to grab here.
let chat = document.getElementById('chat-container');
let chatbgOverlay = document.getElementById('chatbg-overlay');
const msgTpl = document.getElementById('message-template');
const statusTpl = document.getElementById('status-template');

export function bindChatDom() {
    chat = document.getElementById('chat-container');
    chatbgOverlay = document.getElementById('chatbg-overlay');
    bindScrollIntent();
}

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
// SCROLL MANAGEMENT — intent-based sticky bottom (2026-09-08)
//
// The old rule was pure position: every streamed chunk snapped to the bottom
// if you were within 100px of it. Chunks land every few ms, so scrolling up
// meant out-running the snap — the "scroll of god" on phones and laptops.
// Stickiness is a STATE now. It turns OFF the moment the user scrolls up by
// any means (wheel, touch, scrollbar, keys) and back ON when they return to
// the bottom, tap the ↓ pill, send a message, or a chat loads. Content growth
// never flips it: our own snaps only ever move DOWN, so a scroll event that
// moved UP while the content didn't shrink can only be the user.
// =============================================================================

const RESTICK_PX = 40;       // returning this close to the bottom re-engages
let sticky = true;
let _prevTop = 0, _prevHeight = 0;
let jumpBtn = null;

const distanceFromBottom = () => chatbgOverlay
    ? chatbgOverlay.scrollHeight - chatbgOverlay.clientHeight - chatbgOverlay.scrollTop
    : 0;

const setSticky = (on) => {
    sticky = on;
    jumpBtn?.classList.toggle('show', !on);
};

// force = the user asked for the bottom (send, chat load, regen start):
// re-engage and go. Otherwise content grew — follow it only while stuck.
const scrollToBottomIfSticky = (force = false) => {
    if (!chatbgOverlay) return;
    if (force) setSticky(true);
    if (!sticky) return;
    chatbgOverlay.scrollTop = chatbgOverlay.scrollHeight;
    _prevTop = chatbgOverlay.scrollTop;
    _prevHeight = chatbgOverlay.scrollHeight;
};

export const forceScrollToBottom = () => scrollToBottomIfSticky(true);
export const isStickyToBottom = () => sticky;

function bindScrollIntent() {
    const el = chatbgOverlay;
    if (!el || el.dataset.scrollIntent) return;   // organs.js may hand us the same node again
    el.dataset.scrollIntent = '1';
    jumpBtn = document.getElementById('scroll-jump');
    jumpBtn?.addEventListener('click', () => scrollToBottomIfSticky(true));
    const unstick = () => { if (sticky) setSticky(false); };
    // Explicit intent — these only ever come from a human, and they fire
    // before the first pixel moves (the touch one is what makes phones work).
    el.addEventListener('wheel', e => { if (e.deltaY < 0) unstick(); }, { passive: true });
    let touchY = null;
    el.addEventListener('touchstart', e => { touchY = e.touches[0]?.clientY ?? null; }, { passive: true });
    el.addEventListener('touchmove', e => {
        const y = e.touches[0]?.clientY;
        if (touchY != null && y != null && y > touchY + 4) unstick();   // finger down = content up
        if (y != null) touchY = y;
    }, { passive: true });
    // Everything else (scrollbar drag, keyboard) + the re-engage on return.
    _prevTop = el.scrollTop; _prevHeight = el.scrollHeight;
    el.addEventListener('scroll', () => {
        const top = el.scrollTop, h = el.scrollHeight;
        if (top < _prevTop - 1 && h >= _prevHeight) unstick();
        else if (!sticky && distanceFromBottom() <= RESTICK_PX) setSticky(true);
        _prevTop = top; _prevHeight = h;
    }, { passive: true });
}

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

// Cache: persona name → resolved avatar URL (or null if no custom avatar)
const _personaAvatarCache = new Map();

const setPersonaAvatar = (img, personaName) => {
    if (!avatarsInChat) {
        img.style.display = 'none';
        return;
    }
    img.loading = 'lazy';

    // Check cache first — avoids repeated 404s for personas without avatars
    if (_personaAvatarCache.has(personaName)) {
        const cached = _personaAvatarCache.get(personaName);
        if (cached) {
            img.src = cached;
            img.onerror = () => { img.style.display = 'none'; };
        } else {
            // Cached as no custom avatar — use default
            loadAvatarPaths().then(paths => {
                if (paths.assistant) {
                    img.src = paths.assistant;
                    img.onerror = () => { img.style.display = 'none'; };
                } else {
                    img.style.display = 'none';
                }
            });
        }
        return;
    }

    const url = `/api/personas/${encodeURIComponent(personaName)}/avatar`;
    img.src = url;
    img.onload = () => { _personaAvatarCache.set(personaName, url); };
    img.onerror = async () => {
        _personaAvatarCache.set(personaName, null);
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

    // Degraded-task signal: when a heartbeat / scheduled task ends without
    // a real reply (context overflow, tool exhaustion, empty LLM), the
    // backend keeps `content` empty so the text never reaches TTS / Discord
    // / Telegram (Apr-24 incident), but it also tags `metadata.degraded_reason`
    // so we can show the user WHY in chat. Italic muted note — no audio path.
    if (role === 'assistant' && !msg.content && msg.metadata?.degraded_reason) {
        const note = createElem(
            'div',
            { class: 'message-degraded' },
            `⚠️ ${msg.metadata.degraded_reason}`
        );
        contentDiv.appendChild(note);
    }

    // Add metadata footer for assistant messages — compact line + click-to-
    // expand detail accordion (metrics v2, 2026-08-08).
    if (role === 'assistant' && msg.metadata) {
        const meta = msg.metadata;
        const parts = [];
        const tok = meta.tokens || {};
        const cumTok = meta.cumulative_tokens || null;
        const fmt = n => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : n;

        // Timestamp — the message's own DB time (every message has one, so
        // this works retroactively on pre-metrics history). Time-only today,
        // "Aug 7 14:32" otherwise.
        const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const ts = msg.timestamp ? new Date(msg.timestamp) : null;
        if (ts && !isNaN(ts)) {
            const hm = ts.toTimeString().slice(0, 5);
            parts.push(ts.toDateString() === new Date().toDateString()
                ? hm : `${MONTHS[ts.getMonth()]} ${ts.getDate()} ${hm}`);
        }

        if (meta.duration_seconds) {
            parts.push(`${meta.duration_seconds}s`);
        }
        if (meta.tokens_per_second) {
            const label = tok.estimated ? 'tok/s est' : 'tok/s';
            parts.push(`${meta.tokens_per_second} ${label}`);
        }
        if (meta.model) {
            // Basename only — fireworks models are full paths
            // (accounts/fireworks/models/x). Full string lives in the detail.
            parts.push(String(meta.model).split('/').pop());
        }

        // "in" = TOTAL input: uncached prompt + cache reads. The raw provider
        // `prompt` field is NON-cached input only (both providers normalize
        // to that convention) — displaying it raw made "in" collapse on cache
        // hits and balloon after edits invalidated the prefix (2026-08-08).
        // "out" = cumulative completion across the tool loop when >1 call.
        const prompt = tok.prompt || 0;
        const cacheRead = tok.cache_read_tokens || 0;
        const cacheWrite = tok.cache_write_tokens || 0;
        const totalIn = prompt + cacheRead;
        const out = (cumTok && cumTok.iterations > 1) ? (cumTok.completion || 0) : (tok.content || 0);
        if (totalIn || out) {
            parts.push(`${fmt(totalIn)} in / ${fmt(out)} out`);
        }
        if (cacheRead > 0) {
            const pct = totalIn > 0 ? Math.round((cacheRead / totalIn) * 100) : 0;
            parts.push(`cache ${pct}%`);
        } else if (cacheWrite > 0) {
            parts.push('cache miss');
        }
        if (cumTok && cumTok.iterations > 1) {
            parts.push(`${cumTok.iterations} calls`);
        }

        if (parts.length > 0) {
            const metaDiv = createElem('div', { class: 'message-metadata' });
            const line = createElem('div', { class: 'message-metadata-line' });
            line.appendChild(createElem('span', { class: 'mm-chev' }, '▸'));
            line.appendChild(document.createTextNode(parts.join(' · ')));
            metaDiv.appendChild(line);

            // Detail accordion — raw numbers, full identifiers. Thinking is
            // always a chars/4 approximation (providers fold it into
            // completion), hence the tilde.
            const rows = [];
            rows.push(`${meta.provider || '?'} · ${meta.model || '?'}`);
            if (meta.start_time && meta.end_time) {
                rows.push(`${String(meta.start_time).slice(0, 10)} ${String(meta.start_time).slice(11, 19)} → ${String(meta.end_time).slice(11, 19)}`);
            }
            rows.push(`in: ${totalIn} total (${prompt} prompt + ${cacheRead} cached)`
                + (cacheWrite ? ` · ${cacheWrite} cache-written` : ''));
            rows.push(`out: ${tok.content || 0} content`
                + (tok.thinking ? ` · ~${tok.thinking} thinking` : ''));
            if (cumTok && cumTok.iterations > 1) {
                rows.push(`${cumTok.iterations} calls · Σ ${fmt(cumTok.prompt + (cumTok.cache_read || 0))} in / ${fmt(cumTok.completion || 0)} out / ${fmt(cumTok.total || 0)} total`);
            }
            if (tok.estimated) {
                rows.push('counts estimated — provider sent no usage');
            }
            const detail = createElem('div', { class: 'message-metadata-detail' });
            rows.forEach(r => detail.appendChild(createElem('div', {}, r)));
            metaDiv.appendChild(detail);

            line.addEventListener('click', () => metaDiv.classList.toggle('open'));
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
        // Strip avatar tags from history if setting is enabled
        if (window._avatarStripTags) {
            if (msg.content) msg.content = msg.content.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
            if (msg.parts) msg.parts = msg.parts.map(p => p.type === 'content' && p.text
                ? { ...p, text: p.text.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '') } : p);
        }
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
        const clone = statusTpl.content.cloneNode(true);
        const avatar = clone.querySelector('.msg-avatar');
        if (avatar && currentPersona) {
            setPersonaAvatar(avatar, currentPersona);
        }
        chat.appendChild(clone);
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

// Stream-id passthrough — send-handlers uses it to drop stale chunks after
// a Stop→immediate-Send. Bumped inside Streaming on every start/cancel.
export const getCurrentStreamId = () => Streaming.getCurrentStreamId();

export const startTool = (toolId, toolName, args) => {
    Streaming.startTool(toolId, toolName, args, scrollToBottomIfSticky);
};

// Map of tool name → scope keys the tool writes into. Multiple scopes possible
// (e.g. save_person affects both knowledge and people dropdowns). When a tool
// completes, every affected scope's sidebar dropdown gets its counts refreshed.
// The endpoint for each refresh comes from /api/init scope_declarations, so
// adding a new plugin scope is zero-touch on the refresh side — only this map
// needs to know which tools affect which scopes.
const TOOL_SCOPE_MAP = {
    'create_goal':    ['goal'],
    'update_goal':    ['goal'],
    'delete_goal':    ['goal'],
    'save_memory':    ['memory'],
    'delete_memory':  ['memory'],
    'save_knowledge': ['knowledge'],
    'save_person':    ['knowledge', 'people'],
};

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

// Dynamic refresh dispatcher — looks up scope endpoint from /api/init declarations
// cached by shared/init-data.js (populated on first loadSidebar).
const refreshScopesForTool = (toolName) => {
    const scopeKeys = TOOL_SCOPE_MAP[toolName];
    if (!scopeKeys) return;
    // Deferred import to avoid circular dependency at module load time
    import('./shared/init-data.js').then(({ getInitDataSync }) => {
        const declarations = getInitDataSync()?.scope_declarations || [];
        for (const key of scopeKeys) {
            const decl = declarations.find(d => d.key === key);
            if (decl) refreshScopeCounts(`#sb-${key}-scope`, decl.endpoint);
        }
    }).catch(() => { /* silent */ });
};

export const endTool = (toolId, toolName, result, isError) => {
    Streaming.endTool(toolId, toolName, result, isError, scrollToBottomIfSticky);
    if (!isError) refreshScopesForTool(toolName);
};

export const finishStreaming = async (ephemeral = false) => {
    const streamingMsg = document.getElementById('streaming-message');

    Streaming.finishStreaming(updateToolbars);
    
    // Ephemeral: just remove the message, no swap with history
    if (ephemeral) {
        if (streamingMsg) {
            streamingMsg.remove();
        }
        scrollToBottomIfSticky();   // end of turn follows the reader, never yanks
        return;
    }
    
    if (streamingMsg) {
        const chatAtFinish = document.getElementById('chat-select')?.value;
        await new Promise(resolve => setTimeout(resolve, 500));

        // Bail if chat switched during the delay
        const chatNow = document.getElementById('chat-select')?.value;
        if (chatNow !== chatAtFinish || !document.contains(streamingMsg)) {
            console.log('[SWAP] Chat switched during delay, skipping history swap');
        } else {
            try {
                const hist = await api.fetchHistory();
                if (hist && hist.length > 0) {
                    const lastMsg = hist[hist.length - 1];
                    // Strip avatar tags from history if setting is enabled
                    if (window._avatarStripTags && lastMsg.content) {
                        lastMsg.content = lastMsg.content.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
                    }
                    if (window._avatarStripTags && lastMsg.parts) {
                        lastMsg.parts = lastMsg.parts.map(p => p.type === 'content' && p.text
                            ? { ...p, text: p.text.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '') }
                            : p);
                    }
                    const { clone } = createMessage(lastMsg, hist.length - 1, hist.length, true);

                    streamingMsg.replaceWith(clone);
                }
            } catch (e) {
                console.error('[SWAP] Failed:', e);
            }
        }
    }
    
    scrollToBottomIfSticky();   // a reader who scrolled up mid-reply stays put

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

export const renderChatDropdown = (chats, activeChat, _legacyStoryChats = [], privateChats = [], { adopt = true } = {}) => {
    // Combine all chats for the hidden select (needs all chats for switching)
    const allChats = [...chats, ...privateChats];

    // Mode-tagged chats (game/story sessions — plugin surfaces own them) stay
    // out of the VISIBLE picker entirely — no active-chat exception (Krem's
    // isolation ruling 2026-08-03: games/stories are totally separate from
    // Chats; chat-manage stays the one admin surface that lists everything).
    // The hidden select above keeps all chats — it's switching infrastructure.
    const _visible = c => {
        const m = c.mode ?? c.settings?.mode;
        return !m || m === 'chat';
    };
    chats = chats.filter(_visible);
    privateChats = privateChats.filter(_visible);

    // Update hidden select (state holder used throughout the app).
    // `adopt=false` means this render rides a chat-list response that is
    // stale truth (a switch started while it was in flight) — rebuild the
    // option list but keep the user's selection.
    const select = document.getElementById('chat-select');
    const prev = select?.value || '';
    if (select) {
        select.innerHTML = '';
        allChats.forEach(chat => {
            const opt = document.createElement('option');
            opt.value = chat.name;
            opt.textContent = chat.display_name;
            if (adopt ? chat.name === activeChat : chat.name === prev) opt.selected = true;
            select.appendChild(opt);
        });
        // INVARIANT (vaulted-chats Phase 0, 2026-08-13): the select always
        // contains its intended selection, under EITHER adopt mode. The
        // innerHTML wipe is itself a writer — with no selected option a
        // single-line select snaps to index 0, loadSidebar follows the new
        // value, and the sidebar paints (then debounce-SAVES onto) a chat the
        // transcript isn't on. The synthesizer used to cover only !adopt
        // (stale-response rollback); a chat legitimately missing from the
        // list (fresh create mid-flight, archived race, vault lock) hit the
        // index-0 snap with the sidebar following it.
        const intended = adopt ? activeChat : prev;
        if (intended && ![...select.options].some(o => o.value === intended)) {
            const known = allChats.find(c => c.name === intended);
            const opt = document.createElement('option');
            opt.value = intended;
            opt.textContent = known?.display_name || intended;
            opt.selected = true;
            select.appendChild(opt);
        }
    }

    // All display surfaces below (picker checks, cap survival, header/sidebar
    // names) key off the EFFECTIVE selection, never raw activeChat — under a
    // suppressed adoption they must agree with the select, not the stale list.
    const effectiveActive = (adopt || !prev) ? activeChat : prev;

    // Build picker items — regular chats, then private
    // Cap the visible picker at 10 — the list arrives updated_at DESC, so
    // the cap keeps the freshest. The active chat always survives the cut.
    // Everything else lives one click away in Chat Manager (the "See more"
    // item below). The hidden select above stays uncapped on purpose.
    const MAX_PICKER = 10;
    // Reserve up to 3 picker slots for the private section — with 10+
    // regular chats the old math sliced private to ZERO, leaving 🗝 chats
    // reachable only via active-rescue (dropdown hunt #4, 2026-08-13).
    const privReserve = Math.min(privateChats.length, 3);
    let regShow = chats.slice(0, MAX_PICKER - privReserve);
    let privShow = privateChats.slice(0, Math.max(0, MAX_PICKER - regShow.length));
    if (effectiveActive && ![...regShow, ...privShow].some(c => c.name === effectiveActive)) {
        const a = chats.find(c => c.name === effectiveActive);
        const p = a ? null : privateChats.find(c => c.name === effectiveActive);
        if (a) regShow = [...regShow.slice(0, MAX_PICKER - 1), a];
        else if (p) privShow = [...privShow, p].slice(-Math.max(1, MAX_PICKER - regShow.length));
    }
    const hiddenCount = (chats.length + privateChats.length) - (regShow.length + privShow.length);

    let itemsHtml = regShow.map(c => `
        <button class="chat-picker-item ${c.name === effectiveActive ? 'active' : ''}"
                data-chat="${escapeAttr(c.name)}">
            <span class="chat-picker-item-check">${c.name === effectiveActive ? '\u2713' : ''}</span>
            <span class="chat-picker-item-name">${escapeHtml(c.display_name)}</span>
        </button>
    `).join('');

    if (privShow.length > 0) {
        itemsHtml += '<div class="chat-picker-divider"></div>';
        // \ud83d\udddd = the vault glyph (matches vault-backed prompts in the sidebar);
        // \ud83d\udd12 is taken \u2014 it means privacy_required on prompts.
        itemsHtml += privShow.map(c => `
            <button class="chat-picker-item chat-picker-private ${c.name === effectiveActive ? 'active' : ''}"
                    data-chat="${escapeAttr(c.name)}">
                <span class="chat-picker-item-check">${c.name === effectiveActive ? '\u2713' : ''}</span>
                <span class="chat-picker-item-name">\u{1F5DD} ${escapeHtml(c.display_name)}</span>
            </button>
        `).join('');
    }

    if (hiddenCount > 0) {
        // No data-chat: the chat-switch path ignores it; chat.js routes it
        // to Chat Manager. Empty check span keeps the active-update loop safe.
        itemsHtml += `<div class="chat-picker-divider"></div>
            <button class="chat-picker-item chat-picker-more">
                <span class="chat-picker-item-check"></span>
                <span class="chat-picker-item-name">See more \u2014 ${hiddenCount} older chat${hiddenCount === 1 ? '' : 's'}\u2026</span>
            </button>`;
    }

    // Update sidebar chat picker dropdown
    const sbDropdown = document.getElementById('sb-chat-picker-dropdown');
    if (sbDropdown) sbDropdown.innerHTML = itemsHtml;

    // Update header names (check all chats)
    const active = allChats.find(c => c.name === effectiveActive);
    const displayName = active?.display_name || effectiveActive || 'Chat';

    const headerName = document.getElementById('chat-header-name');
    if (headerName) headerName.textContent = displayName;

    const sbName = document.getElementById('sb-chat-name');
    if (sbName) sbName.textContent = displayName;

    // Notify sidebar to reload — ONLY when the selection actually moved.
    // Every render used to fire this, dragging a full sidebar reload behind
    // any list refresh (archive, create, privacy toggle, reconnect resync).
    if (select && select.value !== prev) select.dispatchEvent(new Event('chat-list-ready'));
};

const escapeHtml = (str) => {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
};

// Attribute-context escape: escapeHtml (textContent→innerHTML) does NOT
// escape quotes, so a name in data-attr position could break out of the
// attribute. All creation paths sanitize names today — belt-and-suspenders
// (dropdown hunt #9).
const escapeAttr = (str) => String(str)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');

// =============================================================================
// TEXT EXTRACTION
// =============================================================================

export const extractProseText = (el) => {
    return Parsing.extractProseText(el);
};

// =============================================================================
// EDIT MODE
// =============================================================================

// `text` is the stored markdown source (caller pulls it from the history
// payload). The textarea used to be filled by scraping the rendered DOM back
// to text, which lost every markdown mark — `*stage directions*` came back
// bare and single newlines vanished (`<br>` has empty textContent). Set via
// .value, not innerHTML, so the message text is never parsed as markup.
export const enterEditMode = (msgEl, idx, timestamp, text) => {
    const content = msgEl.querySelector('.message-content');
    const toolbar = msgEl.querySelector('.toolbar');
    
    content.dataset.original = content.innerHTML;
    content.dataset.editTimestamp = timestamp;
    msgEl.dataset.editTimestamp = timestamp;
    
    content.innerHTML = `
        <textarea id="edit-textarea" class="edit-textarea" rows="10"></textarea>
        <div class="edit-actions">
            <button id="save-edit" class="btn btn-primary" data-index="${idx}">Save</button>
            <button id="cancel-edit" class="btn btn-secondary">Cancel</button>
        </div>
    `;
    const ta = document.getElementById('edit-textarea');
    ta.value = text;
    toolbar.style.display = 'none';
    msgEl.classList.add('editing');
    ta.focus();
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
    // Full HTML-escape (& first) + length clamp + String() coercion \u2014 toasts are
    // plain text, and PLUGIN_NOTICE lets any plugin supply the message.
    const safe = String(msg).slice(0, 500)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    toast.innerHTML = `<span class="toast-text">${safe}</span><button class="toast-close">\u00d7</button>`;
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

    // duration <= 0 = persistent (close-button only). Previously the timer
    // ran unconditionally, so duration 0 removed the toast on the next tick —
    // the privacy-mode-removal notice was never seen by anyone (scout find,
    // 2026-07-19; same bug killed the 1h env-build-cap message).
    dismissLater(toast, duration);
};