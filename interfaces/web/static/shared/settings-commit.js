// shared/settings-commit.js — write-through primitives for the Settings view.
//
// The Settings view paints every tab from a snapshot it fetched on entry.
// Writes a tab persists ITSELF (provider-card PUTs, self-management toggles,
// the dashboard name…) never reached that snapshot, so switching tabs and
// back repainted pre-edit state (U1, 2026-09-20). These two moves patch the
// snapshot the way the server merged the write — and only AFTER the server
// confirmed it: commit the value the PUT resolved with, never the one you
// hoped to write (a rolled-back batch key or a 500 must not land here).
//
// `state` = { settings, pendingChanges } read at CALL time — the view
// reassigns both objects on every refetch/save, so callers hand in the
// current ones; nothing here holds a reference across calls.

/** Whole-key write-through: the key is now saved, so it is no longer pending. */
export function commitInto(state, key, value) {
  state.settings[key] = value;
  delete state.pendingChanges[key];
}

/** Sub-key of a map (LLM_PROVIDERS[claude].model = …): merge like the route
 *  does (dict.update on one entry), leaving sibling entries untouched. A new
 *  map object replaces the old so a stale reference can't observe the edit. */
export function mergeInto(state, mapKey, subKey, updates) {
  const map = { ...(state.settings[mapKey] || {}) };
  map[subKey] = { ...(map[subKey] || {}), ...(updates || {}) };
  state.settings[mapKey] = map;
  delete state.pendingChanges[mapKey];
}
