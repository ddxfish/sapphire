// core/nav-rail.js - Navigation rail with flyout menus
import { switchView } from './router.js';

const NAV_ITEMS = [
    { id: 'chat', icon: '\uD83D\uDCAC', label: 'Chat', hasFlyout: true },
    { id: 'personas', icon: '\uD83D\uDC64', label: 'Personas' },
    { id: 'toolsets', icon: '\uD83D\uDD27', label: 'Toolsets' },
    { id: 'spices', icon: '\uD83C\uDF36\uFE0F', label: 'Spices' },
    { id: 'schedule', icon: '\u23F0', label: 'Schedule' },
    { id: 'settings', icon: '\u2699\uFE0F', label: 'Settings' }
];

const MOBILE_MAX_VISIBLE = 5;
let flyoutCloseTimer = null;
let activeFlyout = null;

export function initNavRail() {
    const rail = document.getElementById('nav-rail');
    if (!rail) return;

    // Click handler for nav items
    rail.addEventListener('click', e => {
        const item = e.target.closest('.nav-item');
        if (!item) return;

        const viewId = item.dataset.view;
        if (!viewId) return;

        // On mobile, tapping active chat item toggles flyout
        if (isMobile() && viewId === 'chat' && item.classList.contains('active')) {
            toggleFlyout(item);
            return;
        }

        closeFlyout();
        switchView(viewId);
    });

    // Desktop flyout: mouseenter/mouseleave on chat item
    const chatItem = rail.querySelector('[data-view="chat"]');
    if (chatItem) {
        chatItem.addEventListener('mouseenter', () => {
            if (isMobile()) return;
            clearTimeout(flyoutCloseTimer);
            openFlyout(chatItem);
        });
        chatItem.addEventListener('mouseleave', () => {
            if (isMobile()) return;
            flyoutCloseTimer = setTimeout(closeFlyout, 200);
        });
    }

    // Flyout panel hover keeps it open
    const flyout = document.getElementById('nav-flyout');
    if (flyout) {
        flyout.addEventListener('mouseenter', () => clearTimeout(flyoutCloseTimer));
        flyout.addEventListener('mouseleave', () => {
            if (isMobile()) return;
            flyoutCloseTimer = setTimeout(closeFlyout, 200);
        });
    }

    // Close flyout on outside click (mobile)
    document.addEventListener('click', e => {
        if (!activeFlyout) return;
        if (e.target.closest('#nav-flyout') || e.target.closest('.nav-item')) return;
        closeFlyout();
    });

    // Mobile overflow menu
    initMobileOverflow(rail);
}

function openFlyout(navItem) {
    const flyout = document.getElementById('nav-flyout');
    if (!flyout) return;

    const viewId = navItem.dataset.view;
    activeFlyout = viewId;

    // Position flyout next to item
    if (!isMobile()) {
        const rect = navItem.getBoundingClientRect();
        flyout.style.top = rect.top + 'px';
        flyout.style.left = '64px';
        flyout.style.bottom = 'auto';
    } else {
        // Mobile: bottom sheet style
        flyout.style.top = 'auto';
        flyout.style.left = '0';
        flyout.style.right = '0';
        flyout.style.bottom = '56px';
    }

    flyout.innerHTML = buildFlyoutContent(viewId);
    flyout.classList.remove('hidden');

    // Bind flyout item clicks
    flyout.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', () => handleFlyoutAction(btn.dataset.action, btn.dataset));
    });
}

function closeFlyout() {
    const flyout = document.getElementById('nav-flyout');
    if (flyout) {
        flyout.classList.add('hidden');
        flyout.innerHTML = '';
    }
    activeFlyout = null;
}

function toggleFlyout(navItem) {
    if (activeFlyout === navItem.dataset.view) {
        closeFlyout();
    } else {
        openFlyout(navItem);
    }
}

function buildFlyoutContent(viewId) {
    if (viewId === 'chat') return buildChatFlyout();
    return '';
}

function buildChatFlyout() {
    // Chat list is populated dynamically via updateChatFlyout()
    return `
        <button class="flyout-item flyout-new-chat" data-action="new-chat">
            <span class="flyout-icon">+</span> New Chat
        </button>
        <div class="flyout-separator"></div>
        <div class="flyout-chat-list" id="flyout-chat-list">
            <div class="flyout-item flyout-loading">Loading...</div>
        </div>
    `;
}

// Call this to populate chat list in flyout
export function updateChatFlyout(chats, activeChatName) {
    const list = document.getElementById('flyout-chat-list');
    if (!list) return;

    const maxVisible = 10;
    const visible = chats.slice(0, maxVisible);

    list.innerHTML = visible.map(name => `
        <button class="flyout-item flyout-chat ${name === activeChatName ? 'active' : ''}"
                data-action="switch-chat" data-chat="${name}">
            ${name === activeChatName ? '<span class="flyout-check">\u2713</span>' : '<span class="flyout-check"></span>'}
            <span class="flyout-chat-name">${escapeHtml(name)}</span>
        </button>
    `).join('');

    if (chats.length > maxVisible) {
        list.innerHTML += `<div class="flyout-item flyout-more">${chats.length - maxVisible} more...</div>`;
    }

    // Rebind clicks
    list.querySelectorAll('[data-action]').forEach(btn => {
        btn.addEventListener('click', () => handleFlyoutAction(btn.dataset.action, btn.dataset));
    });
}

function handleFlyoutAction(action, dataset) {
    closeFlyout();

    if (action === 'new-chat') {
        // Dispatch custom event for chat-manager to handle
        document.dispatchEvent(new CustomEvent('nav:new-chat'));
    } else if (action === 'switch-chat') {
        document.dispatchEvent(new CustomEvent('nav:switch-chat', { detail: { chat: dataset.chat } }));
    }
}

// Update the chat name shown in header
export function setChatHeaderName(name) {
    const el = document.getElementById('chat-header-name');
    if (el) el.textContent = name || 'Chat';
}

function initMobileOverflow(rail) {
    // On resize, check if we need overflow
    const check = () => {
        if (!isMobile()) {
            rail.querySelectorAll('.nav-item').forEach(i => i.classList.remove('overflow-hidden'));
            const overflow = rail.querySelector('.nav-overflow');
            if (overflow) overflow.style.display = 'none';
            return;
        }

        const items = rail.querySelectorAll('.nav-item:not(.nav-overflow)');
        items.forEach((item, i) => {
            item.classList.toggle('overflow-hidden', i >= MOBILE_MAX_VISIBLE);
        });

        // Show overflow button if needed
        const overflow = rail.querySelector('.nav-overflow');
        if (overflow) {
            overflow.style.display = items.length > MOBILE_MAX_VISIBLE ? '' : 'none';
        }
    };

    window.addEventListener('resize', check);
    check();

    // Overflow button click
    const overflow = rail.querySelector('.nav-overflow');
    if (overflow) {
        overflow.addEventListener('click', () => {
            const menu = rail.querySelector('.nav-overflow-menu');
            if (menu) menu.classList.toggle('hidden');
        });
    }
}

function isMobile() {
    return window.innerWidth <= 768;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
