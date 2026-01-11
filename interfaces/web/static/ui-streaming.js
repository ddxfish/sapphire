// ui-streaming.js - Real-time streaming with typed SSE events

import { createAccordion } from './ui-parsing.js';

// Streaming state
let streamMsg = null;
let streamContent = '';
let state = {
    inThink: false, thinkBuf: '', thinkCnt: 0, thinkType: null, thinkAcc: null,
    curPara: null, procIdx: 0,
    toolAccordions: {}
};

const createElem = (tag, attrs = {}, content = '') => {
    const el = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => k === 'style' ? el.style.cssText = v : el.setAttribute(k, v));
    if (content) el.textContent = content;
    return el;
};

const resetState = (para = null) => {
    state = {
        inThink: false, thinkBuf: '', thinkCnt: 0, thinkType: null, thinkAcc: null,
        curPara: para, procIdx: 0,
        toolAccordions: {}
    };
};

// Create a tool accordion with loading state
const createToolAccordionElement = (toolName, toolId, args) => {
    const details = createElem('details');
    details.className = 'accordion-tool loading';
    details.dataset.toolId = toolId;
    details.open = false;
    
    const summary = createElem('summary');
    summary.innerHTML = `<span class="tool-spinner"></span> Running: ${toolName}`;
    
    const contentDiv = createElem('div');
    if (args && Object.keys(args).length > 0) {
        try {
            contentDiv.textContent = 'Inputs:\n' + JSON.stringify(args, null, 2);
        } catch {
            contentDiv.textContent = 'Running...';
        }
    } else {
        contentDiv.textContent = 'Running...';
    }
    
    details.appendChild(summary);
    details.appendChild(contentDiv);
    
    return { acc: details, content: contentDiv, summary, toolName };
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
    
    console.log('[STREAM] startStreaming complete, streamMsg set');
    return contentDiv;
};

// Check if streaming is ready
export const isStreamReady = () => streamMsg !== null;

// Handle content text (with think tag parsing)
export const appendStream = (chunk, scrollCallback) => {
    if (!streamMsg) {
        console.warn('[STREAM] appendStream called but streamMsg is null');
        return;
    }
    streamContent += chunk;
    
    const newContent = streamContent.slice(state.procIdx);
    let i = 0;
    
    while (i < newContent.length) {
        if (!state.inThink) {
            const thinkPos = newContent.indexOf('<think>', i);
            const seedPos = newContent.indexOf('<seed:think>', i);
            
            const markers = [
                [thinkPos, 'think', 7], 
                [seedPos, 'seed:think', 12]
            ].filter(m => m[0] !== -1).sort((a, b) => a[0] - b[0]);
            
            if (markers.length === 0) {
                let add = newContent.slice(i);
                if (state.curPara.textContent === '') add = add.replace(/^\s+/, '');
                state.curPara.textContent += add;
                i = newContent.length;
                break;
            }
            
            const [pos, type, len] = markers[0];
            let add = newContent.slice(i, pos);
            if (add && state.curPara.textContent === '') add = add.replace(/^\s+/, '');
            if (add) state.curPara.textContent += add;
            
            state.inThink = true;
            state.thinkCnt++;
            state.thinkBuf = '';
            state.thinkType = type;
            
            const label = type === 'seed:think' ? 'Seed Think' : 'Think';
            const { acc, content } = createAccordion('think', `${label} (Step ${state.thinkCnt})`, '');
            state.thinkAcc = content;
            
            if (streamMsg.last.nextSibling) {
                streamMsg.el.insertBefore(acc, streamMsg.last.nextSibling);
            } else {
                streamMsg.el.appendChild(acc);
            }
            streamMsg.last = acc;
            i = pos + len;
        } else {
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
            
            state.inThink = false;
            state.thinkAcc = null;
            state.thinkType = null;
            
            const newP = createElem('p');
            streamMsg.el.appendChild(newP);
            state.curPara = newP;
            
            i = endPos + endTag.length;
            while (i < newContent.length && /\s/.test(newContent[i])) i++;
        }
    }
    
    state.procIdx += i;
    if (scrollCallback) scrollCallback();
};

// Handle tool_start event
export const startTool = (toolId, toolName, args, scrollCallback) => {
    console.log(`[STREAM] startTool called: ${toolName} (${toolId}), streamMsg=${!!streamMsg}`);
    
    if (!streamMsg) {
        console.error('[STREAM] startTool failed: streamMsg is null!');
        return false;
    }
    
    // Clean up empty current paragraph
    if (state.curPara && !state.curPara.textContent.trim()) {
        state.curPara.remove();
    }
    
    const { acc, content, summary, toolName: name } = createToolAccordionElement(toolName, toolId, args);
    state.toolAccordions[toolId] = { acc, content, summary, toolName };
    
    streamMsg.el.appendChild(acc);
    streamMsg.last = acc;
    
    // Create new paragraph for content after tool
    const newP = createElem('p');
    streamMsg.el.appendChild(newP);
    state.curPara = newP;
    
    console.log(`[STREAM] Tool accordion created for ${toolId}, total tracked: ${Object.keys(state.toolAccordions).length}`);
    
    if (scrollCallback) scrollCallback();
    return true;
};

// Handle tool_end event
export const endTool = (toolId, toolName, result, isError, scrollCallback) => {
    console.log(`[STREAM] endTool called: ${toolName} (${toolId}), tracked tools: ${Object.keys(state.toolAccordions).join(', ')}`);
    
    const toolData = state.toolAccordions[toolId];
    if (!toolData) {
        console.warn(`[STREAM] endTool: No accordion found for ${toolId}, creating fallback`);
        
        // Fallback: create accordion now if streaming is active
        if (streamMsg) {
            const { acc, content, summary } = createToolAccordionElement(toolName, toolId, {});
            acc.classList.remove('loading');
            if (isError) acc.classList.add('error');
            summary.innerHTML = `Tool Result: ${toolName}`;
            content.textContent = 'Result:\n' + result;
            
            // Insert before current paragraph
            if (state.curPara) {
                streamMsg.el.insertBefore(acc, state.curPara);
            } else {
                streamMsg.el.appendChild(acc);
            }
            streamMsg.last = acc;
            
            if (scrollCallback) scrollCallback();
        }
        return;
    }
    
    const { acc, content, summary, toolName: storedName } = toolData;
    
    // Remove loading state
    acc.classList.remove('loading');
    if (isError) {
        acc.classList.add('error');
    }
    
    // Update summary
    const displayName = storedName || toolName;
    summary.innerHTML = `Tool Result: ${displayName}`;
    
    // Update content with result
    const existingContent = content.textContent;
    if (existingContent && existingContent !== 'Running...') {
        content.textContent = existingContent + '\n\nResult:\n' + result;
    } else {
        content.textContent = 'Result:\n' + result;
    }
    
    if (scrollCallback) scrollCallback();
};

export const finishStreaming = (updateToolbarsCallback) => {
    console.log(`[STREAM] finishStreaming, tracked tools: ${Object.keys(state.toolAccordions).length}`);
    
    if (!streamMsg) return;
    
    const msg = document.getElementById('streaming-message');
    if (msg) {
        msg.removeAttribute('id');
        delete msg.dataset.streaming;
        
        const contentDiv = msg.querySelector('.message-content');
        
        // Clean up empty paragraphs
        contentDiv.querySelectorAll('p').forEach(p => {
            if (!p.textContent.trim()) p.remove();
        });
    }
    
    if (updateToolbarsCallback) updateToolbarsCallback();
    streamMsg = null;
    streamContent = '';
    resetState();
};

export const cancelStreaming = () => {
    const streamingMessage = document.getElementById('streaming-message');
    
    if (streamingMessage) {
        streamingMessage.remove();
        console.log('[CLEANUP] Removed streaming message from DOM');
    }
    
    streamMsg = null;
    streamContent = '';
    resetState();
};

export const isStreaming = () => {
    return streamMsg !== null;
};