// ui-parsing.js - Content parsing and formatting

import * as Images from './ui-images.js';

let globalThinkCounter = 0;

const createElem = (tag, attrs = {}, content = '') => {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => k === 'style' ? el.style.cssText = v : el.setAttribute(k, v));
    if (content) el.textContent = content;
    return el;
};

// Escape HTML to prevent XSS while preserving text content
const escapeHtml = (text) => {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
};

export const createAccordion = (type, title, content = '') => {
    const details = createElem('details');
    const summary = createElem('summary');
    const div = createElem('div');
    
    details.className = type === 'think' ? 'accordion-think' : 'accordion-tool';
    
    summary.textContent = title;
    div.textContent = content;
    details.appendChild(summary);
    details.appendChild(div);
    return { acc: details, content: div };
};

export const cloneImagesInline = (contentEl) => {
    const thinkAccordions = Array.from(contentEl.querySelectorAll('details')).filter(details => {
        const summary = details.querySelector('summary');
        return summary && summary.textContent.includes('Think');
    });
    
    if (thinkAccordions.length === 0) return;
    const lastThinkAccordion = thinkAccordions[thinkAccordions.length - 1];
    
    thinkAccordions.forEach(accordion => {
        const imgs = accordion.querySelectorAll('img[data-image-id]');
        imgs.forEach(img => {
            if (img.dataset.inlineClone) return;
            const clone = img.cloneNode(true);
            clone.dataset.inlineClone = 'true';
            lastThinkAccordion.insertAdjacentElement('afterend', clone);
        });
    });
};

export const extractProseText = (el) => {
    if (!el) return '';
    const clone = el.cloneNode(true);
    clone.querySelectorAll('details').forEach(d => d.remove());
    clone.querySelectorAll('img').forEach(img => img.remove());
    let txt = clone.textContent.trim();
    txt = txt.replace(/<(?:seed:)?think>.*?<\/(?:seed:think|seed:cot_budget_reflect|think)>/gs, '');
    return txt.trim();
};

// Extract fenced code blocks and replace with placeholders
const extractCodeBlocks = (text) => {
    const codeBlocks = [];
    let counter = 0;
    
    // Match ```lang\ncode\n``` - language is optional
    const processed = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (match, lang, code) => {
        const placeholder = `__CODE_BLOCK_${counter}__`;
        codeBlocks.push({
            placeholder,
            language: lang || 'plaintext',
            code: code.trimEnd()
        });
        counter++;
        return placeholder;
    });
    
    return { processed, codeBlocks };
};

// Create a code block element with optional header and copy button
const createCodeBlock = (language, code) => {
    const wrapper = document.createElement('pre');
    
    // Add header with language and copy button
    if (language && language !== 'plaintext') {
        const header = document.createElement('div');
        header.className = 'code-block-header';
        header.innerHTML = `
            <span class="code-lang">${escapeHtml(language)}</span>
            <button class="code-copy" title="Copy code">Copy</button>
        `;
        wrapper.appendChild(header);
        
        // Copy button handler
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
    
    // Syntax highlight if hljs is available
    if (window.hljs) {
        try {
            window.hljs.highlightElement(codeEl);
        } catch (e) {
            // Fallback: no highlighting
        }
    }
    
    return wrapper;
};

// Process inline code (single backticks)
const processInlineCode = (html) => {
    // Match `code` but not inside already-escaped contexts
    return html.replace(/`([^`\n]+)`/g, (match, code) => {
        return `<code>${escapeHtml(code)}</code>`;
    });
};

export const parseContent = (el, msg, isHistoryRender = false, scrollCallback = null) => {
    globalThinkCounter = 0;
    
    const txt = typeof msg === 'string' ? msg : (msg.content || '');
    const parts = (typeof msg === 'object' && msg.parts) ? msg.parts : [];
    
    if (!txt && parts.length === 0) {
        el.textContent = '';
        return;
    }
    
    el.innerHTML = '';
    
    if (parts.length > 0) {
        let thinkCnt = 0;
        parts.forEach(part => {
            if (part.type === 'content') {
                renderContentText(el, part.text, isHistoryRender, scrollCallback, thinkCnt);
            } else if (part.type === 'tool_result') {
                renderToolResult(el, part);
            }
        });
        cloneImagesInline(el);
        return;
    }
    
    renderContentText(el, txt, isHistoryRender, scrollCallback, 0);
    cloneImagesInline(el);
};

const renderToolResult = (el, part) => {
    const toolName = part.name || 'Unknown Tool';
    let resultContent = part.content || part.result || '';
    
    // Check for image markers
    const imgMatch = resultContent.match(/<<IMG::([^>]+)>>/);
    if (imgMatch) {
        const imageId = imgMatch[1];
        const textWithoutMarker = resultContent.replace(/<<IMG::[^>]+>>\n?/, '').trim();
        
        const maxLen = 500;
        let displayText = textWithoutMarker;
        if (displayText.length > maxLen) {
            displayText = displayText.substring(0, maxLen) + '...(truncated)';
        }
        
        let accordionContent = '';
        
        if (part.inputs && Object.keys(part.inputs).length > 0) {
            try {
                let inputsStr = JSON.stringify(part.inputs, null, 2);
                if (inputsStr.length > 500) {
                    inputsStr = inputsStr.substring(0, 500) + '\n...(truncated)';
                }
                accordionContent += 'Inputs:\n' + inputsStr + '\n\n';
            } catch (e) {}
        }
        
        accordionContent += 'Result:\n' + displayText;
        
        const { acc, content } = createAccordion('tool', `Tool Result: ${toolName}`, accordionContent);
        
        const img = Images.createImageElement(imageId, false, null);
        img.className = 'tool-result-image';
        content.insertBefore(img, content.firstChild);
        
        el.appendChild(acc);
        return;
    }
    
    // Regular tool result (no image)
    const maxLen = toolName === 'generate_scene_image' ? 2000 : 
                  toolName === 'web_search' ? 1000 : 
                  toolName === 'get_website' ? 800 : 500;
    
    if (resultContent.length > maxLen) {
        resultContent = resultContent.substring(0, maxLen) + '...(truncated)';
    }
    
    let inputsStr = '';
    if (part.inputs && Object.keys(part.inputs).length > 0) {
        try {
            inputsStr = JSON.stringify(part.inputs, null, 2);
            if (inputsStr.length > 500) {
                inputsStr = inputsStr.substring(0, 500) + '\n...(truncated)';
            }
            inputsStr = 'Inputs:\n' + inputsStr + '\n\n';
        } catch (e) {
            inputsStr = '';
        }
    }
    
    const { acc } = createAccordion('tool', `Tool Result: ${toolName}`, inputsStr + 'Result:\n' + resultContent);
    el.appendChild(acc);
};

const renderContentText = (el, txt, isHistoryRender, scrollCallback, thinkCnt) => {
    if (!txt) return;
    
    // Step 1: Extract code blocks first (before any other processing)
    const { processed: textWithoutCode, codeBlocks } = extractCodeBlocks(txt);
    txt = textWithoutCode;
    
    // Step 2: Extract image placeholders
    const { processedContent, images } = Images.extractImagePlaceholders(txt, isHistoryRender, scrollCallback);
    txt = processedContent;
    
    // Step 3: Build safe HTML replacement function
    const safeReplaceImagePlaceholders = (content) => {
        let result = escapeHtml(content);
        
        // Replace image placeholders
        images.forEach(({ placeholder, imageId }) => {
            if (result.includes(placeholder)) {
                const img = Images.createImageElement(imageId, isHistoryRender, scrollCallback);
                const tempDiv = document.createElement('div');
                tempDiv.appendChild(img);
                result = result.replace(placeholder, tempDiv.innerHTML);
            }
        });
        
        // Process inline code
        result = processInlineCode(result);
        
        return result;
    };
    
    // Step 4: Replace code block placeholders with actual elements
    const replaceCodePlaceholders = (container) => {
        codeBlocks.forEach(({ placeholder, language, code }) => {
            // Find text nodes containing placeholder
            const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
            let node;
            while (node = walker.nextNode()) {
                if (node.textContent.includes(placeholder)) {
                    const codeEl = createCodeBlock(language, code);
                    const parts = node.textContent.split(placeholder);
                    const parent = node.parentNode;
                    
                    // Replace text node with: before text, code block, after text
                    if (parts[0]) {
                        parent.insertBefore(document.createTextNode(parts[0]), node);
                    }
                    parent.insertBefore(codeEl, node);
                    if (parts[1]) {
                        parent.insertBefore(document.createTextNode(parts[1]), node);
                    }
                    parent.removeChild(node);
                    break;
                }
            }
        });
    };
    
    // Handle think blocks
    if (txt.includes('<think>') || txt.includes('<seed:think>')) {
        let processed = txt.replace(/<\/seed:cot_budget_reflect>(.*?)<\/seed:think>/gs, '$1</seed:think>');
        const parts = processed.split(/<(?:seed:)?think>|<\/(?:seed:think|seed:cot_budget_reflect|think)>/);
        
        parts.forEach((part, i) => {
            const trimmed = part.trim();
            if (!trimmed) return;
            
            if (i % 2 === 1) {
                globalThinkCounter++;
                const isSeed = processed.substring(0, processed.indexOf(part)).includes('<seed:think>');
                const { acc } = createAccordion('think', `${isSeed ? 'Seed Think' : 'Think'} (Step ${globalThinkCounter})`, '');
                const contentDiv = acc.querySelector('div');
                contentDiv.innerHTML = safeReplaceImagePlaceholders(trimmed);
                Images.replaceImagePlaceholdersInElement(contentDiv, images, isHistoryRender, scrollCallback);
                replaceCodePlaceholders(contentDiv);
                el.appendChild(acc);
            } else {
                const p = createElem('p');
                let paraContent = trimmed.replace(/\*\*/g, '');
                p.innerHTML = safeReplaceImagePlaceholders(paraContent);
                Images.replaceImagePlaceholdersInElement(p, images, isHistoryRender, scrollCallback);
                replaceCodePlaceholders(p);
                el.appendChild(p);
            }
        });
        return;
    }
    
    // Handle orphan think close tags
    const orphanMatch = [...txt.matchAll(/<\/(?:seed:think|seed:cot_budget_reflect|think)>/g)];
    if (orphanMatch.length > 0) {
        const last = orphanMatch[orphanMatch.length - 1];
        const thought = txt.substring(0, last.index).trim();
        const after = txt.substring(last.index + last[0].length).trim();
        
        if (thought) {
            const label = last[0].includes('seed') ? 'Seed Thoughts' : 'Thoughts';
            const { acc } = createAccordion('think', label, '');
            const contentDiv = acc.querySelector('div');
            contentDiv.innerHTML = safeReplaceImagePlaceholders(thought);
            Images.replaceImagePlaceholdersInElement(contentDiv, images, isHistoryRender, scrollCallback);
            replaceCodePlaceholders(contentDiv);
            el.appendChild(acc);
        }
        if (after) {
            const p = createElem('p');
            p.innerHTML = safeReplaceImagePlaceholders(after);
            Images.replaceImagePlaceholdersInElement(p, images, isHistoryRender, scrollCallback);
            replaceCodePlaceholders(p);
            el.appendChild(p);
        }
        return;
    }
    
    // Regular paragraphs
    const paragraphs = txt.split(/\n\s*\n/).filter(p => p.trim());
    
    paragraphs.forEach(para => {
        const p = createElem('p');
        p.innerHTML = safeReplaceImagePlaceholders(para.trim());
        Images.replaceImagePlaceholdersInElement(p, images, isHistoryRender, scrollCallback);
        replaceCodePlaceholders(p);
        el.appendChild(p);
    });
};