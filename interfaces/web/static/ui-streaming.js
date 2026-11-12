// ui-streaming.js - Real-time streaming with typed SSE events

import { createAccordion, fillParagraph, buildToolResult, wrapImageGalleries } from './ui-parsing.js';
import { rollParagraphs, nextFence, fenceInfoOk, fenceLang } from './shared/prose.js';
import { nextThinkCloser, nextThinkOpener, isSeedThink } from './shared/think.js';
import { errWords } from './shared/fetch.js';

// A tool name comes from the model's own output (a prompt-injected model can
// name a tool `<img onerror=…>`), so it is text, never markup (hunt 19).
function setToolSummary(summary, label, name, spinner = false) {
    const parts = [];
    if (spinner) { const sp = document.createElement('span'); sp.className = 'tool-spinner'; parts.push(sp); }
    parts.push(document.createTextNode(`${spinner ? ' ' : ''}${label}: ${name}`));
    summary.replaceChildren(...parts);
}

// Streaming state
let streamMsg = null;
let streamContent = '';
// Monotonic id bumped on every startStreaming / cancelStreaming. Lets the
// SSE reader in api.js drop chunks that belong to a stream the UI already
// abandoned. Without this, Stop→immediate-Send can land OLD stream's
// trailing chunks (still in the SSE buffer between abort and rejection)
// in the NEW message's DOM. 2026-05-14.
let _streamId = 0;
export const getCurrentStreamId = () => _streamId;
let state = {
    inThink: false, thinkBuf: '', thinkCnt: 0, thinkType: null, thinkAcc: null, thinkAccEl: null,
    inCode: false, codeFence: '', codeLang: '', codeBuf: '', codePre: null,
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
        inCode: false, codeFence: '', codeLang: '', codeBuf: '', codePre: null,
        curPara: para, paraBuf: '', procIdx: 0,
        toolAccordions: {}
    };
    pendingToolEvents = [];
};

// The paragraph being written, built the way history builds it (shared/prose.js
// + fillParagraph): a blank line closes the <p> and opens the next, so the
// gaps are there as she writes, not only after the swap. paraBuf holds only
// the open paragraph's text.
const updatePara = () => {
    if (!state.curPara || !state.paraBuf) return;
    const { done, open } = rollParagraphs(state.paraBuf);
    for (const text of done) {
        fillParagraph(state.curPara, text);
        const next = createElem('p');
        state.curPara.after(next);
        state.curPara = next;
    }
    state.paraBuf = done.length ? open.trimStart() : open;
    fillParagraph(state.curPara, state.paraBuf, { images: false });
};

// The open paragraph is finished (a fence, think, tool or the end follows):
// its last fill, images and all.
const sealPara = () => {
    if (state.curPara && state.paraBuf.trim()) fillParagraph(state.curPara, state.paraBuf);
};

// A fence (```) or a think tag (<think>, </think>) can arrive split across
// chunks; a trailing piece that could still become one (1-2 backticks, or a
// `<` with the start of a tag name) is held back until the next chunk shows
// what it is. Before this, a token-by-token stream drew every fence as prose,
// and an inline-think model's split closer never closed its think block.
const heldTail = (s) => s.match(/(?:`{1,2}|~{1,2}|<\/?[A-Za-z:_]{0,24})$/)?.[0].length || 0;

// The stream ended: whatever was held back is the reply's last characters.
const flushHeld = () => {
    const rest = streamContent.slice(state.procIdx);
    if (!rest) return;
    if (state.inCode) { state.codeBuf += rest; updateCodePreview(); }
    else if (state.inThink) { state.thinkBuf += rest; if (state.thinkAcc) state.thinkAcc.textContent = state.thinkBuf; }
    else { state.paraBuf += rest; updatePara(); }
    state.procIdx = streamContent.length;
};

// A fence or a think still open ends with its round (a tool call) or with the
// stream, as history ends it at the part boundary: left open, the next round
// poured into the block ABOVE the tool, and a stopped reply kept a code block
// with no copy button and a "..." header.
const closeOpenBlocks = () => {
    if (state.inCode) {
        finalizeCodeBlock();
        Object.assign(state, { inCode: false, codeFence: '', codeLang: '', codeBuf: '', codePre: null });
    }
    if (state.inThink) {
        state.thinkAccEl?.classList.remove('streaming');
        Object.assign(state, { inThink: false, thinkAcc: null, thinkAccEl: null, thinkType: null });
    }
};

// Leave the current paragraph and open a fresh one at the end of the bubble.
const newPara = () => {
    sealPara();
    const p = createElem('p');
    streamMsg.el.appendChild(p);
    state.curPara = p;
    state.paraBuf = '';
};

// Create a tool accordion with loading state
const createToolAccordionElement = (toolName, toolId, args) => {
    const details = createElem('details');
    details.className = 'accordion-tool loading';
    details.dataset.toolId = toolId;
    details.open = false;
    
    const summary = createElem('summary');
    setToolSummary(summary, 'Running', toolName, true);
    
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
                showToast(`Couldn't remove tool call. ${errWords(err, 'Try again.')}`, 'error');
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

// `adopt` = { seed }: in-place Continue — the bubble is already on the page.
// The old metrics footer goes (the continuation writes new ones) and, when
// the reply ends in a plain paragraph, the new words keep writing INTO it:
// paraBuf is seeded with that paragraph's source text so the sentence
// carries on instead of restarting below.
export const startStreaming = (container, messageElement, scrollCallback, adopt = null) => {
    _streamId++;  // new stream — invalidates any in-flight chunks from a previous one
    const contentDiv = messageElement.querySelector('.message-content');
    let p = null, seeded = false;
    if (adopt) {
        // the footer and the notes about how the LAST attempt ended (cut off,
        // degraded, cancelled) go: the continuation writes its own, and the
        // turn-end redraw brings back whatever the server still says. Left in,
        // the "cut off" note was the last child, so the new words started a
        // fresh paragraph BELOW it - the most common reason to press Continue.
        contentDiv.querySelectorAll('.message-metadata, .row-note, .cancel-marker').forEach(n => n.remove());
        const last = contentDiv.lastElementChild;
        if (adopt.seed && last?.tagName === 'P' && !last.querySelector('img')) { p = last; seeded = true; }
    }
    if (!p) {
        p = createElem('p');
        contentDiv.appendChild(p);
    }

    // Steps count per MESSAGE, as history numbers them (ui-parsing renderThink);
    // counting the whole chat put "Think (Step 29)" on every live reply for two
    // years. A Continue carries on from the thinks already in its message.
    const thinkCount = contentDiv.querySelectorAll('details.accordion-think').length;

    streamMsg = { el: contentDiv, para: p };
    streamContent = '';
    resetState(p);
    state.thinkCnt = thinkCount;
    if (seeded) state.paraBuf = adopt.seed;

    if (!adopt) container.appendChild(messageElement);
    if (scrollCallback) scrollCallback(true);
    
    // Process any tool events that arrived before streaming started
    processPendingToolEvents(scrollCallback);
    
    return contentDiv;
};

// Check if streaming is ready
export const isStreamReady = () => streamMsg !== null;

const COPY_ICON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
const CHECK_ICON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>';

const escapeHtml = (text) => {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
};

// Create streaming code block preview (unhighlighted)
const createCodePreview = (lang) => {
    const pre = createElem('pre');
    pre.className = 'streaming-code';

    const header = document.createElement('div');
    header.className = 'code-block-header';
    const langText = (lang && lang !== 'plaintext') ? lang : '';
    header.innerHTML = `<span class="code-lang">${escapeHtml(langText)}</span><span class="code-status">...</span>`;
    pre.appendChild(header);

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
    if (code) code.textContent = state.codeBuf.trimEnd();   // history's code text (shared/prose.js trims it)
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
            copyBtn.setAttribute('aria-label', 'Copy code');
            copyBtn.innerHTML = COPY_ICON_SVG;
            copyBtn.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(codeText);
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
    newPara();   // content after the code

    // Reset code state
    state.inCode = false;
    state.codeLang = '';
    state.codeFence = '';
    state.codeBuf = '';
    state.codePre = null;
};

// Handle content text (with think tag and code fence parsing)
export const appendStream = (chunk, scrollCallback) => {
    if (!streamMsg) return;
    streamContent += String(chunk ?? '').replace(/\r/g, '');   // CRLF reads as LF, as history (shared/prose.js)
    
    const newContent = streamContent.slice(state.procIdx);
    let i = 0;
    
    while (i < newContent.length) {
        // Inside code fence
        if (state.inCode) {
            const closePos = newContent.indexOf(state.codeFence || '```', i);
            if (closePos === -1) {
                // No closing fence yet, buffer and update preview (a trailing
                // ` or `` waits: it may be the closer split across chunks)
                const rest = newContent.slice(i);
                const hold = heldTail(rest);
                state.codeBuf += rest.slice(0, rest.length - hold);
                updateCodePreview();
                i = newContent.length - hold;
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
            // The closer comes from shared/think.js (twin of core/think.py): any
            // close tag, and one typed in backticks is a word, not the end. Asked
            // of the whole stream so the character before the tag is in view.
            const closeHit = nextThinkCloser(streamContent, state.procIdx + i);
            const endPos = closeHit ? closeHit.index - state.procIdx : -1;
            const endTag = closeHit ? closeHit.text : '';
            
            if (endPos === -1) {
                // a trailing `</thi` waits: it may be the closer split across chunks
                const rest = newContent.slice(i);
                const hold = heldTail(rest);
                state.thinkBuf += rest.slice(0, rest.length - hold);
                if (state.thinkAcc) state.thinkAcc.textContent = state.thinkBuf;
                i = newContent.length - hold;
                break;
            }
            
            state.thinkBuf += newContent.slice(i, endPos);
            if (state.thinkAcc) state.thinkAcc.textContent = state.thinkBuf;

            // GLM quirk: <think>A</think>B</think> - premature close
            // If another </think> follows without a <think>, skip this close
            const afterClose = newContent.slice(endPos + endTag.length);
            const nextClose = nextThinkCloser(afterClose, 0);
            const nextOpen = nextThinkOpener(afterClose, 0);
            const nextCloseIdx = nextClose ? nextClose.index : -1;
            const nextOpenIdx = nextOpen ? nextOpen.index : -1;
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
            newPara();

            i = endPos + endTag.length;
            while (i < newContent.length && /\s/.test(newContent[i])) i++;
            continue;
        }
        
        // Normal content - look for code fence, think tags. A fence opens only
        // at a line start (shared/prose.js, the rule history draws by).
        const fence = nextFence(streamContent, state.procIdx + i);
        const codePos = fence ? fence.index - state.procIdx : -1;
        // An opener only where a block can start, never one typed in backticks
        // (shared/think.js). Asked of the whole stream for the same reason.
        const opener = nextThinkOpener(streamContent, state.procIdx + i);
        
        // Find earliest marker
        const markers = [
            [codePos, 'code', 3],
            [opener ? opener.index - state.procIdx : -1,
             opener && isSeedThink(opener.name) ? 'seed:think' : 'think',
             opener ? opener.text.length : 0]
        ].filter(m => m[0] !== -1).sort((a, b) => a[0] - b[0]);
        
        if (markers.length === 0) {
            // No markers, add all remaining content (a trailing ` or `` waits:
            // it may be an opening fence split across chunks)
            let add = newContent.slice(i);
            const hold = heldTail(add);
            add = add.slice(0, add.length - hold);
            if (state.paraBuf === '') add = add.replace(/^\s+/, '');
            state.paraBuf += add;
            updatePara();
            i = newContent.length - hold;
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
            
            // "```ls -la```" on one line is inline code, not a fence: its words
            // go to the paragraph and the search carries on past them
            const info = newContent.slice(pos + 3, lineEnd);
            if (!fenceInfoOk(info)) {
                state.paraBuf += newContent.slice(pos, lineEnd);
                updatePara();
                i = lineEnd;
                continue;
            }

            // Start code fence - its language is the info's first word
            state.inCode = true;
            state.codeFence = fence.mark;
            state.codeBuf = '';
            state.codeLang = fenceLang(info);
            i = lineEnd + 1;
            import('./ui.js').then(ui => ui.hideStatus());   // a reply that opens with code is visible output
            
            // Remove empty paragraph and create code preview
            if (state.curPara && !state.paraBuf.trim()) {
                state.curPara.remove();
            }
            
            const { pre, code } = createCodePreview(state.codeLang);
            state.codePre = pre;
            streamMsg.el.appendChild(pre);
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
            
            // In reading order, as history draws it: before the open paragraph
            // while it is still empty, after it once it holds words. (A `last`
            // cursor that only blocks moved put a think ABOVE prose written
            // since the last block, and between a tool and its picture.)
            sealPara();
            const open = state.curPara;
            if (open?.isConnected && !open.textContent.trim() && !open.querySelector('img')) open.before(acc);
            else streamMsg.el.appendChild(acc);
            i = pos + len;
        }
    }
    
    state.procIdx += i;
    if (scrollCallback) scrollCallback();
};

// Internal function to actually create tool accordion
const doStartTool = (toolId, toolName, args, scrollCallback) => {
    const isPending = toolId.startsWith('pending-');
    if (state.toolAccordions[toolId]) {
        if (!isPending) return;   // replayed tool_start (reattach) — already on screen
        // pending ids are `pending-<index>`, the same every round: one left by an
        // earlier round (its real tool_start never came) is stale, not this one
        state.toolAccordions[toolId].acc.remove();
        delete state.toolAccordions[toolId];
    }

    // Upgrade: real tool_start arrived for an existing pending accordion
    if (!isPending) {
        const pendingKey = Object.keys(state.toolAccordions).find(
            k => k.startsWith('pending-') && state.toolAccordions[k].toolName === toolName
        );
        if (pendingKey) {
            const existing = state.toolAccordions[pendingKey];
            delete state.toolAccordions[pendingKey];
            existing.acc.dataset.toolId = toolId;
            setToolSummary(existing.summary, 'Running', toolName, true);
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
                    showToast(`Couldn't remove tool call. ${errWords(err, 'Try again.')}`, 'error');
                }
            });
            existing.summary.appendChild(deleteBtn);
            if (args && Object.keys(args).length > 0) {
                try {
                    existing.content.textContent = 'Inputs:\n' + JSON.stringify(args, null, 2);
                } catch { /* keep existing text */ }
            }
            existing.args = args;
            state.toolAccordions[toolId] = existing;
            if (scrollCallback) scrollCallback();
            return;
        }
    }

    flushHeld();   // a held backtick belongs to the words before the tool
    closeOpenBlocks();
    // A tool call ends a round; the next round starts a block, as in history
    // where each round is its own part. Without the break, a round ending
    // "…the file is:" ran into the next round's <think>, which then sat
    // mid-sentence (shared/think.js rule 4) and drew her reasoning as prose.
    streamContent += '\n';

    // Clean up empty current paragraph
    if (state.curPara && !state.paraBuf.trim()) {
        state.curPara.remove();
    }

    const { acc, content, summary, toolName: name } = createToolAccordionElement(toolName, toolId, args);

    // Pending tools show "Preparing..." instead of "Running..."
    if (isPending) {
        setToolSummary(summary, 'Preparing', toolName, true);
        content.textContent = 'Assembling arguments...';
    }

    state.toolAccordions[toolId] = { acc, content, summary, toolName, args };

    streamMsg.el.appendChild(acc);
    newPara();   // content after the tool

    if (scrollCallback) scrollCallback();
};

// Handle tool_start event
export const startTool = (toolId, toolName, args, scrollCallback) => {
    // Dispatch for plugin scripts that need to react to tool execution
    document.dispatchEvent(new CustomEvent('sapphire:tool_start', {
        detail: { id: toolId, name: toolName, args }
    }));

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
    if (!streamMsg) {
        if (scrollCallback) scrollCallback();
        return;
    }
    const toolData = state.toolAccordions[toolId];
    // The finished result is built by THE tool-result renderer history uses
    // (ui-parsing buildToolResult): same text, truncation, images, widgets.
    // The live accordion (spinner, inputs) steps aside for it. The server sends
    // the browser's copy of the result, markers and all (core/ui_markers.for_browser).
    const nodes = buildToolResult({
        name: toolData?.toolName || toolName, tool_call_id: toolId,
        content: result, inputs: toolData?.args,
    }, isError);
    if (toolData) {
        nodes[0].open = toolData.acc.open;
        const old = toolData.nodes || [toolData.acc];      // a replayed tool_end replaces its own build
        // ...and its picture's inline clone (core/events.js), or a replay showed it twice
        const clone = old[old.length - 1].nextElementSibling;
        if (clone?.dataset?.inlineClone) clone.remove();
        old[0].replaceWith(...nodes);
        old.slice(1).forEach(n => n.remove());
    } else {
        // tool_end with no tool_start on screen: the result lands before the open paragraph
        if (state.curPara?.isConnected) state.curPara.before(...nodes);
        else streamMsg.el.append(...nodes);
    }
    state.toolAccordions[toolId] = { ...toolData, acc: nodes[0], nodes, toolName: toolData?.toolName || toolName };

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
    flushHeld();
    closeOpenBlocks();
    sealPara();

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
    _streamId++;  // any chunks still in the SSE pipeline are now stale
    if (streamMsg) { flushHeld(); closeOpenBlocks(); sealPara(); }
    // Don't delete the partial — finalize it like finishStreaming does, so the
    // user sees what was rendered up to the stop. The full message lands in
    // history once the backend finishes draining; F5 / refresh shows the
    // authoritative version. Without this, the partial vanishes and only
    // reappears after the chat reloads, which looks broken.
    const msg = document.getElementById('streaming-message');
    if (msg) {
        msg.removeAttribute('id');
        delete msg.dataset.streaming;
        msg.dataset.cancelled = 'true';

        const contentDiv = msg.querySelector('.message-content');
        if (contentDiv) {
            contentDiv.querySelectorAll('p').forEach(p => {
                if (!p.textContent.trim() && !p.innerHTML.trim()) p.remove();
            });
            contentDiv.querySelectorAll('.accordion-think.streaming').forEach(acc => {
                acc.classList.remove('streaming');
            });
            contentDiv.querySelectorAll('.accordion-tool.loading').forEach(acc => {
                acc.remove();
            });
            wrapImageGalleries(contentDiv);

            const marker = document.createElement('div');
            marker.className = 'cancel-marker';
            marker.textContent = '⊘ Cancelled';
            contentDiv.appendChild(marker);
        }
    }

    streamMsg = null;
    streamContent = '';
    resetState();
};

// The turn goes on server-side but THIS bubble stops being live — the tab
// lost its feed and either reattached past the ring horizon (resync) or the
// typing mirror is finalizing for it. Finalize what's rendered, no
// "cancelled" marker (nothing was cancelled); the end-of-turn refresh paints
// the saved row. 2026-09-15.
export const detachStreaming = () => {
    _streamId++;
    if (streamMsg) { flushHeld(); closeOpenBlocks(); sealPara(); }
    const msg = document.getElementById('streaming-message');
    if (msg) {
        msg.removeAttribute('id');
        delete msg.dataset.streaming;
        const contentDiv = msg.querySelector('.message-content');
        if (contentDiv) {
            contentDiv.querySelectorAll('.accordion-think.streaming').forEach(acc => acc.classList.remove('streaming'));
            contentDiv.querySelectorAll('.accordion-tool.loading').forEach(acc => acc.remove());
            wrapImageGalleries(contentDiv);
        }
    }
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