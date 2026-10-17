// Missile Command — Protect Sapphire from incoming missiles
// Click to shoot (raycast), missiles fly toward her, waves get harder

const SAPPHIRE_POS = { x: 0, y: 1.2, z: 0 };
const SPAWN_RADIUS = 30;
const MISSILE_SPEED_BASE = 3;
const MISSILE_SPEED_WAVE = 0.4;   // speed increase per wave
const WAVE_DELAY = 4;             // seconds between waves
const MISSILES_BASE = 3;          // missiles in wave 1
const MISSILES_PER_WAVE = 2;      // extra missiles per wave
const MAX_HEALTH = 5;

export function createMissileCommand(scene, THREE, camera, canvas, eventDispatch) {
    let active = false;
    let score = 0;
    let wave = 0;
    let health = MAX_HEALTH;
    let _time = 0;
    let _waveTimer = 0;
    let _missileSpeed = MISSILE_SPEED_BASE;
    let _gameOver = false;

    const gameGroup = new THREE.Group();
    const raycaster = new THREE.Raycaster();
    const screenCenter = new THREE.Vector2(0, 0);

    // Missile pool
    const missiles = [];
    const explosions = [];

    // Materials (shared)
    const missileMat = new THREE.MeshBasicMaterial({ color: 0xff3322, transparent: true, opacity: 0.9 });
    const missileGeo = new THREE.SphereGeometry(0.15, 8, 8);
    const trailMat = new THREE.MeshBasicMaterial({ color: 0xff6644, transparent: true, opacity: 0.4 });
    const trailGeo = new THREE.SphereGeometry(0.08, 6, 6);

    // HUD elements (created on start)
    let hud = null;

    function createHUD() {
        const el = document.createElement('div');
        el.id = 'missile-hud';
        el.style.cssText = `
            position: absolute; top: 16px; left: 50%; transform: translateX(-50%);
            z-index: 10; display: flex; gap: 24px; align-items: center;
            padding: 8px 20px; border-radius: 8px;
            background: rgba(0,0,0,0.5); backdrop-filter: blur(4px);
            font-family: -apple-system, sans-serif; font-size: 14px; color: #fff;
            pointer-events: none; user-select: none;
        `;
        el.innerHTML = `
            <span>WAVE <b id="mc-wave">0</b></span>
            <span>SCORE <b id="mc-score">0</b></span>
            <span id="mc-health"></span>
            <span id="mc-status" style="color: #4a9eff; font-size: 12px;"></span>
        `;
        return el;
    }

    function updateHUD() {
        if (!hud) return;
        const waveEl = hud.querySelector('#mc-wave');
        const scoreEl = hud.querySelector('#mc-score');
        const healthEl = hud.querySelector('#mc-health');
        const statusEl = hud.querySelector('#mc-status');
        if (waveEl) waveEl.textContent = wave;
        if (scoreEl) scoreEl.textContent = score;
        if (healthEl) healthEl.innerHTML = Array(health).fill('<span style="color:#ff4466">&#x2665;</span>').join(' ');
        if (statusEl) statusEl.textContent = _gameOver ? 'GAME OVER — press G to restart' : '';
    }

    function spawnMissile() {
        // Random position on sphere around Sapphire
        const theta = Math.random() * Math.PI * 2;
        const phi = 0.2 + Math.random() * 0.6;  // not from directly above/below
        const x = SPAWN_RADIUS * Math.sin(phi) * Math.cos(theta);
        const y = 2 + Math.random() * 8;
        const z = SPAWN_RADIUS * Math.sin(phi) * Math.sin(theta);

        const mesh = new THREE.Mesh(missileGeo, missileMat.clone());
        mesh.position.set(x, y, z);

        // Single trail sphere (not 3 — perf)
        const trail = new THREE.Mesh(trailGeo, trailMat.clone());
        gameGroup.add(trail);
        const trails = [{ mesh: trail, delay: 0.1 }];

        gameGroup.add(mesh);

        // Direction toward Sapphire
        const dir = new THREE.Vector3(
            SAPPHIRE_POS.x - x,
            SAPPHIRE_POS.y - y,
            SAPPHIRE_POS.z - z
        ).normalize();

        missiles.push({
            mesh,
            trails,
            dir,
            positions: [],
            alive: true,
        });
    }

    // Shared explosion geometry (reuse, don't recreate)
    const explGeo = new THREE.SphereGeometry(0.06, 4, 4);

    function spawnExplosion(pos) {
        const count = 6;
        const particles = [];
        for (let i = 0; i < count; i++) {
            const p = new THREE.Mesh(
                explGeo,
                new THREE.MeshBasicMaterial({ color: 0xffaa22, transparent: true, opacity: 1 })
            );
            p.position.copy(pos);
            const vel = new THREE.Vector3(
                (Math.random() - 0.5) * 6,
                (Math.random() - 0.5) * 6,
                (Math.random() - 0.5) * 6
            );
            gameGroup.add(p);
            particles.push({ mesh: p, vel, life: 0.5 + Math.random() * 0.2 });
        }
        explosions.push({ particles, age: 0 });
    }

    function spawnWave() {
        wave++;
        _missileSpeed = MISSILE_SPEED_BASE + wave * MISSILE_SPEED_WAVE;
        const count = MISSILES_BASE + (wave - 1) * MISSILES_PER_WAVE;
        // Stagger spawns over 2 seconds
        for (let i = 0; i < count; i++) {
            setTimeout(() => { if (active && !_gameOver) spawnMissile(); }, i * (2000 / count));
        }
        _waveTimer = 0;
    }

    function shoot() {
        if (!active || _gameOver) return;

        raycaster.setFromCamera(screenCenter, camera);
        const meshes = missiles.filter(m => m.alive).map(m => m.mesh);
        const hits = raycaster.intersectObjects(meshes);

        if (hits.length > 0) {
            const hitMesh = hits[0].object;
            const missile = missiles.find(m => m.mesh === hitMesh);
            if (missile && missile.alive) {
                missile.alive = false;
                spawnExplosion(missile.mesh.position.clone());
                // Cleanup missile
                gameGroup.remove(missile.mesh);
                for (const t of missile.trails) gameGroup.remove(t.mesh);
                score++;
                updateHUD();
            }
        }

        // Muzzle flash at crosshair (brief)
        // (visual feedback even on miss)
    }

    function damageSapphire() {
        health--;
        updateHUD();
        // Trigger avatar reaction
        if (eventDispatch) eventDispatch('avatar_animate', { track: 'attention2', duration: 1500 });

        if (health <= 0) {
            _gameOver = true;
            updateHUD();
            if (eventDispatch) eventDispatch('avatar_animate', { track: 'wave', duration: 4000 });
        }
    }

    // --- Input (listen on document — pointer lock routes events there) ---
    const onMouseDown = (e) => {
        if (!active || !document.pointerLockElement) return;
        if (e.button === 0) { e.preventDefault(); shoot(); }
    };

    // --- Public API ---
    function start(parentEl) {
        if (active && !_gameOver) return;

        // Reset if game over
        score = 0;
        wave = 0;
        health = MAX_HEALTH;
        _time = 0;
        _waveTimer = 0;
        _gameOver = false;
        _missileSpeed = MISSILE_SPEED_BASE;

        // Clear old missiles/explosions
        while (gameGroup.children.length) gameGroup.remove(gameGroup.children[0]);
        missiles.length = 0;
        explosions.length = 0;

        // HUD
        if (hud) hud.remove();
        hud = createHUD();
        parentEl.appendChild(hud);
        updateHUD();

        scene.add(gameGroup);
        document.addEventListener('mousedown', onMouseDown);
        active = true;

        // First wave after short delay
        setTimeout(() => { if (active) spawnWave(); }, 2000);
    }

    function stop() {
        if (!active) return;
        active = false;
        _gameOver = false;

        document.removeEventListener('mousedown', onMouseDown);

        // Cleanup
        while (gameGroup.children.length) gameGroup.remove(gameGroup.children[0]);
        missiles.length = 0;
        explosions.length = 0;

        scene.remove(gameGroup);
        if (hud) { hud.remove(); hud = null; }
    }

    function update(delta) {
        if (!active) return;
        _time += delta;

        if (_gameOver) return;

        // Wave timer
        _waveTimer += delta;
        const allDead = missiles.every(m => !m.alive);
        if (allDead && missiles.length > 0 && _waveTimer > 1) {
            // All missiles destroyed, next wave
            missiles.length = 0;
            _waveTimer = 0;
        }
        if (missiles.length === 0 && _waveTimer > WAVE_DELAY) {
            spawnWave();
        }

        // Move missiles
        for (const m of missiles) {
            if (!m.alive) continue;

            // Store position history for trails
            m.positions.unshift(m.mesh.position.clone());
            if (m.positions.length > 4) m.positions.pop();

            // Move toward Sapphire
            m.mesh.position.addScaledVector(m.dir, _missileSpeed * delta);

            // Update trails
            for (const t of m.trails) {
                const idx = Math.floor(t.delay / 0.08);
                if (m.positions[idx]) {
                    t.mesh.position.copy(m.positions[idx]);
                    t.mesh.visible = true;
                } else {
                    t.mesh.visible = false;
                }
            }

            // Pulse glow
            m.mesh.material.opacity = 0.7 + Math.sin(_time * 8) * 0.3;

            // Check if reached Sapphire
            const dist = m.mesh.position.distanceTo(new THREE.Vector3(SAPPHIRE_POS.x, SAPPHIRE_POS.y, SAPPHIRE_POS.z));
            if (dist < 0.8) {
                m.alive = false;
                spawnExplosion(m.mesh.position.clone());
                gameGroup.remove(m.mesh);
                for (const t of m.trails) gameGroup.remove(t.mesh);
                damageSapphire();
            }
        }

        // Update explosions
        for (let i = explosions.length - 1; i >= 0; i--) {
            const exp = explosions[i];
            exp.age += delta;

            for (const p of exp.particles) {
                p.life -= delta;
                if (p.life <= 0) {
                    gameGroup.remove(p.mesh);
                    continue;
                }
                p.mesh.position.addScaledVector(p.vel, delta);
                p.vel.y -= 5 * delta;  // gravity
                p.mesh.material.opacity = Math.max(0, p.life * 2);
                p.mesh.scale.setScalar(p.life * 1.5);
            }

            if (exp.age > 1) {
                for (const p of exp.particles) gameGroup.remove(p.mesh);
                explosions.splice(i, 1);
            }
        }
    }

    function isActive() { return active; }

    function cleanup() {
        stop();
    }

    return { start, stop, update, isActive, shoot, cleanup };
}
