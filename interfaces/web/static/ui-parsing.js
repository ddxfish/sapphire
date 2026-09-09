// ui-parsing.js - Content parsing and formatting

import * as Images from './ui-images.js';
import { parseGalleryMarker, buildGallery } from './shared/gallery-marker.js';

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
    const wrapper = createElem('div');        // Outer wrapper for grid animation
    const inner = createElem('div');          // Inner content div
    
    details.className = type === 'think' ? 'accordion-think' : 'accordion-tool';
    wrapper.className = 'accordion-body';
    inner.className = 'accordion-inner';
    
    summary.textContent = title;
    inner.textContent = content;
    wrapper.appendChild(inner);
    details.appendChild(summary);
    details.appendChild(wrapper);
    return { acc: details, content: inner };
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
    
    // Remove elements that shouldn't be spoken (order: larger containers first)
    clone.querySelectorAll('details').forEach(d => d.remove());       // Accordions/details
    clone.querySelectorAll('pre').forEach(pre => pre.remove());       // Code blocks (includes header)
    clone.querySelectorAll('code').forEach(c => c.remove());          // Inline code
    clone.querySelectorAll('table').forEach(t => t.remove());         // Tables
    clone.querySelectorAll('img').forEach(img => img.remove());       // Images
    clone.querySelectorAll('svg').forEach(svg => svg.remove());       // SVGs
    clone.querySelectorAll('button').forEach(btn => btn.remove());    // Buttons (Copy, etc)
    clone.querySelectorAll('.code-block-header').forEach(h => h.remove()); // Code headers (safety)
    clone.querySelectorAll('.tool-accordion').forEach(t => t.remove());    // Tool accordions
    clone.querySelectorAll('.accordion-tool').forEach(t => t.remove());    // Tool accordions alt
    clone.querySelectorAll('[class*="code"]').forEach(c => c.remove());    // Any code-related class
    clone.querySelectorAll('.message-metadata').forEach(m => m.remove()); // Metadata footer
    
    // Add periods after list items for TTS pauses (before textContent extraction)
    clone.querySelectorAll('li').forEach(li => {
        // Only add period if the item doesn't already end with sentence-ending punctuation
        const text = li.textContent.trim();
        if (text && !/[.!?]$/.test(text)) {
            li.appendChild(document.createTextNode('. '));
        }
    });
    
    // Add periods after headers for TTS pauses
    clone.querySelectorAll('h1, h2, h3, h4, h5, h6').forEach(h => {
        const text = h.textContent.trim();
        if (text && !/[.!?]$/.test(text)) {
            h.appendChild(document.createTextNode('. '));
        }
    });
    
    // Add periods after paragraphs for TTS pauses
    clone.querySelectorAll('p').forEach(p => {
        const text = p.textContent.trim();
        if (text && !/[.!?]$/.test(text)) {
            p.appendChild(document.createTextNode('. '));
        }
    });
    
    // Replace <br> with period-space for pauses between lines
    clone.querySelectorAll('br').forEach(br => {
        br.replaceWith(document.createTextNode('. '));
    });
    
    let txt = clone.textContent.trim();
    
    // Strip any remaining think tags
    txt = txt.replace(/<(?:seed:)?think>.*?<\/(?:seed:think|seed:cot_budget_reflect|think)>/gs, '');
    
    // Strip any HTML tags that leaked through
    txt = txt.replace(/<[^>]+>/g, '');
    
    // Clean up multiple periods/punctuation (.. or ., or ,. etc) → single period
    txt = txt.replace(/[.!?,]+\s*[.!?,]+/g, '. ');
    
    // Clean up whitespace
    txt = txt.replace(/\s+/g, ' ').trim();
    
    return txt;
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

const COPY_ICON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
const CHECK_ICON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';

// Create a code block element with header (language label + copy icon)
export const createCodeBlock = (language, code) => {
    const wrapper = document.createElement('pre');

    const header = document.createElement('div');
    header.className = 'code-block-header';
    const langText = (language && language !== 'plaintext') ? language : '';
    header.innerHTML = `
        <span class="code-lang">${escapeHtml(langText)}</span>
        <button class="code-copy" title="Copy code" aria-label="Copy code">${COPY_ICON_SVG}</button>
    `;
    wrapper.appendChild(header);

    const copyBtn = header.querySelector('.code-copy');
    copyBtn.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(code);
            copyBtn.innerHTML = CHECK_ICON_SVG;
            copyBtn.classList.add('copied');
            setTimeout(() => {
                copyBtn.innerHTML = COPY_ICON_SVG;
                copyBtn.classList.remove('copied');
            }, 2000);
        } catch (e) {
            copyBtn.classList.add('failed');
            setTimeout(() => copyBtn.classList.remove('failed'), 2000);
        }
    });

    const codeEl = document.createElement('code');
    codeEl.className = `language-${language || 'plaintext'}`;
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

// Process markdown - handles common syntax for streaming display
export const processMarkdown = (text) => {
    const lines = text.split('\n');
    const output = [];
    let i = 0;
    
    while (i < lines.length) {
        const line = lines[i];
        
        // Check for table (line with 2+ pipes suggests a table row)
        const pipeCount = (line.match(/\|/g) || []).length;
        if (pipeCount >= 2) {
            const tableLines = [];
            while (i < lines.length) {
                const currentLine = lines[i];
                const currentPipes = (currentLine.match(/\|/g) || []).length;
                const isSeparator = /^[\s|:\-]+$/.test(currentLine.trim()) && currentLine.includes('|');
                if (currentPipes >= 2 || isSeparator) {
                    tableLines.push(currentLine);
                    i++;
                } else {
                    break;
                }
            }
            if (tableLines.length >= 2) {
                output.push({ type: 'block', html: parseTable(tableLines) });
            } else {
                // Single line with pipes - not a table, render as text
                tableLines.forEach(tl => output.push({ type: 'inline', html: processInlineMarkdown(escapeHtml(tl)) }));
            }
            continue;
        }
        
        // Check for list
        const listMatch = line.match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
        if (listMatch) {
            const listLines = [];
            while (i < lines.length) {
                const lm = lines[i].match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
                if (lm) {
                    listLines.push({ indent: lm[1].length, marker: lm[2], content: lm[3] });
                    i++;
                    continue;
                }
                const prev = listLines[listLines.length - 1];
                const cur = lines[i];
                // Continuation line: indented past the item's marker column
                if (cur.trim() && cur.match(/^\s*/)[0].length >= prev.indent + 2) {
                    prev.content += ' ' + cur.trim();
                    i++;
                    continue;
                }
                // Loose list: bridge blank lines when the next non-blank line is a
                // same-kind list item (ordered stays ordered, bullet stays bullet)
                if (!cur.trim()) {
                    let j = i;
                    while (j < lines.length && !lines[j].trim()) j++;
                    const nm = j < lines.length ? lines[j].match(/^(\s*)([-*+]|\d+\.)\s+/) : null;
                    if (nm) {
                        // Compare kind against the last item at the resuming depth,
                        // not the last item overall (may be a nested sublist)
                        let ref = prev;
                        for (let k = listLines.length - 1; k >= 0; k--) {
                            if (listLines[k].indent <= nm[1].length) { ref = listLines[k]; break; }
                        }
                        if (/^\d+\.$/.test(nm[2]) === /^\d+\.$/.test(ref.marker)) {
                            i = j;
                            continue;
                        }
                    }
                }
                break;
            }
            output.push({ type: 'block', html: parseList(listLines) });
            continue;
        }
        
        // Headers: # ## ### etc
        const headerMatch = line.match(/^(#{1,6})\s+(.+)$/);
        if (headerMatch) {
            const level = headerMatch[1].length;
            const content = processInlineMarkdown(escapeHtml(headerMatch[2]));
            output.push({ type: 'block', html: `<h${level}>${content}</h${level}>` });
            i++;
            continue;
        }
        
        // Horizontal rule: --- or *** or ___
        if (/^[-*_]{3,}\s*$/.test(line)) {
            output.push({ type: 'block', html: '<hr>' });
            i++;
            continue;
        }
        
        // Blockquote: > text (collect consecutive lines)
        if (line.match(/^>\s?/)) {
            const quoteLines = [];
            while (i < lines.length && lines[i].match(/^>\s?/)) {
                quoteLines.push(lines[i].replace(/^>\s?/, ''));
                i++;
            }
            const quoteContent = quoteLines.map(l => processInlineMarkdown(escapeHtml(l))).join('<br>');
            output.push({ type: 'block', html: `<blockquote>${quoteContent}</blockquote>` });
            continue;
        }
        
        // Empty line
        if (!line.trim()) {
            output.push({ type: 'break', html: '' });
            i++;
            continue;
        }
        
        // Regular line - process inline markdown
        output.push({ type: 'inline', html: processInlineMarkdown(escapeHtml(line)) });
        i++;
    }
    
    // Smart join: no <br> between block elements, <br> between inline elements
    let result = '';
    for (let j = 0; j < output.length; j++) {
        const item = output[j];
        const prev = output[j - 1];
        
        if (item.type === 'break') {
            // Empty line = paragraph break
            if (prev && prev.type === 'inline') result += '<br>';
            continue;
        }
        
        if (item.type === 'block') {
            result += item.html;
        } else {
            // Inline: add <br> if previous was also inline
            if (prev && prev.type === 'inline') result += '<br>';
            result += item.html;
        }
    }
    
    return result;
};

// Parse markdown table
const parseTable = (lines) => {
    if (lines.length < 2) return lines.map(l => escapeHtml(l)).join('<br>');
    
    const parseRow = (line) => {
        // Split on | and remove empty first/last elements from delimiters
        const cells = line.split('|').map(cell => cell.trim());
        // Remove empty strings at start and end
        if (cells.length > 0 && cells[0] === '') cells.shift();
        if (cells.length > 0 && cells[cells.length - 1] === '') cells.pop();
        return cells;
    };
    
    const headerCells = parseRow(lines[0]);
    if (headerCells.length === 0) return escapeHtml(lines.join('\n'));
    
    // Check if second line is separator (dashes)
    const isSeparator = /^[\s|:\-]+$/.test(lines[1]);
    const startRow = isSeparator ? 2 : 1;
    
    let html = '<table><thead><tr>';
    headerCells.forEach(cell => {
        html += `<th>${processInlineMarkdown(escapeHtml(cell))}</th>`;
    });
    html += '</tr></thead><tbody>';
    
    for (let i = startRow; i < lines.length; i++) {
        if (/^[\s|:\-]+$/.test(lines[i])) continue; // Skip separator rows
        const cells = parseRow(lines[i]);
        if (cells.length === 0) continue;
        html += '<tr>';
        cells.forEach(cell => {
            html += `<td>${processInlineMarkdown(escapeHtml(cell))}</td>`;
        });
        html += '</tr>';
    }
    
    html += '</tbody></table>';
    return html;
};

// Parse nested list
const parseList = (items) => {
    if (items.length === 0) return '';
    
    // Normalize indents to levels (0, 1, 2, etc based on relative indent)
    const minIndent = Math.min(...items.map(i => i.indent));
    const indentStep = items.length > 1 
        ? Math.min(...items.filter(i => i.indent > minIndent).map(i => i.indent - minIndent)) || 2
        : 2;
    
    items.forEach(item => {
        item.level = Math.floor((item.indent - minIndent) / indentStep);
    });
    
    const buildList = (startIdx, level) => {
        if (startIdx >= items.length) return { html: '', endIdx: startIdx };
        
        const isOrdered = /^\d+\.$/.test(items[startIdx].marker);
        const tag = isOrdered ? 'ol' : 'ul';
        // Preserve source numbering — a list fragmented by upstream splitting
        // must not restart at 1
        const startNum = isOrdered ? parseInt(items[startIdx].marker) : 1;
        let html = startNum > 1 ? `<ol start="${startNum}">` : `<${tag}>`;
        let i = startIdx;
        
        while (i < items.length) {
            const item = items[i];
            
            // Item at lower level = end of this list
            if (item.level < level) break;
            
            // Item at same level = sibling
            if (item.level === level) {
                html += `<li>${processInlineMarkdown(escapeHtml(item.content))}`;
                i++;
                
                // Check for nested items
                if (i < items.length && items[i].level > level) {
                    const nested = buildList(i, items[i].level);
                    html += nested.html;
                    i = nested.endIdx;
                }
                html += '</li>';
            } else {
                // Deeper level without parent - create nested list anyway
                const nested = buildList(i, item.level);
                html += nested.html;
                i = nested.endIdx;
            }
        }
        
        html += `</${tag}>`;
        return { html, endIdx: i };
    };
    
    return buildList(0, 0).html;
};

// Process inline markdown elements
const processInlineMarkdown = (html) => {
    // Checkboxes: [x] or [X] = checked, [ ] = unchecked
    html = html.replace(/\[x\]/gi, '<input type="checkbox" checked disabled>');
    html = html.replace(/\[ \]/g, '<input type="checkbox" disabled>');
    
    // Bold+italic: ***text*** or ___text___
    html = html.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/___([^_]+)___/g, '<strong><em>$1</em></strong>');
    
    // Bold: **text** or __text__
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    
    // Italic: *text* or _text_
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])/g, '<em>$1</em>');
    
    // Strikethrough: ~~text~~
    html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    
    // Inline code: `code`
    html = html.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    
    // Images: ![alt](url) - MUST be before links
    // Balanced parens in URL for Wikipedia etc, tolerates space between ] and (
    html = html.replace(/!\[([^\]]*)\]\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)/g, (m, alt, url) => {
        url = url.trim().replace(/ /g, '%20');
        if (/^https?:\/\//i.test(url)) return `<img src="${url}" alt="${alt}" class="chat-img" referrerpolicy="no-referrer" loading="lazy">`;
        return m;
    });

    // Links: [text](url) - allow http(s), mailto, and local paths starting with /
    html = html.replace(/\[([^\]]+)\]\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)/g, (m, text, url) => {
        url = url.trim().replace(/ /g, '%20');
        if (/^(https?:\/\/|mailto:)/i.test(url)) return `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
        if (/^\/[a-zA-Z]/.test(url)) return `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
        return m;
    });
    
    return html;
};

// Process inline code (single backticks)
const processInlineCode = (html) => {
    // Match `code` but not inside already-escaped contexts
    return html.replace(/`([^`\n]+)`/g, (match, code) => {
        return `<code>${escapeHtml(code)}</code>`;
    });
};

// Check if element contains only images and whitespace/br (no text content)
const isImageOnly = (el) => {
    for (const node of el.childNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) {
            if (node.tagName === 'BR') continue;
            if (node.classList?.contains('chat-img')) continue;
            return false;
        } else if (node.nodeType === Node.TEXT_NODE) {
            if (node.textContent.trim() === '') continue;
            return false;
        }
    }
    return el.querySelector('.chat-img') !== null;
};

// Create a gallery div from an array of img elements
const createGallery = (imgs) => {
    const gallery = document.createElement('div');
    gallery.className = 'image-gallery';

    for (const img of imgs) {
        const item = document.createElement('div');
        item.className = 'gallery-item';

        const cloned = img.cloneNode(true);
        item.appendChild(cloned);

        const alt = img.alt?.trim();
        if (alt) {
            const caption = document.createElement('div');
            caption.className = 'gallery-caption';
            caption.textContent = alt;
            item.appendChild(caption);
        }

        gallery.appendChild(item);
    }

    return gallery;
};

// Detect consecutive images and wrap in gallery containers, add click-to-modal
export const wrapImageGalleries = (container) => {
    if (!container) return;

    // Phase 0: Image-heavy lists — <ol>/<ul> where most items are just images
    Array.from(container.querySelectorAll('ol, ul')).forEach(list => {
        const items = Array.from(list.querySelectorAll(':scope > li'));
        const imgItems = items.filter(li => li.querySelector('.chat-img'));
        if (imgItems.length >= 2 && imgItems.length >= items.length * 0.5) {
            const imgs = imgItems.map(li => li.querySelector('.chat-img'));
            list.replaceWith(createGallery(imgs));
        }
    });

    // Phase 1: Multi-image paragraphs (2+ images in a <p>)
    Array.from(container.querySelectorAll('p')).forEach(p => {
        const imgs = Array.from(p.querySelectorAll('.chat-img'));
        if (imgs.length < 2) return;

        if (isImageOnly(p)) {
            // Pure images — replace the whole paragraph
            p.replaceWith(createGallery(imgs));
        } else {
            // Mixed text + images — extract images into gallery, keep text
            const gallery = createGallery(imgs);
            imgs.forEach(img => {
                if (img.nextSibling && img.nextSibling.nodeName === 'BR') img.nextSibling.remove();
                else if (img.previousSibling && img.previousSibling.nodeName === 'BR') img.previousSibling.remove();
                img.remove();
            });
            while (p.firstChild && p.firstChild.nodeName === 'BR') p.firstChild.remove();
            while (p.lastChild && p.lastChild.nodeName === 'BR') p.lastChild.remove();
            p.after(gallery);
        }
    });

    // Phase 2: Consecutive single-image elements
    let changed = true;
    while (changed) {
        changed = false;
        const children = Array.from(container.children);

        for (let i = 0; i < children.length; i++) {
            const child = children[i];
            if (child.classList?.contains('image-gallery')) continue;

            const img = child.querySelector?.('.chat-img');
            if (!img || !isImageOnly(child)) continue;

            // Collect consecutive single-image elements
            const group = [child];
            for (let j = i + 1; j < children.length; j++) {
                const next = children[j];
                if (next.classList?.contains('image-gallery')) break;
                const nextImg = next.querySelector?.('.chat-img');
                if (nextImg && isImageOnly(next)) {
                    group.push(next);
                } else {
                    break;
                }
            }

            if (group.length >= 2) {
                const allImgs = group.map(el => el.querySelector('.chat-img'));
                const gallery = createGallery(allImgs);
                group[0].replaceWith(gallery);
                for (let k = 1; k < group.length; k++) group[k].remove();
                changed = true;
                break; // Restart scan after DOM mutation
            }
        }
    }

    // Phase 3: Click-to-modal for ALL chat images (gallery and standalone)
    container.querySelectorAll('.chat-img').forEach(img => {
        if (!img.dataset.modalReady) {
            img.dataset.modalReady = 'true';
            img.addEventListener('click', (e) => {
                e.stopPropagation();
                const gallery = img.closest('.image-gallery');
                if (gallery) {
                    const galleryImgs = Array.from(gallery.querySelectorAll('.gallery-item img'));
                    const urls = galleryImgs.map(i => i.src);
                    const idx = galleryImgs.indexOf(img);
                    Images.openImageModal(img.src, urls, idx);
                } else {
                    Images.openImageModal(img.src);
                }
            });
        }
    });
};

export const parseContent = (el, msg, isHistoryRender = false, scrollCallback = null) => {
    globalThinkCounter = 0;

    let txt = typeof msg === 'string' ? msg : (msg.content || '');
    // Strip avatar tags from rendered text if setting is enabled
    if (window._avatarStripTags) {
        txt = txt.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
    }
    const parts = (typeof msg === 'object' && msg.parts) ? msg.parts : [];
    const userImages = (typeof msg === 'object' && msg.images) ? msg.images : [];
    const userFiles = (typeof msg === 'object' && msg.files) ? msg.files : [];

    if (!txt && parts.length === 0 && userImages.length === 0 && userFiles.length === 0) {
        el.textContent = '';
        return;
    }

    el.innerHTML = '';

    // Render user-attached images first (for user messages)
    if (userImages.length > 0) {
        const thumbs = Images.createUserImageThumbnails(userImages);
        el.appendChild(thumbs);
    }

    // Render user-attached files as collapsible accordions
    if (userFiles.length > 0) {
        for (const file of userFiles) {
            el.appendChild(Images.createFileAccordion(file));
        }
    }
    
    if (parts.length > 0) {
        let thinkCnt = 0;
        parts.forEach(part => {
            if (part.type === 'content') {
                let partText = part.text;
                if (window._avatarStripTags) {
                    partText = partText.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
                }
                renderContentText(el, partText, isHistoryRender, scrollCallback, thinkCnt);
            } else if (part.type === 'tool_result') {
                renderToolResult(el, part);
            }
        });
        cloneImagesInline(el);
        wrapImageGalleries(el);
        return;
    }

    renderContentText(el, txt, isHistoryRender, scrollCallback, 0);
    cloneImagesInline(el);
    wrapImageGalleries(el);
};

const addToolDeleteButton = (acc, toolCallId) => {
    if (!toolCallId) return;
    
    const summary = acc.querySelector('summary');
    if (!summary) return;
    
    const deleteBtn = createElem('button', { 
        class: 'tool-delete-btn',
        title: 'Remove from history'
    }, '×');
    
    deleteBtn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        
        if (!confirm('Remove this tool call from history?')) return;
        
        try {
            const { removeToolCall } = await import('./api.js');
            await removeToolCall(toolCallId);
            acc.remove();
        } catch (err) {
            console.error('Failed to remove tool call:', err);
            const { showToast } = await import('./ui.js');
            showToast('Failed to remove tool call', 'error');
        }
    });
    
    summary.appendChild(deleteBtn);
};

const renderToolResult = (el, part) => {
    const toolName = part.name || 'Unknown Tool';
    const toolCallId = part.tool_call_id;
    // Tiles ride a UI marker (shared/gallery-marker.js); the accordion text drops it.
    const { entries: galleryEntries, text: fullResult } =
        parseGalleryMarker(part.content || part.result || '');

    // Get truncation limit based on tool
    const maxLen = toolName === 'web_search' ? 1000 :
                  toolName === 'get_website' ? 800 :
                  toolName === 'web_search_images' ? 1500 :
                  toolName === 'get_site_links' ? 1500 : 500;

    // Check for image markers. A tool can return SEVERAL (e.g. z-image returns a
    // grid + the individual full-size images), so match + strip ALL of them.
    const imgIds = [...fullResult.matchAll(/<<IMG::([^>]+)>>/g)].map(m => m[1]);
    if (imgIds.length) {
        const textWithoutMarker = fullResult.replace(/<<IMG::[^>]+>>\n?/g, '').trim();
        const isTruncated = textWithoutMarker.length > maxLen;
        const truncatedText = isTruncated
            ? textWithoutMarker.substring(0, maxLen) + '...'
            : textWithoutMarker;

        let inputsPrefix = '';
        if (part.inputs && Object.keys(part.inputs).length > 0) {
            try {
                inputsPrefix = 'Inputs:\n' + JSON.stringify(part.inputs, null, 2) + '\n\n';
            } catch (e) {}
        }

        const { acc, content } = createAccordion('tool', `Tool Result: ${toolName}`,
            inputsPrefix + 'Result:\n' + truncatedText);
        addToolDeleteButton(acc, toolCallId);

        // Add expand/collapse toggle if truncated
        if (isTruncated) {
            addExpandToggle(content, inputsPrefix + 'Result:\n', truncatedText, textWithoutMarker);
        }

        // Render every returned image. Inserting each before firstChild in marker
        // order yields the natural top-to-bottom order (grid first, then individuals).
        // NOTE — PATH 1 of 2 for tool images: this renders them INSIDE the accordion.
        // PATH 2 is the inline clone in core/events.js `handleImageReady`, which clones
        // each of these into the reply body once it loads. A tool image shows in BOTH.
        for (const imageId of imgIds) {
            const img = Images.createImageElement(imageId, false, null);
            img.className = 'tool-result-image';   // (a gone-image span keeps its data attr)
            content.insertBefore(img, content.firstChild);
        }

        el.appendChild(acc);
        appendGallery(el, galleryEntries);      // a contact sheet AND tiles can share one result
        return;
    }

    // Regular tool result (no image)
    const isTruncated = fullResult.length > maxLen;
    const truncatedResult = isTruncated
        ? fullResult.substring(0, maxLen) + '...'
        : fullResult;

    let inputsPrefix = '';
    if (part.inputs && Object.keys(part.inputs).length > 0) {
        try {
            inputsPrefix = 'Inputs:\n' + JSON.stringify(part.inputs, null, 2) + '\n\n';
        } catch (e) {}
    }

    const { acc, content } = createAccordion('tool', `Tool Result: ${toolName}`,
        inputsPrefix + 'Result:\n' + truncatedResult);
    addToolDeleteButton(acc, toolCallId);

    // Add expand/collapse toggle if truncated
    if (isTruncated) {
        addExpandToggle(content, inputsPrefix + 'Result:\n', truncatedResult, fullResult);
    }

    el.appendChild(acc);

    appendGallery(el, galleryEntries);
};

// Tiles under a tool result — the ONE renderer (history + live stream share it).
const appendGallery = (el, entries) => {
    const gallery = buildGallery(entries, Images.openImageModal);
    if (gallery) el.appendChild(gallery);
};

// Helper to add expand/collapse toggle for truncated content
const addExpandToggle = (contentEl, prefix, shortText, fullText) => {
    let isExpanded = false;

    const toggle = document.createElement('button');
    toggle.className = 'tool-expand-toggle';
    toggle.textContent = `Show more (${(fullText.length / 1000).toFixed(1)}k chars)`;
    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        isExpanded = !isExpanded;
        // contentEl is the accordion-inner div, update its textContent directly
        // But we need to preserve the toggle button, so use a text node approach
        const textNode = contentEl.firstChild;
        if (textNode && textNode.nodeType === Node.TEXT_NODE) {
            textNode.textContent = prefix + (isExpanded ? fullText : shortText);
        } else {
            // Fallback: rebuild content
            contentEl.textContent = prefix + (isExpanded ? fullText : shortText);
            contentEl.appendChild(toggle);
        }
        toggle.textContent = isExpanded ? 'Show less' : `Show more (${(fullText.length / 1000).toFixed(1)}k chars)`;
    });

    // Convert existing text to a text node so we can update it separately
    const currentText = contentEl.textContent;
    contentEl.textContent = '';
    contentEl.appendChild(document.createTextNode(currentText));
    contentEl.appendChild(toggle);
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
        // processMarkdown handles escapeHtml internally
        let result = processMarkdown(content);
        
        // Replace image placeholders (they survive escaping as __IMG_xxx__)
        images.forEach(({ placeholder, imageId }) => {
            if (result.includes(placeholder)) {
                const img = Images.createImageElement(imageId, isHistoryRender, scrollCallback);
                const tempDiv = document.createElement('div');
                tempDiv.appendChild(img);
                result = result.replace(placeholder, tempDiv.innerHTML);
            }
        });
        
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

        // Detect imbalanced think tags (GLM quirk: <think>A</think>B</think>C)
        const openCount = (processed.match(/<(?:seed:)?think>/g) || []).length;
        const closeCount = (processed.match(/<\/(?:seed:think|seed:cot_budget_reflect|think)>/g) || []).length;

        if (closeCount > openCount) {
            // Greedy match: first <think> to LAST </think>
            const m = processed.match(/^([\s\S]*?)<(?:seed:)?think>([\s\S]*)<\/(?:seed:think|seed:cot_budget_reflect|think)>([\s\S]*)$/);
            if (m) {
                const isSeed = processed.indexOf('<seed:think>') < processed.indexOf('<think>') || !processed.includes('<think>');
                const thinkContent = m[2].replace(/<\/(?:seed:think|seed:cot_budget_reflect|think)>/g, '').trim();

                if (m[1]?.trim()) {
                    const p = createElem('p');
                    p.innerHTML = safeReplaceImagePlaceholders(m[1].trim());
                    Images.replaceImagePlaceholdersInElement(p, images, isHistoryRender, scrollCallback);
                    replaceCodePlaceholders(p);
                    el.appendChild(p);
                }
                if (thinkContent) {
                    globalThinkCounter++;
                    const label = isSeed ? 'Seed Think' : 'Think';
                    const { acc } = createAccordion('think', `${label} (Step ${globalThinkCounter})`, '');
                    const contentDiv = acc.querySelector('div');
                    contentDiv.innerHTML = safeReplaceImagePlaceholders(thinkContent);
                    Images.replaceImagePlaceholdersInElement(contentDiv, images, isHistoryRender, scrollCallback);
                    replaceCodePlaceholders(contentDiv);
                    el.appendChild(acc);
                }
                if (m[3]?.trim()) {
                    const p = createElem('p');
                    p.innerHTML = safeReplaceImagePlaceholders(m[3].trim());
                    Images.replaceImagePlaceholdersInElement(p, images, isHistoryRender, scrollCallback);
                    replaceCodePlaceholders(p);
                    el.appendChild(p);
                }
                return;
            }
        }

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
                p.innerHTML = safeReplaceImagePlaceholders(trimmed);
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