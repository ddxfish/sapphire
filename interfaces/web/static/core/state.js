// core/state.js - Application state, DOM refs, initialization
import * as chat from '../chat.js';
import * as audio from '../audio.js';

// DOM Elements - initialized via initElements()
let elements = null;

export function initElements() {
    elements = {
        form: document.getElementById('chat-form'),
        input: document.getElementById('prompt-input'),
        sendBtn: document.getElementById('send-btn'),
        stopBtn: document.getElementById('stop-btn'),
        micBtn: document.getElementById('mic-btn'),
        container: document.getElementById('chat-container'),
        chatSelect: document.getElementById('chat-select'),
        clearChatBtn: document.getElementById('clear-chat-btn'),
        importChatBtn: document.getElementById('import-chat-btn'),
        exportChatBtn: document.getElementById('export-chat-btn'),
        importFileInput: document.getElementById('import-file-input'),
        muteBtn: document.getElementById('mute-btn'),
        volumeSlider: document.getElementById('volume-slider'),
        chatMenu: document.getElementById('chat-menu')
    };
}

export function getElements() {
    return elements;
}

// Application state
let histLen = 0;
let isProc = false;
let currentAbortController = null;
let isCancelling = false;
let ttsEnabled = true;
let sttEnabled = true;
let sttReady = true;
let promptPrivacyRequired = false;
let convMode = { enabled: false, source: null };   // written by features/convo.js only

// State getters/setters
export const getHistLen = () => histLen;
export const setHistLen = (val) => { histLen = val; };
export const getIsProc = () => isProc;
export const getTtsEnabled = () => ttsEnabled;
export const setTtsEnabled = (val) => { ttsEnabled = val; };
export const getSttEnabled = () => sttEnabled;
export const setSttEnabled = (val) => { sttEnabled = val; };
export const getSttReady = () => sttReady;
export const setSttReady = (val) => { sttReady = val; };
export const getAbortController = () => currentAbortController;
export const setAbortController = (ctrl) => { currentAbortController = ctrl; };
export const getIsCancelling = () => isCancelling;
export const setIsCancelling = (val) => { isCancelling = val; };
export const getPromptPrivacyRequired = () => promptPrivacyRequired;
export const setPromptPrivacyRequired = (val) => { promptPrivacyRequired = val; };
export const getConvo = () => convMode;
export const setConvo = (v) => { convMode = { enabled: !!v?.enabled, source: v?.source || null }; };

// The only writer of #send-btn's label. 'send' carries both a word and a
// glyph; CSS picks one per viewport (mobile shows the glyph).
export function setSendLabel(kind) {
    const btn = elements?.sendBtn || document.getElementById('send-btn');
    if (!btn) return;
    if (kind === 'send') btn.innerHTML = '<span class="btn-txt">Send</span><span class="btn-ico" aria-hidden="true">\u27A4</span>';
    else btn.textContent = kind === 'boot' ? '\u23F3' : '...';
}

export function setProc(proc) {
    isProc = proc;
    const { sendBtn, stopBtn } = elements;
    if (proc) {
        sendBtn.style.display = 'none';
        stopBtn.style.display = 'block';
    } else {
        sendBtn.style.display = 'block';
        stopBtn.style.display = 'none';
        currentAbortController = null;
        isCancelling = false;
    }
}

export async function refresh(playAudio = false) {
    const audioFn = ttsEnabled ? audio.playText : null;
    const { len } = await chat.fetchAndRender(playAudio, audioFn, histLen);
    if (len !== undefined) histLen = len;
    return len;
}

