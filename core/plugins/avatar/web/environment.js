// Procedural environment — sky, floor, lighting, time-of-day + modular locations
// Only active in fullwindow/fullscreen modes
import { loadLocation, listBuiltinLocations } from './location-loader.js';

// Time-of-day color palettes
const TIME_PALETTE = [
    [0,  '#060610', '#0a0a18', '#223355', 0.15, 0.45, '#2a2a3a'],
    [4,  '#0d0818', '#1a1030', '#332244', 0.2, 0.42, '#2a2030'],
    [5,  '#1a1030', '#2d1b4e', '#553366', 0.3, 0.42, '#2a1a3a'],
    [6,  '#4a2040', '#ff8855', '#ffaa66', 0.7, 0.5, '#4a3030'],
    [7,  '#5577bb', '#ffcc88', '#ffddaa', 1.0, 0.6, '#555555'],
    [9,  '#4488cc', '#88bbee', '#ffffff', 1.2, 0.7, '#666666'],
    [12, '#3377cc', '#66aadd', '#ffffee', 1.3, 0.7, '#666666'],
    [15, '#4488bb', '#77aacc', '#fff8ee', 1.15, 0.65, '#606055'],
    [17, '#5577aa', '#ffbb77', '#ffcc88', 1.0, 0.6, '#555544'],
    [18, '#553355', '#ff7744', '#ff9966', 0.7, 0.5, '#443333'],
    [19, '#1a1030', '#443355', '#554466', 0.4, 0.35, '#2a1a3a'],
    [21, '#0d0d1a', '#111122', '#334466', 0.2, 0.3, '#1a1a3a'],
];

function lerpColor(hex1, hex2, t) {
    const r1 = parseInt(hex1.slice(1,3), 16), g1 = parseInt(hex1.slice(3,5), 16), b1 = parseInt(hex1.slice(5,7), 16);
    const r2 = parseInt(hex2.slice(1,3), 16), g2 = parseInt(hex2.slice(3,5), 16), b2 = parseInt(hex2.slice(5,7), 16);
    const r = Math.round(r1 + (r2-r1)*t), g = Math.round(g1 + (g2-g1)*t), b = Math.round(b1 + (b2-b1)*t);
    return (r << 16) | (g << 8) | b;
}
function lerp(a, b, t) { return a + (b - a) * t; }
function getTimeOfDay() { const n = new Date(); return n.getHours() + n.getMinutes() / 60; }

function samplePalette(hour) {
    let lo = TIME_PALETTE[TIME_PALETTE.length - 1], hi = TIME_PALETTE[0];
    for (let i = 0; i < TIME_PALETTE.length; i++) {
        if (TIME_PALETTE[i][0] <= hour) lo = TIME_PALETTE[i];
        if (TIME_PALETTE[i][0] > hour) { hi = TIME_PALETTE[i]; break; }
        if (i === TIME_PALETTE.length - 1) hi = TIME_PALETTE[0];
    }
    const range = hi[0] > lo[0] ? hi[0] - lo[0] : (24 - lo[0] + hi[0]);
    const t = range > 0 ? ((hour - lo[0] + 24) % 24) / range : 0;
    return {
        skyTop: lerpColor(lo[1], hi[1], t), skyBottom: lerpColor(lo[2], hi[2], t),
        sunColor: lerpColor(lo[3], hi[3], t), sunIntensity: lerp(lo[4], hi[4], t),
        ambientIntensity: lerp(lo[5], hi[5], t), ambientColor: lerpColor(lo[6], hi[6], t),
        sunAngle: (hour / 24) * Math.PI * 2 - Math.PI / 2,
    };
}

export function createEnvironment(scene, THREE, renderer) {
    const group = new THREE.Group();
    group.visible = false;
    scene.add(group);

    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    // ═══ FLOOR (shader — fades to void at edges) ═══
    const floorGeo = new THREE.PlaneGeometry(200, 200);
    const floorMat = new THREE.ShaderMaterial({
        uniforms: {
            floorColor: { value: new THREE.Color(0x1a1a2a) },
            fadeStart: { value: 12.0 },
            fadeEnd: { value: 40.0 },
            showGrid: { value: 1.0 },
        },
        vertexShader: `
            varying vec3 vWorldPos;
            void main() {
                vec4 wp = modelMatrix * vec4(position, 1.0);
                vWorldPos = wp.xyz;
                gl_Position = projectionMatrix * viewMatrix * wp;
            }
        `,
        fragmentShader: `
            uniform vec3 floorColor;
            uniform float fadeStart, fadeEnd, showGrid;
            varying vec3 vWorldPos;
            void main() {
                float dist = length(vWorldPos.xz);
                float alpha = 1.0 - smoothstep(fadeStart, fadeEnd, dist);
                float gridX = abs(fract(vWorldPos.x * 0.5) - 0.5);
                float gridZ = abs(fract(vWorldPos.z * 0.5) - 0.5);
                float grid = 1.0 - smoothstep(0.47, 0.5, min(gridX, gridZ));
                vec3 col = floorColor + vec3(grid * 0.04 * showGrid);
                gl_FragColor = vec4(col, alpha);
            }
        `,
        transparent: true,
        depthWrite: false,
        side: THREE.DoubleSide,
    });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -0.02;
    floor.receiveShadow = true;
    floor.renderOrder = -1;  // render after sky (-2), before props (0)
    group.add(floor);

    const shadowFloor = new THREE.Mesh(
        new THREE.PlaneGeometry(60, 60),
        new THREE.ShadowMaterial({ opacity: 0.3 })
    );
    shadowFloor.rotation.x = -Math.PI / 2;
    shadowFloor.position.y = -0.01;
    shadowFloor.receiveShadow = true;
    group.add(shadowFloor);

    // ═══ GLOW RING ═══
    const ringGeo = new THREE.RingGeometry(2.5, 2.7, 64);
    const ringMat = new THREE.MeshBasicMaterial({
        color: 0x4a9eff, transparent: true, opacity: 0.12, side: THREE.DoubleSide
    });
    const glowRing = new THREE.Mesh(ringGeo, ringMat);
    glowRing.rotation.x = -Math.PI / 2;
    glowRing.position.y = 0.01;
    group.add(glowRing);

    // ═══ SKY DOME ═══
    const skyGeo = new THREE.SphereGeometry(80, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2);
    const skyMat = new THREE.ShaderMaterial({
        uniforms: {
            topColor: { value: new THREE.Color(0x0a0a1a) },
            bottomColor: { value: new THREE.Color(0x0d1117) },
        },
        vertexShader: `
            varying vec3 vWorldPos;
            void main() {
                vec4 wp = modelMatrix * vec4(position, 1.0);
                vWorldPos = wp.xyz;
                gl_Position = projectionMatrix * viewMatrix * wp;
            }
        `,
        fragmentShader: `
            uniform vec3 topColor, bottomColor;
            varying vec3 vWorldPos;
            void main() {
                float h = normalize(vWorldPos).y;
                gl_FragColor = vec4(mix(bottomColor, topColor, max(h, 0.0)), 1.0);
            }
        `,
        side: THREE.BackSide,
        depthWrite: false,
    });
    const skyDome = new THREE.Mesh(skyGeo, skyMat);
    skyDome.renderOrder = -2;  // render first, behind everything
    group.add(skyDome);

    // ═══ STARS ═══
    let _starCount = 300;
    let starGeo, starMat, stars;
    function buildStars(count) {
        if (stars) group.remove(stars);
        _starCount = count;
        const pos = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.random() * Math.PI * 0.45;
            const r = 70;
            pos[i*3] = r * Math.sin(phi) * Math.cos(theta);
            pos[i*3+1] = r * Math.cos(phi);
            pos[i*3+2] = r * Math.sin(phi) * Math.sin(theta);
        }
        starGeo = new THREE.BufferGeometry();
        starGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        starMat = new THREE.PointsMaterial({ color: 0xffffff, size: 0.2, transparent: true, opacity: 0.6 });
        stars = new THREE.Points(starGeo, starMat);
        group.add(stars);
    }
    buildStars(300);

    // ═══ SUN ORB ═══
    const sunMat2 = new THREE.MeshBasicMaterial({ color: 0xffcc88, transparent: true, opacity: 0.6 });
    const sunOrb = new THREE.Mesh(new THREE.SphereGeometry(2, 16, 16), sunMat2);
    group.add(sunOrb);

    // ═══ LIGHTING ═══
    const envSunLight = new THREE.DirectionalLight(0xffffff, 1.0);
    envSunLight.castShadow = true;
    envSunLight.shadow.mapSize.set(2048, 2048);
    envSunLight.shadow.camera.near = 0.5;
    envSunLight.shadow.camera.far = 50;
    for (const d of ['left', 'right', 'top', 'bottom']) envSunLight.shadow.camera[d] = d === 'left' || d === 'bottom' ? -15 : 15;
    group.add(envSunLight);

    const envAmbient = new THREE.AmbientLight(0x666666, 0.7);
    group.add(envAmbient);

    // Character fill light — always keeps Sapphire visible, brightens at night
    const fillLight = new THREE.PointLight(0xddeeff, 0.3, 15);
    fillLight.position.set(0, 3.5, 2);
    group.add(fillLight);

    // ═══ WISPS — glowing orbs that orbit Sapphire ═══
    const WISP_COUNT = 4;
    const wisps = [];
    const wispGroup = new THREE.Group();
    group.add(wispGroup);

    for (let i = 0; i < WISP_COUNT; i++) {
        const orb = new THREE.Mesh(
            new THREE.SphereGeometry(0.04, 8, 8),
            new THREE.MeshBasicMaterial({ color: 0x4a9eff, transparent: true, opacity: 0.7 })
        );
        // Soft glow halo around each wisp
        const halo = new THREE.Mesh(
            new THREE.SphereGeometry(0.12, 8, 8),
            new THREE.MeshBasicMaterial({ color: 0x4a9eff, transparent: true, opacity: 0.12 })
        );
        orb.add(halo);

        // Each wisp carries a tiny light to illuminate Sapphire
        const light = new THREE.PointLight(0x4a9eff, 0.15, 4);
        orb.add(light);

        wispGroup.add(orb);
        wisps.push({
            mesh: orb,
            light,
            // Each wisp has unique orbital params
            radius:   0.8 + i * 0.4,                    // distance from center
            height:   1.2 + i * 0.5,                    // base height
            speed:    0.15 + i * 0.07,                   // orbital speed
            phase:    (i / WISP_COUNT) * Math.PI * 2,   // evenly spaced start
            drift:    0.2 + Math.random() * 0.15,        // vertical bob amplitude
            driftSpd: 0.3 + Math.random() * 0.2,         // vertical bob speed
        });
    }

    // ═══ DUST MOTES ═══
    let dustGeo, dustMat, dustMotes, _dustCfg = null;
    function buildDust(cfg) {
        if (dustMotes) group.remove(dustMotes);
        if (!cfg) { _dustCfg = null; return; }
        _dustCfg = cfg;
        const count = cfg.count || 60;
        const [cx, cy, cz] = cfg.center || [0, 1.5, 0];
        const [sx, sy, sz] = cfg.spread || [6, 3, 8];
        const pos = new Float32Array(count * 3);
        for (let i = 0; i < count; i++) {
            pos[i*3]     = cx + (Math.random() - 0.5) * sx;
            pos[i*3 + 1] = cy - sy/2 + Math.random() * sy;
            pos[i*3 + 2] = cz + (Math.random() - 0.5) * sz;
        }
        dustGeo = new THREE.BufferGeometry();
        dustGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        dustMat = new THREE.PointsMaterial({
            color: cfg.color ? parseInt(cfg.color.replace('#',''), 16) : 0xffffff,
            size: 0.04, transparent: true, opacity: 0.25,
        });
        dustMotes = new THREE.Points(dustGeo, dustMat);
        group.add(dustMotes);
    }

    // ═══ LOCATION STATE ═══
    let _location = null;       // current loaded location
    let _locationName = '';
    let _useTimeOfDay = true;
    let _ringPhase = 0;
    let _time = 0;
    let _lastTimeUpdate = 0;
    let _locationLights = [];   // {light, dayIntensity, nightIntensity}

    // ═══ LOCATION LOADING ═══
    async function setLocation(name) {
        // Remove old location props + lights
        if (_location) {
            group.remove(_location.group);
            _location.dispose?.();
            _location = null;
        }
        for (const entry of _locationLights) group.remove(entry.light);
        _locationLights = [];

        _locationName = name;
        const loc = await loadLocation(name, THREE);
        if (!loc) return false;

        _location = loc;
        group.add(loc.group);

        // Apply location config
        const cfg = loc.config;

        // Sky
        if (cfg.sky) {
            _useTimeOfDay = cfg.sky.timeOfDay !== false;
            if (!_useTimeOfDay) {
                if (cfg.sky.topColor) skyMat.uniforms.topColor.value.set(cfg.sky.topColor);
                if (cfg.sky.bottomColor) skyMat.uniforms.bottomColor.value.set(cfg.sky.bottomColor);
            }
            if (cfg.sky.stars) buildStars(cfg.sky.stars);
            else buildStars(300);
        }

        // Floor
        if (cfg.floor) {
            if (cfg.floor.hide) {
                floor.visible = false;
                shadowFloor.visible = false;
            } else {
                floor.visible = true;
                shadowFloor.visible = true;
                if (cfg.floor.color) floorMat.uniforms.floorColor.value.set(cfg.floor.color);
                floorMat.uniforms.fadeStart.value = cfg.floor.fadeStart || 12;
                floorMat.uniforms.fadeEnd.value = cfg.floor.fadeEnd || 40;
                floorMat.uniforms.showGrid.value = cfg.floor.grid !== false ? 1.0 : 0.0;
            }
        }

        // Glow ring
        glowRing.visible = cfg.glowRing !== false;

        // Location lights (time-controlled)
        for (const def of (cfg.lights || [])) {
            const [x, y, z] = def.position || [0, 3, 0];
            const color = def.color ? parseInt(def.color.replace('#', ''), 16) : 0xffcc88;
            const light = new THREE.PointLight(color, def.dayIntensity || 0.1, def.radius || 10);
            light.position.set(x, y, z);
            group.add(light);
            _locationLights.push({
                light,
                dayIntensity: def.dayIntensity || 0.1,
                nightIntensity: def.nightIntensity || 0.6,
            });
        }

        // Dust motes
        buildDust(cfg.dustMotes || null);

        // Apply time immediately
        _lastTimeUpdate = 0;
        if (group.visible) applyTimeOfDay();

        return true;
    }

    // ═══ TIME OF DAY ═══
    function applyTimeOfDay() {
        if (!_useTimeOfDay) return;
        const hour = getTimeOfDay();
        const p = samplePalette(hour);

        skyMat.uniforms.topColor.value.setHex(p.skyTop);
        skyMat.uniforms.bottomColor.value.setHex(p.skyBottom);
        envSunLight.color.setHex(p.sunColor);
        envSunLight.intensity = p.sunIntensity;
        envAmbient.color.setHex(p.ambientColor);
        envAmbient.intensity = p.ambientIntensity;

        const sunDist = 55;
        sunOrb.position.set(Math.cos(p.sunAngle) * sunDist, Math.sin(p.sunAngle) * sunDist * 0.6 + 8, -20);
        sunOrb.material.color.setHex(p.sunColor);
        sunOrb.material.opacity = Math.max(0, Math.sin(p.sunAngle) * 0.8);
        sunOrb.visible = sunOrb.material.opacity > 0.05;
        envSunLight.position.copy(sunOrb.position);

        const nightFactor = Math.max(0, 1 - p.sunIntensity / 0.8);
        starMat.opacity = nightFactor * 0.6;
        stars.visible = nightFactor > 0.05;

        // Update window glass in location
        if (_location) {
            for (const glass of _location.windows) {
                glass.material.color.setHex(p.skyBottom);
            }
        }

        if (dustMat) dustMat.opacity = 0.1 + p.sunIntensity * 0.15;
        if (_dustCfg?.nightOnly && dustMotes) dustMotes.visible = nightFactor > 0.3;

        const fb = 0.1 + p.ambientIntensity * 0.12;
        floorMat.uniforms.floorColor.value.setRGB(fb, fb, fb * 1.15);

        // Night mix: 0 = full day, 1 = full night
        const nightMix = Math.max(0, 1 - p.sunIntensity / 0.8);

        // Character fill light — keeps Sapphire visible at night
        fillLight.intensity = lerp(0.3, 1.2, nightMix);

        // Location lights — interpolate between day/night intensity
        for (const entry of _locationLights) {
            entry.light.intensity = lerp(entry.dayIntensity, entry.nightIntensity, nightMix);
        }
    }

    // ═══ UPDATE ═══
    function update(delta) {
        if (!group.visible) return;
        _time += delta;

        const now = performance.now();
        if (now - _lastTimeUpdate > 30000 || _lastTimeUpdate === 0) {
            _lastTimeUpdate = now;
            applyTimeOfDay();
        }

        // Location object animations (embers, flicker, etc.)
        if (_location) _location.update(delta);

        // Dust drift
        if (dustGeo && dustMotes?.visible) {
            const dPos = dustGeo.attributes.position.array;
            const count = dPos.length / 3;
            for (let i = 0; i < count; i++) {
                dPos[i*3]     += Math.sin(_time * 0.3 + i) * delta * 0.05;
                dPos[i*3 + 1] += Math.sin(_time * 0.2 + i * 0.7) * delta * 0.03;
                dPos[i*3 + 2] += Math.cos(_time * 0.25 + i * 0.5) * delta * 0.04;
            }
            dustGeo.attributes.position.needsUpdate = true;
        }

        // Wisps orbit
        for (const w of wisps) {
            const angle = w.phase + _time * w.speed;
            w.mesh.position.set(
                Math.sin(angle) * w.radius,
                w.height + Math.sin(_time * w.driftSpd + w.phase) * w.drift,
                Math.cos(angle) * w.radius
            );
            // Gentle pulse on opacity
            w.mesh.material.opacity = 0.5 + Math.sin(_time * 1.5 + w.phase) * 0.2;
            w.light.intensity = 0.1 + Math.sin(_time * 1.5 + w.phase) * 0.08;
        }

        // Glow ring pulse
        if (glowRing.visible) {
            _ringPhase += delta * 0.5;
            ringMat.opacity = 0.08 + Math.sin(_ringPhase) * 0.04;
        }
    }

    function setVisible(visible) {
        group.visible = visible;
        if (visible) {
            _lastTimeUpdate = 0;
            applyTimeOfDay();
            // Auto-load cabin if no location set
            if (!_location) setLocation('cabin');
        }
    }

    function enableAvatarShadows(model) {
        model.traverse(child => { if (child.isMesh) child.castShadow = true; });
    }

    function getLocationName() { return _locationName; }
    function getHotspots() { return _location?.hotspots || []; }

    return {
        update, setVisible, enableAvatarShadows,
        setLocation, getLocationName, getHotspots,
        listLocations: listBuiltinLocations,
        group,
    };
}
