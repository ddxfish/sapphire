// Location loader — reads a location JSON, instantiates objects, builds the scene group
import { OBJECTS } from './objects.js';

// Built-in locations (shipped with plugin)
const BUILTIN = ['cabin', 'void', 'starfield', 'garden'];

export async function loadLocation(name, THREE) {
    // Fetch location JSON (built-in from plugin web dir, or user-created from API)
    let loc;
    const builtinUrl = `/plugin-web/avatar/locations/${name}.json`;
    const userUrl = `/api/plugin/avatar/locations/${name}`;

    try {
        let resp = await fetch(builtinUrl);
        if (!resp.ok) resp = await fetch(userUrl);
        if (!resp.ok) throw new Error(`Location '${name}' not found`);
        loc = await resp.json();
    } catch (e) {
        console.error(`[Location] Failed to load '${name}':`, e);
        return null;
    }

    return buildLocation(loc, THREE);
}

export function buildLocation(loc, THREE) {
    const group = new THREE.Group();
    const updaters = [];
    const windows = [];  // track windows so environment can update glass color

    // Instantiate objects
    for (const def of (loc.objects || [])) {
        const factory = OBJECTS[def.type];
        if (!factory) {
            console.warn(`[Location] Unknown object type: ${def.type}`);
            continue;
        }

        const obj = factory(THREE, def.options || {});
        const [x, y, z] = def.position || [0, 0, 0];
        obj.group.position.set(x, y, z);

        if (def.rotation) {
            obj.group.rotation.y = def.rotation * (Math.PI / 180);
        }

        group.add(obj.group);
        if (obj.update) updaters.push(obj.update);
        if (def.type === 'window' && obj.glass) windows.push(obj.glass);
    }

    return {
        group,
        config: loc,
        hotspots: loc.hotspots || [],
        windows,
        update(delta) {
            for (const fn of updaters) fn(delta);
        },
        dispose() {
            // Future: proper geometry/material disposal
        },
    };
}

export function listBuiltinLocations() {
    return BUILTIN.map(name => ({ name, builtin: true }));
}
