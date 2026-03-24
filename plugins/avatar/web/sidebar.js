// 3D Avatar — threejs GLTF with animation blending driven by SSE events
import * as eventBus from '/static/core/event-bus.js';

const THREE_CDN = 'https://esm.sh/three@0.170.0';
const GLTF_CDN = 'https://esm.sh/three@0.170.0/addons/loaders/GLTFLoader.js';
const MODEL_URL = '/api/avatar/sapphire.glb';
const CROSSFADE_MS = 400;

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

// Track cleanup between sidebar reloads
let _cleanup = null;

export async function init(container) {
    // Tear down previous instance if sidebar reloaded
    if (_cleanup) _cleanup();

    const canvas = container.querySelector('#avatar-canvas');
    const statusEl = container.querySelector('#avatar-status');
    if (!canvas) return;

    // Dynamic import three.js from CDN (cached after first load)
    let THREE, GLTFLoader;
    try {
        THREE = await import(THREE_CDN);
        const gltfMod = await import(GLTF_CDN);
        GLTFLoader = gltfMod.GLTFLoader;
    } catch (e) {
        console.error('[Avatar] Failed to load three.js:', e);
        canvas.style.display = 'none';
        container.innerHTML += '<div style="text-align:center;color:var(--text-muted);padding:8px;">Three.js failed to load</div>';
        return;
    }

    // Scene setup — transparent background
    const scene = new THREE.Scene();
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;

    // Camera — pulled back for full upper body + face
    const camera = new THREE.PerspectiveCamera(30, canvas.clientWidth / canvas.clientHeight, 0.1, 100);
    camera.position.set(0, 1.3, 4.4);
    camera.lookAt(0, 1.1, 0);

    // Lighting
    const ambient = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambient);
    const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
    dirLight.position.set(2, 3, 2);
    scene.add(dirLight);
    const rimLight = new THREE.DirectionalLight(0x4a9eff, 0.4);
    rimLight.position.set(-1, 2, -2);
    scene.add(rimLight);

    // Resize handler
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

        // Start with wave, then crossfade to idle
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

    // Animation crossfade
    function crossfadeTo(stateName) {
        const trackName = TRACK_MAP[stateName] || 'idle';
        const action = actions[trackName];
        if (!action || currentAction === action) return;

        action.reset();
        action.setLoop(
            trackName === 'happy' || trackName === 'wave' || trackName === 'attention'
                ? THREE.LoopOnce : THREE.LoopRepeat
        );
        action.clampWhenFinished = true;

        if (currentAction) {
            action.crossFadeFrom(currentAction, CROSSFADE_MS / 1000, true);
        }
        action.play();
        currentAction = action;
    }

    // State machine
    let current = 'wave';
    let resetTimer = null;

    function setState(name, force = false) {
        const state = STATES[name];
        if (!state) return;

        const cur = STATES[current];
        if (!force && name !== 'idle' && cur && state.priority < cur.priority && cur.persist) return;

        clearTimeout(resetTimer);
        current = name;
        crossfadeTo(name);
        if (statusEl) statusEl.textContent = name === 'idle' ? '' : name;

        if (state.duration) {
            resetTimer = setTimeout(() => setState('idle'), state.duration);
        }
    }

    // Wire SSE events — store unsubscribe functions for cleanup
    const unsubs = [];
    for (const [event, transition] of Object.entries(TRANSITIONS)) {
        const unsub = eventBus.on(event, () => setState(transition.state, transition.force));
        if (unsub) unsubs.push(unsub);
    }

    // Render loop
    const clock = new THREE.Clock();
    let running = true;

    function animate() {
        if (!running) return;
        requestAnimationFrame(animate);
        if (mixer) mixer.update(clock.getDelta());
        resize();
        renderer.render(scene, camera);
    }
    animate();

    // Cleanup function for re-init or removal
    _cleanup = () => {
        running = false;
        clearTimeout(resetTimer);
        unsubs.forEach(fn => fn());
        renderer.dispose();
        _cleanup = null;
    };

    // Auto-cleanup if DOM is removed
    const observer = new MutationObserver(() => {
        if (!document.contains(canvas)) {
            if (_cleanup) _cleanup();
            observer.disconnect();
        }
    });
    observer.observe(container.parentElement || document.body, { childList: true, subtree: true });
}
