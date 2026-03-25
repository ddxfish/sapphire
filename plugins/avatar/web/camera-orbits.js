// Camera orbit system — idle auto-orbits and random starts
//
// Orbit types:
//   Front-focused (120 degree arc, weighted heavily):
//     - gentle_sine: slow horizontal sine wave, slight vertical bob
//     - soft_sway: very slow pendulum, almost imperceptible
//     - elliptical_close: tight ellipse, portrait-distance, focused on face
//     - elliptical_wide: wider ellipse, mid-body framing
//     - w_sweep: W-shaped path across the front arc
//   Full 360 (rare):
//     - full_orbit_slow: complete circle with vertical sine, very slow
//     - full_orbit_drift: full circle with varying speed and altitude

const DEG = Math.PI / 180;

// Orbit definitions
// Each orbit: fn(t, params) => {angle, radius, height}
// angle: radians from front (0 = directly in front)
// radius: distance from target
// height: camera Y offset from target

const ORBITS = {
    gentle_sine: {
        weight: 30,
        speed: [0.03, 0.06],
        fn: (t, p) => ({
            angle: Math.sin(t * p.speed) * 50 * DEG,
            radius: p.baseDist * (0.95 + Math.sin(t * p.speed * 0.7) * 0.05),
            height: p.baseHeight + Math.sin(t * p.speed * 1.3) * p.modelHeight * 0.04,
        }),
    },
    soft_sway: {
        weight: 25,
        speed: [0.015, 0.03],
        fn: (t, p) => ({
            angle: Math.sin(t * p.speed) * 30 * DEG,
            radius: p.baseDist,
            height: p.baseHeight + Math.sin(t * p.speed * 0.8) * p.modelHeight * 0.02,
        }),
    },
    elliptical_close: {
        weight: 15,
        speed: [0.025, 0.045],
        fn: (t, p) => {
            const a = t * p.speed;
            return {
                angle: Math.sin(a) * 45 * DEG,
                radius: p.baseDist * (0.7 + Math.cos(a * 0.6) * 0.1),
                height: p.baseHeight + p.modelHeight * 0.15 + Math.sin(a * 0.5) * p.modelHeight * 0.05,
            };
        },
    },
    elliptical_wide: {
        weight: 15,
        speed: [0.02, 0.04],
        fn: (t, p) => {
            const a = t * p.speed;
            return {
                angle: Math.sin(a) * 55 * DEG,
                radius: p.baseDist * (1.0 + Math.sin(a * 0.8) * 0.15),
                height: p.baseHeight + Math.sin(a * 0.6) * p.modelHeight * 0.06,
            };
        },
    },
    w_sweep: {
        weight: 10,
        speed: [0.03, 0.05],
        fn: (t, p) => {
            const a = t * p.speed;
            // W shape: two sine waves summed
            const sweep = Math.sin(a) * 0.6 + Math.sin(a * 2.1) * 0.4;
            return {
                angle: sweep * 50 * DEG,
                radius: p.baseDist * (0.9 + Math.cos(a * 0.5) * 0.1),
                height: p.baseHeight + Math.sin(a * 1.5) * p.modelHeight * 0.05,
            };
        },
    },
    full_orbit_slow: {
        weight: 3,
        speed: [0.012, 0.02],
        fn: (t, p) => ({
            angle: t * p.speed,  // continuous rotation
            radius: p.baseDist * (1.0 + Math.sin(t * p.speed * 3) * 0.08),
            height: p.baseHeight + Math.sin(t * p.speed * 2) * p.modelHeight * 0.1,
        }),
    },
    full_orbit_drift: {
        weight: 2,
        speed: [0.015, 0.025],
        fn: (t, p) => {
            const a = t * p.speed;
            // Varying speed via sine modulation
            const drift = a + Math.sin(a * 0.3) * 0.5;
            return {
                angle: drift,
                radius: p.baseDist * (0.9 + Math.sin(a * 0.7) * 0.15),
                height: p.baseHeight + Math.sin(a * 0.4) * p.modelHeight * 0.12,
            };
        },
    },
};

function pickOrbit() {
    const entries = Object.entries(ORBITS);
    const total = entries.reduce((s, [, o]) => s + o.weight, 0);
    let roll = Math.random() * total;
    for (const [name, orbit] of entries) {
        roll -= orbit.weight;
        if (roll <= 0) {
            const [lo, hi] = orbit.speed;
            const speed = lo + Math.random() * (hi - lo);
            return { name, fn: orbit.fn, speed };
        }
    }
    return { name: 'gentle_sine', fn: ORBITS.gentle_sine.fn, speed: 0.04 };
}

export function createCameraOrbitSystem(camera, controls, THREE) {
    let enabled = true;
    let active = false;
    let _time = 0;
    let _idleTime = 0;
    let _orbit = pickOrbit();
    let _userInteracting = false;
    let _lastUserInput = 0;

    // Model info — set after model loads
    let modelCenter = { x: 0, y: 1.15, z: 0 };
    let modelHeight = 2.3;
    let baseDist = 5.5;
    let baseHeight = 1.3;  // camera Y when looking at upper body (~50% up)

    const IDLE_DELAY = 8;          // seconds of no input before orbit starts
    const TRANSITION_SPEED = 1.5;  // seconds to blend into orbit
    let _blendFactor = 0;          // 0 = user camera, 1 = orbit camera

    // Saved user camera state for blending back
    let _userCamPos = null;

    // --- User interaction detection ---
    const _onInput = () => {
        _lastUserInput = performance.now();
        if (active) {
            // User took over — stop orbiting, blend out
            active = false;
            _blendFactor = 0;
            _idleTime = 0;
        }
    };

    const inputEvents = ['mousedown', 'wheel', 'touchstart', 'pointerdown'];
    const canvas = controls.domElement;
    for (const evt of inputEvents) {
        canvas.addEventListener(evt, _onInput, { passive: true });
    }

    function setModelInfo(center, height) {
        modelCenter = { x: center.x, y: center.y, z: center.z };
        modelHeight = height;
        baseDist = Math.max(height, 1.5) * 2.2;
        baseHeight = center.y + height * 0.15;  // slightly above center = upper body framing
    }

    function randomStart() {
        // Random camera position focused on upper body, front 120 degrees
        const angle = (Math.random() - 0.5) * 120 * DEG;
        const dist = baseDist * (0.7 + Math.random() * 0.5);
        const height = baseHeight + (Math.random() - 0.3) * modelHeight * 0.15;

        const x = modelCenter.x + Math.sin(angle) * dist;
        const z = modelCenter.z + Math.cos(angle) * dist;

        camera.position.set(x, height, z);
        controls.target.set(modelCenter.x, modelCenter.y + modelHeight * 0.15, modelCenter.z);
        controls.update();
    }

    function update(delta) {
        if (!enabled) return;

        const now = performance.now();
        const idleSec = (now - _lastUserInput) / 1000;

        if (!active && idleSec > IDLE_DELAY) {
            // Start orbiting
            active = true;
            _time = 0;
            _blendFactor = 0;
            _orbit = pickOrbit();
            _userCamPos = camera.position.clone();
        }

        if (!active) return;

        _time += delta;

        // Blend in smoothly
        if (_blendFactor < 1) {
            _blendFactor = Math.min(1, _blendFactor + delta / TRANSITION_SPEED);
        }

        const params = {
            speed: _orbit.speed,
            baseDist,
            baseHeight,
            modelHeight,
        };

        const o = _orbit.fn(_time, params);

        // Convert orbit coords to world position
        const targetX = modelCenter.x + Math.sin(o.angle) * o.radius;
        const targetZ = modelCenter.z + Math.cos(o.angle) * o.radius;
        const targetY = o.height;

        // Smooth blend from current position
        const ease = _blendFactor * _blendFactor * (3 - 2 * _blendFactor); // smoothstep
        camera.position.x += (targetX - camera.position.x) * ease * delta * 2;
        camera.position.y += (targetY - camera.position.y) * ease * delta * 2;
        camera.position.z += (targetZ - camera.position.z) * ease * delta * 2;

        // Target stays on upper body
        const lookY = modelCenter.y + modelHeight * 0.15;
        controls.target.set(modelCenter.x, lookY, modelCenter.z);

        // Switch to a new orbit pattern every 30-60 seconds
        if (_time > 30 + Math.random() * 30) {
            _time = 0;
            _orbit = pickOrbit();
        }
    }

    function toggle() {
        enabled = !enabled;
        if (!enabled && active) {
            active = false;
            _blendFactor = 0;
        }
        if (enabled) {
            _lastUserInput = performance.now();  // reset idle timer
        }
        return enabled;
    }

    function isEnabled() { return enabled; }
    function isActive() { return active; }

    function cleanup() {
        for (const evt of inputEvents) {
            canvas.removeEventListener(evt, _onInput);
        }
    }

    return {
        update,
        toggle,
        isEnabled,
        isActive,
        setModelInfo,
        randomStart,
        cleanup,
    };
}
