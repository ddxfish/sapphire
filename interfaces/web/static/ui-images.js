// ui-images.js - Image lifecycle and loading management

// Track pending images for proper scroll timing
let pendingImages = new Set();
let scrollAfterImagesTimeout = null;

// Track pending upload images (before send)
let pendingUploadImages = [];

export const hasPendingImages = () => {
    return pendingImages.size > 0;
};

export const clearPendingImages = () => {
    pendingImages.clear();
    if (scrollAfterImagesTimeout) {
        clearTimeout(scrollAfterImagesTimeout);
        scrollAfterImagesTimeout = null;
    }
};

// ============================================================================
// UPLOAD IMAGE MANAGEMENT
// ============================================================================

export const getPendingUploadImages = () => [...pendingUploadImages];

export const addPendingUploadImage = (imageData) => {
    // imageData: {data: base64, media_type: string, filename: string, previewUrl: string}
    pendingUploadImages.push(imageData);
    return pendingUploadImages.length - 1;
};

export const removePendingUploadImage = (index) => {
    if (index >= 0 && index < pendingUploadImages.length) {
        const removed = pendingUploadImages.splice(index, 1)[0];
        if (removed.previewUrl) {
            URL.revokeObjectURL(removed.previewUrl);
        }
    }
};

export const clearPendingUploadImages = () => {
    pendingUploadImages.forEach(img => {
        if (img.previewUrl) URL.revokeObjectURL(img.previewUrl);
    });
    pendingUploadImages = [];
};

export const hasPendingUploadImages = () => pendingUploadImages.length > 0;

// Convert pending uploads to format for API
export const getImagesForApi = () => {
    return pendingUploadImages.map(img => ({
        data: img.data,
        media_type: img.media_type
    }));
};

// Create preview element for upload zone
export const createUploadPreview = (imageData, index, onRemove) => {
    const container = document.createElement('div');
    container.className = 'upload-preview-item';
    container.dataset.index = index;
    
    const img = document.createElement('img');
    img.src = imageData.previewUrl || `data:${imageData.media_type};base64,${imageData.data}`;
    img.alt = imageData.filename || 'Uploaded image';
    
    const removeBtn = document.createElement('button');
    removeBtn.className = 'upload-preview-remove';
    removeBtn.innerHTML = '×';
    removeBtn.title = 'Remove image';
    removeBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        onRemove(index);
    };
    
    container.appendChild(img);
    container.appendChild(removeBtn);
    return container;
};

// Image modal functions (defined before createUserImageThumbnails which uses them)
export const closeImageModal = () => {
    const modal = document.getElementById('image-modal');
    if (!modal) return;
    
    modal.style.display = 'none';
    document.body.style.overflow = '';
};

export const openImageModal = (src) => {
    console.log('[ImageModal] Opening modal with src length:', src?.length);
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('image-modal-img');
    console.log('[ImageModal] modal:', !!modal, 'modalImg:', !!modalImg);
    if (!modal || !modalImg) return;
    
    modalImg.src = src;
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
};

// Setup modal event listeners (call once on init)
export const setupImageModal = () => {
    const modal = document.getElementById('image-modal');
    const backdrop = modal?.querySelector('.image-modal-backdrop');
    const closeBtn = document.getElementById('image-modal-close');
    
    if (backdrop) backdrop.addEventListener('click', closeImageModal);
    if (closeBtn) closeBtn.addEventListener('click', closeImageModal);
    
    // Close on escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal?.style.display === 'flex') {
            closeImageModal();
        }
    });
};

// Render user message images (thumbnails in history)
export const createUserImageThumbnails = (images) => {
    const container = document.createElement('div');
    container.className = 'user-images';
    
    images.forEach(img => {
        const imgEl = document.createElement('img');
        imgEl.src = `data:${img.media_type};base64,${img.data}`;
        imgEl.className = 'user-image-thumb';
        imgEl.alt = 'Attached image';
        imgEl.onclick = (e) => {
            console.log('[ImageModal] Thumbnail clicked');
            e.stopPropagation();
            openImageModal(imgEl.src);
        };
        container.appendChild(imgEl);
    });
    
    return container;
};

export const scheduleScrollAfterImages = (scrollCallback, force = false) => {
    if (scrollAfterImagesTimeout) {
        clearTimeout(scrollAfterImagesTimeout);
    }
    
    scrollAfterImagesTimeout = setTimeout(() => {
        if (pendingImages.size === 0) {
            scrollCallback(force);
        }
    }, 100);
};

/**
 * Creates an image element with retry logic and load tracking.
 * @param {string} imageId - The image identifier
 * @param {boolean} isHistoryRender - Whether this is from history (affects scroll behavior)
 * @param {function} scrollCallback - Optional scroll function to call when image loads
 * @returns {HTMLImageElement}
 */
export const createImageElement = (imageId, isHistoryRender = false, scrollCallback = null) => {
    const img = document.createElement('img');
    img.src = `/api/sdxl-image/${imageId}`;
    img.className = 'inline-image';
    img.alt = 'Generated image';
    img.dataset.imageId = imageId;
    img.dataset.retryCount = '0';
    
    const MAX_RETRIES = 20;
    
    // Track this image if it's from history render
    if (isHistoryRender) {
        pendingImages.add(imageId);
    }
    
    img.onload = function() {
        if (this.naturalWidth > 0 && this.naturalHeight > 0) {
            // Remove from pending and schedule scroll if needed
            if (isHistoryRender && pendingImages.has(imageId)) {
                pendingImages.delete(imageId);
                if (scrollCallback) {
                    scheduleScrollAfterImages(scrollCallback, true);
                }
            }
            
            // Dispatch custom event for inline cloning (handled in main.js)
            this.dispatchEvent(new CustomEvent('imageReady', {
                bubbles: true,
                detail: { imageId: imageId, isHistoryRender: isHistoryRender }
            }));
        }
    };
    
    img.onerror = function() {
        const retries = parseInt(this.dataset.retryCount || '0');
        if (retries >= MAX_RETRIES) {
            this.alt = 'Image failed';
            // Remove from pending on failure too
            if (isHistoryRender && pendingImages.has(imageId)) {
                pendingImages.delete(imageId);
                if (scrollCallback) {
                    scheduleScrollAfterImages(scrollCallback, true);
                }
            }
            return;
        }
        this.dataset.retryCount = (retries + 1).toString();
        setTimeout(() => {
            this.src = `/api/sdxl-image/${imageId}?t=${Date.now()}`;
        }, 2000);
    };
    
    return img;
};

/**
 * Replace image placeholders in HTML string with actual image elements
 * @param {string} content - Content with <<IMG::id>> placeholders
 * @param {boolean} isHistoryRender - Whether from history render
 * @param {function} scrollCallback - Optional scroll callback
 * @returns {Object} - { html: processed HTML string, images: array of {placeholder, imageId} }
 */
export const extractImagePlaceholders = (content, isHistoryRender = false, scrollCallback = null) => {
    const imgPattern = /<<IMG::([^>]+)>>/g;
    const images = [];
    let imgIndex = 0;
    
    const processedContent = content.replace(imgPattern, (match, imageId) => {
        const placeholder = `__IMAGE_PLACEHOLDER_${imgIndex}__`;
        images.push({ placeholder, imageId });
        imgIndex++;
        return placeholder;
    });
    
    return { processedContent, images };
};

/**
 * Replace image placeholders in an element with actual image elements
 */
export const replaceImagePlaceholdersInElement = (element, images, isHistoryRender = false, scrollCallback = null) => {
    images.forEach(({ placeholder, imageId }) => {
        const placeholderImgs = element.querySelectorAll(`img[src*="${placeholder}"]`);
        placeholderImgs.forEach(img => {
            const newImg = createImageElement(imageId, isHistoryRender, scrollCallback);
            img.replaceWith(newImg);
        });
    });
};