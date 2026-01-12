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
    // Get computed colors - resolve actual color values
    const styles = getComputedStyle(document.documentElement);
    let fillColor = styles.getPropertyValue('--trim').trim();
    
    // If trim is transparent/empty/unset, use accent-blue
    if (!fillColor || fillColor === 'transparent' || fillColor.startsWith('var(')) {
        fillColor = styles.getPropertyValue('--accent-blue').trim() || '#4a9eff';
    }
    
    // Resolve bg-tertiary to actual color
    let bgColor = styles.getPropertyValue('--bg-tertiary').trim() || '#2a2a2a';
    
    volumeSlider.style.background = `linear-gradient(to right, ${fillColor} 0%, ${fillColor} ${val}%, ${bgColor} ${val}%, ${bgColor} 100%)`;
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