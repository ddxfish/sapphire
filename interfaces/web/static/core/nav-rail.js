// core/nav-rail.js - Navigation rail
import { switchView } from './router.js';

const NAV_ITEMS = [
    { id: 'chat', icon: '\uD83D\uDCAC', label: 'Chat' },
    { id: 'personas', icon: '\uD83D\uDC64', label: 'Personas' },
    { id: 'toolsets', icon: '\uD83D\uDD27', label: 'Toolsets' },
    { id: 'spices', icon: '\uD83C\uDF36\uFE0F', label: 'Spices' },
    { id: 'schedule', icon: '\u23F0', label: 'Schedule' },
    { id: 'settings', icon: '\u2699\uFE0F', label: 'Settings' }
];

const MOBILE_MAX_VISIBLE = 5;

export function initNavRail() {
    const rail = document.getElementById('nav-rail');
    if (!rail) return;

    rail.addEventListener('click', e => {
        const item = e.target.closest('.nav-item');
        if (!item) return;
        const viewId = item.dataset.view;
        if (viewId) switchView(viewId);
    });

    initMobileOverflow(rail);
}

// Update the chat name shown in header
export function setChatHeaderName(name) {
    const el = document.getElementById('chat-header-name');
    if (el) el.textContent = name || 'Chat';
}

function initMobileOverflow(rail) {
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

        const overflow = rail.querySelector('.nav-overflow');
        if (overflow) {
            overflow.style.display = items.length > MOBILE_MAX_VISIBLE ? '' : 'none';
        }
    };

    window.addEventListener('resize', check);
    check();

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
