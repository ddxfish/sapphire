// Her stage: she steps out of the sidebar box and stands IN the chat, on a
// floor over the chat's scene background, in front of the transcript.
//
//   legs   go({x, d} | {spot}): turn, walk (the clip's own travel drives her,
//          so no foot slide), arrive, face front; at a seat she sits.
//   floor  the canvas moves into #chatbg, over the transcript. x = 0..100 %
//          across, d = 0..100 % deep: the floor has perspective, her eye line
//          is the horizon, so walking back she shrinks and rises a little.
//   seats  couch on the left wall, desk on the right, a chair and a bed at the back.
//          Only the one she is using (or walking to) is drawn; the rest fade out.
//   ghost  where she crosses a bubble SHE goes part see-through (the bubble
//          stays solid): only her own pixels over the text are touched.
//   face   a close-up of her face for the sidebar panel (her lips are small out here).
//
// Where she goes is decided elsewhere (her `<<avatar: go_…>>` tags, the
// brain's `place`); this only walks her there.

const TURN = 9;                 // rad/s she pivots
const FOV = 24;                 // degrees, vertical
const FAR = 0.6;                // her size at the back of the floor, of her size up front
const FLOOR = 0.08;             // of the chat's height left under her feet up front
const EYES = 0.93;              // her eye line, of her height: the horizon
const SPOTS = { left: { x: 6 }, right: { x: 94 }, center: { x: 50 }, middle: { x: 50 },
                back: { d: 100 }, front: { d: 0 } };
// where each seat stands (percent) and which way she faces in it (0 = at us)
const SEATS = { couch: { x: 11, d: 40, yaw: 0.9 }, desk: { x: 87, d: 40, yaw: 1.15 }, chair: { d: 100, yaw: 0 },
                bed: { x: 24, d: 100, yaw: 0 } };
const PROP_FADE = 0.4;          // seconds a seat takes to appear / go
const SCAN_MS = 300;            // how often the bubbles near the view are looked up again
const FACE_PX = 220;
let _last = null;               // survives a sidebar rebuild: she stays where she stood

// `go_left|right|center` (across), `go_back|front` (depth), `go_40` (across),
// `go_40_100` (across, deep), `go_couch|desk|chair|bed` (a seat, when it is out)
// -> {x?, d?} | {spot} | null
export function parseGo(track, has = () => false) {
    const m = /^go_([a-z0-9]+)(?:_(\d{1,3}))?$/i.exec(track || '');
    if (!m) return null;
    let k = m[1].toLowerCase();
    if (k === 'sit') k = 'chair';
    if (SEATS[k]) return !m[2] && has(k) ? { spot: k } : null;
    if (SPOTS[k]) return m[2] ? null : { ...SPOTS[k] };
    if (!/^\d{1,3}$/.test(k)) return null;
    return m[2] ? { x: Math.min(100, +k), d: Math.min(100, +m[2]) } : { x: Math.min(100, +k) };
}

// A clip that travels (walk, jog, crawl) is made to stride in place; returns
// how far it went per loop (armature units, 0 = it never travelled).
export function inPlace(clip) {
    const tr = clip.tracks.find(t => t.name.endsWith('.position'));
    if (!tr || tr.times.length < 2) return 0;
    const v = tr.values, n = tr.times.length, t0 = tr.times[0], span = tr.times[n - 1] - t0;
    const dx = v[(n - 1) * 3] - v[0], dz = v[(n - 1) * 3 + 2] - v[2];
    const dist = Math.hypot(dx, dz);
    if (dist < 0.5 || span <= 0) return 0;
    for (let i = 0; i < n; i++) {
        const f = (tr.times[i] - t0) / span;
        v[i * 3] -= dx * f;
        v[i * 3 + 2] -= dz * f;
    }
    return dist;
}

function css() {
    if (document.getElementById('avatar-stage-css')) return;
    const s = document.createElement('style');
    s.id = 'avatar-stage-css';
    s.textContent = `
        /* over the transcript (#chatbg-overlay, z 1), under the jump pill (z 3); clicks pass through */
        #avatar-stage { position: absolute; inset: 0; z-index: 2; pointer-events: none; overflow: hidden; }
        #avatar-stage canvas { display: block; width: 100% !important; height: 100% !important; border-radius: 0 !important; }
    `;
    document.head.appendChild(s);
}

// ── furniture: each is built around where she sits (origin = under her hips, +z = the way she faces) ──
function kit(THREE) {
    const mat = (color, roughness = 0.8, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness, ...extra });
    const part = (g, m, sx, sy, sz, x, y, z) => {
        const b = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), m);
        b.position.set(x, y, z);
        g.add(b);
    };
    return { mat, part };
}

function buildChair(THREE, h, seatY) {
    const { mat, part } = kit(THREE), g = new THREE.Group();
    const wood = mat(0x5a3a24, 0.7), pad = mat(0x2d4a7a, 0.9);
    const w = 0.3 * h, d = 0.27 * h, leg = 0.022 * h, zc = -0.04 * h;
    part(g, wood, w, 0.025 * h, d, 0, seatY - 0.03 * h, zc);
    part(g, pad, w * 0.92, 0.03 * h, d * 0.92, 0, seatY - 0.005 * h, zc);
    for (const sx of [-1, 1]) {
        part(g, wood, leg, seatY - 0.04 * h, leg, sx * (w / 2 - leg), (seatY - 0.04 * h) / 2, zc + d / 2 - leg);
        part(g, wood, leg, seatY + 0.32 * h, leg, sx * (w / 2 - leg), (seatY + 0.32 * h) / 2, zc - d / 2 + leg);
    }
    for (const y of [0.1, 0.19, 0.28]) part(g, wood, w - leg * 2, 0.035 * h, leg * 0.8, 0, seatY + y * h, zc - d / 2 + leg);
    return g;
}

function buildCouch(THREE, h, seatY) {
    const { mat, part } = kit(THREE), g = new THREE.Group();
    const body = mat(0x3b4a63, 0.95), cush = mat(0x4d6183, 0.95), foot = mat(0x2a1c12, 0.6);
    const w = 0.95 * h, d = 0.36 * h, zc = -0.06 * h, arm = 0.07 * h;
    part(g, body, w, seatY - 0.07 * h, d, 0, (seatY - 0.07 * h) / 2 + 0.03 * h, zc);            // base
    for (const sx of [-1, 1]) {
        part(g, cush, (w - 2 * arm) / 2 - 0.01 * h, 0.06 * h, d * 0.86, sx * (w - 2 * arm) / 4, seatY - 0.02 * h, zc + 0.02 * h);   // seat cushions
        part(g, cush, (w - 2 * arm) / 2 - 0.01 * h, 0.2 * h, 0.07 * h, sx * (w - 2 * arm) / 4, seatY + 0.14 * h, zc - d / 2 + 0.09 * h);  // back cushions
        part(g, body, arm, seatY + 0.11 * h, d, sx * (w / 2 - arm / 2), (seatY + 0.11 * h) / 2 + 0.03 * h, zc);                     // arms
        for (const sz of [-1, 1]) part(g, foot, 0.03 * h, 0.03 * h, 0.03 * h, sx * (w / 2 - 0.04 * h), 0.015 * h, zc + sz * (d / 2 - 0.04 * h));
    }
    part(g, body, w, seatY + 0.26 * h, 0.07 * h, 0, (seatY + 0.26 * h) / 2 + 0.03 * h, zc - d / 2 + 0.035 * h);                     // back
    return g;
}

function buildDesk(THREE, h, seatY) {
    const { mat, part } = kit(THREE), g = buildChair(THREE, h, seatY);
    const wood = mat(0x6b4a2e, 0.6), dark = mat(0x16181d, 0.5);
    const top = seatY + 0.16 * h, w = 0.62 * h, d = 0.3 * h, zc = 0.34 * h;
    part(g, wood, w, 0.022 * h, d, 0, top, zc);
    for (const sx of [-1, 1]) part(g, wood, 0.022 * h, top, d * 0.9, sx * (w / 2 - 0.02 * h), top / 2, zc);
    part(g, wood, w - 0.04 * h, 0.12 * h, 0.012 * h, 0, top - 0.08 * h, zc + d / 2 - 0.02 * h);  // modesty panel
    part(g, dark, 0.05 * h, 0.012 * h, 0.07 * h, 0, top + 0.017 * h, zc + 0.06 * h);             // monitor foot
    part(g, dark, 0.012 * h, 0.07 * h, 0.012 * h, 0, top + 0.05 * h, zc + 0.07 * h);             // monitor neck
    part(g, dark, 0.3 * h, 0.18 * h, 0.012 * h, 0, top + 0.16 * h, zc + 0.07 * h);               // monitor
    part(g, mat(0x9fd0ff, 0.3, { emissive: 0x4a9eff, emissiveIntensity: 0.9 }),
         0.28 * h, 0.16 * h, 0.004 * h, 0, top + 0.16 * h, zc + 0.062 * h);                      // screen, facing her
    part(g, dark, 0.2 * h, 0.008 * h, 0.07 * h, 0, top + 0.015 * h, zc - 0.07 * h);              // keyboard
    return g;
}

// `topY` = the mattress top. The sleeping clip lays her across (along x), her
// head to -x and her middle a little past her origin, so the bed is built the same way.
function buildBed(THREE, h, topY) {
    const { mat, part } = kit(THREE), g = new THREE.Group();
    const wood = mat(0x5a3a24, 0.7), sheet = mat(0xd9dee8, 0.95), pillow = mat(0xf4f6fa, 0.95), quilt = mat(0x3b4a63, 0.95);
    const len = 1.02 * h, w = 0.48 * h, zc = -0.04 * h, xc = 0.07 * h;
    part(g, wood, len + 0.04 * h, topY - 0.1 * h, w + 0.04 * h, xc, (topY - 0.1 * h) / 2 + 0.04 * h, zc);     // frame
    part(g, sheet, len, 0.08 * h, w, xc, topY - 0.04 * h, zc);                                                  // mattress
    part(g, wood, 0.03 * h, topY + 0.2 * h, w + 0.04 * h, xc - (len / 2 + 0.015 * h), (topY + 0.2 * h) / 2, zc);  // headboard
    part(g, pillow, 0.18 * h, 0.04 * h, w * 0.7, xc - (len / 2 - 0.12 * h), topY + 0.015 * h, zc);
    part(g, quilt, len * 0.22, 0.085 * h, w + 0.01 * h, xc + len / 2 - len * 0.11, topY - 0.038 * h, zc);          // folded quilt at the foot
    return g;
}

const BUILD = { chair: buildChair, couch: buildCouch, desk: buildDesk, bed: buildBed };

export function createStage({ THREE, scene, renderer, canvas, model, actions, travel, height, play, onArrive }) {
    css();
    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 500);
    const faceCam = new THREE.PerspectiveCamera(20, 1, 0.05, 200);
    const tanF = Math.tan(FOV * Math.PI / 360);
    const home = { parent: canvas.parentElement, next: canvas.nextSibling, h: canvas.style.height };
    const hips = model.getObjectByName('Hips'), head = model.getObjectByName('Head');
    const walk = actions.walk ? 'walk' : Object.keys(actions).find(n => /^walk/.test(n));
    const sitClip = actions.sit ? 'sit' : (actions.talking_seated ? 'talking_seated' : null);

    // armature units -> world (the clip's travel and hip heights are in armature units)
    const sc = new THREE.Vector3(1, 1, 1);
    (hips?.parent || model).getWorldScale(sc);
    const speed = walk && travel[walk] ? travel[walk] * sc.z / actions[walk].getClip().duration : 0;
    const hipY = (name) => {
        const tr = actions[name]?.getClip().tracks.find(t => t.name.endsWith('.position'));
        return tr ? tr.values[1] * sc.y : null;
    };
    const seatY = sitClip && hipY(sitClip) != null ? hipY(sitClip) - 0.06 * height : 0.3 * height;
    const lieClip = actions.sleeping_idle ? 'sleeping_idle' : (actions.sleeping ? 'sleeping' : null);
    const bedY = lieClip && hipY(lieClip) != null ? hipY(lieClip) - 0.08 * height : 0.22 * height;
    const STEP = 0.24 * height;                     // she stands this far in front of a seat before sitting

    let on = false, host = null, size = 20, ghost = 0.3, scanAt = 0, near = [];
    let cfg = { chair: null, couch: false, desk: false, bed: false };
    const props = {};                               // seat name -> its furniture
    let cur = { x: 94, d: 0, spot: null, ..._last };    // where she is headed / stands
    let state = 'rest', seated = false, stood = false, yaw = 0, wantYaw = 0;
    let dist = 10, aspect = 1;                      // camera distance to the front line; chat w/h
    let faceCtx = null, faceAt = null;
    const v3 = new THREE.Vector3();

    // the ghost pass: one quad that multiplies what is already drawn (colour and
    // alpha, the canvas is premultiplied) by `ghost`, scissored to each bubble
    const veil = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.MeshBasicMaterial({
        transparent: true, depthTest: false, depthWrite: false, blending: THREE.CustomBlending,
        blendEquation: THREE.AddEquation, blendSrc: THREE.ZeroFactor, blendDst: THREE.SrcAlphaFactor,
        blendSrcAlpha: THREE.ZeroFactor, blendDstAlpha: THREE.SrcAlphaFactor }));
    veil.frustumCulled = false;
    const veilScene = new THREE.Scene(), veilCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    veilScene.add(veil);

    // a soft shadow pins her to the floor of a flat picture
    const sh = document.createElement('canvas');
    sh.width = sh.height = 64;
    const g2 = sh.getContext('2d'), grad = g2.createRadialGradient(32, 32, 2, 32, 32, 30);
    grad.addColorStop(0, 'rgba(0,0,0,0.5)');
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    g2.fillStyle = grad;
    g2.fillRect(0, 0, 64, 64);
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(0.55 * height, 0.3 * height),
        new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(sh), transparent: true, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.position.y = 0.004 * height;
    shadow.visible = false;
    scene.add(shadow);

    // percent -> world. Deeper = further from the camera; x stays a share of
    // what the camera sees AT that depth, so 0 and 100 are the chat's edges anywhere.
    const zOf = (d) => -dist * (1 / FAR - 1) * Math.max(0, Math.min(100, d || 0)) / 100;
    const xOf = (x, z) => {
        const half = (dist - z) * tanF * aspect - 0.22 * height;
        return half * (Math.max(0, Math.min(100, x)) / 50 - 1);
    };
    const has = (name) => name === 'chair' ? cfg.chair != null : name === 'bed' ? !!(cfg.bed && lieClip) : !!cfg[name];
    const seat = (name) => {
        const s = SEATS[name], z = zOf(s.d);
        return { x: xOf(name === 'chair' ? cfg.chair : s.x, z), z, yaw: s.yaw };
    };
    // where her feet belong now: in the seat, a step in front of it, or on her open spot
    function spot(sit) {
        if (!cur.spot) { const z = zOf(cur.d); return { x: xOf(cur.x, z), z, yaw: 0 }; }
        const s = seat(cur.spot);
        return sit ? s : { x: s.x + Math.sin(s.yaw) * STEP, z: s.z + Math.cos(s.yaw) * STEP, yaw: 0 };
    }
    const sitting = () => seated && !stood;

    function frame() {
        const w = host?.clientWidth || 1, h = host?.clientHeight || 1, pr = renderer.getPixelRatio();
        if (canvas.width !== Math.floor(w * pr) || canvas.height !== Math.floor(h * pr)) renderer.setSize(w, h, false);
        const s = size / 100;
        aspect = w / h;
        dist = height / (2 * s * tanF);                 // up front she is `size` of the chat tall
        camera.aspect = aspect;
        camera.far = dist * 4;
        camera.position.set(0, EYES * height, dist);    // level, at her eye line: the horizon runs through her eyes
        camera.rotation.set(0, 0, 0);
        // shift the picture so her feet up front land on the floor line near the bottom
        camera.setViewOffset(w, h, 0, (2 * EYES * s - (1 - 2 * FLOOR)) * h / 2, w, h);
        camera.updateMatrixWorld();
        for (const [name, g] of Object.entries(props)) {
            const p = seat(name);
            g.position.set(p.x, 0, p.z);
            g.rotation.y = p.yaw;
        }
    }

    // at rest she holds her spot (a resized chat, a new size); k < 1 eases her onto it
    function pin(k = 1) {
        const p = spot(sitting());
        model.position.x += (p.x - model.position.x) * k;
        model.position.z += (p.z - model.position.z) * k;
        wantYaw = p.yaw;
    }

    function furnish() {
        for (const name of Object.keys(BUILD)) {
            const want = on && has(name);
            if (want && !props[name]) {
                const g = props[name] = BUILD[name](THREE, height, name === 'bed' ? bedY : seatY);
                g.userData.a = 0;                   // how drawn it is, 0..1
                g.visible = false;
                scene.add(g);
            }
            if (!want && props[name]) { scene.remove(props[name]); delete props[name]; }
        }
        if (cur.spot && !has(cur.spot)) { cur.spot = null; seated = false; }
        if (on) frame();
    }

    function enter() {
        if (on) return true;
        const bg = document.getElementById('chatbg'), over = document.getElementById('chatbg-overlay');
        if (!bg || !over) return false;
        host = document.createElement('div');
        host.id = 'avatar-stage';
        over.after(host);
        host.appendChild(canvas);
        bg.classList.add('avatar-stage-on');
        on = true;
        furnish();
        seated = !!cur.spot; stood = false;
        state = 'rest';
        shadow.visible = true;
        pin();
        yaw = wantYaw;
        return true;
    }

    function exit() {
        if (!on) return;
        on = false;
        home.parent?.insertBefore(canvas, home.next);
        canvas.style.height = home.h;
        host?.remove(); host = null;
        document.getElementById('chatbg')?.classList.remove('avatar-stage-on');
        near = [];
        model.position.set(0, 0, 0);
        model.rotation.y = 0;
        seated = false; state = 'rest';
        shadow.visible = false;
        furnish();
    }

    // `to` = {spot} (a seat that is out) or the parts of her open spot that change ({x?, d?}).
    function go(to, { teleport = false } = {}) {
        if (!to) return;
        if (to.spot && !has(to.spot)) return;
        cur = to.spot ? { ...cur, spot: to.spot } : { x: to.x ?? cur.x, d: to.d ?? cur.d, spot: null };
        _last = { ...cur };
        stood = false;
        if (!on) return;
        const t = spot(false), far = Math.hypot(t.x - model.position.x, t.z - model.position.z);
        if (teleport || far < 0.06 * height || !speed) {
            const sat = seated;
            state = 'rest';
            seated = !!cur.spot;
            if (teleport) { pin(); yaw = wantYaw; }
            if (sat !== seated) onArrive();
            return;
        }
        seated = false;
        if (state !== 'walk') { state = 'walk'; play(walk); }
    }

    // she thins out over every bubble in view. The bubbles within a screen of
    // the view are looked up now and then; their boxes are read fresh each frame
    // (a scroll moves them).
    function veilBubbles() {
        const r = host.getBoundingClientRect(), now = performance.now();
        if (!r.width || !r.height) return;
        if (now - scanAt > SCAN_MS) {
            scanAt = now;
            near = [];
            for (const el of document.querySelectorAll('#chat-container .message-content')) {
                const b = el.getBoundingClientRect();
                if (b.bottom > r.top - r.height && b.top < r.bottom + r.height) near.push(el);
            }
        }
        veil.material.opacity = ghost;
        renderer.autoClear = false;
        renderer.setScissorTest(true);
        for (const el of near) {
            const b = el.getBoundingClientRect();
            const x0 = Math.max(b.left, r.left), x1 = Math.min(b.right, r.right);
            const y0 = Math.max(b.top, r.top), y1 = Math.min(b.bottom, r.bottom);
            if (x1 <= x0 || y1 <= y0) continue;
            renderer.setScissor(x0 - r.left, r.bottom - y1, x1 - x0, y1 - y0);    // from the bottom left
            renderer.render(veilScene, veilCam);
        }
        renderer.setScissorTest(false);
        renderer.autoClear = true;
    }

    function update(dt) {
        if (!on) return;
        frame();
        for (const [name, g] of Object.entries(props)) {    // only her seat is in the room
            const want = cur.spot === name ? 1 : 0, a = g.userData.a;
            if (a === want) continue;
            const n = want ? Math.min(1, a + dt / PROP_FADE) : Math.max(0, a - dt / PROP_FADE);
            g.userData.a = n;
            g.visible = n > 0;
            g.traverse(o => {
                if (!o.material) return;
                if (o.material.transparent !== (n < 1)) { o.material.transparent = n < 1; o.material.needsUpdate = true; }
                o.material.opacity = n;
            });
        }
        if (state === 'walk') {
            const t = spot(false), dx = t.x - model.position.x, dz = t.z - model.position.z;
            const left = Math.hypot(dx, dz), step = speed * dt;
            if (left <= step) {
                state = 'rest';
                seated = !!cur.spot;
                onArrive();
            } else {
                model.position.x += dx / left * step;
                model.position.z += dz / left * step;
                wantYaw = Math.atan2(dx, dz);           // 0 = facing us
            }
        }
        if (state === 'rest') pin(Math.min(1, dt * 8));
        let turn = wantYaw - yaw;
        turn = Math.atan2(Math.sin(turn), Math.cos(turn));  // the short way round
        yaw += Math.max(-TURN * dt, Math.min(TURN * dt, turn));
        model.rotation.y = yaw;
        if (hips) {
            hips.getWorldPosition(v3);
            shadow.position.x = v3.x; shadow.position.z = v3.z;
        }
    }

    // her face for the panel: a small pass from in front of her, copied out, then the stage
    function render() {
        if (faceCtx && head && faceCtx.canvas.isConnected) {
            head.getWorldPosition(v3);
            v3.y += 0.045 * height;
            if (!faceAt) faceAt = v3.clone(); else faceAt.lerp(v3, 0.25);
            faceCam.position.set(faceAt.x + Math.sin(yaw) * 0.8 * height, faceAt.y, faceAt.z + Math.cos(yaw) * 0.8 * height);
            faceCam.lookAt(faceAt);
            const pr = renderer.getPixelRatio(), s = Math.min(FACE_PX, host.clientWidth, host.clientHeight);
            shadow.visible = false;
            renderer.setScissorTest(true);
            renderer.setViewport(0, 0, s, s);
            renderer.setScissor(0, 0, s, s);
            renderer.render(scene, faceCam);
            const fc = faceCtx.canvas;
            faceCtx.clearRect(0, 0, fc.width, fc.height);
            faceCtx.drawImage(canvas, 0, canvas.height - s * pr, s * pr, s * pr, 0, 0, fc.width, fc.height);
            renderer.setScissorTest(false);
            renderer.setViewport(0, 0, host.clientWidth, host.clientHeight);
            shadow.visible = true;
        }
        renderer.render(scene, camera);
        if (ghost < 1) veilBubbles();
    }

    return {
        enter, exit, go, update, render, has,
        isOn: () => on,
        walking: () => state === 'walk',
        seated: () => on && sitting(),
        seatName: () => (on && sitting()) ? cur.spot : null,
        sitClip: () => sitClip,
        lieClip: () => lieClip,
        stand() { stood = true; },                  // she gets up where she is (a dance of her own)
        setSize(v) { size = Math.max(8, Math.min(95, +v || 20)); },
        setGhost(v) { ghost = Math.max(0, Math.min(100, +(v ?? 30))) / 100; },   // how solid she is over a bubble
        setProps(p) { cfg = { chair: p.chair ?? null, couch: !!p.couch, desk: !!p.desk, bed: !!p.bed }; furnish(); },
        setFace(c) { faceCtx = c ? c.getContext('2d') : null; faceAt = null; },
        cleanup() { exit(); camera.clearViewOffset(); scene.remove(shadow); veil.geometry.dispose(); veil.material.dispose(); },
    };
}
