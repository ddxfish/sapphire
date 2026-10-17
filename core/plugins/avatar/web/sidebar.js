// 3D Avatar — threejs GLTF with animation blending driven by SSE events
import * as eventBus from '/static/core/event-bus.js';
import * as audio from '/static/audio.js';
import { triggerSendWithText, handleStop } from '/static/handlers/send-handlers.js';
import { createEnvironment } from './environment.js';
import { createCameraOrbitSystem } from './camera-orbits.js';
import { createPlayerController } from './player-controller.js';
import { createMissileCommand } from './missile-command.js';

// Routed through Sapphire's /cdn-cache/ proxy — first request fetches from
// esm.sh and saves to user/cdn_cache/, subsequent serves from disk. ?bundle
// inlines all internal deps so the cached file is self-contained (otherwise
// internal esm.sh imports would still hit the CDN). 2026-05-13.
const THREE_CDN = '/cdn-cache/esm.sh/three@0.170.0?bundle&target=es2022';
const GLTF_CDN  = '/cdn-cache/esm.sh/three@0.170.0/addons/loaders/GLTFLoader.js?bundle&target=es2022&external=three';
const ORBIT_CDN = '/cdn-cache/esm.sh/three@0.170.0/addons/controls/OrbitControls.js?bundle&target=es2022&external=three';
const CROSSFADE_MS = 400;

// Hardcoded fallback defaults (used when no config exists). Keys must match
// state names that some entry in TRANSITIONS actually targets — anything else
// is dead. 'error' routes to toasts; 'wave' is the greeting (played by clip
// name directly, not via this map); 'thinking' had no event mapping.
const FALLBACK_TRACK_MAP = {
    idle: 'idle', listening: 'listening', processing: 'thinking',
    typing: 'defaultanim', speaking: 'attention',
    toolcall: 'attention2', wakeword: 'attention', happy: 'happy',
    agent: 'thinking', cron: 'thinking',
    user_typing: 'attention', reading: 'listening',
};
const FALLBACK_IDLE_POOL = [
    { track: 'idle', weight: 60, oneshot: false },
    { track: 'defaultanim', weight: 10, oneshot: false },  // typing on keyboard — idle variety
    { track: 'listening', weight: 10, oneshot: false },
    { track: 'attention', weight: 5, oneshot: true },
    { track: 'happy', weight: 4, oneshot: true },
    { track: 'wave', weight: 3, oneshot: true },
];
const FALLBACK_CAMERA = { x: 0, y: 1.3, z: 4.4 };
const FALLBACK_TARGET = { x: 0, y: 1.1, z: 0 };

// Event → state-name map, split by which rail the event writes.
//   `null` value means "clear that rail" (the picker then falls to whatever's
//   below it, e.g. rail 5 variety / rail 6 base). This replaces the old
//   force-to-'idle' transitions which had to compete with priorities.
// AI-initiated events express Sapph's internal state — they DO NOT trigger
// wind-down on rail 2 (her loop is undisturbed while her brain ticks).
const AI_TRANSITIONS = {
    [eventBus.Events.AI_TYPING_START]:           'typing',
    [eventBus.Events.AI_TYPING_END]:             'happy',
    [eventBus.Events.TTS_PLAYING]:               'speaking',
    [eventBus.Events.TTS_STOPPED]:               null,
    [eventBus.Events.TOOL_EXECUTING]:            'toolcall',
    [eventBus.Events.TOOL_COMPLETE]:             'typing',
    [eventBus.Events.AGENT_SPAWNED]:             'agent',
    [eventBus.Events.AGENT_COMPLETED]:           'happy',
    [eventBus.Events.AGENT_DISMISSED]:           null,
    [eventBus.Events.CONTINUITY_TASK_STARTING]:  'cron',
    [eventBus.Events.CONTINUITY_TASK_COMPLETE]:  null,
};
// User-initiated events express a human reaching for her. They request
// wind-down on rail 2 (her loop ends gracefully at next iteration). Values
// are { state, ttlMs? } — ttlMs auto-clears rail 4 after N ms when no
// natural follow-up event will (USER_SENT is a brief reaction with no
// matching end-event; STT events have explicit pairs, no TTL needed).
const USER_TRANSITIONS = {
    [eventBus.Events.STT_RECORDING_START]:  { state: 'listening' },
    [eventBus.Events.STT_RECORDING_END]:    { state: 'processing' },
    [eventBus.Events.STT_PROCESSING]:       { state: 'processing' },
    [eventBus.Events.WAKEWORD_DETECTED]:    { state: 'wakeword' },
    [eventBus.Events.USER_TYPING]:          { state: 'user_typing' },
    [eventBus.Events.USER_SENT]:            { state: 'reading', ttlMs: 3000 },
};

// Track cleanup between sidebar reloads
let _cleanup = null;
// Monotonic init token. Bumped on each init() entry. The setup body is
// async — without a token, two concurrent loadSidebar()/init() calls (chat
// switch + SPICE_CHANGED + PROMPT_DELETED fire in burst) both see _cleanup
// as null (the first hasn't assigned yet because its setup is mid-flight)
// and both run full WebGL + observer + RAF setup. The earlier one's
// resources get orphaned, leaking WebGL contexts toward the browser cap.
// Token check at end of init() lets only the latest entry win. 2026-05-14.
let _initToken = 0;

export async function init(container) {
    if (_cleanup) _cleanup();
    const myToken = ++_initToken;

    const canvas = container.querySelector('#avatar-canvas');
    const statusEl = container.querySelector('#avatar-status');
    if (!canvas) return;

    // Enabled switch (Krem 2026-08-23): one server-side flag gates BOTH the
    // scene (GPU) and the prompt instructions (hook). Off → nothing below
    // this block runs; on → a fresh init builds the scene.
    const displayBox = container.querySelector('#avatar-display');
    const enabledCb = container.querySelector('#avatar-enabled');
    const enabledNote = container.querySelector('#avatar-enabled-note');
    let enabled = true;
    try {
        const r = await fetch('/api/plugin/avatar/config');
        if (r.ok) enabled = (await r.json()).enabled !== false;
    } catch (_) { /* default on */ }
    if (myToken !== _initToken) return;
    const paintEnabled = (on) => {
        if (displayBox) displayBox.style.display = on ? '' : 'none';
        if (enabledCb) enabledCb.checked = on;
        if (enabledNote) enabledNote.style.display = on ? 'none' : '';
    };
    paintEnabled(enabled);
    if (enabledCb) enabledCb.onchange = async () => {
        const on = enabledCb.checked;
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
        try {
            await fetch('/api/plugin/avatar/config', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ enabled: on }),
            });
        } catch (_) { /* the switch still applies locally this session */ }
        if (on) { init(container); return; }      // rebuild the scene
        if (_cleanup) _cleanup();                 // renderer.dispose() — GPU back
        paintEnabled(false);
    };
    if (!enabled) return;

    // --- Display mode controls ---
    const displayEl = container.querySelector('#avatar-display');
    const btnExpand = container.querySelector('#avatar-btn-expand');
    const btnFullscreen = container.querySelector('#avatar-btn-fullscreen');
    let displayMode = 'sidebar';
    let _onDisplayModeChange = null;  // set after scene is ready
    let _isPlayerMode = () => false;   // set after player controller is created
    let _setPlayerMode = null;        // set after player controller is created
    let _stopGame = null;             // set after game is created

    function setDisplayMode(mode) {
        if (mode === displayMode) return;
        if (displayMode === 'fullscreen' && document.fullscreenElement) {
            document.exitFullscreen?.();
        }
        displayMode = mode;
        if (mode === 'sidebar') {
            displayEl.classList.remove('avatar-fullwindow');
            canvas.style.height = '280px';
            if (btnExpand) { btnExpand.innerHTML = '&#x2922;'; btnExpand.title = 'Expand'; }
            if (btnFullscreen) btnFullscreen.style.display = '';
            // Exit player mode and reset camera
            if (_isPlayerMode()) _setPlayerMode?.(false);
        } else if (mode === 'fullwindow') {
            displayEl.classList.add('avatar-fullwindow');
            canvas.style.height = '100%';
            if (btnExpand) { btnExpand.innerHTML = '&#x2715;'; btnExpand.title = 'Collapse'; }
            if (btnFullscreen) btnFullscreen.style.display = '';
        } else if (mode === 'fullscreen') {
            displayEl.classList.add('avatar-fullwindow');
            canvas.style.height = '100%';
            displayEl.requestFullscreen?.().catch(() => { displayMode = 'fullwindow'; });
            if (btnExpand) { btnExpand.innerHTML = '&#x2715;'; btnExpand.title = 'Exit fullscreen'; }
            if (btnFullscreen) btnFullscreen.style.display = 'none';
        }
        if (_onDisplayModeChange) _onDisplayModeChange(mode);
    }

    btnExpand?.addEventListener('click', () => {
        setDisplayMode(displayMode === 'sidebar' ? 'fullwindow' : 'sidebar');
    });
    btnFullscreen?.addEventListener('click', () => setDisplayMode('fullscreen'));

    const _onEscKey = (e) => {
        if (e.key === 'Escape' && displayMode !== 'sidebar') {
            // First ESC: exit player mode. Second ESC: exit fullwindow.
            if (_isPlayerMode()) {
                _setPlayerMode?.(false);
                return;
            }
            if (displayMode === 'fullwindow') {
                setDisplayMode('sidebar');
            }
        }
    };
    const _onFsChange = () => {
        if (!document.fullscreenElement && displayMode === 'fullscreen') {
            displayMode = 'fullwindow'; // prevent recursive exitFullscreen call
            setDisplayMode('sidebar');
        }
    };
    document.addEventListener('keydown', _onEscKey);
    document.addEventListener('fullscreenchange', _onFsChange);

    // Early cleanup — covers cases where Three.js or model loading fails
    const _earlyCleanup = () => {
        clearInterval(_micPoll);
        _chatUnsubs.forEach(fn => fn());
        document.removeEventListener('keydown', _onEscKey);
        document.removeEventListener('fullscreenchange', _onFsChange);
        if (displayMode !== 'sidebar') {
            displayEl.classList.remove('avatar-fullwindow');
            canvas.style.height = '280px';
        }
        if (_cleanup === _earlyCleanup) _cleanup = null;
    };
    // Stale-init guard: another init() may have entered while this one was
    // running. If our token isn't current, tear down what we built and abort.
    if (myToken !== _initToken) {
        _earlyCleanup();
        return;
    }
    _cleanup = _earlyCleanup;

    // --- Fullwindow STT mic ---
    let _micPoll = null;
    const avatarMic = container.querySelector('#avatar-mic');
    if (avatarMic) {
        const updateMicState = () => {
            const ttsActive = audio.isTtsPlaying() || audio.isLocalTtsPlaying();
            if (ttsActive) {
                avatarMic.classList.add('tts-playing');
                avatarMic.classList.remove('recording');
                avatarMic.textContent = '\u23F9';
            } else if (audio.getRecState()) {
                avatarMic.classList.add('recording');
                avatarMic.classList.remove('tts-playing');
                avatarMic.textContent = '\u23FA';
            } else {
                avatarMic.classList.remove('recording', 'tts-playing');
                avatarMic.textContent = '\uD83C\uDFA4';
            }
        };
        avatarMic.addEventListener('mousedown', async (e) => {
            e.preventDefault();
            if (audio.isTtsPlaying() || audio.isLocalTtsPlaying()) {
                audio.stop(true);
            } else {
                await audio.handlePress(avatarMic);
            }
            updateMicState();
        });
        avatarMic.addEventListener('mouseup', async () => {
            await audio.handleRelease(avatarMic, triggerSendWithText);
            updateMicState();
        });
        avatarMic.addEventListener('touchstart', async (e) => {
            e.preventDefault();
            if (audio.isTtsPlaying() || audio.isLocalTtsPlaying()) {
                audio.stop(true);
            } else {
                await audio.handlePress(avatarMic);
            }
            updateMicState();
        });
        avatarMic.addEventListener('touchend', async () => {
            await audio.handleRelease(avatarMic, triggerSendWithText);
            updateMicState();
        });
        avatarMic.addEventListener('mouseleave', () => {
            if (audio.getRecState()) {
                setTimeout(() => {
                    if (audio.getRecState()) audio.handleRelease(avatarMic, triggerSendWithText);
                    updateMicState();
                }, 500);
            }
        });
        // Poll mic state while in expanded mode
        _micPoll = setInterval(updateMicState, 300);
    }

    // --- Chat overlay (WoW-style) ---
    const chatOverlay = container.querySelector('#avatar-chat');
    const chatLog = container.querySelector('#avatar-chat-log');
    const chatToggle = container.querySelector('#avatar-chat-toggle');
    const chatStop = container.querySelector('#avatar-chat-stop');
    const chatVolume = container.querySelector('#avatar-chat-volume');
    let _chatUnsubs = [];
    let _aiStreamEl = null;  // current streaming message element
    let _thinkOpen = false;  // true while inside <think> block
    const MAX_MESSAGES = 50;

    if (chatOverlay) {
        // Collapse/expand toggle
        chatToggle?.addEventListener('click', () => {
            chatOverlay.classList.toggle('collapsed');
            chatToggle.textContent = chatOverlay.classList.contains('collapsed') ? '\u25B2' : '\u25BC';
        });

        // Stop generation
        chatStop?.addEventListener('click', () => handleStop());

        // Volume slider
        if (chatVolume) {
            chatVolume.value = Math.round(audio.getVolume() * 100);
            chatVolume.addEventListener('input', () => {
                audio.setVolume(chatVolume.value / 100);
            });
        }

        function addChatMsg(role, text) {
            if (!chatLog) return;
            const el = document.createElement('div');
            el.className = `chat-msg chat-msg-${role}`;
            el.textContent = text;
            chatLog.appendChild(el);
            // Trim old messages
            while (chatLog.children.length > MAX_MESSAGES) chatLog.removeChild(chatLog.firstChild);
            chatLog.scrollTop = chatLog.scrollHeight;
            return el;
        }

        // User sent a message
        _chatUnsubs.push(eventBus.on(eventBus.Events.USER_SENT, (data) => {
            if (data?.text) addChatMsg('user', data.text);
        }));

        // AI streaming chunks
        // Backend sends think content wrapped in <think>...</think> as type:content chunks.
        // The </think> only arrives when the LLM finishes thinking (done event).
        // Strategy: track open/close tags, only display text outside think blocks.
        _chatUnsubs.push(eventBus.on(eventBus.Events.CHAT_CHUNK, (data) => {
            if (!data?.text) return;
            let text = data.text;

            // Check for think open/close tags in this chunk
            if (text.includes('<think>') || text.includes('<seed:think')) {
                _thinkOpen = true;
                // Strip everything from <think> onward in this chunk
                text = text.replace(/<(?:seed:)?think[^>]*>[\s\S]*/i, '');
            }
            if (_thinkOpen && (text.includes('</think>') || text.includes('</seed:think'))) {
                _thinkOpen = false;
                // Keep everything after the closing tag
                text = text.replace(/[\s\S]*<\/[^>]*think[^>]*>/i, '');
            }

            // If inside think block, skip entirely
            if (_thinkOpen) return;

            // Strip avatar tags
            text = text.replace(/<<avatar:\s*[a-zA-Z0-9_]+(?:\s+(?:once|loop|\d+(?:\.\d+)?s))?>>/g, '');
            // Strip any stray think tags (edge case: open+close in same chunk)
            text = text.replace(/<\/?(?:seed:)?think[^>]*>/gi, '');

            if (text && text.trim()) {
                if (!_aiStreamEl) _aiStreamEl = addChatMsg('ai', '');
                _aiStreamEl.textContent += text;
                chatLog.scrollTop = chatLog.scrollHeight;
            }
        }));

        // AI done typing — finalize stream element
        _chatUnsubs.push(eventBus.on(eventBus.Events.AI_TYPING_END, () => {
            _aiStreamEl = null;
            _thinkOpen = false;
        }));

        // Wakeword conversations bypass streaming — fetch messages when LLM finishes
        let _wakewordPending = false;
        _chatUnsubs.push(eventBus.on(eventBus.Events.WAKEWORD_DETECTED, () => {
            _wakewordPending = true;
        }));
        _chatUnsubs.push(eventBus.on(eventBus.Events.AI_TYPING_END, async () => {
            if (!_wakewordPending) return;
            _wakewordPending = false;
            try {
                const resp = await fetch('/api/history');
                if (!resp.ok) return;
                const data = await resp.json();
                const msgs = data.messages || [];
                const recent = msgs.slice(-2);
                for (const m of recent) {
                    if (m.role === 'user') {
                        const text = (m.content || '').trim();
                        if (text) addChatMsg('user', text);
                    } else if (m.role === 'assistant' && m.parts) {
                        // Assistant uses parts array
                        const text = m.parts
                            .filter(p => p.type === 'content')
                            .map(p => p.text || '')
                            .join('')
                            .replace(/<think>[\s\S]*?<\/think>/gi, '')
                            .trim();
                        if (text) addChatMsg('ai', text);
                    }
                }
            } catch (e) { /* silent */ }
        }));

        // AI starts — show stop button, reset stream state (D4: skip foreign turns)
        _chatUnsubs.push(eventBus.on(eventBus.Events.AI_TYPING_START, (data) => {
            if (data?.foreign) return;
            _aiStreamEl = null;
            _thinkOpen = false;
            chatStop?.classList.add('visible');
        }));

        // AI done or error — hide stop button (D4: a phone call's END/TTS_STOPPED
        // must not hide the operator's own Stop button mid-turn).
        const hideStop = (data) => {
            if (data?.foreign || data?.surface === 'phone') return;
            chatStop?.classList.remove('visible');
        };
        _chatUnsubs.push(eventBus.on(eventBus.Events.AI_TYPING_END, hideStop));
        _chatUnsubs.push(eventBus.on(eventBus.Events.LLM_ERROR, hideStop));
        _chatUnsubs.push(eventBus.on(eventBus.Events.TTS_STOPPED, hideStop));
    }

    // --- Load config from backend ---
    let avatarConfig = {};
    let modelFile = 'sapphire.glb';
    let trackMap = { ...FALLBACK_TRACK_MAP };
    let idlePool = [...FALLBACK_IDLE_POOL];
    let greetingTrack = 'wave';
    let camPos = { ...FALLBACK_CAMERA };
    let camTarget = { ...FALLBACK_TARGET };
    let modelScale = 1.0;
    let baseStateTrack = null;  // legacy single base; used as fallback only
    let timeBuckets = [{ name: 'day', start: 7 }, { name: 'night', start: 21 }];
    let varietyIntervalMin = 2;

    try {
        const resp = await fetch('/api/plugin/avatar/config');
        if (resp.ok) {
            avatarConfig = await resp.json();
            modelFile = avatarConfig.active_model || modelFile;
            const modelCfg = (avatarConfig.models || {})[modelFile];
            if (modelCfg) {
                if (modelCfg.track_map) trackMap = { ...FALLBACK_TRACK_MAP, ...modelCfg.track_map };
                if (modelCfg.idle_pool?.length) idlePool = modelCfg.idle_pool;
                if (modelCfg.greeting_track !== undefined) greetingTrack = modelCfg.greeting_track;
                if (modelCfg.base_state) baseStateTrack = modelCfg.base_state;
                if (modelCfg.time_buckets?.length) timeBuckets = modelCfg.time_buckets;
                if (modelCfg.variety_interval_min) varietyIntervalMin = modelCfg.variety_interval_min;
                if (modelCfg.camera) camPos = modelCfg.camera;
                if (modelCfg.target) camTarget = modelCfg.target;
                if (modelCfg.scale) modelScale = modelCfg.scale;
            }
        }
    } catch (e) { /* use fallbacks */ }

    // Normalize pool entries. Legacy entries (track/weight/oneshot, no
    // `variety` field) were ALL variety tracks — migrate them so old configs
    // keep cycling. `base` is the list of time-bucket names a track can rest
    // in (empty = not a base candidate).
    idlePool = idlePool.map(v => ({
        track: v.track,
        weight: v.weight ?? 10,
        oneshot: v.oneshot ?? false,
        variety: v.variety ?? true,
        base: Array.isArray(v.base) ? v.base : [],
    }));

    const MODEL_URL = `/api/avatar/${modelFile}`;

    // Build oneshot set from idle pool config
    const ONESHOT_TRACKS = new Set(
        idlePool.filter(v => v.oneshot).map(v => v.track)
    );
    // Also treat known oneshot animations as oneshot
    for (const t of ['happy', 'wave', 'attention', 'attention2']) ONESHOT_TRACKS.add(t);

    // Which time bucket are we in right now? The latest bucket whose `start`
    // hour is <= the current hour wins, wrapping past midnight (so if the
    // earliest bucket starts at 07:00, hours 00:00–06:59 belong to the last
    // bucket of the day, e.g. night).
    function currentBucket() {
        if (!timeBuckets.length) return null;
        const hour = new Date().getHours();
        const sorted = [...timeBuckets].sort((a, b) => a.start - b.start);
        let pick = sorted[sorted.length - 1];  // wrap: before first start = last bucket
        for (const b of sorted) {
            if (hour >= b.start) pick = b;
        }
        return pick.name;
    }

    // Is the current time bucket "quiet" (no variety overlays)? A bucket can
    // set quiet:true; if unset, 'night' defaults quiet so legacy configs that
    // predate the flag still let her sleep undisturbed.
    function isQuietNow() {
        const name = currentBucket();
        const b = timeBuckets.find(x => x.name === name);
        if (!b) return false;
        return b.quiet ?? (name === 'night');
    }

    // Random base track eligible for a given bucket. Falls back to the legacy
    // base_state, then trackMap.idle, then 'idle'.
    function pickBaseTrack(bucketName) {
        const eligible = idlePool.filter(v => v.base.includes(bucketName) && actions[v.track]);
        if (eligible.length) {
            return eligible[Math.floor(Math.random() * eligible.length)].track;
        }
        if (baseStateTrack && actions[baseStateTrack]) return baseStateTrack;
        return trackMap.idle || 'idle';
    }

    // Weighted random pick among VARIETY-tagged tracks (the occasional overlay).
    function pickIdleVariant() {
        const pool = idlePool.filter(v => v.variety);
        if (!pool.length) return null;
        const total = pool.reduce((s, v) => s + v.weight, 0);
        let roll = Math.random() * total;
        for (const v of pool) {
            roll -= v.weight;
            if (roll <= 0) return v.track;
        }
        return pool[pool.length - 1].track;  // float-edge fallback
    }

    // Dynamic imports (cached after first load)
    let THREE, GLTFLoader, OrbitControls;
    try {
        THREE = await import(THREE_CDN);
        const gltfMod = await import(GLTF_CDN);
        const orbitMod = await import(ORBIT_CDN);
        GLTFLoader = gltfMod.GLTFLoader;
        OrbitControls = orbitMod.OrbitControls;
    } catch (e) {
        console.error('[Avatar] Failed to load three.js:', e);
        canvas.style.display = 'none';
        container.innerHTML += '<div style="text-align:center;color:var(--text-muted);padding:8px;">Three.js failed to load</div>';
        return;
    }

    // Scene
    const scene = new THREE.Scene();
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    // Environment (procedural scene — only visible in expanded modes)
    const env = createEnvironment(scene, THREE, renderer);

    // Camera — from config
    const camera = new THREE.PerspectiveCamera(30, canvas.clientWidth / canvas.clientHeight, 0.1, 200);
    camera.position.set(camPos.x, camPos.y, camPos.z);

    // Orbit controls
    const controls = new OrbitControls(camera, canvas);
    controls.target.set(camTarget.x, camTarget.y, camTarget.z);
    controls.minDistance = 0.5;
    controls.maxDistance = 20;
    controls.enablePan = true;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.5;
    controls.panSpeed = 0.4;
    controls.maxPolarAngle = Math.PI * 0.85;
    controls.update();

    // Camera orbit system
    const orbitSystem = createCameraOrbitSystem(camera, controls, THREE);

    // Player controller (WASD + mouse look)
    const playerCtrl = createPlayerController(camera, controls, canvas, THREE);
    _isPlayerMode = () => playerCtrl.isEnabled();
    _setPlayerMode = setPlayerMode;

    // Orbit toggle button
    const btnOrbit = container.querySelector('#avatar-btn-orbit');
    if (btnOrbit) {
        btnOrbit.classList.add('orbit-active');  // on by default
        btnOrbit.addEventListener('click', () => {
            if (playerCtrl.isEnabled()) return;  // can't orbit in player mode
            const on = orbitSystem.toggle();
            btnOrbit.classList.toggle('orbit-active', on);
        });
    }

    // Player mode toggle
    const btnPlayer = container.querySelector('#avatar-btn-player');
    function setPlayerMode(on) {
        if (on === playerCtrl.isEnabled()) return;
        if (!on && _stopGame) _stopGame();
        playerCtrl.toggle();
        btnPlayer?.classList.toggle('player-active', on);
        displayEl.classList.toggle('player-mode', on);
        if (on && orbitSystem.isEnabled()) {
            orbitSystem.toggle();
            btnOrbit?.classList.remove('orbit-active');
        }
    }
    if (btnPlayer) {
        btnPlayer.addEventListener('click', () => setPlayerMode(!playerCtrl.isEnabled()));
    }

    // Auto-activate player mode on WASD/Space in fullwindow
    const PLAYER_TRIGGER_KEYS = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'Space']);
    const _onPlayerAutoActivate = (e) => {
        if (displayMode === 'sidebar') return;
        if (playerCtrl.isEnabled()) return;
        if (!PLAYER_TRIGGER_KEYS.has(e.code)) return;
        e.preventDefault();
        setPlayerMode(true);
    };
    document.addEventListener('keydown', _onPlayerAutoActivate);

    // Missile Command game
    const missileGame = createMissileCommand(scene, THREE, camera, canvas, eventBus.dispatch);
    _stopGame = () => { if (missileGame.isActive()) missileGame.stop(); };

    const _onGameKey = (e) => {
        if (e.code !== 'KeyG') return;
        if (displayMode === 'sidebar') return;
        if (!playerCtrl.isEnabled()) return;  // must be in player mode
        e.preventDefault();
        if (missileGame.isActive()) {
            missileGame.stop();
        } else {
            missileGame.start(displayEl);
        }
    };
    document.addEventListener('keydown', _onGameKey);

    // Double-click to reset camera (only in orbit mode)
    canvas.addEventListener('dblclick', () => {
        if (playerCtrl.isEnabled()) return;
        camera.position.set(camPos.x, camPos.y, camPos.z);
        controls.target.set(camTarget.x, camTarget.y, camTarget.z);
        controls.update();
    });

    // Lighting (default — used in sidebar mode, hidden when environment is active)
    const defaultAmbient = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(defaultAmbient);
    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(2, 3, 2);
    scene.add(dirLight);
    const rimLight = new THREE.DirectionalLight(0x4a9eff, 0.4);
    rimLight.position.set(-1, 2, -2);
    scene.add(rimLight);

    // Resize
    function resize() {
        const w = canvas.clientWidth;
        const h = canvas.clientHeight;
        if (canvas.width !== w || canvas.height !== h) {
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        }
    }
    resize();

    // Load model
    const loader = new GLTFLoader();
    let mixer, actions = {}, currentAction = null;

    // Rails system. Six slots, top wins. Each writer sets its slot and calls
    // applyWinningRail() which crossfades to whatever wins. No priority math,
    // no time-locks — rail ownership replaces both.
    //
    //   1 Sapph oneshot — `<<avatar: rumba>>` tags. Pass 1: every Sapph tag
    //                     lands here as oneshot. Pass 2 splits in loop/once.
    //   2 Sapph loop    — wired in Pass 2; stays inactive for now.
    //   3 AI-init SSE   — typing/speaking/toolcall/agent/cron etc.
    //   4 User-init SSE — listening/user_typing/wakeword/reading etc.
    //   5 Idle variety  — weighted pick from idle_pool, plays while base wins.
    //   6 Base state    — always-on background. Pass 3 makes it configurable.
    //
    // Must exist before the greeting flow (inside the model-load try-block).
    let idleTimer = null;
    const rails = {
        1: { active: false, track: null, mode: 'oneshot' },
        2: { active: false, track: null, mode: 'loop', windDownRequested: false },
        3: { active: false, track: null, mode: 'loop' },
        4: { active: false, track: null, mode: 'loop' },
        5: { active: false, track: null, mode: 'oneshot' },
        6: { active: false, track: null, mode: 'loop' },
    };

    try {
        const gltf = await new Promise((resolve, reject) => {
            loader.load(MODEL_URL, resolve, undefined, reject);
        });

        scene.add(gltf.scene);

        // Apply scale from config (user-controlled, default 1.0)
        if (modelScale !== 1.0) {
            gltf.scene.scale.multiplyScalar(modelScale);
        }

        // Frame camera on model center after scaling
        const box = new THREE.Box3().setFromObject(gltf.scene);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());

        // If no custom camera was saved, auto-frame based on model bounds
        const hasCustomCamera = (avatarConfig.models || {})[modelFile]?.camera;
        if (!hasCustomCamera) {
            const dist = Math.max(size.y, size.x) * 2.5;
            camTarget = { x: center.x, y: center.y, z: center.z };
            camPos = { x: center.x, y: center.y + size.y * 0.1, z: center.z + dist };
            camera.position.set(camPos.x, camPos.y, camPos.z);
            controls.target.set(camTarget.x, camTarget.y, camTarget.z);
            controls.update();
        }

        // Dynamic zoom limits based on model size
        const maxDim = Math.max(size.x, size.y, size.z);
        controls.minDistance = maxDim * 0.3;
        controls.maxDistance = maxDim * 10;

        // Feed model info to orbit system and randomize start position
        orbitSystem.setModelInfo(center, size.y);
        orbitSystem.randomStart();

        mixer = new THREE.AnimationMixer(gltf.scene);

        for (const clip of gltf.animations) {
            const action = mixer.clipAction(clip);
            action.clampWhenFinished = true;
            actions[clip.name] = action;
        }

        // Rail 6 (base state) — always-active, time-aware background. Picks a
        // random base track eligible for the current time bucket (day/night/
        // …). Re-rolled by the rotation timer below and on bucket crossover.
        rails[6].active = true;
        rails[6].track = pickBaseTrack(currentBucket());

        // Unified mixer 'finished' listener — clears oneshot rails when their
        // clip ends. Replaces per-action listeners that crossfadeTo and the
        // old avatar_animate used to attach individually.
        mixer.addEventListener('finished', (e) => {
            const clipName = e.action.getClip().name;
            for (let i = 1; i <= 6; i++) {
                if (rails[i].active && rails[i].track === clipName && rails[i].mode === 'oneshot') {
                    rails[i].active = false;
                    rails[i].track = null;
                    applyWinningRail();
                    return;
                }
            }
        });

        // Mixer 'loop' listener for rail 2 wind-down. Fires at each loop
        // iteration boundary of a LoopRepeat action. If rail 2 has been
        // marked windDownRequested by a user-init event, clear it now —
        // her loop completes naturally to the bar, then yields gracefully.
        mixer.addEventListener('loop', (e) => {
            if (!rails[2].active || !rails[2].windDownRequested) return;
            if (e.action.getClip().name !== rails[2].track) return;
            rails[2].active = false;
            rails[2].track = null;
            rails[2].windDownRequested = false;
            applyWinningRail();
        });

        // Greeting → rail 1 as oneshot. When its clip ends, the listener
        // above clears rail 1, picker falls to rail 6 (base), variety arms
        // naturally. No greeting? Just apply the picker — rail 6 wins.
        if (greetingTrack && actions[greetingTrack]) {
            writeSapphRail(greetingTrack);
        } else {
            applyWinningRail();
        }

        // Enable avatar shadow casting for environment
        env.enableAvatarShadows(gltf.scene);

        // Load configured location + populate selector
        const configuredLocation = avatarConfig.active_location || 'cabin';
        env.setLocation(configuredLocation);

        const locationSelect = container.querySelector('#avatar-location-select');
        if (locationSelect) {
            for (const loc of env.listLocations()) {
                const opt = document.createElement('option');
                opt.value = loc.name;
                opt.textContent = loc.name[0].toUpperCase() + loc.name.slice(1);
                if (loc.name === configuredLocation) opt.selected = true;
                locationSelect.appendChild(opt);
            }
            locationSelect.addEventListener('change', async () => {
                const name = locationSelect.value;
                await env.setLocation(name);
                // Save to config
                fetch('/api/plugin/avatar/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active_location: name }),
                });
            });
        }

        // Wire environment toggle to display mode changes
        _onDisplayModeChange = (mode) => {
            const expanded = mode !== 'sidebar';
            env.setVisible(expanded);
            defaultAmbient.visible = !expanded;
            dirLight.visible = !expanded;
            // Rim light stays on always — it's her signature
            // Reset camera to default framing when returning to sidebar
            if (!expanded) {
                camera.position.set(camPos.x, camPos.y, camPos.z);
                controls.target.set(camTarget.x, camTarget.y, camTarget.z);
                controls.update();
            }
        };

        // Expand zoom limits for environment exploration
        controls.maxDistance = 35;

    } catch (e) {
        console.error('[Avatar] Failed to load model:', e);
        canvas.style.display = 'none';
        container.innerHTML += '<div style="text-align:center;color:var(--text-muted);padding:8px;">Model failed to load</div>';
        return;
    }

    // --- Animation crossfade (renderer primitive) ---
    // Pure three.js execution layer — takes a track name + loop bool, plays
    // it. The rail layer above decides what to play; this just blends.
    function crossfadeTo(trackName, isOneshot) {
        const action = actions[trackName];
        if (!action) return;
        const desiredLoop = isOneshot ? THREE.LoopOnce : THREE.LoopRepeat;
        if (currentAction === action) {
            // Same track — sync loop mode if it changed (e.g. rail 5 picked
            // the same track rail 6 was already playing but with oneshot).
            if (action.loop !== desiredLoop) {
                action.setLoop(desiredLoop);
                if (isOneshot) action.reset();
            }
            return;
        }
        action.reset();
        action.setLoop(desiredLoop);
        action.clampWhenFinished = true;
        if (currentAction) {
            action.crossFadeFrom(currentAction, CROSSFADE_MS / 1000, true);
        }
        action.play();
        currentAction = action;
    }

    // --- Rail picker ---
    function winningRail() {
        for (let i = 1; i <= 6; i++) if (rails[i].active) return i;
        return null;
    }

    function applyWinningRail() {
        const id = winningRail();
        if (id == null) return;
        const slot = rails[id];
        if (statusEl) statusEl.textContent = (id === 6) ? '' : (slot.track || '');
        crossfadeTo(slot.track, slot.mode === 'oneshot');
        // Falling into base means "nothing is happening" in the user-facing
        // sense — arm the variety timer. Otherwise cancel any pending pick.
        if (id === 6) scheduleIdleVariant();
        else clearTimeout(idleTimer);
    }

    // --- Rail writers ---
    function writeAIRail(state) {
        if (state == null) {
            rails[3].active = false;
            rails[3].track = null;
        } else {
            const track = trackMap[state] || state;
            rails[3].active = true;
            rails[3].track = track;
            rails[3].mode = ONESHOT_TRACKS.has(track) ? 'oneshot' : 'loop';
        }
        applyWinningRail();
    }

    let _userRailTtlTimer = null;
    function writeUserRail(state, ttlMs) {
        clearTimeout(_userRailTtlTimer);
        if (state == null) {
            rails[4].active = false;
            rails[4].track = null;
        } else {
            const track = trackMap[state] || state;
            rails[4].active = true;
            rails[4].track = track;
            rails[4].mode = ONESHOT_TRACKS.has(track) ? 'oneshot' : 'loop';
            // Wind-down request: a human is reaching for her. If her loop
            // (rail 2) is currently active, mark it for graceful end at the
            // next clip iteration boundary. The mixer 'loop' listener clears
            // rail 2 when this flag is set, then the picker falls through.
            if (rails[2].active) rails[2].windDownRequested = true;
            // Optional TTL: events without natural end-pairs (USER_SENT)
            // self-expire so she doesn't get stuck in their pose. Resets on
            // every write — STT_RECORDING_START with no ttlMs cancels any
            // prior USER_SENT timer (the new state supersedes).
            if (ttlMs) {
                _userRailTtlTimer = setTimeout(() => {
                    rails[4].active = false;
                    rails[4].track = null;
                    applyWinningRail();
                }, ttlMs);
            }
        }
        applyWinningRail();
    }

    function writeSapphRail(track, mode = 'once') {
        // mode='once' → rail 1 (oneshot, clip-end clears slot).
        // mode='loop' → rail 2 (loop, holds until Sapph replaces it or a
        // user-init event triggers wind-down).
        if (!actions[track]) {
            console.warn(`[Avatar] Track "${track}" not found in model`);
            return;
        }
        if (mode === 'loop') {
            // Writing a fresh loop clears any pending wind-down from a
            // previous loop she replaced.
            rails[2].active = true;
            rails[2].track = track;
            rails[2].mode = 'loop';
            rails[2].windDownRequested = false;
            // Clear rail 1 too so a stale oneshot doesn't mask the new loop.
            rails[1].active = false;
            rails[1].track = null;
        } else {
            rails[1].active = true;
            rails[1].track = track;
            rails[1].mode = 'oneshot';
        }
        applyWinningRail();
    }

    function writeIdleRail(track) {
        if (!actions[track]) return;
        rails[5].active = true;
        rails[5].track = track;
        // Variety ALWAYS plays once and returns to base — even tracks that
        // would normally loop. A variety overlay is a brief gesture, not a
        // sustained state; without this, a non-oneshot pick (e.g. `typing`)
        // would loop for the whole variety interval. The mixer 'finished'
        // listener clears rail 5 when the clip ends → picker returns to base.
        rails[5].mode = 'oneshot';
        applyWinningRail();
    }

    // --- Idle variety + base rotation ---
    // On each cycle: re-roll rail 6's base track for the CURRENT time bucket
    // (this also catches day↔night crossover for free — pickBaseTrack reads
    // the clock), then fire a variety overlay if she's idle. Oneshot variety
    // auto-cycles via the mixer 'finished' listener; loop variety re-schedules
    // here. Interval is the user's variety_interval_min (with ±30% jitter).
    function scheduleIdleVariant() {
        clearTimeout(idleTimer);
        const baseMs = Math.max(0.5, varietyIntervalMin) * 60000;
        const delay = baseMs * (0.7 + Math.random() * 0.6);
        idleTimer = setTimeout(() => {
            // Re-roll base for the current bucket. Applies on next fall-through
            // to rail 6 (after variety) or immediately if nothing else plays.
            const freshBase = pickBaseTrack(currentBucket());
            if (winningRail() !== 6) {
                rails[6].track = freshBase;  // shows when she next falls idle
                scheduleIdleVariant();
                return;
            }
            // Quiet bucket (e.g. night): rotate the base pose but fire NO
            // variety overlay — she rests undisturbed. Keep rescheduling so
            // base rotation continues and we catch the bucket crossover.
            if (isQuietNow()) {
                rails[6].track = freshBase;
                applyWinningRail();
                scheduleIdleVariant();
                return;
            }
            const track = pickIdleVariant();
            if (track && actions[track]) {
                rails[6].track = freshBase;  // she'll settle here when variety ends
                writeIdleRail(track);  // oneshot — mixer 'finished' re-arms via rail-6 return
            } else {
                // No variety tracks configured — just rotate the base pose.
                rails[6].track = freshBase;
                applyWinningRail();
                scheduleIdleVariant();
            }
        }, delay);
    }

    // Wire SSE events — AI-init writers feed rail 3, user-init feed rail 4.
    const unsubs = [];
    for (const [event, state] of Object.entries(AI_TRANSITIONS)) {
        // D4: the operator's avatar reflects HER local state — ignore a phone
        // call / background conversation's turn (AI_TYPING carries foreign; TTS
        // carries surface). Events without these flags pass through unchanged.
        const unsub = eventBus.on(event, (data) => {
            if (data?.foreign || data?.surface === 'phone') return;
            writeAIRail(state);
        });
        if (unsub) unsubs.push(unsub);
    }
    for (const [event, cfg] of Object.entries(USER_TRANSITIONS)) {
        const unsub = eventBus.on(event, () => writeUserRail(cfg.state, cfg.ttlMs));
        if (unsub) unsubs.push(unsub);
    }

    // Sapph-triggered animations: <<avatar: trackname [once|loop]>>.
    // mode='loop' lands on rail 2 (held until she replaces it or user-init
    // event triggers wind-down). mode='once' (default) lands on rail 1
    // (clears on clip-end). Unknown/missing mode → 'once'.
    const avatarUnsub = eventBus.on('avatar_animate', (data) => {
        const { track, mode } = data || {};
        if (track) writeSapphRail(track, mode === 'loop' ? 'loop' : 'once');
    });
    if (avatarUnsub) unsubs.push(avatarUnsub);

    // Render loop
    const clock = new THREE.Clock();
    let running = true;

    function animate() {
        if (!running) return;
        requestAnimationFrame(animate);
        const delta = clock.getDelta();
        if (mixer) mixer.update(delta);
        env.update(delta);
        if (playerCtrl.isEnabled()) {
            playerCtrl.update(delta);
        } else {
            orbitSystem.update(delta);
            controls.update();
        }
        missileGame.update(delta);
        resize();
        renderer.render(scene, camera);
    }
    animate();

    // Cleanup
    const _thisCleanup = () => {
        running = false;
        clearTimeout(idleTimer);
        clearTimeout(_userRailTtlTimer);
        clearInterval(_micPoll);
        unsubs.forEach(fn => fn());
        _chatUnsubs.forEach(fn => fn());
        document.removeEventListener('keydown', _onPlayerAutoActivate);
        document.removeEventListener('keydown', _onGameKey);
        missileGame.cleanup();
        playerCtrl.cleanup();
        orbitSystem.cleanup();
        controls.dispose();
        renderer.dispose();
        document.removeEventListener('keydown', _onEscKey);
        document.removeEventListener('fullscreenchange', _onFsChange);
        if (displayMode !== 'sidebar') {
            displayEl.classList.remove('avatar-fullwindow');
            canvas.style.height = '280px';
        }
        if (_cleanup === _thisCleanup) _cleanup = null;
    };

    // Stale-init guard: if another init() entered while this one was building
    // (rapid sidebar reload bursts), abandon THIS instance — teardown the
    // resources we just created and don't overwrite the newer instance's
    // _cleanup. Without this, both instances run concurrently, both register
    // observers + RAF loops, and the earlier one leaks. 2026-05-14.
    if (myToken !== _initToken) {
        _thisCleanup();
        return;
    }
    _cleanup = _thisCleanup;

    // Debounced disposal — a brief detach during framework DOM rebuild
    // (sidebar accordion refresh, view switch) should NOT permanently dispose
    // the WebGL context. Only treat detachment as final after 1s. Previously
    // any momentary detach killed the avatar irreversibly. 2026-05-13.
    let _detachTimer = null;
    const observer = new MutationObserver(() => {
        if (!document.contains(canvas)) {
            if (!_detachTimer) {
                _detachTimer = setTimeout(() => {
                    _detachTimer = null;
                    if (!document.contains(canvas)) {
                        if (_cleanup) _cleanup();
                        observer.disconnect();
                    }
                }, 1000);
            }
        } else if (_detachTimer) {
            clearTimeout(_detachTimer);
            _detachTimer = null;
        }
    });
    observer.observe(container.parentElement || document.body, { childList: true, subtree: true });
}
