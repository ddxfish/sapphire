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
    
    let txt = clone.textContent.trim();
    
    // Strip any remaining think tags
    txt = txt.replace(/<(?:seed:)?think>.*?<\/(?:seed:think|seed:cot_budget_reflect|think)>/gs, '');
    
    // Strip any HTML tags that leaked through
    txt = txt.replace(/<[^>]+>/g, '');
    
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

// Create a code block element with optional header and copy button
export const createCodeBlock = (language, code) => {
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
                if (!lm) break;
                listLines.push({ indent: lm[1].length, marker: lm[2], content: lm[3] });
                i++;
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
        let html = `<${tag}>`;
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
    html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img src="$2" alt="$1" style="max-width:100%">');
    
    // Links: [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    
    return html;
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
    const userImages = (typeof msg === 'object' && msg.images) ? msg.images : [];
    
    if (!txt && parts.length === 0 && userImages.length === 0) {
        el.textContent = '';
        return;
    }
    
    el.innerHTML = '';
    
    // Render user-attached images first (for user messages)
    if (userImages.length > 0) {
        const thumbs = Images.createUserImageThumbnails(userImages);
        el.appendChild(thumbs);
    }
    
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