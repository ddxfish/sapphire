// ui-images.js - Image lifecycle and loading management

// Track pending images for proper scroll timing
let pendingImages = new Set();
let scrollAfterImagesTimeout = null;

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