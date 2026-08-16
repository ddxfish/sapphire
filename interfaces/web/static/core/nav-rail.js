// core/nav-rail.js - Navigation rail with flyout support
import { switchView } from './router.js';

const MOBILE_MAX_VISIBLE = 6;

export function initNavRail() {
    const rail = document.getElementById('nav-rail');
    if (!rail) return;

    // Main nav click handler
    rail.addEventListener('click', e => {
        // Flyout item click
        const flyoutItem = e.target.closest('.nav-flyout-item');
        if (flyoutItem) {
            e.stopPropagation();
            const viewId = flyoutItem.dataset.view;
            if (viewId) switchView(viewId);
            // Close any open flyouts
            rail.querySelectorAll('.nav-group-parent').forEach(p => p.classList.remove('flyout-open'));
            return;
        }

        const item = e.target.closest('.nav-item');
        if (!item) return;

        // Group parent: on mobile, first tap opens flyout; second tap (or desktop click) navigates
        if (item.classList.contains('nav-group-parent')) {
            if (isMobile() && !item.classList.contains('flyout-open')) {
                e.stopPropagation();
                // Close other flyouts
                rail.querySelectorAll('.nav-group-parent').forEach(p => p.classList.remove('flyout-open'));
                item.classList.add('flyout-open');
                // Center the flyout above the tapped item, clamped to the
                // viewport. Open first so offsetWidth is measurable; clear
                // any desktop inline top so the CSS bottom anchor applies.
                const flyout = item.querySelector('.nav-flyout');
                if (flyout) {
                    flyout.style.top = '';
                    const rect = item.getBoundingClientRect();
                    const w = flyout.offsetWidth || 150;
                    const left = Math.max(8, Math.min(
                        rect.left + rect.width / 2 - w / 2,
                        window.innerWidth - w - 8));
                    flyout.style.left = left + 'px';
                }
                return;
            }
        }

        const viewId = item.dataset.view;
        if (viewId) switchView(viewId);
    });

    // Desktop hover for flyout
    rail.querySelectorAll('.nav-group-parent').forEach(parent => {
        let hoverTimer = null;
        parent.addEventListener('mouseenter', () => {
            if (isMobile()) return;
            clearTimeout(hoverTimer);
            // Position the flyout vertically to match the parent button;
            // clear any mobile inline left so the CSS left:68px applies.
            const flyout = parent.querySelector('.nav-flyout');
            if (flyout) {
                const rect = parent.getBoundingClientRect();
                flyout.style.top = rect.top + 'px';
                flyout.style.left = '';
            }
            parent.classList.add('flyout-open');
        });
        parent.addEventListener('mouseleave', () => {
            if (isMobile()) return;
            hoverTimer = setTimeout(() => parent.classList.remove('flyout-open'), 200);
        });
    });

    // Close flyouts on outside click
    document.addEventListener('click', e => {
        if (!e.target.closest('.nav-group-parent')) {
            rail.querySelectorAll('.nav-group-parent').forEach(p => p.classList.remove('flyout-open'));
        }
    });

    initMobileOverflow(rail);
}

// Update the chat name shown in header and sidebar
export function setChatHeaderName(name) {
    const display = name || 'Chat';
    const el = document.getElementById('chat-header-name');
    if (el) el.textContent = display;
    const sb = document.getElementById('sb-chat-name');
    if (sb) sb.textContent = display;
}

function initMobileOverflow(rail) {
    const menu = rail.querySelector('.nav-overflow-menu');
    const overflow = rail.querySelector('.nav-overflow');

    const check = () => {
        if (!isMobile()) {
            rail.querySelectorAll('.nav-item').forEach(i => i.classList.remove('overflow-hidden'));
            if (overflow) overflow.style.display = 'none';
            if (menu) menu.classList.add('hidden');
            return;
        }

        const items = rail.querySelectorAll('.nav-item:not(.nav-overflow)');
        items.forEach((item, i) => {
            item.classList.toggle('overflow-hidden', i >= MOBILE_MAX_VISIBLE);
        });

        if (overflow) {
            overflow.style.display = items.length > MOBILE_MAX_VISIBLE ? '' : 'none';
        }

        // Populate overflow menu with hidden items
        if (menu) {
            menu.innerHTML = '';
            items.forEach((item, i) => {
                if (i < MOBILE_MAX_VISIBLE) return;
                const viewId = item.dataset.view;
                const icon = item.querySelector('.nav-icon')?.textContent || '';
                const label = item.querySelector('.nav-label')?.textContent || viewId;
                const btn = document.createElement('button');
                btn.className = 'nav-overflow-item';
                btn.dataset.view = viewId;
                // textContent-derived but still going through innerHTML — use
                // DOM API so the round-trip stays safe even if upstream nav
                // items ever start carrying HTML. Day-ruiner #A defense-depth.
                const iconSpan = document.createElement('span');
                iconSpan.textContent = icon;
                const labelSpan = document.createElement('span');
                labelSpan.textContent = label;
                btn.appendChild(iconSpan);
                btn.appendChild(labelSpan);
                menu.appendChild(btn);
            });
        }
    };

    window.addEventListener('resize', check);
    check();

    if (overflow) {
        overflow.addEventListener('click', e => {
            e.stopPropagation();
            if (menu) menu.classList.toggle('hidden');
        });
    }

    // Overflow menu item clicks
    if (menu) {
        menu.addEventListener('click', e => {
            const item = e.target.closest('.nav-overflow-item');
            if (!item) return;
            const viewId = item.dataset.view;
            if (viewId) switchView(viewId);
            menu.classList.add('hidden');
        });
    }

    // Close on outside click
    document.addEventListener('click', e => {
        if (menu && !menu.classList.contains('hidden') && !e.target.closest('.nav-overflow') && !e.target.closest('.nav-overflow-menu')) {
            menu.classList.add('hidden');
        }
    });
}

function isMobile() {
    return window.innerWidth <= 768;
}
