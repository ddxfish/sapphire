// First-person player controller — WASD + mouse look + jump + crouch
// Uses PointerLock API for mouse capture

const MOVE_SPEED = 5;
const SPRINT_MULT = 1.8;
const LOOK_SENSITIVITY = 0.002;
const GRAVITY = -15;
const JUMP_VELOCITY = 6;
const PLAYER_HEIGHT = 1.7;
const CROUCH_HEIGHT = 0.9;
const GROUND_Y = 0;

export function createPlayerController(camera, controls, canvas, THREE) {
    let enabled = false;
    let locked = false;

    // Movement state
    const keys = {};
    let velocityY = 0;
    let onGround = true;
    let crouching = false;
    let currentHeight = PLAYER_HEIGHT;

    // Camera rotation (euler angles)
    let yaw = 0;
    let pitch = 0;

    // Vectors for movement calculation
    const moveDir = new THREE.Vector3();
    const forward = new THREE.Vector3();
    const right = new THREE.Vector3();

    // --- Input handlers ---
    const PLAYER_KEYS = new Set([
        'KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyC', 'Space',
        'ShiftLeft', 'ShiftRight', 'ControlLeft', 'ControlRight',
        'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
    ]);
    const onKeyDown = (e) => {
        if (!enabled || !locked) return;
        if (PLAYER_KEYS.has(e.code)) e.preventDefault();
        keys[e.code] = true;
        if (e.code === 'Space') tryJump();
    };

    const onKeyUp = (e) => {
        if (!enabled) return;
        keys[e.code] = false;
    };

    const onMouseMove = (e) => {
        if (!enabled || !locked) return;
        yaw   -= e.movementX * LOOK_SENSITIVITY;
        pitch -= e.movementY * LOOK_SENSITIVITY;
        pitch = Math.max(-Math.PI * 0.45, Math.min(Math.PI * 0.45, pitch));
    };

    const onPointerLockChange = () => {
        locked = document.pointerLockElement === canvas;
    };

    const onClick = () => {
        if (enabled && !locked) {
            canvas.requestPointerLock();
        }
    };

    // --- Actions ---
    function tryJump() {
        if (onGround) {
            velocityY = JUMP_VELOCITY;
            onGround = false;
        }
    }

    function activate() {
        if (enabled) return;
        enabled = true;

        // Capture current camera orientation as starting yaw/pitch
        const dir = new THREE.Vector3();
        camera.getWorldDirection(dir);
        yaw = Math.atan2(-dir.x, -dir.z);
        pitch = Math.asin(dir.y);

        // Disable orbit controls
        controls.enabled = false;

        // Bind events
        document.addEventListener('keydown', onKeyDown);
        document.addEventListener('keyup', onKeyUp);
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('pointerlockchange', onPointerLockChange);
        canvas.addEventListener('click', onClick);

        // Lock pointer immediately
        canvas.requestPointerLock();

        currentHeight = PLAYER_HEIGHT;
        velocityY = 0;
        crouching = false;
        Object.keys(keys).forEach(k => keys[k] = false);
    }

    function deactivate() {
        if (!enabled) return;
        enabled = false;

        // Release pointer lock
        if (document.pointerLockElement === canvas) {
            document.exitPointerLock();
        }

        // Unbind events
        document.removeEventListener('keydown', onKeyDown);
        document.removeEventListener('keyup', onKeyUp);
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('pointerlockchange', onPointerLockChange);
        canvas.removeEventListener('click', onClick);

        // Re-enable orbit controls
        controls.enabled = true;
        controls.update();

        locked = false;
        Object.keys(keys).forEach(k => keys[k] = false);
    }

    function toggle() {
        if (enabled) deactivate();
        else activate();
        return enabled;
    }

    function update(delta) {
        if (!enabled) return;

        // Crouch (C or Ctrl)
        crouching = keys['KeyC'] || keys['ControlLeft'] || keys['ControlRight'];
        const targetHeight = crouching ? CROUCH_HEIGHT : PLAYER_HEIGHT;
        currentHeight += (targetHeight - currentHeight) * Math.min(1, delta * 10);

        // Gravity + jump
        if (!onGround) {
            velocityY += GRAVITY * delta;
        }
        let camY = camera.position.y + velocityY * delta;
        const groundLevel = GROUND_Y + currentHeight;
        if (onGround) {
            // Stay on ground — tracks crouch height changes
            camY = groundLevel;
            velocityY = 0;
        } else if (camY <= groundLevel) {
            camY = groundLevel;
            velocityY = 0;
            onGround = true;
        }

        // Movement direction from keys
        moveDir.set(0, 0, 0);
        if (keys['KeyW'] || keys['ArrowUp'])    moveDir.z += 1;
        if (keys['KeyS'] || keys['ArrowDown'])  moveDir.z -= 1;
        if (keys['KeyA'] || keys['ArrowLeft'])   moveDir.x -= 1;
        if (keys['KeyD'] || keys['ArrowRight']) moveDir.x += 1;

        if (moveDir.lengthSq() > 0) {
            moveDir.normalize();
            const sprinting = keys['ShiftLeft'] || keys['ShiftRight'];
            const speed = MOVE_SPEED * (sprinting ? SPRINT_MULT : 1) * delta;

            // Forward/right relative to yaw (ignore pitch for movement)
            forward.set(-Math.sin(yaw), 0, -Math.cos(yaw));
            right.set(Math.cos(yaw), 0, -Math.sin(yaw));

            camera.position.addScaledVector(forward, moveDir.z * speed);
            camera.position.addScaledVector(right, moveDir.x * speed);
        }

        camera.position.y = camY;

        // Apply look rotation
        camera.quaternion.setFromEuler(new THREE.Euler(pitch, yaw, 0, 'YXZ'));
    }

    function isEnabled() { return enabled; }
    function isLocked() { return locked; }

    function cleanup() {
        if (enabled) deactivate();
    }

    return {
        update,
        toggle,
        activate,
        deactivate,
        isEnabled,
        isLocked,
        cleanup,
    };
}
