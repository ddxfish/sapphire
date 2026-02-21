// ui-streaming.js - Real-time streaming with typed SSE events

import { createAccordion, createCodeBlock, processMarkdown, wrapImageGalleries, _createGalleryListing, _createCategoryGrid } from './ui-parsing.js';

// Streaming state
let streamMsg = null;
let streamContent = '';
let state = {
    inThink: false, thinkBuf: '', thinkCnt: 0, thinkType: null, thinkAcc: null, thinkAccEl: null,
    inCode: false, codeLang: '', codeBuf: '', codePre: null,
    curPara: null, paraBuf: '', procIdx: 0,
    toolAccordions: {}
};

// Queue for events that arrive before streaming is initialized
let pendingToolEvents = [];

const createElem = (tag, attrs = {}, content = '') => {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => k === 'style' ? el.style.cssText = v : el.setAttribute(k, v));
    if (content) el.textContent = content;
    return el;
};

const resetState = (para = null) => {
    state = {
        inThink: false, thinkBuf: '', thinkCnt: 0, thinkType: null, thinkAcc: null, thinkAccEl: null,
        inCode: false, codeLang: '', codeBuf: '', codePre: null,
        curPara: para, paraBuf: '', procIdx: 0,
        toolAccordions: {}
    };
    pendingToolEvents = [];
};

// Update paragraph with markdown rendering
const updatePara = () => {
    if (!state.curPara || !state.paraBuf) return;
    state.curPara.innerHTML = processMarkdown(state.paraBuf);
};

// Create a tool accordion with loading state
const createToolAccordionElement = (toolName, toolId, args) => {
    const details = createElem('details');
    details.className = 'accordion-tool loading';
    details.dataset.toolId = toolId;
    details.open = false;
    
    const summary = createElem('summary');
    summary.innerHTML = `<span class="tool-spinner"></span> Running: ${toolName}`;
    
    // Add delete button (skip for pending placeholders)
    if (toolId && !String(toolId).startsWith('pending-')) {
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
                await removeToolCall(toolId);
                details.remove();
            } catch (err) {
                console.error('Failed to remove tool call:', err);
                const { showToast } = await import('./ui.js');
                showToast('Failed to remove tool call', 'error');
            }
        });
        
        summary.appendChild(deleteBtn);
    }
    
    const wrapper = createElem('div');
    wrapper.className = 'accordion-body';
    const contentDiv = createElem('div');
    contentDiv.className = 'accordion-inner';
    
    if (args && Object.keys(args).length > 0) {
        try {
            contentDiv.textContent = 'Inputs:\n' + JSON.stringify(args, null, 2);
        } catch {
            contentDiv.textContent = 'Running...';
        }
    } else {
        contentDiv.textContent = 'Running...';
    }
    
    wrapper.appendChild(contentDiv);
    details.appendChild(summary);
    details.appendChild(wrapper);
    
    return { acc: details, content: contentDiv, summary, toolName };
};

// Process any queued tool events
const processPendingToolEvents = (scrollCallback) => {
    if (!streamMsg || pendingToolEvents.length === 0) return;
    
    const events = [...pendingToolEvents];
    pendingToolEvents = [];
    
    for (const event of events) {
        if (event.type === 'start') {
            doStartTool(event.toolId, event.toolName, event.args, scrollCallback);
        } else if (event.type === 'end') {
            doEndTool(event.toolId, event.toolName, event.result, event.isError, scrollCallback);
        }
    }
};

export const startStreaming = (container, messageElement, scrollCallback) => {
    const contentDiv = messageElement.querySelector('.message-content');
    const p = createElem('p');
    contentDiv.appendChild(p);
    
    const existingThinks = container.querySelectorAll('details summary');
    const thinkCount = Array.from(existingThinks).filter(s => s.textContent.includes('Think')).length;
    
    streamMsg = { el: contentDiv, para: p, last: p };
    streamContent = '';
    resetState(p);
    state.thinkCnt = thinkCount;
    
    container.appendChild(messageElement);
    if (scrollCallback) scrollCallback(true);
    
    // Process any tool events that arrived before streaming started
    processPendingToolEvents(scrollCallback);
    
    return contentDiv;
};

// Check if streaming is ready
export const isStreamReady = () => streamMsg !== null;

// Create streaming code block preview (unhighlighted)
const createCodePreview = (lang) => {
    const pre = createElem('pre');
    pre.className = 'streaming-code';
    
    // Add header if language specified
    if (lang && lang !== 'plaintext') {
        const header = document.createElement('div');
        header.className = 'code-block-header';
        header.innerHTML = `<span class="code-lang">${lang}</span><span class="code-status">...</span>`;
        pre.appendChild(header);
    }
    
    const code = createElem('code');
    code.className = `language-${lang || 'plaintext'}`;
    pre.appendChild(code);
    
    return { pre, code };
};

// Update streaming code block content
const updateCodePreview = () => {
    if (!state.codePre) return;
    const code = state.codePre.querySelector('code');
    if (code) code.textContent = state.codeBuf;
};

// Finalize code block with syntax highlighting
const finalizeCodeBlock = () => {
    if (!state.codePre) return;
    
    const code = state.codePre.querySelector('code');
    if (code && window.hljs) {
        try {
            window.hljs.highlightElement(code);
        } catch (e) { /* ignore */ }
    }
    
    // Update header status to copy button
    const header = state.codePre.querySelector('.code-block-header');
    if (header) {
        const status = header.querySelector('.code-status');
        if (status) {
            const codeText = state.codeBuf.trimEnd();
            const copyBtn = document.createElement('button');
            copyBtn.className = 'code-copy';
            copyBtn.title = 'Copy code';
            copyBtn.textContent = 'Copy';
            copyBtn.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(codeText);
                    copyBtn.textContent = 'Copied!';
                    setTimeout(() => copyBtn.textContent = 'Copy', 2000);
                } catch (e) {
                    copyBtn.textContent = 'Failed';
                    setTimeout(() => copyBtn.textContent = 'Copy', 2000);
                }
            });
            status.replaceWith(copyBtn);
        }
    }
    
    state.codePre.classList.remove('streaming-code');
};

// Render completed code block
const renderCodeBlock = () => {
    if (!streamMsg) return;
    
    // Finalize the streaming preview
    finalizeCodeBlock();
    
    // Create new paragraph for content after code
    const newP = createElem('p');
    streamMsg.el.appendChild(newP);
    state.curPara = newP;
    state.paraBuf = '';
    
    // Reset code state
    state.inCode = false;
    state.codeLang = '';
    state.codeBuf = '';
    state.codePre = null;
};

// Handle content text (with think tag and code fence parsing)
export const appendStream = (chunk, scrollCallback) => {
    if (!streamMsg) return;
    streamContent += chunk;
    
    const newContent = streamContent.slice(state.procIdx);
    let i = 0;
    
    while (i < newContent.length) {
        // Inside code fence
        if (state.inCode) {
            const closePos = newContent.indexOf('```', i);
            if (closePos === -1) {
                // No closing fence yet, buffer and update preview
                state.codeBuf += newContent.slice(i);
                updateCodePreview();
                i = newContent.length;
                break;
            }
            // Found closing fence
            state.codeBuf += newContent.slice(i, closePos);
            updateCodePreview();
            renderCodeBlock();
            i = closePos + 3;
            // Skip trailing newline after code block
            if (i < newContent.length && newContent[i] === '\n') i++;
            continue;
        }
        
        // Inside think block
        if (state.inThink) {
            let endPos = -1;
            let endTag = '';
            
            if (state.thinkType === 'seed:think') {
                const ends = [
                    [newContent.indexOf('</seed:think>', i), '</seed:think>'],
                    [newContent.indexOf('</think>', i), '</think>'],
                    [newContent.indexOf('</seed:cot_budget_reflect>', i), '</seed:cot_budget_reflect>']
                ].filter(e => e[0] !== -1).sort((a, b) => a[0] - b[0]);
                if (ends.length > 0) [endPos, endTag] = ends[0];
            } else {
                endPos = newContent.indexOf('</think>', i);
                endTag = '</think>';
            }
            
            if (endPos === -1) {
                state.thinkBuf += newContent.slice(i);
                if (state.thinkAcc) state.thinkAcc.textContent = state.thinkBuf;
                i = newContent.length;
                break;
            }
            
            state.thinkBuf += newContent.slice(i, endPos);
            if (state.thinkAcc) state.thinkAcc.textContent = state.thinkBuf;

            // GLM quirk: <think>A</think>B</think> - premature close
            // If another </think> follows without a <think>, skip this close
            const afterClose = newContent.slice(endPos + endTag.length);
            const nextCloseIdx = afterClose.search(/<\/(?:seed:think|think)>/);
            const nextOpenIdx = afterClose.search(/<(?:seed:)?think>/);
            if (nextCloseIdx !== -1 && (nextOpenIdx === -1 || nextCloseIdx < nextOpenIdx)) {
                i = endPos + endTag.length;
                state.thinkBuf += '\n';
                continue;
            }

            // Remove streaming class when think block completes
            if (state.thinkAccEl) state.thinkAccEl.classList.remove('streaming');
            
            state.inThink = false;
            state.thinkAcc = null;
            state.thinkAccEl = null;
            state.thinkType = null;
            
            const newP = createElem('p');
            streamMsg.el.appendChild(newP);
            state.curPara = newP;
            state.paraBuf = '';
            
            i = endPos + endTag.length;
            while (i < newContent.length && /\s/.test(newContent[i])) i++;
            continue;
        }
        
        // Normal content - look for code fence, think tags
        const codePos = newContent.indexOf('```', i);
        const thinkPos = newContent.indexOf('<think>', i);
        const seedPos = newContent.indexOf('<seed:think>', i);
        
        // Find earliest marker
        const markers = [
            [codePos, 'code', 3],
            [thinkPos, 'think', 7],
            [seedPos, 'seed:think', 12]
        ].filter(m => m[0] !== -1).sort((a, b) => a[0] - b[0]);
        
        if (markers.length === 0) {
            // No markers, add all remaining content
            let add = newContent.slice(i);
            if (state.paraBuf === '') add = add.replace(/^\s+/, '');
            state.paraBuf += add;
            updatePara();
            i = newContent.length;
            break;
        }
        
        const [pos, type, len] = markers[0];
        
        // Add content before marker
        let add = newContent.slice(i, pos);
        if (add && state.paraBuf === '') add = add.replace(/^\s+/, '');
        if (add) {
            state.paraBuf += add;
            updatePara();
        }
        
        if (type === 'code') {
            // Find end of opening line to get language
            const lineEnd = newContent.indexOf('\n', pos + 3);
            if (lineEnd === -1) {
                // Opening line incomplete, wait for newline
                // Advance i to marker position so we don't reprocess content before it
                i = pos;
                break;
            }
            
            // Start code fence - extract language
            state.inCode = true;
            state.codeBuf = '';
            state.codeLang = newContent.slice(pos + 3, lineEnd).trim();
            i = lineEnd + 1;
            
            // Remove empty paragraph and create code preview
            if (state.curPara && !state.paraBuf.trim()) {
                state.curPara.remove();
            }
            
            const { pre, code } = createCodePreview(state.codeLang);
            state.codePre = pre;
            streamMsg.el.appendChild(pre);
            streamMsg.last = pre;
        } else {
            // Think tag - hide "Generating..." status since we now have visible output
            import('./ui.js').then(ui => ui.hideStatus());
            
            state.inThink = true;
            state.thinkCnt++;
            state.thinkBuf = '';
            state.thinkType = type;
            
            const label = type === 'seed:think' ? 'Seed Think' : 'Think';
            const { acc, content } = createAccordion('think', `${label} (Step ${state.thinkCnt})`, '');
            acc.classList.add('streaming');
            state.thinkAcc = content;
            state.thinkAccEl = acc;
            
            if (streamMsg.last.nextSibling) {
                streamMsg.el.insertBefore(acc, streamMsg.last.nextSibling);
            } else {
                streamMsg.el.appendChild(acc);
            }
            streamMsg.last = acc;
            i = pos + len;
        }
    }
    
    state.procIdx += i;
    if (scrollCallback) scrollCallback();
};

// Internal function to actually create tool accordion
const doStartTool = (toolId, toolName, args, scrollCallback) => {
    const isPending = toolId.startsWith('pending-');

    // Upgrade: real tool_start arrived for an existing pending accordion
    if (!isPending) {
        const pendingKey = Object.keys(state.toolAccordions).find(
            k => k.startsWith('pending-') && state.toolAccordions[k].toolName === toolName
        );
        if (pendingKey) {
            const existing = state.toolAccordions[pendingKey];
            delete state.toolAccordions[pendingKey];
            existing.acc.dataset.toolId = toolId;
            existing.summary.innerHTML = `<span class="tool-spinner"></span> Running: ${toolName}`;
            // Add delete button now that we have the real tool call id
            const deleteBtn = createElem('button', {
                class: 'tool-delete-btn',
                title: 'Remove from history'
            }, '\u00d7');
            deleteBtn.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!confirm('Remove this tool call from history?')) return;
                try {
                    const { removeToolCall } = await import('./api.js');
                    await removeToolCall(toolId);
                    existing.acc.remove();
                } catch (err) {
                    console.error('Failed to remove tool call:', err);
                    const { showToast } = await import('./ui.js');
                    showToast('Failed to remove tool call', 'error');
                }
            });
            existing.summary.appendChild(deleteBtn);
            if (args && Object.keys(args).length > 0) {
                try {
                    existing.content.textContent = 'Inputs:\n' + JSON.stringify(args, null, 2);
                } catch { /* keep existing text */ }
            }
            state.toolAccordions[toolId] = existing;
            if (scrollCallback) scrollCallback();
            return;
        }
    }

    // Clean up empty current paragraph
    if (state.curPara && !state.paraBuf.trim()) {
        state.curPara.remove();
    }

    const { acc, content, summary, toolName: name } = createToolAccordionElement(toolName, toolId, args);

    // Pending tools show "Preparing..." instead of "Running..."
    if (isPending) {
        summary.innerHTML = `<span class="tool-spinner"></span> Preparing: ${toolName}`;
        content.textContent = 'Assembling arguments...';
    }

    state.toolAccordions[toolId] = { acc, content, summary, toolName };

    streamMsg.el.appendChild(acc);
    streamMsg.last = acc;

    // Create new paragraph for content after tool
    const newP = createElem('p');
    streamMsg.el.appendChild(newP);
    state.curPara = newP;
    state.paraBuf = '';

    if (scrollCallback) scrollCallback();
};

// Handle tool_start event
export const startTool = (toolId, toolName, args, scrollCallback) => {
    if (!streamMsg) {
        // Queue the event for later processing
        pendingToolEvents.push({ type: 'start', toolId, toolName, args });
        return false;
    }
    
    doStartTool(toolId, toolName, args, scrollCallback);
    return true;
};

// Internal function to actually update tool accordion
const doEndTool = (toolId, toolName, result, isError, scrollCallback) => {
    let toolData = state.toolAccordions[toolId];

    if (!toolData) {
        // Fallback: create accordion now
        if (!streamMsg) {
            if (scrollCallback) scrollCallback();
            return;
        }
        const { acc, content, summary } = createToolAccordionElement(toolName, toolId, {});
        acc.classList.remove('loading');
        if (isError) acc.classList.add('error');
        summary.innerHTML = `Tool Result: ${toolName}`;
        content.textContent = 'Result:\n' + result;

        if (state.curPara) {
            streamMsg.el.insertBefore(acc, state.curPara);
        } else {
            streamMsg.el.appendChild(acc);
        }
        streamMsg.last = acc;
        toolData = { acc, content, summary, toolName };
        state.toolAccordions[toolId] = toolData;
    } else {
        const { acc, content, summary, toolName: storedName } = toolData;
        acc.classList.remove('loading');
        if (isError) acc.classList.add('error');
        summary.innerHTML = `Tool Result: ${storedName || toolName}`;

        const existingContent = content.textContent;
        if (existingContent && existingContent !== 'Running...') {
            content.textContent = existingContent + '\n\nResult:\n' + result;
        } else {
            content.textContent = 'Result:\n' + result;
        }
    }

    // Auto-inject from tool results (marker-based, works with any tool)
    if (!isError && streamMsg) {
        const galleryMatch = result.match(/<!--GALLERY:(\[.*\])-->/s);
        if (galleryMatch) {
            try {
                const imgUrls = JSON.parse(galleryMatch[1]);
                if (imgUrls.length > 0) {
                    const gallery = document.createElement('div');
                    gallery.className = 'image-gallery';
                    for (const url of imgUrls) {
                        const item = document.createElement('div');
                        item.className = 'gallery-item';
                        const img = document.createElement('img');
                        img.src = url;
                        img.className = 'chat-img';
                        item.appendChild(img);
                        gallery.appendChild(item);
                    }
                    toolData.acc.after(gallery);
                }
            } catch (e) {
                console.warn('[Gallery] Failed to parse gallery data:', e);
            }
        }

        const listMatch = result.match(/<!--GALLERIES:(\[.*\])-->/s);
        if (listMatch) {
            try {
                const listing = _createGalleryListing(JSON.parse(listMatch[1]));
                if (listing) toolData.acc.after(listing);
            } catch (e) {
                console.warn('[Gallery] Failed to parse gallery listing:', e);
            }
        }

        const catMatch = result.match(/<!--CATEGORIES:(\[.*\])-->/s);
        if (catMatch) {
            try {
                const grid = _createCategoryGrid(JSON.parse(catMatch[1]));
                if (grid) toolData.acc.after(grid);
            } catch (e) {
                console.warn('[Gallery] Failed to parse category data:', e);
            }
        }
    }

    if (scrollCallback) scrollCallback();
};

// Handle tool_end event
export const endTool = (toolId, toolName, result, isError, scrollCallback) => {
    if (!streamMsg) {
        // Queue the event for later processing
        pendingToolEvents.push({ type: 'end', toolId, toolName, result, isError });
        return;
    }
    
    doEndTool(toolId, toolName, result, isError, scrollCallback);
};

export const finishStreaming = (updateToolbarsCallback) => {
    if (!streamMsg) return;
    
    const msg = document.getElementById('streaming-message');
    if (msg) {
        msg.removeAttribute('id');
        delete msg.dataset.streaming;
        
        const contentDiv = msg.querySelector('.message-content');
        
        // Clean up empty paragraphs
        contentDiv.querySelectorAll('p').forEach(p => {
            if (!p.textContent.trim() && !p.innerHTML.trim()) p.remove();
        });
        
        // Remove streaming class from any think accordions that didn't complete
        contentDiv.querySelectorAll('.accordion-think.streaming').forEach(acc => {
            acc.classList.remove('streaming');
        });

        // Remove orphaned tool accordions that never completed (e.g. exceeded MAX_PARALLEL_TOOLS)
        contentDiv.querySelectorAll('.accordion-tool.loading').forEach(acc => {
            acc.remove();
        });

        // Wrap consecutive images into galleries
        wrapImageGalleries(contentDiv);
    }
    
    if (updateToolbarsCallback) updateToolbarsCallback();
    streamMsg = null;
    streamContent = '';
    resetState();
};

export const cancelStreaming = () => {
    const streamingMessage = document.getElementById('streaming-message');
    if (streamingMessage) streamingMessage.remove();
    
    streamMsg = null;
    streamContent = '';
    resetState();
};

export const isStreaming = () => {
    return streamMsg !== null;
};

export const hasVisibleContent = () => {
    if (!streamMsg) return false;
    // Check if paragraph buffer has content
    if (state.paraBuf.trim().length > 0) return true;
    // Check if any <p> elements have visible text (not in accordions)
    const paragraphs = streamMsg.el.querySelectorAll('p');
    for (const p of paragraphs) {
        if (p.textContent.trim().length > 0) return true;
    }
    return false;
};