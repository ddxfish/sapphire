// shared/init-data.js - Cached init data for plugins
// Fetches /api/init once, plugins read from cache

let initData = null;
let initPromise = null;

/**
 * Fetch init data (cached - only fetches once per page load)
 * @returns {Promise<Object>} Init data with abilities, functions, prompts, spices, etc.
 */
export async function getInitData() {
    if (initData) return initData;

    if (!initPromise) {
        initPromise = fetch('/api/init')
            .then(res => {
                if (!res.ok) throw new Error('Failed to fetch init data');
                return res.json();
            })
            .then(data => {
                initData = data;
                return data;
            })
            .catch(err => {
                initPromise = null;  // Allow retry on error
                throw err;
            });
    }

    return initPromise;
}

/**
 * Force refresh of init data (call after significant changes)
 */
export async function refreshInitData() {
    initData = null;
    initPromise = null;
    return getInitData();
}

/**
 * Get cached init data synchronously (returns null if not loaded)
 */
export function getInitDataSync() {
    return initData;
}

// Convenience accessors
export async function getAbilities() {
    const data = await getInitData();
    return data.abilities;
}

export async function getFunctions() {
    const data = await getInitData();
    return data.functions;
}

export async function getPrompts() {
    const data = await getInitData();
    return data.prompts;
}

export async function getSpices() {
    const data = await getInitData();
    return data.spices;
}
