// 3D Avatar — threejs GLTF with animation blending driven by SSE events
import * as eventBus from '/static/core/event-bus.js';
import * as audio from '/static/audio.js';
import { triggerSendWithText, handleStop } from '/static/handlers/send-handlers.js';
import { createEnvironment } from './environment.js';
import { createCameraOrbitSystem } from './camera-orbits.js';
import { createPlayerController } from './player-controller.js';
import { createMissileCommand } from './missile-command.js';
import { createMouth } from './mouth.js';
import { createStage, parseGo, inPlace } from './stage.js';

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

// Split brain (2026-11-09): her state, the reaches the server hears, idle
// variety and the base pose are decided ONCE on the server (brain.py) and
// arrive as `avatar_state` — every renderer of her shows the same her. This
// tab keeps only what only it knows:
//   her tags (rails 1-2), fired as she SAYS them (mouth.js cues) or off the
//   text when no voice carries the turn; `speaking` from its own voice clock;
//   and the reaches only a browser sees (typing in the box, a message sent).
// A human reaching for her (any of these) asks her tag loop to wind down.
const LOCAL_REACHES = {
    [eventBus.Events.USER_TYPING]:          { state: 'user_typing' },
    [eventBus.Events.USER_SENT]:            { state: 'reading', ttlMs: 3000 },
};
const WIND_DOWN_EVENTS = [
    eventBus.Events.STT_RECORDING_START, eventBus.Events.STT_RECORDING_END,
    eventBus.Events.STT_PROCESSING, eventBus.Events.WAKEWORD_DETECTED,
    eventBus.Events.USER_TYPING, eventBus.Events.USER_SENT,
];
const TEXT_TAG_HOLD_MS = 500;   // a text tag waits this long to learn whether a voice carries the turn

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
    let mouthGain = 1.6;

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
                if (modelCfg.camera) camPos = modelCfg.camera;
                if (modelCfg.target) camTarget = modelCfg.target;
                if (modelCfg.scale) modelScale = modelCfg.scale;
                if (modelCfg.mouth_gain) mouthGain = modelCfg.mouth_gain;
            }
        }
    } catch (e) { /* use fallbacks */ }

    const MODEL_URL = `/api/avatar/${modelFile}`;

    // Oneshot set for the rails this tab writes itself (speaking, local
    // reaches); the brain sends its own rails' modes. Same rule as brain.py.
    const ONESHOT_TRACKS = new Set(idlePool.filter(v => v.oneshot).map(v => v.track));
    for (const t of ['happy', 'wave', 'attention', 'attention2']) ONESHOT_TRACKS.add(t);

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

    // Face lock: the camera stays on her face, talking or not
    const btnFace = container.querySelector('#avatar-btn-face');
    btnFace?.addEventListener('click', () => {
        const on = orbitSystem.setFaceLock(!btnFace.classList.contains('face-active'));
        btnFace.classList.toggle('face-active', on);
    });

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
        const pr = renderer.getPixelRatio();     // the drawing buffer is CSS size x pixel ratio
        if (canvas.width !== Math.floor(w * pr) || canvas.height !== Math.floor(h * pr)) {
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        }
    }
    resize();

    // Load model
    const loader = new GLTFLoader();
    let mixer, actions = {}, currentAction = null;
    let mouth = null;   // her lipsync (mouth.js), set once the model is in
    let stage = null;   // her legs + the chat stage (stage.js)
    // the waist split for sitting (crossfadeTo); declared here: the greeting plays before that code runs
    const LOWER = /^(Hips|Armature|Body|coat_|(Left|Right)(UpLeg|Leg|Foot|Toe))/;
    const halves = {};
    let lowAction = null;

    // Rails system. Six slots, top wins; applyWinningRail() crossfades to
    // whatever wins. No priority math, no time-locks — rail ownership.
    //
    //   1 her tag, once  — `<<avatar: rumba>>`        (this tab)
    //   2 her tag, loop  — `<<avatar: rumba loop>>`   (this tab)
    //   3 her state      — `speaking` from this tab's voice clock, else the brain's
    //   4 a human reach  — the brain's (mic, wakeword), else this tab's (typing, sent)
    //   5 idle variety   — the brain's
    //   6 base           — the brain's (a plain idle until it answers)
    //
    // Must exist before the greeting flow (inside the model-load try-block).
    const rails = {
        1: { active: false, track: null, mode: 'oneshot' },
        2: { active: false, track: null, mode: 'loop', windDownRequested: false },
    };
    let brainRails = { 3: null, 4: null, 5: null, 6: null };   // {track, mode, id} | null
    let brainSeq = -1;
    let speakingSlot = null, localReach = null;              // this tab's rail 3 / 4 writers
    let slotIds = 0;
    const finished = new Set();   // ids of oneshots that already played out here
    let playing = null;           // {rail, slot} on screen now

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

        const travel = {};
        for (const clip of gltf.animations) {
            travel[clip.name] = inPlace(clip);      // walk clips stride in place; legs move her
            const action = mixer.clipAction(clip);
            action.clampWhenFinished = true;
            actions[clip.name] = action;
        }

        // A oneshot that played out yields its rail: her tag rail clears; a
        // brain or local slot is marked finished by id (the brain clears it on
        // its own clock; a rewrite comes with a new id and plays again).
        mixer.addEventListener('finished', (e) => {
            if (!playing || playing.slot.mode !== 'oneshot') return;
            if (e.action.getClip().name !== playing.slot.track) return;
            if (playing.rail === 1) { rails[1].active = false; rails[1].track = null; }
            else finished.add(playing.slot.id);
            applyWinningRail();
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

        // Her mouth (lipsync) + the face shot's anchor (the Head bone). Her
        // tags fire through the mouth: as she says the words they rode.
        try { mouth = await createMouth({ root: gltf.scene, audio, eventBus, gain: mouthGain }); }
        catch (e) { console.warn('[Avatar] mouth unavailable:', e); }
        mouth?.onCue((q) => fireTag(q.track, q.mode));
        stage = createStage({
            THREE, scene, renderer, canvas, model: gltf.scene, actions, travel, height: size.y,
            play: (track) => crossfadeTo(track, false),
            onArrive: () => { playing = null; applyWinningRail(); },
        });
        orbitSystem.setHead(gltf.scene.getObjectByName('Head'), gltf.scene);

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
        mouth?.dispose();
        canvas.style.display = 'none';
        container.innerHTML += '<div style="text-align:center;color:var(--text-muted);padding:8px;">Model failed to load</div>';
        return;
    }

    // --- Animation crossfade (renderer primitive) ---
    // Pure three.js execution layer — takes a track name + loop bool, plays
    // it. The rail layer above decides what to play; this just blends.
    // `restart`: the same oneshot written again (a new id) plays from the top.
    // Seated (`seat`): her legs hold the sit clip and only the top half of
    // the track plays, so she waves, talks and types from her chair without
    // standing up, and faces the way the seat does.
    function half(track, low) {
        const src = actions[track];
        if (!src) return null;
        const key = `${track}:${low}`;
        if (!halves[key]) {
            const c = src.getClip();
            halves[key] = mixer.clipAction(new THREE.AnimationClip(c.name, c.duration, c.tracks.filter(t => LOWER.test(t.name) === low)));
            halves[key].clampWhenFinished = true;
        }
        return halves[key];
    }
    function crossfadeTo(trackName, isOneshot, restart = false, seat = false) {
        const action = seat ? half(trackName, false) : actions[trackName];
        if (!action) return;
        const low = seat ? half(stage.sitClip(), true) : null;
        if (low !== lowAction) {
            lowAction?.fadeOut(CROSSFADE_MS / 1000);
            low?.reset().setLoop(THREE.LoopRepeat).fadeIn(CROSSFADE_MS / 1000).play();
            lowAction = low;
        }
        const desiredLoop = isOneshot ? THREE.LoopOnce : THREE.LoopRepeat;
        if (currentAction === action) {
            // Same track — sync loop mode if it changed (e.g. rail 5 picked
            // the same track rail 6 was already playing but with oneshot).
            if (action.loop !== desiredLoop || (restart && isOneshot)) {
                action.setLoop(desiredLoop);
                if (isOneshot) action.reset().play();
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
    // A slot counts while its clip exists here and (if a oneshot) it hasn't
    // played out on this screen yet.
    function live(s) { return s && s.track && actions[s.track] && !finished.has(s.id); }
    function winning() {
        if (rails[1].active) return { rail: 1, slot: { ...rails[1], id: 'r1' } };
        if (rails[2].active) return { rail: 2, slot: { ...rails[2], id: 'r2' } };
        const order = [[3, speakingSlot], [3, brainRails[3]], [4, brainRails[4]],
                       [4, localReach], [5, brainRails[5]], [6, brainRails[6]]];
        for (const [rail, slot] of order) if (live(slot)) return { rail, slot };
        const idle = trackMap.idle || 'idle';     // the brain hasn't answered yet
        return { rail: 6, slot: { track: actions[idle] ? idle : Object.keys(actions)[0], mode: 'loop', id: 'idle' } };
    }

    function applyWinningRail() {
        const win = winning();
        if (!win.slot.track) return;
        if (stage?.walking()) { playing = null; return; }      // her legs have her; the winner plays on arrival
        const restart = !playing || playing.slot.id !== win.slot.id;
        if (stage?.seatName() === 'bed' && win.rail !== 2) {     // in bed she sleeps, whole body
            if (statusEl) statusEl.textContent = '';
            crossfadeTo(stage.lieClip(), false, restart);
            playing = win;
            return;
        }
        if (stage?.seated()) {
            if (win.rail !== 2) {                               // seated: the winner plays from the waist up
                let t = win.slot.track, once = win.slot.mode === 'oneshot';
                if (win.slot === speakingSlot) { t = actions.talking_seated ? 'talking_seated' : t; once = false; }
                else if (win.rail === 3 && !once && stage.seatName() === 'desk' && actions.typing) t = 'typing';   // working at her desk
                else if (win.rail >= 5) { t = stage.sitClip(); once = false; }                                    // resting
                if (statusEl) statusEl.textContent = '';
                crossfadeTo(t, once, restart, true);
                playing = win;
                return;
            }
            stage.stand();                                      // a loop of her own (a dance): she gets up
        }
        if (statusEl) statusEl.textContent = (win.rail === 6) ? '' : win.slot.track;
        crossfadeTo(win.slot.track, win.slot.mode === 'oneshot', restart);
        playing = win;
    }

    // --- Rail writers ---
    // A reach only this browser sees (typing in the box, a message sent).
    let _userRailTtlTimer = null;
    function writeLocalReach(state, ttlMs) {
        clearTimeout(_userRailTtlTimer);
        const track = trackMap[state] || state;
        localReach = { track, mode: ONESHOT_TRACKS.has(track) ? 'oneshot' : 'loop', id: `u${++slotIds}` };
        // Optional TTL: events without natural end-pairs (USER_SENT)
        // self-expire so she doesn't get stuck in their pose.
        if (ttlMs) {
            _userRailTtlTimer = setTimeout(() => { localReach = null; applyWinningRail(); }, ttlMs);
        }
        applyWinningRail();
    }

    // `speaking` follows this tab's own voice clock (mouth.js): the browser
    // lane never fires TTS_PLAYING, and the brain can't know when a tab plays.
    function setSpeakingSlot(on) {
        if (on === !!speakingSlot) return;
        const track = trackMap.speaking || 'attention';
        speakingSlot = on ? { track, mode: ONESHOT_TRACKS.has(track) ? 'oneshot' : 'loop', id: `s${++slotIds}` } : null;
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
            playing = null;           // the same tag again plays from the top
        }
        applyWinningRail();
    }

    // Her tags: `go_…` moves her (stage.js), anything else is a clip.
    function fireTag(track, mode) {
        if (/^go_/i.test(track)) { stage?.go(parseGo(track, stage.has)); return; }
        writeSapphRail(track, mode === 'loop' ? 'loop' : 'once');
    }

    // --- The brain's rails (brain.py) ---
    let placeId = null;
    function takeBrain(snap) {
        if (!snap?.rails) return;
        if (snap.place && snap.place.id !== placeId) {          // the brain moved her (the first one just puts her there)
            stage?.go(snap.place, { teleport: placeId === null });
            placeId = snap.place.id;
        }
        brainRails = { 3: null, 4: null, 5: null, 6: null };
        for (const k of [3, 4, 5, 6]) brainRails[k] = snap.rails[k] || null;
        brainSeq = snap.seq ?? brainSeq;
        applyWinningRail();
    }
    const unsubs = [];
    unsubs.push(eventBus.on('avatar_state', takeBrain));
    unsubs.push(eventBus.on('avatar_config', (d) => {        // settings saved: her mouth now
        if (d?.active_model === modelFile && d.mouth_gain) mouth?.setGain(d.mouth_gain);
    }));
    fetch('/api/plugin/avatar/state').then(r => r.ok ? r.json() : null).then(snap => {
        if (running && snap && (snap.seq ?? 0) >= brainSeq) takeBrain(snap);
    }).catch(() => { /* a plain idle until the brain speaks */ });

    // A human reaching for her: the browser-only reaches write rail 4 here,
    // and every reach asks her tag loop (rail 2) to wind down at its next bar.
    for (const [event, cfg] of Object.entries(LOCAL_REACHES)) {
        unsubs.push(eventBus.on(event, () => writeLocalReach(cfg.state, cfg.ttlMs)));
    }
    for (const event of WIND_DOWN_EVENTS) {
        unsubs.push(eventBus.on(event, () => { if (rails[2].active) rails[2].windDownRequested = true; }));
    }

    // Her tags off the TEXT (api.js `source: 'text'`) fire only when no voice
    // carries this turn: with a voice, the same tag rides the mouth curve and
    // fires as she says it (above). `avatar_voice` comes from the server when
    // a voice starts, on any lane; the hold covers the two wires' race.
    // A user's own typed tags (no source) fire at once.
    let turnAt = 0, voiceAt = -1;
    unsubs.push(eventBus.on(eventBus.Events.AI_TYPING_START, (d) => { if (!d?.foreign) turnAt = performance.now(); }));
    unsubs.push(eventBus.on('avatar_voice', () => { voiceAt = performance.now(); }));
    const avatarUnsub = eventBus.on('avatar_animate', (data) => {
        const { track, mode, source } = data || {};
        if (!track) return;
        const go = () => fireTag(track, mode);
        if (source !== 'text') return go();
        setTimeout(() => { if (running && voiceAt < turnAt) go(); }, TEXT_TAG_HOLD_MS);
    });
    if (avatarUnsub) unsubs.push(avatarUnsub);

    // --- Step into the chat: the sidebar box becomes her control panel ---
    const panel = container.querySelector('#avatar-stage-panel');
    const stSize = container.querySelector('#avatar-stage-size');
    const stGhost = container.querySelector('#avatar-stage-ghost');
    const stChairOn = container.querySelector('#avatar-stage-chair-on');
    const stChairX = container.querySelector('#avatar-stage-chair-x');
    const stageCfg = { on: false, size: 20, ghost: 30, chair: null, couch: false, desk: false, bed: false, ...(avatarConfig.stage || {}) };
    const saveStage = () => fetch('/api/plugin/avatar/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json',
                   'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]')?.content || '' },
        body: JSON.stringify({ stage: stageCfg }),
    }).catch(() => { /* this tab still has it */ });
    function setStage(want, save = true) {
        if (want) {
            if (displayMode !== 'sidebar') setDisplayMode('sidebar');
            stage.setSize(stageCfg.size);
            stage.setGhost(stageCfg.ghost);
            stage.setProps(stageCfg);
            stage.setFace(container.querySelector('#avatar-stage-face'));
            if (!stage.enter()) return;         // no chat on this page
        } else {
            stage.exit();
        }
        stageCfg.on = stage.isOn();
        displayEl.style.display = stageCfg.on ? 'none' : '';
        if (panel) panel.style.display = stageCfg.on ? '' : 'none';
        playing = null;
        applyWinningRail();
        if (save) saveStage();
    }
    if (stage && panel) {
        if (stSize) stSize.value = stageCfg.size;
        if (stChairOn) stChairOn.checked = stageCfg.chair != null;
        if (stChairX) { stChairX.value = stageCfg.chair ?? 88; stChairX.disabled = stageCfg.chair == null; }
        container.querySelector('#avatar-btn-stage')?.addEventListener('click', () => setStage(true));
        container.querySelector('#avatar-stage-back')?.addEventListener('click', () => setStage(false));
        stSize?.addEventListener('input', () => { stageCfg.size = +stSize.value; stage.setSize(stageCfg.size); });
        stSize?.addEventListener('change', saveStage);
        if (stGhost) stGhost.value = stageCfg.ghost;
        stGhost?.addEventListener('input', () => { stageCfg.ghost = +stGhost.value; stage.setGhost(stageCfg.ghost); });
        stGhost?.addEventListener('change', saveStage);
        const chairChanged = () => {
            stageCfg.chair = stChairOn.checked ? +stChairX.value : null;
            stChairX.disabled = !stChairOn.checked;
            stage.setProps(stageCfg);
        };
        for (const name of ['couch', 'desk', 'bed']) {
            const cb = container.querySelector(`#avatar-stage-${name}`);
            if (!cb) continue;
            cb.checked = !!stageCfg[name];
            cb.addEventListener('change', () => {
                stageCfg[name] = cb.checked;
                stage.setProps(stageCfg);
                playing = null;
                applyWinningRail();
                saveStage();
            });
        }
        stChairOn?.addEventListener('change', () => { chairChanged(); applyWinningRail(); saveStage(); });
        stChairX?.addEventListener('input', chairChanged);
        stChairX?.addEventListener('change', saveStage);
        panel.querySelectorAll('[data-go]').forEach(b => b.addEventListener('click', () => fireTag(b.dataset.go)));
        if (stageCfg.on) setStage(true, false);
    }

    // Render loop
    const clock = new THREE.Clock();
    let running = true;

    function animate() {
        if (!running) return;
        requestAnimationFrame(animate);
        const delta = clock.getDelta();
        if (mixer) mixer.update(delta);
        if (mouth) {
            mouth.update(delta);
            const sp = mouth.isSpeaking();
            orbitSystem.setSpeaking(sp);
            setSpeakingSlot(sp);
        }
        env.update(delta);
        if (stage?.isOn()) {
            stage.update(delta);
            stage.render();
            return;
        }
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
        clearTimeout(_userRailTtlTimer);
        clearInterval(_micPoll);
        unsubs.forEach(fn => fn());
        _chatUnsubs.forEach(fn => fn());
        mouth?.dispose();
        stage?.cleanup();
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
    // The watch is THIS instance's: it stops with it and only ever tears down
    // itself. (It used to call the module's _cleanup, which after a sidebar
    // rebuild is the NEXT instance's: the old one, seeing its own canvas gone,
    // killed the new avatar a second after it appeared.)
    const observer = new MutationObserver(() => {
        if (!running) { clearTimeout(_detachTimer); observer.disconnect(); return; }
        if (!document.contains(canvas) || !document.contains(displayEl)) {   // on the stage the canvas lives in the chat
            if (!_detachTimer) {
                _detachTimer = setTimeout(() => {
                    _detachTimer = null;
                    if (running && (!document.contains(canvas) || !document.contains(displayEl))) _thisCleanup();
                    if (!running) observer.disconnect();
                }, 1000);
            }
        } else if (_detachTimer) {
            clearTimeout(_detachTimer);
            _detachTimer = null;
        }
    });
    observer.observe(container.parentElement || document.body, { childList: true, subtree: true });
}
