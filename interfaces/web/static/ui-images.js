// ui-images.js - Image lifecycle and loading management

// Track pending images for proper scroll timing
let pendingImages = new Set();
let scrollAfterImagesTimeout = null;

// Track pending upload images (before send)
let pendingUploadImages = [];

// Track pending upload files (before send)
let pendingFiles = [];

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

// ============================================================================
// UPLOAD FILE MANAGEMENT
// ============================================================================

const TEXT_EXTENSIONS = {
    '.py': 'python', '.txt': 'text', '.md': 'markdown',
    '.js': 'javascript', '.ts': 'typescript', '.json': 'json',
    '.yaml': 'yaml', '.yml': 'yaml', '.toml': 'toml',
    '.ini': 'ini', '.cfg': 'ini', '.conf': 'ini',
    '.sh': 'bash', '.bash': 'bash',
    '.html': 'html', '.css': 'css', '.xml': 'xml',
    '.csv': 'csv', '.log': 'text', '.env': 'bash',
    '.rs': 'rust', '.go': 'go', '.java': 'java',
    '.c': 'c', '.cpp': 'cpp', '.h': 'c',
};

export const ALLOWED_FILE_EXTENSIONS = new Set(Object.keys(TEXT_EXTENSIONS));

const extToLang = (filename) => {
    const dot = filename.lastIndexOf('.');
    if (dot === -1) return 'text';
    return TEXT_EXTENSIONS[filename.slice(dot).toLowerCase()] || 'text';
};

export const getPendingFiles = () => [...pendingFiles];

export const addPendingFile = (fileData) => {
    // fileData: {filename: string, text: string}
    pendingFiles.push(fileData);
    return pendingFiles.length - 1;
};

export const removePendingFile = (index) => {
    if (index >= 0 && index < pendingFiles.length) {
        pendingFiles.splice(index, 1);
    }
};

export const clearPendingFiles = () => { pendingFiles = []; };

export const hasPendingFiles = () => pendingFiles.length > 0;

export const getFilesForApi = () => {
    return pendingFiles.map(f => ({
        filename: f.filename,
        text: f.text
    }));
};

// Create preview chip for file in upload zone
export const createFilePreview = (fileData, index, onRemove) => {
    const chip = document.createElement('div');
    chip.className = 'file-preview-chip';
    chip.dataset.index = index;

    const lineCount = (fileData.text.match(/\n/g) || []).length + 1;

    const label = document.createElement('span');
    label.className = 'file-chip-label';
    label.textContent = `${fileData.filename} (${lineCount} lines)`;

    const removeBtn = document.createElement('button');
    removeBtn.className = 'file-chip-remove';
    removeBtn.innerHTML = '×';
    removeBtn.title = 'Remove file';
    removeBtn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        onRemove(index);
    };

    chip.appendChild(label);
    chip.appendChild(removeBtn);
    return chip;
};

// Create file accordion for history rendering
export const createFileAccordion = (file) => {
    const details = document.createElement('details');
    details.className = 'accordion-file';

    const summary = document.createElement('summary');
    const lineCount = (file.text.match(/\n/g) || []).length + 1;
    summary.textContent = `${file.filename} (${lineCount} lines)`;

    const wrapper = document.createElement('div');
    wrapper.className = 'accordion-body';
    const inner = document.createElement('div');
    inner.className = 'accordion-inner';

    // Use createCodeBlock from ui-parsing for syntax highlighting
    const lang = extToLang(file.filename);
    const { createCodeBlock } = await_createCodeBlock();
    const codeBlock = createCodeBlock(lang, file.text);
    inner.appendChild(codeBlock);

    wrapper.appendChild(inner);
    details.appendChild(summary);
    details.appendChild(wrapper);
    return details;
};

// Lazy import helper for createCodeBlock (avoids circular import)
function await_createCodeBlock() {
    // Direct inline implementation to avoid circular dependency with ui-parsing
    return {
        createCodeBlock: (language, code) => {
            const wrapper = document.createElement('pre');
            if (language && language !== 'text') {
                const header = document.createElement('div');
                header.className = 'code-block-header';
                header.innerHTML = `
                    <span class="code-lang">${language}</span>
                    <button class="code-copy" title="Copy code">Copy</button>
                `;
                wrapper.appendChild(header);
                const copyBtn = header.querySelector('.code-copy');
                copyBtn.addEventListener('click', async () => {
                    try {
                        await navigator.clipboard.writeText(code);
                        copyBtn.textContent = 'Copied!';
                        setTimeout(() => copyBtn.textContent = 'Copy', 2000);
                    } catch (e) {
                        copyBtn.textContent = 'Failed';
                        setTimeout(() => copyBtn.textContent = 'Copy', 2000);
                    }
                });
            }
            const codeEl = document.createElement('code');
            codeEl.className = `language-${language}`;
            codeEl.textContent = code;
            wrapper.appendChild(codeEl);
            if (window.hljs) {
                try { window.hljs.highlightElement(codeEl); } catch (e) {}
            }
            return wrapper;
        }
    };
}

// Image modal functions (defined before createUserImageThumbnails which uses them)
let modalGalleryImages = [];
let modalCurrentIndex = -1;

const updateModalNav = () => {
    const prevBtn = document.getElementById('image-modal-prev');
    const nextBtn = document.getElementById('image-modal-next');
    const counter = document.getElementById('image-modal-counter');

    if (modalGalleryImages.length <= 1) {
        if (prevBtn) prevBtn.style.display = 'none';
        if (nextBtn) nextBtn.style.display = 'none';
        if (counter) counter.style.display = 'none';
        return;
    }

    if (prevBtn) prevBtn.style.display = modalCurrentIndex > 0 ? '' : 'none';
    if (nextBtn) nextBtn.style.display = modalCurrentIndex < modalGalleryImages.length - 1 ? '' : 'none';
    if (counter) {
        counter.style.display = '';
        counter.textContent = `${modalCurrentIndex + 1} / ${modalGalleryImages.length}`;
    }
};

const navigateModal = (delta) => {
    if (modalGalleryImages.length === 0) return;
    const newIndex = modalCurrentIndex + delta;
    if (newIndex < 0 || newIndex >= modalGalleryImages.length) return;
    modalCurrentIndex = newIndex;
    const modalImg = document.getElementById('image-modal-img');
    if (modalImg) modalImg.src = modalGalleryImages[modalCurrentIndex];
    updateModalNav();
};

export const closeImageModal = () => {
    const modal = document.getElementById('image-modal');
    if (!modal) return;

    modal.style.display = 'none';
    document.body.style.overflow = '';
    modalGalleryImages = [];
    modalCurrentIndex = -1;
};

export const openImageModal = (src, galleryImages = null, currentIndex = -1) => {
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('image-modal-img');
    if (!modal || !modalImg) return;

    modalImg.src = src;
    modalGalleryImages = galleryImages || [];
    modalCurrentIndex = currentIndex;

    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    updateModalNav();
};

// Setup modal event listeners (call once on init)
export const setupImageModal = () => {
    const modal = document.getElementById('image-modal');
    const backdrop = modal?.querySelector('.image-modal-backdrop');
    const closeBtn = document.getElementById('image-modal-close');
    const prevBtn = document.getElementById('image-modal-prev');
    const nextBtn = document.getElementById('image-modal-next');

    if (backdrop) backdrop.addEventListener('click', closeImageModal);
    if (closeBtn) closeBtn.addEventListener('click', closeImageModal);
    if (prevBtn) prevBtn.addEventListener('click', (e) => { e.stopPropagation(); navigateModal(-1); });
    if (nextBtn) nextBtn.addEventListener('click', (e) => { e.stopPropagation(); navigateModal(1); });

    document.addEventListener('keydown', (e) => {
        if (modal?.style.display !== 'flex') return;
        if (e.key === 'Escape') closeImageModal();
        else if (e.key === 'ArrowLeft') navigateModal(-1);
        else if (e.key === 'ArrowRight') navigateModal(1);
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