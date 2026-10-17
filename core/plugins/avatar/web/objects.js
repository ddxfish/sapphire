// Reusable object library for avatar environments
// Each object: create(THREE, options) => { group, update(delta), dispose() }
// Objects are positioned by locations — they build at origin, location places them.

function box(THREE, w, h, d, color, roughness = 0.85) {
    const m = new THREE.Mesh(
        new THREE.BoxGeometry(w, h, d),
        new THREE.MeshStandardMaterial({ color, roughness })
    );
    m.castShadow = true;
    m.receiveShadow = true;
    return m;
}

function cyl(THREE, rTop, rBot, h, segs, color, roughness = 0.8) {
    const m = new THREE.Mesh(
        new THREE.CylinderGeometry(rTop, rBot, h, segs),
        new THREE.MeshStandardMaterial({ color, roughness })
    );
    m.castShadow = true;
    return m;
}

// ═══════════════════════════════════════════
// FIREPLACE
// ═══════════════════════════════════════════
function createFireplace(THREE, opts = {}) {
    const g = new THREE.Group();

    // Stone hearth
    g.add(box(THREE, 2.8, 1.6, 1.0, 0x3a2a1a, 0.95)).position.set(0, 0.8, 0);
    // Firebox opening
    g.add(box(THREE, 1.8, 1.0, 0.3, 0x111111, 1.0)).position.set(0, 0.7, 0.4);
    // Mantel
    g.add(box(THREE, 3.2, 0.12, 1.2, 0x4a3a28, 0.8)).position.set(0, 1.64, 0);
    // Chimney
    g.add(box(THREE, 2.0, 3.0, 0.8, 0x2a2020, 0.95)).position.set(0, 3.1, -0.1);

    // Fire glow
    const fireLight = new THREE.PointLight(0xff6622, 1.2, 10);
    fireLight.position.set(0, 0.8, 0.8);
    g.add(fireLight);

    // Embers
    const count = 40;
    const positions = new Float32Array(count * 3);
    const basePositions = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
        positions[i*3]     = (Math.random() - 0.5) * 1.2;
        positions[i*3 + 1] = 0.4 + Math.random() * 0.8;
        positions[i*3 + 2] = 0.3 + (Math.random() - 0.5) * 0.4;
        basePositions[i*3]     = positions[i*3];
        basePositions[i*3 + 1] = positions[i*3 + 1];
        basePositions[i*3 + 2] = positions[i*3 + 2];
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    g.add(new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xff4400, size: 0.1, transparent: true, opacity: 0.8 })));

    // Warm rug
    const rug = new THREE.Mesh(
        new THREE.PlaneGeometry(3.5, 2.5),
        new THREE.MeshStandardMaterial({ color: 0x5a2a2a, roughness: 0.95 })
    );
    rug.rotation.x = -Math.PI / 2;
    rug.position.set(0, 0.02, 3);
    rug.receiveShadow = true;
    g.add(rug);

    let _time = 0;
    return {
        group: g,
        update(delta) {
            _time += delta;
            const pos = geo.attributes.position.array;
            for (let i = 0; i < count; i++) {
                pos[i*3 + 1] += delta * (0.15 + Math.random() * 0.2);
                pos[i*3]     += (Math.random() - 0.5) * delta * 0.15;
                if (pos[i*3 + 1] > 2.0) {
                    pos[i*3]     = basePositions[i*3] + (Math.random() - 0.5) * 0.5;
                    pos[i*3 + 1] = basePositions[i*3 + 1];
                    pos[i*3 + 2] = basePositions[i*3 + 2];
                }
            }
            geo.attributes.position.needsUpdate = true;
            fireLight.intensity = 0.8 + Math.sin(_time * 8) * 0.2 + Math.random() * 0.3;
        },
    };
}

// ═══════════════════════════════════════════
// COUCH (with coffee table + mug)
// ═══════════════════════════════════════════
function createCouch(THREE, opts = {}) {
    const g = new THREE.Group();

    g.add(box(THREE, 3.2, 0.5, 1.3, 0x2a3a5a, 0.85)).position.set(0, 0.4, 0);
    g.add(box(THREE, 3.2, 0.8, 0.2, 0x223355, 0.85)).position.set(0, 0.85, -0.55);
    for (const side of [-1, 1]) {
        g.add(box(THREE, 0.2, 0.6, 1.3, 0x223355, 0.85)).position.set(side * 1.6, 0.65, 0);
    }

    // Coffee table
    g.add(box(THREE, 1.6, 0.08, 0.8, 0x4a3a28, 0.75)).position.set(0, 0.55, 1.5);
    for (const lx of [-0.65, 0.65]) {
        for (const lz of [-0.3, 0.3]) {
            g.add(cyl(THREE, 0.03, 0.03, 0.55, 6, 0x3a2a1a)).position.set(lx, 0.27, 1.5 + lz);
        }
    }

    // Mug
    g.add(cyl(THREE, 0.06, 0.05, 0.12, 12, 0x4a9eff, 0.5)).position.set(0.3, 0.65, 1.5);

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// MINIBAR
// ═══════════════════════════════════════════
function createMinibar(THREE, opts = {}) {
    const g = new THREE.Group();

    g.add(box(THREE, 1.6, 1.4, 0.7, 0x3a2a1a, 0.85)).position.set(0, 0.7, 0);
    g.add(box(THREE, 1.8, 0.06, 0.8, 0x4a3a28, 0.75)).position.set(0, 1.42, 0);

    const colors = [0x338855, 0x885533, 0x4488aa, 0xaa5533, 0x336655];
    for (let i = 0; i < 5; i++) {
        const h = 0.3 + Math.random() * 0.15;
        g.add(cyl(THREE, 0.05, 0.06, h, 8, colors[i], 0.3)).position.set(-0.5 + i * 0.25, 1.45 + h/2, 0);
    }

    // Snack bowl
    g.add(cyl(THREE, 0.18, 0.12, 0.1, 16, 0x5a4a3a, 0.7)).position.set(0.5, 1.50, 0);

    // Stool
    g.add(cyl(THREE, 0.25, 0.25, 0.06, 16, 0x3a3030, 0.85)).position.set(0, 0.95, 1.2);
    g.add(cyl(THREE, 0.04, 0.06, 0.95, 8, 0x444444, 0.5)).position.set(0, 0.47, 1.2);

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// WINDOW (lake/view window with cross bars + light spill)
// ═══════════════════════════════════════════
function createWindow(THREE, opts = {}) {
    const w = opts.width || 5.0;
    const h = opts.height || 3.5;
    const g = new THREE.Group();

    g.add(box(THREE, w, h, 0.15, 0x2a2a2a, 0.8)).position.set(0, h/2 + 0.25, 0);

    // Glass
    const glass = new THREE.Mesh(
        new THREE.PlaneGeometry(w - 0.4, h - 0.4),
        new THREE.MeshBasicMaterial({ color: 0x224466, transparent: true, opacity: 0.85 })
    );
    glass.position.set(0, h/2 + 0.25, 0.08);
    g.add(glass);

    // Cross bars
    g.add(box(THREE, w - 0.4, 0.06, 0.08, 0x333333, 0.8)).position.set(0, h/2 + 0.25, 0.1);
    g.add(box(THREE, 0.06, h - 0.4, 0.08, 0x333333, 0.8)).position.set(0, h/2 + 0.25, 0.1);

    // Light spill
    const light = new THREE.SpotLight(0x4477aa, 0.6, 14, Math.PI / 3.5);
    light.position.set(0, h - 0.5, 1);
    light.target.position.set(0, 0, 8);
    g.add(light);
    g.add(light.target);

    return {
        group: g,
        glass,  // exposed so environment can shift color with sky
        update() {},
    };
}

// ═══════════════════════════════════════════
// CHAIR (with legs)
// ═══════════════════════════════════════════
function createChair(THREE, opts = {}) {
    const g = new THREE.Group();
    const color = opts.color || 0x4a3a28;

    g.add(box(THREE, 0.8, 0.08, 0.8, color, 0.85)).position.set(0, 0.65, 0);
    g.add(box(THREE, 0.8, 0.8, 0.08, color, 0.85)).position.set(0, 1.05, -0.38);

    for (const lx of [-0.3, 0.3]) {
        for (const lz of [-0.3, 0.3]) {
            g.add(cyl(THREE, 0.03, 0.03, 0.65, 6, 0x3a2a1a)).position.set(lx, 0.32, lz);
        }
    }

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// TABLE (round or rectangular)
// ═══════════════════════════════════════════
function createTable(THREE, opts = {}) {
    const g = new THREE.Group();
    const style = opts.style || 'round';

    if (style === 'round') {
        g.add(cyl(THREE, 0.35, 0.35, 0.06, 16, 0x3a2a1a, 0.75)).position.set(0, 0.62, 0);
        g.add(cyl(THREE, 0.05, 0.08, 0.6, 8, 0x3a2a1a)).position.set(0, 0.3, 0);
    } else {
        g.add(box(THREE, 1.2, 0.06, 0.6, 0x4a3a28, 0.75)).position.set(0, 0.62, 0);
        for (const lx of [-0.5, 0.5]) {
            for (const lz of [-0.22, 0.22]) {
                g.add(cyl(THREE, 0.03, 0.03, 0.6, 6, 0x3a2a1a)).position.set(lx, 0.3, lz);
            }
        }
    }

    // Books
    if (opts.books) {
        const bookColors = [0x8a3030, 0x2a4a6a, 0x3a6a3a];
        for (let i = 0; i < 3; i++) {
            const b = box(THREE, 0.2, 0.04, 0.14, bookColors[i], 0.9);
            b.position.set(-0.08, 0.67 + i * 0.04, 0);
            b.rotation.y = (i - 1) * 0.15;
            g.add(b);
        }
    }

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// BED (frame, mattress, pillows, headboard, blanket, nightstand, lamp)
// ═══════════════════════════════════════════
function createBed(THREE, opts = {}) {
    const g = new THREE.Group();

    g.add(box(THREE, 2.6, 0.5, 3.5, 0x3a2a20, 0.9)).position.set(0, 0.25, 0);
    g.add(box(THREE, 2.3, 0.25, 3.2, 0x4a5577, 0.95)).position.set(0, 0.62, 0);

    for (const px of [-0.5, 0.5]) {
        g.add(box(THREE, 0.7, 0.15, 0.45, 0x6677aa, 0.95)).position.set(px, 0.82, -1.2);
    }

    g.add(box(THREE, 2.6, 1.2, 0.12, 0x3a2a20, 0.9)).position.set(0, 1.1, -1.8);

    const blanket = box(THREE, 2.2, 0.06, 1.8, 0x3a4a6a, 0.95);
    blanket.position.set(0, 0.78, 0.5);
    blanket.rotation.x = 0.05;
    g.add(blanket);

    // Nightstand
    g.add(box(THREE, 0.6, 0.65, 0.5, 0x3a2a20, 0.85)).position.set(1.8, 0.32, -1.0);

    // Lamp
    g.add(cyl(THREE, 0.08, 0.1, 0.04, 12, 0x444444, 0.5)).position.set(1.8, 0.67, -1.0);
    g.add(cyl(THREE, 0.02, 0.02, 0.35, 6, 0x555555, 0.5)).position.set(1.8, 0.85, -1.0);
    g.add(cyl(THREE, 0.15, 0.1, 0.18, 12, 0xddc088, 0.9)).position.set(1.8, 1.1, -1.0);

    const bedLight = new THREE.PointLight(0xffaa55, 0.3, 5);
    bedLight.position.set(1.8, 1.2, -1.0);
    g.add(bedLight);

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// TREE (simple low-poly — cylinder trunk + sphere canopy)
// ═══════════════════════════════════════════
function createTree(THREE, opts = {}) {
    const g = new THREE.Group();
    const h = opts.height || 3;
    const canopyColor = opts.canopyColor || 0x2a6a2a;

    g.add(cyl(THREE, 0.12, 0.15, h * 0.5, 8, 0x5a3a1a, 0.9)).position.set(0, h * 0.25, 0);
    const canopy = new THREE.Mesh(
        new THREE.SphereGeometry(h * 0.35, 8, 6),
        new THREE.MeshStandardMaterial({ color: canopyColor, roughness: 0.9 })
    );
    canopy.position.set(0, h * 0.6, 0);
    canopy.castShadow = true;
    g.add(canopy);

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// ROCK (simple irregular shape)
// ═══════════════════════════════════════════
function createRock(THREE, opts = {}) {
    const g = new THREE.Group();
    const s = opts.scale || 1;
    const rock = new THREE.Mesh(
        new THREE.DodecahedronGeometry(0.4 * s, 0),
        new THREE.MeshStandardMaterial({ color: opts.color || 0x555555, roughness: 0.95 })
    );
    rock.scale.set(1, 0.6, 0.9);
    rock.position.y = 0.2 * s;
    rock.castShadow = true;
    g.add(rock);
    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// PLATFORM (floating platform for space environments)
// ═══════════════════════════════════════════
function createPlatform(THREE, opts = {}) {
    const g = new THREE.Group();
    const r = opts.radius || 8;
    const platform = new THREE.Mesh(
        new THREE.CylinderGeometry(r, r * 1.1, 0.3, 32),
        new THREE.MeshStandardMaterial({ color: opts.color || 0x1a1a2a, roughness: 0.7, metalness: 0.3 })
    );
    platform.position.y = -0.15;
    platform.receiveShadow = true;
    g.add(platform);

    // Edge glow ring
    const ring = new THREE.Mesh(
        new THREE.RingGeometry(r - 0.05, r + 0.05, 64),
        new THREE.MeshBasicMaterial({ color: opts.glowColor || 0x4a9eff, transparent: true, opacity: 0.2, side: THREE.DoubleSide })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.02;
    g.add(ring);

    return { group: g, update() {} };
}

// ═══════════════════════════════════════════
// ARCADE CABINET
// ═══════════════════════════════════════════
function createArcade(THREE, opts = {}) {
    const g = new THREE.Group();

    // Cabinet body
    g.add(box(THREE, 0.9, 1.8, 0.8, 0x1a1a2a, 0.85)).position.set(0, 0.9, 0);
    // Screen bezel
    g.add(box(THREE, 0.7, 0.5, 0.05, 0x111111, 0.9)).position.set(0, 1.35, 0.41);
    // Screen (emissive)
    const screen = new THREE.Mesh(
        new THREE.PlaneGeometry(0.6, 0.4),
        new THREE.MeshBasicMaterial({ color: 0x4a9eff, transparent: true, opacity: 0.8 })
    );
    screen.position.set(0, 1.35, 0.44);
    g.add(screen);
    // Control panel (angled)
    const panel = box(THREE, 0.7, 0.05, 0.35, 0x222233, 0.8);
    panel.position.set(0, 1.0, 0.5);
    panel.rotation.x = -0.3;
    g.add(panel);
    // Joystick
    g.add(cyl(THREE, 0.02, 0.02, 0.08, 6, 0xff3333, 0.5)).position.set(-0.1, 1.07, 0.52);
    g.add(cyl(THREE, 0.03, 0.03, 0.02, 8, 0xff3333, 0.5)).position.set(-0.1, 1.12, 0.52);
    // Buttons
    for (let i = 0; i < 3; i++) {
        g.add(cyl(THREE, 0.025, 0.025, 0.015, 8, [0x44ff44, 0xff4444, 0x4444ff][i], 0.5))
            .position.set(0.05 + i * 0.07, 1.06, 0.52);
    }
    // Screen glow
    const glow = new THREE.PointLight(0x4a9eff, 0.3, 3);
    glow.position.set(0, 1.35, 0.6);
    g.add(glow);

    let _t = 0;
    return {
        group: g,
        update(delta) {
            _t += delta;
            screen.material.color.setHex(_t % 2 < 1 ? 0x4a9eff : 0x3a8eef);  // subtle pulse
            glow.intensity = 0.2 + Math.sin(_t * 2) * 0.1;
        },
    };
}

// ═══════════════════════════════════════════
// REGISTRY
// ═══════════════════════════════════════════
export const OBJECTS = {
    fireplace:  createFireplace,
    couch:      createCouch,
    minibar:    createMinibar,
    window:     createWindow,
    chair:      createChair,
    table:      createTable,
    bed:        createBed,
    tree:       createTree,
    rock:       createRock,
    platform:   createPlatform,
    arcade:     createArcade,
};
