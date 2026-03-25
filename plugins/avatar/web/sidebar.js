// 3D Avatar — threejs GLTF with animation blending driven by SSE events
import * as eventBus from '/static/core/event-bus.js';
import * as audio from '/static/audio.js';
import { triggerSendWithText } from '/static/handlers/send-handlers.js';
import { createEnvironment } from './environment.js';
import { createCameraOrbitSystem } from './camera-orbits.js';

const THREE_CDN = 'https://esm.sh/three@0.170.0';
const GLTF_CDN = 'https://esm.sh/three@0.170.0/addons/loaders/GLTFLoader.js';
const ORBIT_CDN = 'https://esm.sh/three@0.170.0/addons/controls/OrbitControls.js';
const CROSSFADE_MS = 400;

// Hardcoded fallback defaults (used when no config exists)
const FALLBACK_TRACK_MAP = {
    idle: 'idle', listening: 'listening', processing: 'thinking',
    thinking: 'thinking', typing: 'thinking', speaking: 'attention',
    toolcall: 'attention2', wakeword: 'attention', happy: 'happy',
    wave: 'wave', error: 'idle', agent: 'thinking', cron: 'thinking',
};
const FALLBACK_IDLE_POOL = [
    { track: 'idle', weight: 60, oneshot: false },
    { track: 'defaultanim', weight: 20, oneshot: false },
    { track: 'listening', weight: 8, oneshot: false },
    { track: 'attention', weight: 5, oneshot: true },
    { track: 'happy', weight: 4, oneshot: true },
    { track: 'wave', weight: 3, oneshot: true },
];
const FALLBACK_CAMERA = { x: 0, y: 1.3, z: 4.4 };
const FALLBACK_TARGET = { x: 0, y: 1.1, z: 0 };

// State machine — priority-based with persist/duration (not configurable)
const STATES = {
    idle:       { priority: 0 },
    listening:  { priority: 30, persist: true },
    processing: { priority: 25, persist: true },
    thinking:   { priority: 20, persist: true },
    typing:     { priority: 40, persist: true },
    speaking:   { priority: 50, persist: true },
    toolcall:   { priority: 35, duration: 3000 },
    wakeword:   { priority: 45, duration: 2000 },
    error:      { priority: 10, duration: 4000 },
    happy:      { priority: 5,  duration: 3000 },
    agent:      { priority: 15, persist: true },
    cron:       { priority: 12, duration: 3000 },
    wave:       { priority: 5,  duration: 4500 },
};

// force: true = always transitions, even through a persist state
const TRANSITIONS = {
    [eventBus.Events.STT_RECORDING_START]:  { state: 'listening' },
    [eventBus.Events.STT_RECORDING_END]:    { state: 'processing', force: true },
    [eventBus.Events.STT_PROCESSING]:       { state: 'processing' },
    [eventBus.Events.AI_TYPING_START]:      { state: 'typing' },
    [eventBus.Events.AI_TYPING_END]:        { state: 'happy', force: true },
    [eventBus.Events.TTS_PLAYING]:          { state: 'speaking' },
    [eventBus.Events.TTS_STOPPED]:          { state: 'idle', force: true },
    [eventBus.Events.TOOL_EXECUTING]:       { state: 'toolcall' },
    [eventBus.Events.TOOL_COMPLETE]:        { state: 'typing', force: true },
    [eventBus.Events.WAKEWORD_DETECTED]:    { state: 'wakeword' },
    [eventBus.Events.LLM_ERROR]:            { state: 'error', force: true },
    [eventBus.Events.TTS_ERROR]:            { state: 'error', force: true },
    [eventBus.Events.STT_ERROR]:            { state: 'error', force: true },
    [eventBus.Events.AGENT_SPAWNED]:        { state: 'agent' },
    [eventBus.Events.AGENT_COMPLETED]:      { state: 'happy', force: true },
    [eventBus.Events.AGENT_DISMISSED]:      { state: 'idle', force: true },
    [eventBus.Events.CONTINUITY_TASK_STARTING]: { state: 'cron' },
    [eventBus.Events.CONTINUITY_TASK_COMPLETE]: { state: 'idle', force: true },
};

// Track cleanup between sidebar reloads
let _cleanup = null;

export async function init(container) {
    if (_cleanup) _cleanup();

    const canvas = container.querySelector('#avatar-canvas');
    const statusEl = container.querySelector('#avatar-status');
    if (!canvas) return;

    // --- Display mode controls ---
    const displayEl = container.querySelector('#avatar-display');
    const btnExpand = container.querySelector('#avatar-btn-expand');
    const btnFullscreen = container.querySelector('#avatar-btn-fullscreen');
    let displayMode = 'sidebar';
    let _onDisplayModeChange = null;  // set after scene is ready

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
        if (e.key === 'Escape' && displayMode === 'fullwindow') {
            setDisplayMode('sidebar');
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
    _cleanup = () => {
        clearInterval(_micPoll);
        document.removeEventListener('keydown', _onEscKey);
        document.removeEventListener('fullscreenchange', _onFsChange);
        if (displayMode !== 'sidebar') {
            displayEl.classList.remove('avatar-fullwindow');
            canvas.style.height = '280px';
        }
        _cleanup = null;
    };

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

    // --- Load config from backend ---
    let avatarConfig = {};
    let modelFile = 'sapphire.glb';
    let trackMap = { ...FALLBACK_TRACK_MAP };
    let idlePool = [...FALLBACK_IDLE_POOL];
    let greetingTrack = 'wave';
    let camPos = { ...FALLBACK_CAMERA };
    let camTarget = { ...FALLBACK_TARGET };
    let modelScale = 1.0;

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
            }
        }
    } catch (e) { /* use fallbacks */ }

    const MODEL_URL = `/api/avatar/${modelFile}`;

    // Build oneshot set from idle pool config
    const ONESHOT_TRACKS = new Set(
        idlePool.filter(v => v.oneshot).map(v => v.track)
    );
    // Also treat known oneshot animations as oneshot
    for (const t of ['happy', 'wave', 'attention', 'attention2']) ONESHOT_TRACKS.add(t);

    // Weighted random idle pick
    function pickIdleVariant() {
        const total = idlePool.reduce((s, v) => s + v.weight, 0);
        let roll = Math.random() * total;
        for (const v of idlePool) {
            roll -= v.weight;
            if (roll <= 0) return v.track;
        }
        return trackMap.idle || 'idle';
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

    // Orbit toggle button
    const btnOrbit = container.querySelector('#avatar-btn-orbit');
    if (btnOrbit) {
        btnOrbit.classList.add('orbit-active');  // on by default
        btnOrbit.addEventListener('click', () => {
            const on = orbitSystem.toggle();
            btnOrbit.classList.toggle('orbit-active', on);
        });
    }

    // Double-click to reset camera
    canvas.addEventListener('dblclick', () => {
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

        // Start with greeting track, then idle
        const greetAction = greetingTrack ? actions[greetingTrack] : null;
        if (greetAction) {
            greetAction.setLoop(THREE.LoopOnce);
            greetAction.play();
            currentAction = greetAction;
            mixer.addEventListener('finished', function onGreetDone(e) {
                if (e.action === greetAction) {
                    mixer.removeEventListener('finished', onGreetDone);
                    crossfadeTo('idle');
                }
            });
        } else {
            crossfadeTo('idle');
        }

        // Enable avatar shadow casting for environment
        env.enableAvatarShadows(gltf.scene);

        // Wire environment toggle to display mode changes
        _onDisplayModeChange = (mode) => {
            const expanded = mode !== 'sidebar';
            env.setVisible(expanded);
            defaultAmbient.visible = !expanded;
            dirLight.visible = !expanded;
            // Rim light stays on always — it's her signature
        };

        // Expand zoom limits for environment exploration
        controls.maxDistance = 35;

    } catch (e) {
        console.error('[Avatar] Failed to load model:', e);
        canvas.style.display = 'none';
        container.innerHTML += '<div style="text-align:center;color:var(--text-muted);padding:8px;">Model failed to load</div>';
        return;
    }

    // --- Animation crossfade ---
    function crossfadeTo(stateName) {
        const trackName = trackMap[stateName] || stateName;  // allow raw track names for idle variety
        const action = actions[trackName];
        if (!action || currentAction === action) return;

        action.reset();
        action.setLoop(ONESHOT_TRACKS.has(trackName) ? THREE.LoopOnce : THREE.LoopRepeat);
        action.clampWhenFinished = true;

        if (currentAction) {
            action.crossFadeFrom(currentAction, CROSSFADE_MS / 1000, true);
        }
        action.play();
        currentAction = action;

        // When a oneshot finishes during idle, pick next idle variant
        if (ONESHOT_TRACKS.has(trackName) && current === 'idle') {
            mixer.addEventListener('finished', function onDone(e) {
                if (e.action === action) {
                    mixer.removeEventListener('finished', onDone);
                    if (current === 'idle') scheduleIdleVariant();
                }
            });
        }
    }

    // --- Idle variety system ---
    let idleTimer = null;

    function scheduleIdleVariant() {
        clearTimeout(idleTimer);
        const delay = 8000 + Math.random() * 12000;
        idleTimer = setTimeout(() => {
            if (current !== 'idle') return;
            const track = pickIdleVariant();
            crossfadeTo(track);
            if (!ONESHOT_TRACKS.has(track)) {
                scheduleIdleVariant();
            }
        }, delay);
    }

    // --- State machine ---
    let current = greetingTrack ? 'wave' : 'idle';
    let resetTimer = null;
    let _aiAnimLockUntil = 0;

    function setState(name, force = false) {
        if (Date.now() < _aiAnimLockUntil) return;

        const state = STATES[name];
        if (!state) return;

        const cur = STATES[current];
        if (!force && name !== 'idle' && cur && state.priority < cur.priority && cur.persist) return;

        clearTimeout(resetTimer);
        clearTimeout(idleTimer);
        current = name;
        crossfadeTo(name);
        if (statusEl) statusEl.textContent = name === 'idle' ? '' : name;

        if (name === 'idle') {
            scheduleIdleVariant();
        }

        if (state.duration) {
            resetTimer = setTimeout(() => setState('idle', true), state.duration);
        }
    }

    // Wire SSE events
    const unsubs = [];
    for (const [event, transition] of Object.entries(TRANSITIONS)) {
        const unsub = eventBus.on(event, () => setState(transition.state, transition.force));
        if (unsub) unsubs.push(unsub);
    }

    // AI-triggered animations: <<avatar: trackname>> in chat responses
    let _avatarReturnTimer = null;
    const avatarUnsub = eventBus.on('avatar_animate', (data) => {
        const { track, duration } = data || {};
        console.log(`[Avatar] Received avatar_animate: track="${track}" lock=${Date.now() < _aiAnimLockUntil}`);
        const action = actions[track];
        if (!action) {
            console.warn(`[Avatar] Track "${track}" not found in model`);
            return;
        }

        clearTimeout(_avatarReturnTimer);

        // Lock state machine — protect this animation for its duration (min 2s)
        const clipDuration = action.getClip().duration * 1000;
        const lockMs = duration || Math.max(clipDuration, 2000);
        _aiAnimLockUntil = Date.now() + lockMs;

        // Play as oneshot overlay
        action.reset();
        action.setLoop(THREE.LoopOnce);
        action.clampWhenFinished = true;
        if (currentAction) action.crossFadeFrom(currentAction, CROSSFADE_MS / 1000, true);
        action.play();
        currentAction = action;

        // Return to previous state when done
        const returnToPrev = () => {
            if (currentAction !== action) return;
            crossfadeTo(current);
        };

        if (duration) {
            _avatarReturnTimer = setTimeout(returnToPrev, duration);
        } else {
            mixer.addEventListener('finished', function onDone(e) {
                if (e.action === action) {
                    mixer.removeEventListener('finished', onDone);
                    returnToPrev();
                }
            });
        }
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
        orbitSystem.update(delta);
        controls.update();
        resize();
        renderer.render(scene, camera);
    }
    animate();

    // Cleanup
    _cleanup = () => {
        running = false;
        clearTimeout(resetTimer);
        clearTimeout(idleTimer);
        clearTimeout(_avatarReturnTimer);
        clearInterval(_micPoll);
        unsubs.forEach(fn => fn());
        orbitSystem.cleanup();
        controls.dispose();
        renderer.dispose();
        document.removeEventListener('keydown', _onEscKey);
        document.removeEventListener('fullscreenchange', _onFsChange);
        if (displayMode !== 'sidebar') {
            displayEl.classList.remove('avatar-fullwindow');
            canvas.style.height = '280px';
        }
        _cleanup = null;
    };

    const observer = new MutationObserver(() => {
        if (!document.contains(canvas)) {
            if (_cleanup) _cleanup();
            observer.disconnect();
        }
    });
    observer.observe(container.parentElement || document.body, { childList: true, subtree: true });
}
