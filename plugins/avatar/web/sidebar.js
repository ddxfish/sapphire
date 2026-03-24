// 3D Avatar — threejs GLTF with animation blending driven by SSE events
import * as eventBus from '/static/core/event-bus.js';

const THREE_CDN = 'https://esm.sh/three@0.170.0';
const GLTF_CDN = 'https://esm.sh/three@0.170.0/addons/loaders/GLTFLoader.js';
const ORBIT_CDN = 'https://esm.sh/three@0.170.0/addons/controls/OrbitControls.js';
const MODEL_URL = '/api/avatar/sapphire.glb';
const CROSSFADE_MS = 400;

// Default camera
const CAM_DEFAULT = { x: 0, y: 1.3, z: 4.4 };
const CAM_TARGET  = { x: 0, y: 1.1, z: 0 };
const CAM_ZOOM_MIN = 2.0;
const CAM_ZOOM_MAX = 8.0;

// State -> animation track mapping
const TRACK_MAP = {
    idle:       'idle',
    listening:  'listening',
    processing: 'thinking',
    thinking:   'thinking',
    typing:     'thinking',
    speaking:   'attention',
    toolcall:   'attention2',
    wakeword:   'attention',
    happy:      'happy',
    wave:       'wave',
    error:      'idle',
    agent:      'thinking',
    cron:       'thinking',
};

// State machine — priority-based with persist/duration
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

// force: true = always transitions, even through a persist state (used for "end" events)
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

// Idle variety — weighted random picks when idle
const IDLE_VARIANTS = [
    { track: 'idle',         weight: 60 },  // most of the time, just breathe
    { track: 'defaultanim',  weight: 20 },  // secondary idle
    { track: 'listening',    weight: 8 },   // glance around
    { track: 'attention',    weight: 5 },   // perk up briefly
    { track: 'happy',        weight: 4 },   // little smile
    { track: 'wave',         weight: 3 },   // rare wave
];

function pickIdleVariant() {
    const total = IDLE_VARIANTS.reduce((s, v) => s + v.weight, 0);
    let roll = Math.random() * total;
    for (const v of IDLE_VARIANTS) {
        roll -= v.weight;
        if (roll <= 0) return v.track;
    }
    return 'idle';
}

// Track cleanup between sidebar reloads
let _cleanup = null;

export async function init(container) {
    if (_cleanup) _cleanup();

    const canvas = container.querySelector('#avatar-canvas');
    const statusEl = container.querySelector('#avatar-status');
    if (!canvas) return;

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

    // Camera
    const camera = new THREE.PerspectiveCamera(30, canvas.clientWidth / canvas.clientHeight, 0.1, 100);
    camera.position.set(CAM_DEFAULT.x, CAM_DEFAULT.y, CAM_DEFAULT.z);

    // Orbit controls
    const controls = new OrbitControls(camera, canvas);
    controls.target.set(CAM_TARGET.x, CAM_TARGET.y, CAM_TARGET.z);
    controls.minDistance = CAM_ZOOM_MIN;
    controls.maxDistance = CAM_ZOOM_MAX;
    controls.enablePan = true;
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.5;
    controls.panSpeed = 0.4;
    // Limit vertical so you can't go under the floor
    controls.maxPolarAngle = Math.PI * 0.85;
    controls.update();

    // Double-click to reset camera
    canvas.addEventListener('dblclick', () => {
        camera.position.set(CAM_DEFAULT.x, CAM_DEFAULT.y, CAM_DEFAULT.z);
        controls.target.set(CAM_TARGET.x, CAM_TARGET.y, CAM_TARGET.z);
        controls.update();
    });

    // Lighting
    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
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
        mixer = new THREE.AnimationMixer(gltf.scene);

        for (const clip of gltf.animations) {
            const action = mixer.clipAction(clip);
            action.clampWhenFinished = true;
            actions[clip.name] = action;
        }

        // Start with wave, then idle
        if (actions.wave) {
            const waveAction = actions.wave;
            waveAction.setLoop(THREE.LoopOnce);
            waveAction.play();
            currentAction = waveAction;
            mixer.addEventListener('finished', function onWaveDone(e) {
                if (e.action === waveAction) {
                    mixer.removeEventListener('finished', onWaveDone);
                    crossfadeTo('idle');
                }
            });
        } else {
            crossfadeTo('idle');
        }
    } catch (e) {
        console.error('[Avatar] Failed to load model:', e);
        canvas.style.display = 'none';
        container.innerHTML += '<div style="text-align:center;color:var(--text-muted);padding:8px;">Model failed to load</div>';
        return;
    }

    // --- Animation crossfade ---
    // oneshot tracks play once then return to idle
    const ONESHOT_TRACKS = new Set(['happy', 'wave', 'attention', 'attention2']);

    function crossfadeTo(stateName) {
        const trackName = TRACK_MAP[stateName] || stateName;  // allow raw track names for idle variety
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
        // Next variant in 8-20 seconds
        const delay = 8000 + Math.random() * 12000;
        idleTimer = setTimeout(() => {
            if (current !== 'idle') return;
            const track = pickIdleVariant();
            crossfadeTo(track);
            // If it's a looping track (idle, defaultanim), schedule next switch
            if (!ONESHOT_TRACKS.has(track)) {
                scheduleIdleVariant();
            }
            // Oneshot tracks schedule via the 'finished' listener in crossfadeTo
        }, delay);
    }

    // --- State machine ---
    let current = 'wave';
    let resetTimer = null;
    let _aiAnimLockUntil = 0;  // AI-triggered animations are protected until this timestamp

    function setState(name, force = false) {
        // AI-triggered animation guard — block all state changes during the lock window
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
            if (currentAction !== action) return;  // something else took over
            crossfadeTo(current);  // re-enter current state machine state
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
        unsubs.forEach(fn => fn());
        controls.dispose();
        renderer.dispose();
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
