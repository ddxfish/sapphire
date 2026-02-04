// core/state.js - Application state, DOM refs, initialization
import * as chat from '../chat.js';
import * as audio from '../audio.js';
import PluginLoader from '../plugin-loader.js';

// DOM Elements - initialized via initElements()
let elements = null;

export function initElements() {
    elements = {
        form: document.getElementById('chat-form'),
        input: document.getElementById('prompt-input'),
        sendBtn: document.getElementById('send-btn'),
        stopBtn: document.getElementById('stop-btn'),
        toggleBtn: document.getElementById('toggle-sidebar'),
        micBtn: document.getElementById('mic-btn'),
        stopTtsBtn: document.getElementById('stop-tts-btn'),
        promptPill: document.getElementById('prompt-pill'),
        abilityPill: document.getElementById('ability-pill'),
        spiceIndicator: document.getElementById('spice-indicator'),
        storyIndicator: document.getElementById('story-indicator'),
        container: document.getElementById('chat-container'),
        chatSelect: document.getElementById('chat-select'),
        settingsModal: document.getElementById('settings-modal'),
        clearChatBtn: document.getElementById('clear-chat-btn'),
        importChatBtn: document.getElementById('import-chat-btn'),
        exportChatBtn: document.getElementById('export-chat-btn'),
        importFileInput: document.getElementById('import-file-input'),
        muteBtn: document.getElementById('mute-btn'),
        volumeSlider: document.getElementById('volume-slider'),
        appMenu: document.getElementById('app-menu'),
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
let promptPrivacyRequired = false;
let pluginLoader = null;
let avatarPlugin = null;

// State getters/setters
export const getHistLen = () => histLen;
export const setHistLen = (val) => { histLen = val; };
export const getIsProc = () => isProc;
export const getTtsEnabled = () => ttsEnabled;
export const setTtsEnabled = (val) => { ttsEnabled = val; };
export const getAbortController = () => currentAbortController;
export const setAbortController = (ctrl) => { currentAbortController = ctrl; };
export const getIsCancelling = () => isCancelling;
export const setIsCancelling = (val) => { isCancelling = val; };
export const getPromptPrivacyRequired = () => promptPrivacyRequired;
export const setPromptPrivacyRequired = (val) => { promptPrivacyRequired = val; };
export const getPluginLoader = () => pluginLoader;

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

// Avatar/Plugin initialization
// Optional initData param allows pre-seeding plugins config from /api/init
export async function initAvatar(initData = null) {
    pluginLoader = new PluginLoader('#sidebar-plugin-area');
    window.pluginLoader = pluginLoader;

    // Use plugins_config from init data if available (avoids separate fetch)
    if (initData?.plugins_config) {
        pluginLoader.setConfigFromInitData(initData.plugins_config);
    }

    await pluginLoader.loadPlugins();
    
    const assistantAvatar = document.querySelector('.sidebar .avatar');
    if (assistantAvatar) {
        assistantAvatar.addEventListener('click', () => audio.stop());
    }
    
    const plugin3d = pluginLoader.plugins.find(p => p.name === 'sapphire-3d');
    if (plugin3d) {
        avatarPlugin = plugin3d.instance;
        console.log('3D avatar plugin loaded');
        if (typeof avatarPlugin.onClick === 'function') {
            avatarPlugin.onClick(() => audio.stop());
        }
    } else {
        console.log('No 3D avatar plugin found, using standard plugins');
    }
}