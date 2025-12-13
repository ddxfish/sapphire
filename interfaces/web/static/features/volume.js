// features/volume.js - Volume slider and mute controls
import * as audio from '../audio.js';
import { getElements } from '../core/state.js';

export function initVolumeControls() {
    const { volumeSlider, muteBtn } = getElements();
    
    const savedVolume = localStorage.getItem('sapphire-volume');
    const savedMuted = localStorage.getItem('sapphire-muted');
    
    if (savedVolume !== null) {
        const vol = parseInt(savedVolume, 10);
        volumeSlider.value = vol;
        audio.setVolume(vol / 100);
    }
    updateSliderFill();

    if (savedMuted === 'true') {
        audio.setMuted(true);
        muteBtn.textContent = '🔇';
        muteBtn.classList.add('muted');
    }
}

function updateSliderFill() {
    const { volumeSlider } = getElements();
    const val = parseInt(volumeSlider.value, 10);
    volumeSlider.style.background = `linear-gradient(to right, var(--accent-blue) 0%, var(--accent-blue) ${val}%, var(--bg-tertiary) ${val}%, var(--bg-tertiary) 100%)`;
}

export function handleVolumeChange() {
    const { volumeSlider, muteBtn } = getElements();
    const val = parseInt(volumeSlider.value, 10);
    
    audio.setVolume(val / 100);
    localStorage.setItem('sapphire-volume', val);
    updateSliderFill();
    
    // Auto-unmute when adjusting volume
    if (audio.isMuted() && val > 0) {
        audio.setMuted(false);
        muteBtn.textContent = '🔊';
        muteBtn.classList.remove('muted');
        localStorage.setItem('sapphire-muted', 'false');
    }
    
    // Update icon based on level
    if (!audio.isMuted()) {
        if (val === 0) muteBtn.textContent = '🔇';
        else if (val < 50) muteBtn.textContent = '🔉';
        else muteBtn.textContent = '🔊';
    }
}

export function handleMuteToggle() {
    const { volumeSlider, muteBtn } = getElements();
    const nowMuted = !audio.isMuted();
    
    audio.setMuted(nowMuted);
    localStorage.setItem('sapphire-muted', nowMuted);
    
    if (nowMuted) {
        muteBtn.textContent = '🔇';
        muteBtn.classList.add('muted');
    } else {
        muteBtn.classList.remove('muted');
        const val = parseInt(volumeSlider.value, 10);
        if (val === 0) muteBtn.textContent = '🔇';
        else if (val < 50) muteBtn.textContent = '🔉';
        else muteBtn.textContent = '🔊';
    }
}