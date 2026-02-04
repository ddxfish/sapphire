// handlers/send-handlers.js - Send, stop, and input handlers
import * as api from '../api.js';
import * as ui from '../ui.js';
import * as audio from '../audio.js';
import * as chat from '../chat.js';
import * as Images from '../ui-images.js';
import { isPrivacyMode } from '../features/privacy.js';
import {
    getElements,
    getIsProc,
    getTtsEnabled,
    getPromptPrivacyRequired,
    setProc,
    setAbortController,
    getAbortController,
    setIsCancelling,
    getIsCancelling,
    refresh,
    setHistLen
} from '../core/state.js';

export async function handleSend() {
    const { input, sendBtn } = getElements();
    const txt = input.value.trim();
    if (!txt && !Images.hasPendingUploadImages()) return;

    // Block send if current prompt requires privacy but privacy mode is off
    if (getPromptPrivacyRequired() && !isPrivacyMode()) {
        ui.showToast('This prompt requires Privacy Mode to be enabled', 'error');
        return;
    }

    const abortController = new AbortController();
    setAbortController(abortController);
    setIsCancelling(false);
    
    setProc(true);
    input.value = '';
    sendBtn.disabled = true;
    sendBtn.textContent = '...';
    input.dispatchEvent(new Event('input'));
    
    // Get pending images and clear them
    const pendingImages = Images.getImagesForApi();
    const hasImages = pendingImages.length > 0;
    
    ui.addUserMessage(txt, hasImages ? Images.getPendingUploadImages() : null);
    Images.clearPendingUploadImages();
    updateImagePreviewArea();
    
    ui.showStatus();
    ui.updateStatus('Connecting...');
    
    try {
        let streamOk = false;
        const audioFn = getTtsEnabled() ? audio.playText : null;
        
        await api.streamChat(
            txt,
            chunk => {
                if (!streamOk) {
                    ui.updateStatus('Generating...');
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.appendStream(chunk);
                // Hide status once actual visible content appears
                if (ui.hasVisibleContent()) {
                    ui.hideStatus();
                }
            },
            async (ephemeral) => {
                if (getIsCancelling()) {
                    console.log('Stream completed but cancellation in progress - skipping finishStreaming');
                    return;
                }
                
                if (ephemeral) {
                    console.log('[EPHEMERAL] Module response - skipping TTS and swap');
                    await ui.finishStreaming(true);
                    await refresh(false);
                    return;
                }
                
                if (streamOk) {
                    await ui.finishStreaming();
                    // Note: finishStreaming already syncs with history - no refresh needed
                    
                    setTimeout(() => {
                        if (audioFn) {
                            const el = document.querySelector('.message.assistant:last-child .message-content');
                            if (el) {
                                const prose = ui.extractProseText(el);
                                audioFn(prose);
                            }
                        }
                    }, 200);
                }
            },
            async (e, statusCode) => {
                if (e.message === 'Cancelled') {
                    console.log('Stream cancelled by user');
                    if (streamOk) ui.cancelStreaming();
                    return;
                }
                console.error('Stream failed:', e.message);
                if (streamOk) ui.cancelStreaming();
                ui.showToast(e.message, 'error');
            },
            abortController.signal,
            null,  // prefill
            // Tool event handlers
            (id, name, args) => {
                if (!streamOk) {
                    ui.updateStatus('Generating...');
                    ui.startStreaming();
                    streamOk = true;
                }
                ui.startTool(id, name, args);
            },
            (id, name, result, error) => {
                ui.endTool(id, name, result, error);
            },
            // Stream started handler
            () => {
                ui.updateStatus('Processing...');
            },
            // Iteration start handler (after tool calls)
            (iteration) => {
                if (iteration > 1) {
                    ui.showStatus();
                    ui.updateStatus('Generating...');
                }
            },
            // Images
            hasImages ? pendingImages : null
        );
        
        if (streamOk) return null;
    } catch (e) {
        if (e.message !== 'Cancelled') {
            console.error('Error send message:', e);
            ui.showToast(e.message, 'error');
        }
        return null;
    } finally {
        ui.hideStatus();
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        input.focus();
        setProc(false);
    }
}

// Update image preview area in DOM
function updateImagePreviewArea() {
    const previewArea = document.getElementById('image-preview-area');
    if (!previewArea) return;
    
    previewArea.innerHTML = '';
    const pending = Images.getPendingUploadImages();
    
    if (pending.length === 0) {
        previewArea.style.display = 'none';
        return;
    }
    
    previewArea.style.display = 'flex';
    pending.forEach((img, idx) => {
        const preview = Images.createUploadPreview(img, idx, (index) => {
            Images.removePendingUploadImage(index);
            updateImagePreviewArea();
        });
        previewArea.appendChild(preview);
    });
}

// Handle image file selection/paste/drop
export async function handleImageUpload(file) {
    if (!file.type.startsWith('image/')) {
        ui.showToast('Only image files are supported', 'error');
        return;
    }
    
    // Check size (10MB max)
    if (file.size > 10 * 1024 * 1024) {
        ui.showToast('Image too large (max 10MB)', 'error');
        return;
    }
    
    try {
        const result = await api.uploadImage(file);
        
        // Create preview URL from file
        const previewUrl = URL.createObjectURL(file);
        
        Images.addPendingUploadImage({
            data: result.data,
            media_type: result.media_type,
            filename: result.filename,
            previewUrl: previewUrl
        });
        
        updateImagePreviewArea();
        ui.showToast('Image attached', 'success', 2000);
    } catch (e) {
        console.error('Image upload failed:', e);
        ui.showToast(e.message, 'error');
    }
}

// Setup paste and drag-drop handlers
export function setupImageHandlers() {
    const input = document.getElementById('prompt-input');
    const form = document.getElementById('chat-form');
    const uploadBtn = document.getElementById('image-upload-btn');
    const fileInput = document.getElementById('image-upload-input');
    
    // Upload button click -> trigger file input
    if (uploadBtn && fileInput) {
        uploadBtn.addEventListener('click', () => fileInput.click());
    }
    
    // File input handler
    if (fileInput) {
        fileInput.addEventListener('change', async (e) => {
            const files = e.target.files;
            for (const file of files) {
                await handleImageUpload(file);
            }
            fileInput.value = '';
        });
    }
    
    // Paste handler
    document.addEventListener('paste', async (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (file) await handleImageUpload(file);
                return;
            }
        }
    });
    
    // Drag-drop handlers on form
    if (form) {
        form.addEventListener('dragover', (e) => {
            e.preventDefault();
            form.classList.add('drag-over');
        });
        
        form.addEventListener('dragleave', (e) => {
            e.preventDefault();
            form.classList.remove('drag-over');
        });
        
        form.addEventListener('drop', async (e) => {
            e.preventDefault();
            form.classList.remove('drag-over');
            
            const files = e.dataTransfer?.files;
            if (!files) return;
            
            for (const file of files) {
                if (file.type.startsWith('image/')) {
                    await handleImageUpload(file);
                }
            }
        });
    }
}

export async function handleStop() {
    const controller = getAbortController();
    
    if (controller) {
        setIsCancelling(true);
        console.log('Cancellation flag set');
        
        try {
            await api.cancelGeneration();
            console.log('Cancel request sent to backend');
        } catch (e) {
            console.error('Failed to send cancel request:', e);
        }
        
        controller.abort();
        ui.cancelStreaming();
        ui.hideStatus();
        setProc(false);
        ui.showToast('Generation stopped', 'success');
    }
}

export async function triggerSendWithText(text) {
    if (getIsProc()) {
        console.log('Already processing, ignoring transcribed text');
        return;
    }
    
    const { input } = getElements();
    input.value = text;
    input.dispatchEvent(new Event('input'));
    await handleSend();
}

export function handleInput() {
    const { input } = getElements();
    input.parentElement.dataset.replicatedValue = input.value;
}

export function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    }
}