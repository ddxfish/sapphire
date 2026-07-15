// interfaces/web/static/views/builder.js - Online persona builder interface
import { eventBus } from '/static/core/event-bus.js?v=' + window.BOOT_VERSION;
import { fetchWithCsrf, showError, showSuccess } from '/static/shared/fetch.js?v=' + window.BOOT_VERSION;

export async function render(container) {
    container.innerHTML = '';
    
    const state = {
        step: 'input',  // 'input' or 'preview'
        description: '',
        generatedPersona: null,
        loading: false,
        config: null
    };
    
    // Load builder configuration
    try {
        const response = await fetch('/api/persona-builder/config');
        if (!response.ok) {
            throw new Error('Failed to load builder configuration');
        }
        state.config = await response.json();
    } catch (error) {
        console.error('Failed to load builder config:', error);
        showError('Failed to load persona builder');
        return;
    }
    
    // Render the builder UI
    function renderUI() {
        if (state.step === 'input') {
            renderInputStep(container, state);
        } else if (state.step === 'preview') {
            renderPreviewStep(container, state);
        }
    }
    
    renderUI();
    
    // Event handler for generate button
    container.addEventListener('click', async (e) => {
        if (e.target.matches('#generate-btn')) {
            await handleGenerate(state, () => renderUI());
        }
        if (e.target.matches('#download-btn')) {
            handleDownload(state);
        }
        if (e.target.matches('#back-btn')) {
            state.step = 'input';
            renderUI();
        }
        if (e.target.matches('#try-full-btn')) {
            window.location.href = '/setup';
        }
    });
}

function renderInputStep(container, state) {
    const { config } = state;
    
    container.innerHTML = `
        <div class="builder-container">
            <div class="builder-header">
                <h1>Create a Sapphire Companion</h1>
                <p>Describe the AI companion you want, and we'll generate a persona for you to try</p>
            </div>
            
            <div class="builder-form">
                <div class="form-group">
                    <label for="description">What companion do you want to create?</label>
                    <textarea 
                        id="description"
                        class="form-input"
                        placeholder="${config?.form_fields?.description?.placeholder || 'E.g., A wise mentor who loves history, or a cheerful morning buddy'}"
                        maxlength="${config?.form_fields?.description?.max_length || 2000}"
                    ></textarea>
                    <div class="char-count">
                        <span id="char-count">0</span> / ${config?.form_fields?.description?.max_length || 2000}
                    </div>
                </div>
                
                <div class="form-group">
                    <label for="notes">Or upload persona notes (optional)</label>
                    <input 
                        type="file" 
                        id="notes"
                        class="form-input"
                        accept=".json,.txt,.pdf"
                    />
                </div>
                
                <button id="generate-btn" class="btn btn-primary btn-large">
                    <span class="btn-text">Generate Persona</span>
                    <span class="btn-loading" style="display:none;">Generating...</span>
                </button>
            </div>
            
            <div class="builder-info">
                <h3>How it works</h3>
                <ol>
                    <li>Describe the companion you want</li>
                    <li>We'll use AI to generate a complete persona</li>
                    <li>Preview and download the result</li>
                    <li>Install Sapphire and import your persona</li>
                </ol>
            </div>
        </div>
    `;
    
    // Update character count
    const textarea = container.querySelector('#description');
    if (textarea) {
        textarea.value = state.description;
        textarea.addEventListener('input', (e) => {
            state.description = e.target.value;
            const charCount = container.querySelector('#char-count');
            if (charCount) {
                charCount.textContent = e.target.value.length;
            }
        });
        // Initial character count
        container.querySelector('#char-count').textContent = state.description.length;
    }
}

function renderPreviewStep(container, state) {
    const persona = state.generatedPersona;
    const settings = persona?.settings || {};
    
    container.innerHTML = `
        <div class="builder-container">
            <div class="builder-header">
                <button id="back-btn" class="btn-back">← Back</button>
                <h1>Your Persona: ${persona.name}</h1>
                <p class="tagline">"${persona.tagline}"</p>
            </div>
            
            <div class="preview-content">
                <div class="preview-section">
                    <h3>Description</h3>
                    <p>${escapeHtml(persona.description)}</p>
                </div>
                
                <div class="preview-section">
                    <h3>Configuration</h3>
                    <div class="config-grid">
                        <div class="config-item">
                            <label>Prompt</label>
                            <span>${escapeHtml(settings.prompt || 'default')}</span>
                        </div>
                        <div class="config-item">
                            <label>Toolset</label>
                            <span>${escapeHtml(settings.toolset || 'personality')}</span>
                        </div>
                        <div class="config-item">
                            <label>Spice Set</label>
                            <span>${escapeHtml(settings.spice_set || 'companion')}</span>
                        </div>
                        <div class="config-item">
                            <label>Voice</label>
                            <span>${escapeHtml(settings.voice || 'af_heart')}</span>
                        </div>
                        <div class="config-item">
                            <label>Pitch</label>
                            <span>${(settings.pitch || 1.0).toFixed(2)}</span>
                        </div>
                        <div class="config-item">
                            <label>Speed</label>
                            <span>${(settings.speed || 1.0).toFixed(2)}</span>
                        </div>
                    </div>
                </div>
                
                <div class="preview-actions">
                    <button id="download-btn" class="btn btn-secondary">
                        Download Persona JSON
                    </button>
                    <button id="try-full-btn" class="btn btn-primary">
                        Install Sapphire & Try It
                    </button>
                </div>
            </div>
        </div>
    `;
}

async function handleGenerate(state, onComplete) {
    const description = state.description?.trim();
    
    if (!description) {
        showError('Please describe the companion you want to create');
        return;
    }
    
    if (description.length < 10) {
        showError('Description is too short (minimum 10 characters)');
        return;
    }
    
    const btn = document.querySelector('#generate-btn');
    const btnText = btn?.querySelector('.btn-text');
    const btnLoading = btn?.querySelector('.btn-loading');
    
    try {
        // Show loading state
        state.loading = true;
        if (btn) btn.disabled = true;
        if (btnText) btnText.style.display = 'none';
        if (btnLoading) btnLoading.style.display = 'inline';
        
        // Generate persona
        const response = await fetchWithCsrf('/api/persona-builder/generate', {
            method: 'POST',
            body: JSON.stringify({
                description: description,
                notes: ''
            })
        });
        
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            const message = error.detail || `Generation failed (HTTP ${response.status})`;
            throw new Error(message);
        }
        
        const result = await response.json();
        
        if (!result.persona) {
            throw new Error('Invalid response from server');
        }
        
        state.generatedPersona = result.persona;
        state.step = 'preview';
        
        onComplete();
        showSuccess('Persona generated successfully!');
        
    } catch (error) {
        console.error('Generation failed:', error);
        
        // Provide helpful error messages
        let userMessage = 'Failed to generate persona';
        if (error.message.includes('fetch')) {
            userMessage = 'Connection error - please check your internet connection';
        } else if (error.message.includes('HTTP 429')) {
            userMessage = 'Too many requests - please wait a moment and try again';
        } else if (error.message.includes('HTTP 500')) {
            userMessage = 'Server error - your LLM may be unavailable. Try installing Sapphire locally.';
        } else if (error.message) {
            userMessage = error.message;
        }
        
        showError(userMessage);
    } finally {
        state.loading = false;
        if (btn) btn.disabled = false;
        if (btnText) btnText.style.display = 'inline';
        if (btnLoading) btnLoading.style.display = 'none';
    }
}

function handleDownload(state) {
    if (!state.generatedPersona) {
        showError('No persona to download');
        return;
    }
    
    try {
        const dataStr = JSON.stringify(state.generatedPersona, null, 2);
        const dataBlob = new Blob([dataStr], { type: 'application/json' });
        const url = URL.createObjectURL(dataBlob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${state.generatedPersona.name}-persona.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        
        showSuccess('Persona downloaded!');
    } catch (error) {
        console.error('Download failed:', error);
        showError('Failed to download persona');
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
