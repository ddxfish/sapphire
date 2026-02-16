// features/user-profile.js - User profile modal (nav-rail avatar button)
import { getInitData, refreshInitData } from '../shared/init-data.js';
import { uploadAvatar, checkAvatar } from '../shared/settings-api.js';
import { avatarUrl } from '../shared/persona-api.js';
import * as ui from '../ui.js';

let modal, avatarImg, usernameInput, personaSelect, personaAvatar, profileBtn, profileInitial;
let currentUsername = '';
let saveTimer = null;

export function initUserProfile() {
    modal = document.getElementById('user-profile-modal');
    profileBtn = document.getElementById('nav-profile-btn');
    profileInitial = document.getElementById('nav-profile-initial');
    if (!modal || !profileBtn) return;

    avatarImg = modal.querySelector('#up-avatar');
    usernameInput = modal.querySelector('#up-username');
    personaSelect = modal.querySelector('#up-persona-select');
    personaAvatar = modal.querySelector('#up-persona-avatar');

    profileBtn.addEventListener('click', openModal);
    modal.querySelector('#up-close').addEventListener('click', closeModal);
    modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });

    // Avatar upload
    const avatarBtn = modal.querySelector('#up-avatar-btn');
    const avatarFile = modal.querySelector('#up-avatar-file');
    avatarBtn.addEventListener('click', () => avatarFile.click());
    avatarFile.addEventListener('change', handleAvatarUpload);

    // Username save on blur/enter
    usernameInput.addEventListener('blur', saveUsername);
    usernameInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); saveUsername(); }
    });

    // Persona select
    personaSelect.addEventListener('change', saveDefaultPersona);

    // Set initial state from cached init data
    loadInitial();
}

async function loadInitial() {
    try {
        const data = await getInitData();
        currentUsername = data.settings?.DEFAULT_USERNAME || 'Human Protagonist';
        updateInitialBadge(currentUsername, data.avatars?.user);
    } catch {}
}

function updateInitialBadge(name, avatarPath) {
    if (!profileBtn) return;
    if (avatarPath) {
        profileInitial.style.display = 'none';
        let img = profileBtn.querySelector('img');
        if (!img) {
            img = document.createElement('img');
            img.onerror = () => { img.remove(); profileInitial.style.display = ''; };
            profileBtn.appendChild(img);
        }
        img.src = `${avatarPath}?t=${Date.now()}`;
    } else {
        const initial = (name || '?').charAt(0).toUpperCase();
        profileInitial.textContent = initial;
        profileInitial.style.display = '';
        const img = profileBtn.querySelector('img');
        if (img) img.remove();
    }
}

async function openModal() {
    try {
        const data = await getInitData();
        currentUsername = data.settings?.DEFAULT_USERNAME || 'Human Protagonist';
        usernameInput.value = currentUsername;

        // User avatar
        const avatarCheck = await checkAvatar('user').catch(() => ({ exists: false }));
        if (avatarCheck.exists) {
            avatarImg.src = `${avatarCheck.path}?t=${Date.now()}`;
            avatarImg.style.visibility = '';
        } else {
            avatarImg.src = '/static/users/user.webp';
        }

        // Persona list
        const personas = data.personas?.list || [];
        const defaultP = data.personas?.default || '';
        personaSelect.innerHTML = '<option value="">(None)</option>' +
            personas.map(p => `<option value="${p.name}" ${p.name === defaultP ? 'selected' : ''}>${p.name}</option>`).join('');

        // Persona avatar
        updatePersonaAvatar(defaultP);

        modal.style.display = '';
    } catch (e) {
        console.warn('[Profile] Failed to open:', e);
        ui.showToast('Failed to load profile', 'error');
    }
}

function closeModal() {
    modal.style.display = 'none';
}

function updatePersonaAvatar(name) {
    if (name) {
        personaAvatar.src = avatarUrl(name);
        personaAvatar.style.visibility = '';
    } else {
        personaAvatar.src = '/static/users/assistant.webp';
    }
}

async function saveUsername() {
    const name = usernameInput.value.trim();
    if (!name || name === currentUsername) return;
    try {
        const res = await fetch(`/api/settings/DEFAULT_USERNAME`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: name, persist: true })
        });
        if (res.ok) {
            currentUsername = name;
            updateInitialBadge(name, null);
            // Check if avatar exists to preserve it in badge
            const avatarCheck = await checkAvatar('user').catch(() => ({ exists: false }));
            updateInitialBadge(name, avatarCheck.exists ? avatarCheck.path : null);
            refreshInitData();
        }
    } catch (e) {
        console.warn('[Profile] Save username failed:', e);
    }
}

async function handleAvatarUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    const btn = modal.querySelector('#up-avatar-btn');
    btn.disabled = true;
    btn.textContent = 'Uploading...';
    try {
        const result = await uploadAvatar('user', file);
        if (result.path) {
            const ts = Date.now();
            avatarImg.src = `${result.path}?t=${ts}`;
            avatarImg.style.visibility = '';
            updateInitialBadge(currentUsername, result.path);
        }
        ui.showToast('Avatar updated', 'success');
        refreshInitData();
    } catch (err) {
        ui.showToast(`Upload failed: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Change Avatar';
        e.target.value = '';
    }
}

async function saveDefaultPersona() {
    const name = personaSelect.value;
    updatePersonaAvatar(name);
    try {
        if (name) {
            await fetch('/api/personas/default', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
        } else {
            await fetch('/api/personas/default', { method: 'DELETE' });
        }
        refreshInitData();
    } catch (e) {
        console.warn('[Profile] Save default persona failed:', e);
    }
}
