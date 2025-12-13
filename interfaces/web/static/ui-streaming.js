// ui-streaming.js - Real-time streaming state and content assembly

import * as Images from './ui-images.js';
import { createAccordion } from './ui-parsing.js';

// Streaming state
let streamMsg = null;
let streamContent = '';
let state = {
    inThink: false, thinkBuf: '', thinkCnt: 0, thinkType: null, thinkAcc: null,
    curPara: null, procIdx: 0
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
        curPara: para, procIdx: 0
    };
};

export const startStreaming = (container, messageElement, scrollCallback) => {
    const contentDiv = messageElement.querySelector('.message-content');
    const p = createElem('p');
    contentDiv.appendChild(p);
    
    // Count existing think accordions in the entire chat
    const existingThinks = container.querySelectorAll('details summary');
    const thinkCount = Array.from(existingThinks).filter(s => s.textContent.includes('Think')).length;
    
    streamMsg = { el: contentDiv, para: p, last: p };
    streamContent = '';
    resetState(p);
    state.thinkCnt = thinkCount;  // Start from existing count
    
    container.appendChild(messageElement);
    if (scrollCallback) scrollCallback(true);
    return contentDiv;
};

export const appendStream = (chunk, scrollCallback) => {
    if (!streamMsg) return;
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
                if (add.includes('⚙️ Running')) {
                    state.curPara.innerHTML += add.replace('⚙️', '<span class="tool-spinner"></span>');
                } else {
                    state.curPara.textContent += add;
                }
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
        } else if (state.inThink) {
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

export const finishStreaming = (updateToolbarsCallback) => {
    if (!streamMsg) return;
    
    // Remove streaming marker
    const msg = document.getElementById('streaming-message');
    if (msg) {
        msg.removeAttribute('id');
        delete msg.dataset.streaming;
        
        // Filter out early think prose if tools were called
        const contentDiv = msg.querySelector('.message-content');
        const hasTools = contentDiv.querySelectorAll('details summary').length > 0 &&
                         Array.from(contentDiv.querySelectorAll('details summary'))
                             .some(s => s.textContent.includes('Tool'));
        
        if (hasTools) {
            // Remove paragraphs that appear before first think/tool accordion
            const firstAccordion = contentDiv.querySelector('details');
            if (firstAccordion) {
                let node = contentDiv.firstChild;
                while (node && node !== firstAccordion) {
                    const next = node.nextSibling;
                    if (node.nodeName === 'P') {
                        node.remove();
                    }
                    node = next;
                }
            }
        }
    }
    
    if (updateToolbarsCallback) updateToolbarsCallback();
    streamMsg = null;
    streamContent = '';
    resetState();
};

export const cancelStreaming = () => {
    // With race condition prevented, we can rely on the streaming message ID
    const streamingMessage = document.getElementById('streaming-message');
    
    if (streamingMessage) {
        streamingMessage.remove();
        console.log('[CLEANUP] Removed streaming message from DOM');
    } else {
        console.log('[INFO] No streaming message found (already removed or never created)');
    }
    
    // Clean up state
    streamMsg = null;
    streamContent = '';
    resetState();
};

export const isStreaming = () => {
    return streamMsg !== null;
};