// ui-parsing.js - Content parsing and formatting

import * as Images from './ui-images.js';
import { parseGalleryMarker, buildGallery } from './shared/gallery-marker.js';
import { parseFilesMarker, buildFilesRows } from './shared/files-marker.js';
import { parseAskMarker, buildAskCards } from './shared/ask-marker.js';
import { parseStageMarker, buildStageCards } from './shared/stage-marker.js';
import { thinkSegments, stripThink, isSeedThink } from './shared/think.js';
import { proseBlocks, dedent } from './shared/prose.js';
import { errWords } from './shared/fetch.js';

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
    clone.querySelectorAll('.row-note, .tool-block').forEach(n => n.remove()); // the app's notes about a row (never content)
    // the widgets that ride under a tool (shared/*-marker.js) are UI, not her words:
    // 🔊 read a question card, gallery captions, "↓ song.wav" and "🌐 Browser" aloud
    clone.querySelectorAll('.ask-card, .stage-card, .gallery-row, .files-row, .image-gallery, .cancel-marker')
        .forEach(n => n.remove());
    
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
    txt = stripThink(txt);
    
    // Strip any HTML tags that leaked through — but not her avatar tags: the
    // catch-all ate `<<avatar: wave>` and left a spoken `>`. Both voice
    // doors strip them server-side, and the streaming one times them into
    // her speech (a replay animates her too).
    const tags = [];
    txt = txt.replace(/<<avatar:[^<>]*>>/g, m => `\u0000${tags.push(m) - 1}\u0000`);
    txt = txt.replace(/<[^>]+>/g, '');
    txt = txt.replace(/\u0000(\d+)\u0000/g, (_, i) => tags[+i]);
    
    // Clean up multiple periods/punctuation (.. or ., or ,. etc) → single period
    txt = txt.replace(/[.!?,]+\s*[.!?,]+/g, '. ');
    
    // Clean up whitespace
    txt = txt.replace(/\s+/g, ' ').trim();
    
    return txt;
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
                // A blank line never reaches here: shared/prose.js splits paragraphs
                // first, so a loose list is one <ol start=…> per paragraph.
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
    
    // in the scroll wrapper style.css defines (.message-content is overflow:hidden,
    // so a wide table was clipped with no way to see its right side)
    let html = '<div class="table-wrapper"><table><thead><tr>';
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
    
    html += '</tbody></table></div>';
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
// A value going inside "…" in an attribute. escapeHtml (textContent→innerHTML)
// escapes & < > but NOT quotes: `![x" onerror="…"](https://…)` closed the
// attribute and ran a handler in the user's tab - any text reaching the
// renderer (her reply after a prompt injection, a room guest, a pasted line)
// could do it (UI scout, 2026-11-12).
const attr = (s) => s.replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const processInlineMarkdown = (html) => {
    // Checkboxes: [x] or [X] = checked, [ ] = unchecked - never the start of a
    // link: "[X](https://x.com/…)" drew a ticked box and a raw url
    html = html.replace(/\[x\](?!\s*\()/gi, '<input type="checkbox" checked disabled>');
    html = html.replace(/\[ \](?!\s*\()/g, '<input type="checkbox" disabled>');
    
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
    
    // Inline code: `code`, or ```code``` on one line (never a block: shared/prose.js)
    html = html.replace(/(`{1,3})([^`\n]+)\1/g, '<code>$2</code>');
    
    // Images: ![alt](url) - MUST be before links
    // Balanced parens in URL for Wikipedia etc, tolerates space between ] and (
    html = html.replace(/!\[([^\]]*)\]\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)/g, (m, alt, url) => {
        url = url.trim().replace(/ /g, '%20');
        if (/^https?:\/\//i.test(url)) return `<img src="${attr(url)}" alt="${attr(alt)}" class="chat-img" referrerpolicy="no-referrer" loading="lazy">`;
        return m;
    });

    // Links: [text](url) - allow http(s), mailto, and local paths starting with /
    html = html.replace(/\[([^\]]+)\]\s*\(([^()]*(?:\([^)]*\)[^()]*)*)\)/g, (m, text, url) => {
        url = url.trim().replace(/ /g, '%20');
        if (/^(https?:\/\/|mailto:)/i.test(url)) return `<a href="${attr(url)}" target="_blank" rel="noopener">${text}</a>`;
        if (/^\/[a-zA-Z]/.test(url)) return `<a href="${attr(url)}" target="_blank" rel="noopener">${text}</a>`;
        return m;
    });
    
    return html;
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
        // not ones Phase 0 already put in a gallery (a ghost row of captions
        // with no pictures), nor a table's: they belong in their cells
        const imgs = Array.from(p.querySelectorAll('.chat-img')).filter(img => !img.closest('.image-gallery, table'));
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
    // Attachments in the message's own text (shared/files-marker.js): a take the
    // user played lands in their bubble with its player. The text drops the marker.
    const { groups: textFiles, text: txtNoFiles } = parseFilesMarker(txt);
    txt = txtNoFiles;
    // Strip avatar tags from rendered text if setting is enabled
    if (window._avatarStripTags) {
        txt = txt.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
    }
    const parts = (typeof msg === 'object' && msg.parts) ? msg.parts : [];
    const userImages = (typeof msg === 'object' && msg.images) ? msg.images : [];
    const userFiles = (typeof msg === 'object' && msg.files) ? msg.files : [];

    if (!txt && parts.length === 0 && userImages.length === 0 && userFiles.length === 0
            && textFiles.length === 0) {
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
        const partGroups = [];      // the parts ARE the text here; msg.content repeats them
        parts.forEach(part => {
            if (part.type === 'content') {
                const partFiles = parseFilesMarker(part.text);
                let partText = partFiles.text;
                partGroups.push(...partFiles.groups);
                if (window._avatarStripTags) {
                    partText = partText.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
                }
                renderContentText(el, partText, isHistoryRender, scrollCallback, thinkCnt);
            } else if (part.type === 'tool_result') {
                renderToolResult(el, part);
            }
        });
        wrapImageGalleries(el);
        appendFiles(el, partGroups);
        return;
    }

    renderContentText(el, txt, isHistoryRender, scrollCallback, 0);
    wrapImageGalleries(el);
    appendFiles(el, textFiles);
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
            showToast(`Couldn't remove tool call. ${errWords(err, 'Try again.')}`, 'error');
        }
    });
    
    summary.appendChild(deleteBtn);
};

const renderToolResult = (el, part) => el.append(...buildToolResult(part, !!part.error));

// THE tool-result renderer: history and the live stream (ui-streaming
// doEndTool) both build here. Returns the accordion and the widgets that ride
// under it. `isError`: the live tool_end's flag, or the stored row's `error`.
export const buildToolResult = (part, isError = false) => {
    const toolName = part.name || 'Unknown Tool';
    const toolCallId = part.tool_call_id;
    // Tiles ride a UI marker (shared/gallery-marker.js); the accordion text drops it.
    const { entries: galleryEntries, title: galleryTitle, text: galleryStripped } =
        parseGalleryMarker(part.content || part.result || '');
    const { groups: fileGroups, text: filesStripped } = parseFilesMarker(galleryStripped);
    // A question card (ask_user) rides its own marker (shared/ask-marker.js).
    const { cards: askCards, text: askStripped } = parseAskMarker(filesStripped);
    const { cards: stageCards, text: fullResult } = parseStageMarker(askStripped, toolName);

    // Get truncation limit based on tool
    const maxLen = toolName === 'web_search' ? 1000 :
                  toolName === 'get_website' ? 800 :
                  toolName === 'web_search_images' ? 1500 :
                  toolName === 'web_view_images' ? 1500 :
                  toolName === 'get_site_links' ? 1500 : 500;

    // Check for image markers. A tool can return SEVERAL (e.g. z-image returns a
    // grid + the individual full-size images), so match + strip ALL of them.
    const imgIds = [...fullResult.matchAll(/<<IMG::([^>]+)>>/g)].map(m => m[1]);
    const text = imgIds.length ? fullResult.replace(/<<IMG::[^>]+>>\n?/g, '').trim() : fullResult;
    const isTruncated = text.length > maxLen;
    const shown = isTruncated ? text.substring(0, maxLen) + '...' : text;

    let inputsPrefix = '';
    if (part.inputs && Object.keys(part.inputs).length > 0) {
        try {
            inputsPrefix = 'Inputs:\n' + JSON.stringify(part.inputs, null, 2) + '\n\n';
        } catch (e) {}
    }

    const { acc, content } = createAccordion('tool', `Tool Result: ${toolName}`,
        inputsPrefix + 'Result:\n' + shown);
    if (isError) acc.classList.add('error');
    if (toolCallId) acc.dataset.toolId = toolCallId;   // a rebuild keeps it open (ui.js graftLive)
    addToolDeleteButton(acc, toolCallId);

    if (isTruncated) addExpandToggle(content, inputsPrefix + 'Result:\n', shown, text);

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

    // Her contact sheet stays in the accordion; the row IS the user's view of
    // the same numbered set (the inline clone in core/events.js skips flagged
    // accordions — a sheet AND a row would show the pictures twice).
    if (galleryEntries.length) acc.dataset.gallery = '1';
    return [
        acc,
        buildGallery(galleryEntries, Images.openImageModal, galleryTitle),
        ...buildFilesRows(fileGroups),
        ...buildAskCards(askCards),
        ...buildStageCards(stageCards),
    ].filter(Boolean);
};

// Players + download buttons — the ONE renderer (shared/files-marker.js).
const appendFiles = (el, groups) => {
    buildFilesRows(groups).forEach(row => el.appendChild(row));
};

// Helper to add expand/collapse toggle for truncated content
const addExpandToggle = (contentEl, prefix, shortText, fullText) => {
    let isExpanded = false;

    const toggle = document.createElement('button');
    toggle.className = 'tool-expand-toggle';
    toggle.textContent = `Show more (${(fullText.length / 1000).toFixed(1)}k chars)`;
    // Its OWN text node, held by reference: the tool's images are prepended
    // after this runs, so "the first child" was an <img> and the old fallback
    // (textContent = …) wiped the pictures on the first click.
    const textNode = document.createTextNode(contentEl.textContent);
    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        isExpanded = !isExpanded;
        textNode.textContent = prefix + (isExpanded ? fullText : shortText);
        toggle.textContent = isExpanded ? 'Show less' : `Show more (${(fullText.length / 1000).toFixed(1)}k chars)`;
    });

    contentEl.textContent = '';
    contentEl.appendChild(textNode);
    contentEl.appendChild(toggle);
};

const renderContentText = (el, txt, isHistoryRender, scrollCallback, thinkCnt) => {
    if (!txt) return;
    // Thinking first, on the RAW text (shared/think.js, the twin of
    // core/think.py). A think block is her scratch paper: it renders as plain
    // text. No markdown, no images, no code blocks, so nothing inside it can
    // break the page; a tag she TYPED while thinking is a word (2026-09-27).
    const segs = thinkSegments(txt);
    if (!segs.some(s => s.type === 'think')) {
        renderProse(el, txt, isHistoryRender, scrollCallback);
        return;
    }
    segs.forEach(s => {
        if (s.type === 'think') renderThink(el, s);
        else renderProse(el, s.text, isHistoryRender, scrollCallback);
    });
};

const renderThink = (el, seg) => {
    const text = seg.text.trim();
    if (!text) return;
    globalThinkCounter++;
    const label = isSeedThink(seg.name) ? 'Seed Think' : 'Think';
    const { acc, content } = createAccordion('think', `${label} (Step ${globalThinkCounter})`, '');
    content.textContent = text;          // plain text: .accordion-inner is pre-wrap
    el.appendChild(acc);
};

const renderProse = (el, txt, isHistoryRender, scrollCallback) => {
    if (!txt || !txt.trim()) return;
    // The same blocks the live stream builds (shared/prose.js): a <p> per
    // paragraph, code as its own <pre> beside them, never inside one.
    for (const b of proseBlocks(txt)) {
        if (b.type === 'code') { el.appendChild(createCodeBlock(b.lang, b.code)); continue; }
        const p = el.appendChild(createElem('p'));   // in place first: fillParagraph reads its neighbour
        fillParagraph(p, b.text, { isHistoryRender, scrollCallback });
    }
};

// One paragraph into its <p>: markdown, and <<IMG::id>> markers as real image
// elements (placed as nodes, so their load/retry listeners live). History and
// the live stream both fill paragraphs here. `images: false` leaves markers
// out: the stream's still-open paragraph re-fills every chunk, and an image
// made each time would re-fetch and re-clone (core/events.js) on every one.
export const fillParagraph = (p, text, { isHistoryRender = false, scrollCallback = null, images = true } = {}) => {
    const { processedContent, images: found } = Images.extractImagePlaceholders(dedent(text));
    let html = processMarkdown(processedContent);
    found.forEach(({ placeholder }, i) => {
        html = html.replace(placeholder, images ? `<span data-img-slot="${i}"></span>` : '');
    });
    p.innerHTML = html;
    p.querySelectorAll('span[data-img-slot]').forEach(slot => {
        slot.replaceWith(Images.createImageElement(found[+slot.dataset.imgSlot].imageId, isHistoryRender, scrollCallback));
    });
    continueLooseList(p);
};

// A loose list a model numbers "1." every item (blank lines between) is ONE
// list in markdown; split into paragraphs it read 1, 1, 1. A list-only
// paragraph that starts at 1 right after another list-only paragraph carries
// its numbering on. An explicit "2." (a start attribute) is left alone.
const listOnly = (p) => p?.tagName === 'P' && p.childElementCount === 1
    && p.firstElementChild.tagName === 'OL' && p.textContent.trim() === p.firstElementChild.textContent.trim();
const continueLooseList = (p) => {
    const prev = p.previousElementSibling;
    if (!listOnly(p) || !listOnly(prev)) return;
    const ol = p.firstElementChild, before = prev.firstElementChild;
    if (ol.hasAttribute('start')) return;
    ol.start = (before.start || 1) + before.children.length;
};