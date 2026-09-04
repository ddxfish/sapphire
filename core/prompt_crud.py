import json
import logging
import time
from .prompt_manager import prompt_manager
from . import prompt_state

logger = logging.getLogger(__name__)


def list_prompts():
    """List all available prompts (monoliths + scenario presets)."""
    all_prompts = []

    # Get monolith prompts
    if prompt_manager.monoliths:
        all_prompts.extend(list(prompt_manager.monoliths.keys()))

    # Get scenario presets (assembled prompts)
    if prompt_manager.scenario_presets:
        all_prompts.extend(list(prompt_manager.scenario_presets.keys()))

    # Remove duplicates and filter out internal keys
    all_prompts = [p for p in set(all_prompts) if not p.startswith('_')]

    hidden = hidden_prompt_kinds()
    if hidden:
        all_prompts = [p for p in all_prompts if p not in hidden]

    return sorted(all_prompts)


def hidden_prompt_kinds():
    """{name: kind} for pack prompts hidden from pickers (kind != 'user'),
    minus any shadowed by a user or vault entry — a shadow is the user's own
    copy and stays visible. LIST-level only: get_prompt/activation resolve
    the full merge, so the story engine keeps activating costumes by exact
    name. UI payloads carry this map so dropdown synthesizers can label a
    chat still pointing at a hidden name (e.g. a story costume) honestly."""
    from core import prompt_packs, prompt_vault
    hidden = {n: k for n, k in prompt_packs.get_kinds().items() if k != 'user'}
    if not hidden:
        return hidden
    shadow = (set(prompt_manager._monoliths) | set(prompt_manager._scenario_presets)
              | set(prompt_vault.overlay_monoliths()) | set(prompt_vault.overlay_presets()))
    return {n: k for n, k in hidden.items() if n not in shadow}


def visible_components():
    """Merged components minus hidden pack pieces (kind != 'user',
    unshadowed) — the UI/tool view of the piece library. Rendering must keep
    reading the full prompt_manager.components merge; this filter is
    list-level only."""
    from core import prompt_packs, prompt_vault
    merged = prompt_manager.components
    kinds = prompt_packs.component_kinds()
    if not kinds:
        return merged
    user = prompt_manager._components
    vault = prompt_vault.overlay_components()
    out = {}
    for ctype, entries in merged.items():
        drop = {k for k, kind in kinds.get(ctype, {}).items()
                if kind != 'user'
                and k not in user.get(ctype, {})
                and k not in vault.get(ctype, {})}
        out[ctype] = {k: v for k, v in entries.items() if k not in drop} if drop else entries
    return out


def _monolith_result(name: str, mono) -> dict:
    if not isinstance(mono, dict):
        mono = {'content': str(mono)}
    return {
        'name': name,
        'type': 'monolith',
        'content': mono.get('content', ''),
        'privacy_required': mono.get('privacy_required', False)
    }


def _preset_result(name: str, components: dict) -> dict:
    privacy_required = components.get('_privacy_required', False)
    # Filter out metadata for assembly
    clean_components = {k: v for k, v in components.items() if not k.startswith('_')}
    return {
        'name': name,
        'type': 'assembled',
        'components': clean_components,
        'content': prompt_manager.assemble_from_components(clean_components),
        'privacy_required': privacy_required
    }


def get_prompt(name: str):
    """Resolve a prompt name to {name, type, content, privacy_required, ...}.

    THE single resolver — every consumer (activation, boot prime, stream
    brains, continuity tasks, previews, char counts, exports) resolves
    through here. Handles the 'default' sentinel that five consumers used
    to each fumble their own way (stream brains ran with the literal text
    "System prompt not loaded.", boot wore a hardcoded fallback all
    session). Content is un-templated except for the sentinel (which
    renders through the live assembler); live paths apply
    _replace_templates / the chat-layer replace, which is idempotent.

    Lookup order (C-10, Krem's ruling 2026-08-09): USER entries win — even
    across types. Private dicts are checked before the pack-merged views so
    a pack shipping name X of the OTHER type can't shadow the user's X.
    """
    if name in prompt_manager._monoliths:
        return _monolith_result(name, prompt_manager._monoliths[name])
    if name in prompt_manager._scenario_presets:
        return _preset_result(name, prompt_manager._scenario_presets[name])

    # Pack-shipped entries (merged views minus the user hits above)
    if name in prompt_manager.monoliths:
        return _monolith_result(name, prompt_manager.monoliths[name])
    if name in prompt_manager.scenario_presets:
        return _preset_result(name, prompt_manager.scenario_presets[name])

    if name == 'default':
        # 'default' is the assembled-mode SENTINEL, not a stored name — it
        # means "run the current assembled state". Resolving it here (instead
        # of returning None) is what keeps stream brains, boot prime, and
        # continuity tasks on the same text the main chat runs.
        components = {k: (list(v) if isinstance(v, list) else v)
                      for k, v in prompt_state._assembled_state.items()
                      if k not in ('spice', 'next_spice', 'active_preset')}
        return {
            'name': 'default',
            'type': 'assembled',
            'components': components,
            'content': prompt_state.assemble_prompt()['content'],
            'privacy_required': False
        }

    return None


VAULT_LOCKED_MSG = "Vault is locked — unlock the vault to save this item."


def save_prompt(name: str, data: dict, allow_overwrite: bool = True,
                reason: str = None, audit: bool = True,
                origin: str = None) -> tuple[bool, str]:
    """
    Save a prompt - updates user JSON files (monoliths or scenario_presets)
    or the vault, per the write-routing rule below.

    Args:
        name: Prompt name
        data: Prompt data with 'type' and 'content'/'components'
        allow_overwrite: False refuses to replace an existing same-type USER
            prompt (pack prompts don't count — shadowing them is the intended
            edit path). Accepted-but-ignored until 2026-08-09; persona import
            passed it believing it worked and silently overwrote. Vault
            entries DO count as existing while unlocked.
        reason: optional why — rides the prompt-ledger audit event
        audit: False when a higher-level event already describes this save
            (piece activations) — the audit snapshot still refreshes
        origin: 'vault' when the CLIENT knows the content came from the vault
            (editor origin stamp). While locked that save is REFUSED with
            VAULT_LOCKED_MSG — the stale-editor guard (recon finding 6): an
            editor holding decrypted vault content must never splash it into
            the regular plaintext store after an idle-lock. 'regular' forces
            the user store (import lanes: installing shared content must not
            depend on invisible lock state). None = natural routing.

    Vault write routing (v1, no per-item toggle):
        exists in USER store → regular (user wins the merge, so the user
        copy is what you see and therefore what you edit); exists in VAULT
        (unlocked) → vault; pack-shadow edit → regular (customizing shipped
        prompts keeps identical semantics regardless of vault state); truly
        NEW: vault unlocked → vault, locked → regular.

    Returns:
        (success: bool, message: str)
    """
    try:
        # Reserved sentinels: 'default' means "the current assembled state"
        # (the resolver renders it live) and 'assembled' flips is_assembled_mode
        # unconditionally. A stored prompt wearing either name would make the
        # sentinel ambiguous — blocked at the write door (ruling 2026-08-09).
        if name in ('default', 'assembled'):
            msg = (f"'{name}' is a reserved name — it refers to the current "
                   f"assembled state. Pick another name.")
            logger.warning(f"Refused to save prompt under reserved name '{name}'")
            return False, msg

        prompt_type = data.get('type', 'monolith')

        # Cross-type collision checks read the PRIVATE dicts (DR-7): a pack
        # shipping the same name must not block the user's save — user
        # entries win the merge, shadowing packs is the intended edit path.
        if prompt_type == 'monolith':
            if name in prompt_manager._scenario_presets:
                msg = f"Name '{name}' already exists as assembled prompt"
                logger.warning(msg)
                return False, msg

        elif prompt_type == 'assembled':
            if name in prompt_manager._monoliths:
                msg = f"Name '{name}' already exists as monolith prompt"
                logger.warning(msg)
                return False, msg

        from core import prompt_vault
        if not allow_overwrite:
            existing = (name in prompt_manager._monoliths if prompt_type == 'monolith'
                        else name in prompt_manager._scenario_presets) \
                or prompt_vault.vault_has_prompt(name)
            if existing:
                msg = f"Prompt '{name}' already exists (overwrite not allowed)"
                logger.warning(msg)
                return False, msg

        # Shape guards — a persisted content:null once took the whole UI
        # down (/api/init 500 on len(None)); non-string/dict shapes detonate
        # in the renderer later. Reject at the write door.
        if prompt_type == 'monolith' and not isinstance(data.get('content'), str):
            return False, "Prompt content must be a string"
        if prompt_type == 'assembled':
            comps = data.get('components')
            if not isinstance(comps, dict):
                return False, "Assembled prompt requires a components dict"
            bad = [k for k, v in comps.items() if not isinstance(v, (str, list))]
            if bad:
                return False, f"Component values must be strings or lists (bad: {', '.join(bad)})"

        # ── Vault write routing (see docstring) ──
        if origin == 'vault' and not prompt_vault.vault_unlocked():
            logger.warning(f"Refused vault-origin save of '{name}' while locked")
            return False, VAULT_LOCKED_MSG
        in_user = (name in prompt_manager._monoliths
                   or name in prompt_manager._scenario_presets)
        if not in_user and prompt_vault.vault_unlocked() and origin != 'regular':
            from core import prompt_packs
            pack_shadow_edit = (name in prompt_packs.get_sources()
                                and not prompt_vault.vault_has_prompt(name))
            if origin == 'vault' or not pack_shadow_edit:
                if prompt_type == 'monolith':
                    ok, code = prompt_vault.set_monolith(name, data['content'])
                else:
                    ok, code = prompt_vault.set_preset(name, data['components'].copy())
                if not ok:
                    if code == 'cross_type':
                        return False, (f"Name '{name}' already exists as the other "
                                       f"prompt type in the vault")
                    return False, f"Vault save refused ({code})"
                logger.info(f"Saved {prompt_type} '{name}' (vault)")
                return True, f"Saved {'monolith' if prompt_type == 'monolith' else 'assembled'} '{name}' (vault)"

        # Mutate + save inside the manager lock: unlocked mutation raced the
        # file watcher's reload() dict REBIND — the write landed in an
        # abandoned dict and the save persisted the reloaded one (silent
        # loss with a success response). Savers propagate refusal (the
        # load-failed latch) instead of returning None.
        if prompt_type == 'monolith':
            with prompt_manager._lock:
                prompt_manager._monoliths[name] = {
                    'content': data['content'],
                    'privacy_required': data.get('privacy_required', False)
                }
                saved = prompt_manager.save_monoliths(reason=reason, audit=audit)
            if not saved:
                return False, ("Save refused — the prompt store failed to load earlier. "
                               "Fix user/prompts/prompt_monoliths.json and reload.")
            logger.info(f"Saved monolith '{name}'")
            return True, f"Saved monolith '{name}'"

        elif prompt_type == 'assembled':
            components = data['components'].copy()
            # Store privacy_required at top level of preset
            components['_privacy_required'] = data.get('privacy_required', False)
            with prompt_manager._lock:
                prompt_manager._scenario_presets[name] = components
                saved = prompt_manager.save_scenario_presets(reason=reason, audit=audit)
            if not saved:
                return False, ("Save refused — the prompt store failed to load earlier. "
                               "Fix user/prompts/prompt_pieces.json and reload.")
            logger.info(f"Saved assembled prompt '{name}'")
            return True, f"Saved assembled '{name}'"

        else:
            msg = f"Unknown prompt type: {prompt_type}"
            logger.error(msg)
            return False, msg
            
    except Exception as e:
        logger.error(f"Failed to save prompt '{name}': {e}")
        import traceback
        traceback.print_exc()
        return False, str(e)


def delete_prompt(name: str, reason: str = None) -> bool:
    """Delete a prompt from storage.

    If the deleted prompt is currently active, auto-switch the active preset
    to 'default' and publish SETTINGS_CHANGED. Without this, get_current_prompt
    silently falls back to a stale assembled state — user deletes the active
    prompt, next turn renders from whatever _assembled_state happens to hold.
    Silent-default class. H3 fix 2026-04-22.
    """
    try:
        deleted = False

        # Detect active-before-delete so we can loudly hand off. Check both
        # the state-dict active_preset and the prompt_manager attr (they can
        # drift — separate scout finding) to cover either source.
        try:
            was_active = (
                prompt_state._assembled_state.get("active_preset") == name
                or getattr(prompt_manager, '_active_preset_name', None) == name
            )
        except Exception:
            was_active = False

        # Delete from monoliths if present — PRIVATE dict membership, not the
        # merged property: pack-shipped prompts are read-only (mirror-only
        # rule). Deleting a user entry that shadows a pack prompt makes the
        # pack version show through again — intended.
        if name in prompt_manager._monoliths:
            with prompt_manager._lock:
                del prompt_manager._monoliths[name]
                prompt_manager.save_monoliths(reason=reason)
            logger.info(f"Deleted monolith '{name}'")
            deleted = True

        # Delete from scenario_presets if present
        if name in prompt_manager._scenario_presets:
            with prompt_manager._lock:
                del prompt_manager._scenario_presets[name]
                prompt_manager.save_scenario_presets(reason=reason)
            logger.info(f"Deleted assembled prompt '{name}'")
            deleted = True

        # Vault entries (only visible — and deletable — while unlocked).
        # Only when no user entry was deleted: removing a user shadow
        # reveals the vault version underneath, same shadow-reveal
        # semantics as packs. Sealed names fall through to not-found.
        if not deleted:
            from core import prompt_vault
            if prompt_vault.vault_unlocked():
                for _deleter, _kind in ((prompt_vault.delete_monolith, 'monolith'),
                                        (prompt_vault.delete_preset, 'assembled')):
                    ok, _code = _deleter(name)
                    if ok:
                        logger.info(f"Deleted {_kind} '{name}' (vault)")
                        deleted = True
                        break

        # Hand off active state loudly if we just deleted the active prompt.
        if deleted and was_active:
            try:
                prompt_state.set_active_preset_name('default')
                logger.warning(
                    f"Deleted prompt '{name}' was ACTIVE — active preset "
                    f"reset to 'default'. Chats still pointing at '{name}' "
                    f"will fall back to default on next activation."
                )
                # Re-render the live prompt too — before this, the running
                # chat kept SPEAKING with the deleted prompt's text until the
                # next activation (storage was updated, the brain wasn't).
                revalidate_active(reason=f"deleted active prompt '{name}'")
                try:
                    from core.event_bus import publish, Events
                    publish(Events.SETTINGS_CHANGED, {
                        "key": "active_prompt",
                        "value": "default",
                        "reason": f"deleted_active:{name}",
                    })
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Active-preset reset after delete failed: {e}")
        
        if not deleted:
            from core import prompt_packs
            if name in prompt_packs.get_sources():
                logger.warning(
                    f"Prompt '{name}' is shipped by plugin "
                    f"'{prompt_packs.get_sources()[name]}' — read-only, not "
                    f"deleted. Disable the plugin to remove it, or save a "
                    f"user prompt with the same name to shadow it."
                )
            else:
                logger.warning(f"Prompt '{name}' not found in any storage")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to delete prompt '{name}': {e}")
        return False


def save_component(comp_type: str, key: str, value: str,
                   reason: str = None, origin: str = None) -> tuple[bool, str]:
    """Item-level save funnel for a single prompt piece (create or update).

    THE single write path for pieces — the web route and the AI meta tool
    both land here (phase 0 of the prompt vault: per-item write routing
    needs item identity, which save_components() doesn't have). Writes the
    PRIVATE dict under the manager lock (watcher-reload rebind race),
    propagates a refused save, publishes COMPONENTS_CHANGED.

    Vault routing mirrors save_prompt: existing user piece stays regular,
    existing vault piece stays vault, pack-shadow edits stay regular, NEW
    pieces follow the lock state (unlocked → vault). origin='vault' while
    locked is refused with VAULT_LOCKED_MSG (stale-editor guard, finding 6).
    Vault-routed events are NAME-FREE — SSE replays recent events to every
    new tab, across a later lock.
    """
    if not isinstance(value, str):
        return False, "Component value must be a string"
    from core import prompt_vault
    if origin == 'vault' and not prompt_vault.vault_unlocked():
        logger.warning(f"Refused vault-origin piece save '{comp_type}/{key}' while locked")
        return False, VAULT_LOCKED_MSG
    in_user = key in prompt_manager._components.get(comp_type, {})
    if not in_user and prompt_vault.vault_unlocked() and origin != 'regular':
        from core import prompt_packs
        pack_shadow_edit = (prompt_packs.piece_source(comp_type, key) is not None
                            and not prompt_vault.vault_has_piece(comp_type, key))
        if origin == 'vault' or not pack_shadow_edit:
            ok, code = prompt_vault.set_piece(comp_type, key, value)
            if not ok:
                return False, f"Vault save refused ({code})"
            try:
                from core.event_bus import publish, Events
                publish(Events.COMPONENTS_CHANGED, {"action": "vault_changed"})
            except Exception:
                pass
            return True, f"Saved {comp_type}/{key} (vault)"
    with prompt_manager._lock:
        prompt_manager._components.setdefault(comp_type, {})[key] = value
        saved = prompt_manager.save_components(reason=reason)
    if not saved:
        return False, ("Save refused — the prompt store failed to load earlier. "
                       "Fix user/prompts/prompt_pieces.json and reload.")
    try:
        from core.event_bus import publish, Events
        publish(Events.COMPONENTS_CHANGED, {"type": comp_type, "key": key})
    except Exception:
        pass
    return True, f"Saved {comp_type}/{key}"


def delete_component(comp_type: str, key: str,
                     reason: str = None) -> tuple[bool, str]:
    """Item-level delete funnel for a prompt piece (user entries only).

    Returns (ok, code): code is '' on success, else one of
    'not_found' | 'pack_owned' | 'store_latch' — callers keep their own
    HTTP-status mapping / tool wording. Membership is checked UNDER the
    lock (the old route grabbed the dict reference before locking — a
    watcher reload between check and lock could rebind it and the delete
    landed in an abandoned dict).
    """
    with prompt_manager._lock:
        user_components = prompt_manager._components
        present = comp_type in user_components and key in user_components[comp_type]
        if present:
            del user_components[comp_type][key]
            saved = prompt_manager.save_components(reason=reason)
    if not present:
        # Vault piece? (Only reachable while unlocked — delete_piece answers
        # 'locked'/'not_found' otherwise and we fall through.) User-shadow
        # deletes stay in the branch above and reveal the vault version.
        from core import prompt_vault
        ok_vault, _vcode = prompt_vault.delete_piece(comp_type, key)
        if ok_vault:
            try:
                from core.event_bus import publish, Events
                publish(Events.COMPONENTS_CHANGED, {"action": "vault_changed"})
            except Exception:
                pass
            return True, ''
        from core import prompt_packs
        if prompt_packs.piece_source(comp_type, key):
            return False, 'pack_owned'
        return False, 'not_found'
    if not saved:
        return False, 'store_latch'
    try:
        from core.event_bus import publish, Events
        publish(Events.COMPONENTS_CHANGED,
                {"type": comp_type, "key": key, "action": "deleted"})
    except Exception:
        pass
    return True, ''


def is_vault_prompt(name: str) -> bool:
    """True when NAME currently resolves from the vault (present in the
    unlocked vault and not shadowed by a user entry). Routes use this for
    event hygiene: vault-resolved names never ride the event bus."""
    from core import prompt_vault
    return (prompt_vault.vault_has_prompt(name)
            and name not in prompt_manager._monoliths
            and name not in prompt_manager._scenario_presets)


# ── v1.1 store toggle: move items between the vault and the regular store ──
# Sequencing rule (both directions): WRITE the destination before DELETING
# the source — a crash mid-move leaves the item duplicated (user copy
# shadows vault), never lost. Plaintext-side saves run audit=False: the
# move itself is names-only in the ledger (the vault side's own row tells
# the story); past ledger history of a moved-in prompt is history.

def move_prompt_to_vault(name: str) -> tuple[bool, str]:
    """Regular store → vault. Privacy becomes True by construction."""
    from core import prompt_vault
    if not prompt_vault.vault_unlocked():
        return False, VAULT_LOCKED_MSG
    with prompt_manager._lock:
        if name in prompt_manager._monoliths:
            entry = prompt_manager._monoliths[name]
            content = entry.get('content', '') if isinstance(entry, dict) else str(entry)
            ok, code = prompt_vault.set_monolith(name, content)
            if not ok:
                return False, f"Vault refused the move ({code})"
            del prompt_manager._monoliths[name]
            saved = prompt_manager.save_monoliths(
                reason=f"moved '{name}' into the vault", audit=False)
        elif name in prompt_manager._scenario_presets:
            preset = {k: v for k, v in prompt_manager._scenario_presets[name].items()
                      if k != '_privacy_required'}
            ok, code = prompt_vault.set_preset(name, preset)
            if not ok:
                return False, f"Vault refused the move ({code})"
            del prompt_manager._scenario_presets[name]
            saved = prompt_manager.save_scenario_presets(
                reason=f"moved '{name}' into the vault", audit=False)
        else:
            return False, f"Prompt '{name}' not found in the regular store"
    if not saved:
        return False, (f"'{name}' is in the vault, but the regular-store delete "
                       f"failed to persist (store latch) — the plaintext copy "
                       f"still shadows it. Fix the store and retry.")
    # Referrers may already point at the name (it was a public prompt) —
    # stamp the refs index only if something actually references it.
    if _vault_name_still_referenced(name):
        prompt_vault.refs_stamp(name)
    _publish_vault_changed()
    logger.info(f"Moved prompt '{name}' into the vault")
    return True, f"Moved '{name}' into the vault"


def move_prompt_from_vault(name: str) -> tuple[bool, str]:
    """Vault → regular store. Leaving the vault CLEARS privacy_required
    (Krem's ruling 2026-08-12: vault membership IS the privacy bit)."""
    from core import prompt_vault
    if not prompt_vault.vault_unlocked():
        return False, VAULT_LOCKED_MSG
    if name in prompt_manager._monoliths or name in prompt_manager._scenario_presets:
        return False, (f"'{name}' also exists in the regular store — the user "
                       f"copy already shadows the vault copy; delete one first")
    monos = prompt_vault.overlay_monoliths()
    presets = prompt_vault.overlay_presets()
    if name in monos:
        with prompt_manager._lock:
            prompt_manager._monoliths[name] = {
                'content': monos[name].get('content', ''), 'privacy_required': False}
            saved = prompt_manager.save_monoliths(
                reason=f"moved '{name}' out of the vault", audit=False)
            if not saved:
                prompt_manager._monoliths.pop(name, None)
        if not saved:
            return False, "Regular store refused the save (store latch) — nothing moved"
        ok, code = prompt_vault.delete_monolith(name)
    elif name in presets:
        preset = {k: v for k, v in presets[name].items() if k != '_privacy_required'}
        preset['_privacy_required'] = False
        with prompt_manager._lock:
            prompt_manager._scenario_presets[name] = preset
            saved = prompt_manager.save_scenario_presets(
                reason=f"moved '{name}' out of the vault", audit=False)
            if not saved:
                prompt_manager._scenario_presets.pop(name, None)
        if not saved:
            return False, "Regular store refused the save (store latch) — nothing moved"
        ok, code = prompt_vault.delete_preset(name)
    else:
        return False, f"'{name}' is not in the vault"
    if not ok:
        return False, (f"Copied out, but the vault-side delete failed ({code}) — "
                       f"the regular copy shadows the vault copy")
    _publish_vault_changed()
    logger.info(f"Moved prompt '{name}' out of the vault")
    return True, f"Moved '{name}' to the regular store (now public — privacy flag cleared)"


def move_piece_to_vault(comp_type: str, key: str) -> tuple[bool, str]:
    from core import prompt_vault
    if not prompt_vault.vault_unlocked():
        return False, VAULT_LOCKED_MSG
    with prompt_manager._lock:
        if key not in prompt_manager._components.get(comp_type, {}):
            return False, f"Piece '{comp_type}/{key}' not found in the regular store"
        ok, code = prompt_vault.set_piece(comp_type, key,
                                          prompt_manager._components[comp_type][key])
        if not ok:
            return False, f"Vault refused the move ({code})"
        del prompt_manager._components[comp_type][key]
        saved = prompt_manager.save_components(
            reason=f"moved '{comp_type}/{key}' into the vault", audit=False)
    if not saved:
        return False, (f"'{comp_type}/{key}' is in the vault, but the regular-store "
                       f"delete failed to persist — the plaintext copy still shadows it")
    _publish_vault_changed(components=True)
    logger.info(f"Moved piece '{comp_type}/{key}' into the vault")
    return True, f"Moved '{comp_type}/{key}' into the vault"


def move_piece_from_vault(comp_type: str, key: str) -> tuple[bool, str]:
    from core import prompt_vault
    if not prompt_vault.vault_unlocked():
        return False, VAULT_LOCKED_MSG
    if key in prompt_manager._components.get(comp_type, {}):
        return False, f"'{comp_type}/{key}' also exists in the regular store — delete one copy first"
    if not prompt_vault.vault_has_piece(comp_type, key):
        return False, f"'{comp_type}/{key}' is not in the vault"
    value = prompt_vault.overlay_components().get(comp_type, {}).get(key, '')
    with prompt_manager._lock:
        prompt_manager._components.setdefault(comp_type, {})[key] = value
        saved = prompt_manager.save_components(
            reason=f"moved '{comp_type}/{key}' out of the vault", audit=False)
        if not saved:
            prompt_manager._components[comp_type].pop(key, None)
    if not saved:
        return False, "Regular store refused the save (store latch) — nothing moved"
    ok, code = prompt_vault.delete_piece(comp_type, key)
    if not ok:
        return False, (f"Copied out, but the vault-side delete failed ({code}) — "
                       f"the regular copy shadows the vault copy")
    _publish_vault_changed(components=True)
    logger.info(f"Moved piece '{comp_type}/{key}' out of the vault")
    return True, f"Moved '{comp_type}/{key}' to the regular store (plaintext)"


def _publish_vault_changed(components=False):
    """Name-free refresh nudge for both stores' listeners."""
    try:
        from core.event_bus import publish, Events
        publish(Events.PROMPT_CHANGED, {"name": "", "action": "vault_changed"})
        if components:
            publish(Events.COMPONENTS_CHANGED, {"action": "vault_changed"})
    except Exception:
        pass


def vault_ref_sync(new_name=None, old_name=None):
    """References-index bookkeeping (ruling C amendment) — called by the
    three referrer funnels (chat settings, personas, continuity tasks)
    whenever a prompt reference changes hands.

    Stamp: only lands for names in the UNLOCKED vault (refs_stamp no-ops
    otherwise) — locked names aren't offered anywhere, so the index can
    never grow while sealed. Release: any lock state, and only when the
    ground-truth scan says NO referrer still pins the name."""
    try:
        from core import prompt_vault
        if new_name and new_name != old_name:
            prompt_vault.refs_stamp(new_name)
        if old_name and old_name != new_name \
                and old_name in prompt_vault.refs_names() \
                and not _vault_name_still_referenced(old_name):
            prompt_vault.refs_drop(old_name)
    except Exception as e:
        logger.warning(f"Vault ref sync failed: {e}")


def _vault_name_still_referenced(name: str) -> bool:
    """Ground-truth scan across the three referrer classes. Fail-SAFE per
    source: an unreadable source counts as still-referenced — a stale name
    lingering in the index is a smaller sin than dropping a live reference
    (the name was already leaked by the referrer that created it)."""
    try:
        from core.api_fastapi import get_system
        system = get_system()
    except Exception:
        return True
    try:
        if name in system.llm_chat.session_manager.get_all_prompt_settings():
            return True
    except Exception:
        return True
    try:
        from core.personas.persona_manager import persona_manager
        for p in persona_manager.get_all().values():
            if (p.get('settings') or {}).get('prompt') == name:
                return True
    except Exception:
        return True
    try:
        sched = getattr(system, 'continuity_scheduler', None)
        if sched is not None:
            with sched._lock:
                if any(t.get('prompt') == name for t in sched._tasks.values()):
                    return True
    except Exception:
        return True
    return False


def save_components_batch(items: dict, keep=frozenset(), overwrite: bool = True,
                          reason: str = None) -> tuple[bool, str]:
    """Bulk piece writer (persona-card import): one lock, one disk save,
    one COMPONENTS_CHANGED — not N of each.

    Deliberately NOT vault-routed: importing a shared persona card is
    "install shipped content", not "create private material" — routing it
    by invisible global lock state would be a many-worlds surprise, and
    vaulted pieces would break the card's own re-export. Vault entries are
    created via the item-level funnels or a future per-item toggle (v1.1).

    items: {comp_type: {key: value}}. Non-dict groups and non-string values
    are skipped with a warning (card bundles are third-party data — a
    persisted non-string detonates the renderer later). keep: set of
    (comp_type, key) tuples the user chose to keep local. overwrite False
    skips keys already present in the MERGED view (pack pieces count —
    matches the import UI's "existing" notion).
    """
    wrote = 0
    with prompt_manager._lock:
        for comp_type, defs in (items or {}).items():
            if not isinstance(defs, dict):
                continue
            for key, value in defs.items():
                if not isinstance(value, str):
                    logger.warning(f"Skipping non-string piece {comp_type}/{key} in batch")
                    continue
                if (comp_type, key) in keep:
                    continue
                if not overwrite and prompt_manager.components.get(comp_type, {}).get(key):
                    continue
                prompt_manager._components.setdefault(comp_type, {})[key] = value
                wrote += 1
        saved = prompt_manager.save_components(reason=reason) if wrote else True
    if not saved:
        return False, ("Save refused — the prompt store failed to load earlier. "
                       "Fix user/prompts/prompt_pieces.json and reload.")
    if wrote:
        # The old import loop published nothing — imported pieces sat stale
        # in every open tab until a manual refresh.
        try:
            from core.event_bus import publish, Events
            publish(Events.COMPONENTS_CHANGED, {"action": "imported", "count": wrote})
        except Exception:
            pass
    return True, f"Wrote {wrote} pieces"


def activate_prompt(name: str, system) -> tuple[bool, str]:
    """Activate a prompt: snapshot into the live chat, track the active
    preset, apply scenario pieces, persist to chat settings.

    Single implementation shared by the /api/prompts/{name}/load route and
    the meta tools (which previously duplicated this via loopback HTTP).
    Preserves the route's original operation order. set_active_preset_name
    publishes PROMPT_CHANGED for UI consumers.

    Stream-aware: during a phone/driver stream (brain override installed for
    a non-active chat) a prompt switch stamps THAT chat's settings only —
    the global live prompt belongs to the UI's active chat and stays put.
    Before this, a mid-call prompt_switch costumed the operator's web chat.
    """
    data = get_prompt(name)
    if not data:
        return False, f"Prompt '{name}' not found"

    try:
        from core.chat.stream_brain import get_override
        _o = get_override()
        stream_chat = _o.get('chat') if _o else None
    except Exception:
        _o = None
        stream_chat = None
    if _o and _o.get('ephemeral'):
        # Chatless background turn: no chat to costume, and the operator
        # branch below is a LIVE recostume of the user's open chat.
        return False, ("You're not in a chat right now (background task) — "
                       "prompt unchanged")
    if stream_chat and stream_chat != system.llm_chat.session_manager.get_active_chat_name():
        # update_chat_settings routes to the effective (stream) chat.
        system.llm_chat.session_manager.update_chat_settings(
            {"prompt": name}, expected_active=stream_chat)
        return True, f"Activated '{name}' for chat '{stream_chat}' (takes effect next turn)"

    content = data.get('content') if isinstance(data, dict) else str(data)
    # R5 intent: captured before the live snapshot — if a vault eviction
    # retargets the active chat mid-activation, the stamp refuses (the
    # eviction's switch-means-apply re-applies the landing chat's own
    # prompt, so the live snapshot below heals on its own).
    _active = system.llm_chat.session_manager.get_active_chat_name()
    # Pieces BEFORE the live snapshot (C-5): a preset that fails validation
    # aborts here with the previous prompt AND both trackers untouched —
    # no half-activated chimera.
    if name in getattr(prompt_manager, 'scenario_presets', {}):
        if not prompt_state.apply_scenario(name):
            return False, (f"Preset '{name}' failed validation — activation "
                           f"aborted, previous prompt kept")
    system.llm_chat.set_system_prompt(content)
    prompt_state.set_active_preset_name(name)
    if not system.llm_chat.session_manager.update_chat_settings(
            {"prompt": name}, expected_active=_active):
        return False, (f"Active chat changed mid-activation — '{name}' not "
                       f"stamped")
    return True, f"Activated '{name}'"


def revalidate_active(system=None, reason: str = "") -> bool:
    """Re-render the ACTIVE prompt into the live chat after storage changed
    underneath it: file-watcher reload, pack unregister, delete-active,
    active-preset edit. One function closes the whole staleness class —
    before it, an edit to the active assembled preset went live then was
    silently REVERTED by the next spice rotation (apply_scenario was never
    re-run, so _assembled_state kept the old pieces).
    """
    try:
        if system is None:
            # get_system RAISES (503) before the system is up — quiet no-op,
            # not an error: a file save during the boot window is normal.
            try:
                from core.api_fastapi import get_system
                system = get_system()
            except Exception:
                return False
        if system is None or not getattr(system, 'llm_chat', None):
            return False

        name = prompt_state.get_active_preset_name()
        if not name or name == 'unknown':
            return False

        if name in prompt_manager.scenario_presets:
            if not prompt_state.apply_scenario(name):
                # Active preset went bad under us (disk edit) — loud handoff
                # to default, same H3 discipline as the vanished-name case.
                logger.warning(f"[PROMPTS] Active preset '{name}' failed to "
                               f"re-apply — handing off to assembled default")
                prompt_state.set_active_preset_name('default')
                name = 'default'

        data = get_prompt(name)
        if not isinstance(data, dict):
            # Active name vanished from storage — loud handoff to default
            # (same H3 discipline as delete_prompt).
            logger.warning(f"[PROMPTS] Active prompt '{name}' no longer resolves — "
                           f"re-rendering as assembled default")
            prompt_state.set_active_preset_name('default')
            data = get_prompt('default')

        system.llm_chat.set_system_prompt((data or {}).get('content', '') or '')
        logger.info(f"[PROMPTS] Revalidated active prompt '{name}'"
                    + (f" — {reason}" if reason else ""))
        return True
    except Exception as e:
        logger.warning(f"[PROMPTS] revalidate_active failed: {e}")
        return False

# ── cleanup & bulk tools (usage index + piece trash) ──────────────────────
# Two primitives the delete-modal and cleanup tools ride on. Trash is the
# safety net for bulk deletes: plaintext pieces move to a sidecar store
# instead of dying, and the vault-unlock reconcile restores anything a
# hidden vault prompt still referenced. Vault/pack pieces never land here —
# vault deletes stay inside the vault funnel, pack pieces are read-only.

def piece_usage() -> dict:
    """{type: {key: [prompt names]}} across ALL assembled prompts — user +
    packs + vault overlay. Vault prompts are visible only while unlocked;
    destructive callers must gate on vault state (or the piece-refs hashes)."""
    usage = {}
    for name, comps in prompt_manager.scenario_presets.items():
        if not isinstance(comps, dict):
            continue
        for ctype, val in comps.items():
            if ctype.startswith('_'):
                continue
            keys = val if isinstance(val, list) else ([val] if val else [])
            for k in keys:
                if isinstance(k, str) and k:
                    usage.setdefault(ctype, {}).setdefault(k, []).append(name)
    return usage


def _trash_path():
    return prompt_manager.USER_DIR / "prompt_pieces_trash.json"


def _load_trash() -> list:
    path = _trash_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        items = data.get('items', []) if isinstance(data, dict) else []
        return [it for it in items if isinstance(it, dict)
                and isinstance(it.get('type'), str)
                and isinstance(it.get('key'), str)]
    except Exception as e:
        logger.error(f"Trash store unreadable — treating as empty: {e}")
        return []


def _save_trash(items) -> bool:
    try:
        prompt_manager._write_json_atomic(
            _trash_path(), {"version": 1, "items": items}, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Trash store save failed: {e}")
        return False


def _publish_components_changed(action):
    try:
        from core.event_bus import publish, Events
        publish(Events.COMPONENTS_CHANGED, {"action": action})
    except Exception:
        pass


def trash_pieces(items, reason=None) -> tuple:
    """Soft-delete pieces: items = [(type, key)]. Plaintext pieces move into
    the sidecar trash store; pieces living in the UNLOCKED vault move into
    the vault's own encrypted trash (content never lands on plaintext disk).
    Returns (trashed, skipped) — trashed rows carry 'store', skipped a 'why'.
    Plaintext ordering is fail-safe: trash persists BEFORE the store delete,
    and a store-save failure rolls both back — a piece is never in neither
    place. Lock order is pm→pv, same as the move funnels."""
    from core import prompt_vault
    vault_ok = []
    vault_skipped = []
    plain_items = []
    for ctype, key in items:
        if key in prompt_manager._components.get(ctype, {}):
            plain_items.append((ctype, key))
        elif prompt_vault.vault_unlocked():
            ok, code = prompt_vault.trash_piece(ctype, key)
            if ok:
                vault_ok.append({'type': ctype, 'key': key, 'store': 'vault'})
            else:
                vault_skipped.append({'type': ctype, 'key': key,
                                      'why': 'not in the plaintext store or unlocked vault'})
        elif prompt_vault.vault_exists():
            # Sealed vault: can't tell a vault piece from a typo — honest skip.
            vault_skipped.append({'type': ctype, 'key': key,
                                  'why': 'vault is locked'})
        else:
            vault_skipped.append({'type': ctype, 'key': key,
                                  'why': 'not in the plaintext store'})
    with prompt_manager._lock:
        trash = _load_trash()
        original = list(trash)
        pending, skipped = [], list(vault_skipped)
        for ctype, key in plain_items:
            store = prompt_manager._components.get(ctype, {})
            if key not in store:
                skipped.append({'type': ctype, 'key': key,
                                'why': 'not in the plaintext store'})
                continue
            trash.append({"type": ctype, "key": key, "text": store[key],
                          "deleted_at": time.time()})
            pending.append((ctype, key))
        if not pending:
            if vault_ok:
                _publish_components_changed('pieces_trashed')
            return vault_ok, skipped
        if not _save_trash(trash):
            return vault_ok, skipped + [{'type': t, 'key': k,
                                         'why': 'trash save failed'}
                                        for t, k in pending]
        removed = {}
        for ctype, key in pending:
            removed[(ctype, key)] = prompt_manager._components[ctype].pop(key)
        if not prompt_manager.save_components(
                reason=reason or f"trashed {len(pending)} piece(s)"):
            for (ctype, key), text in removed.items():
                prompt_manager._components.setdefault(ctype, {})[key] = text
            _save_trash(original)
            return vault_ok, skipped + [{'type': t, 'key': k,
                                         'why': 'store save failed'}
                                        for t, k in pending]
    _publish_components_changed('pieces_trashed')
    logger.info(f"Trashed {len(pending)} plaintext piece(s): "
                + ", ".join(f"{t}/{k}" for t, k in pending)
                + (f" + {len(vault_ok)} vault piece(s)" if vault_ok else ""))
    return (vault_ok
            + [{'type': t, 'key': k, 'store': 'plain'} for t, k in pending]), skipped


def list_trash() -> list:
    """Trash entries newest-first, both stores: plaintext sidecar entries
    carry store='plain'; the vault's encrypted trash (store='vault') joins
    only while unlocked — sealed content stays invisible."""
    from core import prompt_vault
    merged = [{**it, 'store': 'plain'} for it in _load_trash()] \
        + [{**it, 'store': 'vault'} for it in prompt_vault.trash_list()]
    return sorted(merged, key=lambda it: it.get('deleted_at', 0), reverse=True)


def restore_pieces(items) -> tuple:
    """Restore trashed pieces (latest entry per type/key wins; all entries
    for a restored key drop). Never overwrites a live piece — skipped with
    'live piece exists' instead. items: (type, key) or (type, key, store) —
    store 'vault' routes through the vault's encrypted trash (unlocked only),
    default 'plain' hits the sidecar store."""
    from core import prompt_vault
    plain_items, vault_ok, vault_skipped = [], [], []
    for it in items:
        ctype, key = it[0], it[1]
        store = it[2] if len(it) > 2 else 'plain'
        if store != 'vault':
            plain_items.append((ctype, key))
            continue
        ok, code = prompt_vault.trash_restore(ctype, key)
        if ok:
            vault_ok.append({'type': ctype, 'key': key, 'store': 'vault'})
        else:
            why = {'locked': 'vault is locked',
                   'exists': 'live piece exists'}.get(code, 'not in trash')
            vault_skipped.append({'type': ctype, 'key': key, 'why': why})
    with prompt_manager._lock:
        trash = _load_trash()
        restored, skipped, added = [], list(vault_skipped), {}
        for ctype, key in plain_items:
            entries = [it for it in trash
                       if it['type'] == ctype and it['key'] == key]
            if not entries:
                skipped.append({'type': ctype, 'key': key,
                                'why': 'not in trash'})
                continue
            if key in prompt_manager._components.get(ctype, {}):
                skipped.append({'type': ctype, 'key': key,
                                'why': 'live piece exists'})
                continue
            latest = max(entries, key=lambda it: it.get('deleted_at', 0))
            prompt_manager._components.setdefault(ctype, {})[key] = \
                latest.get('text', '')
            added[(ctype, key)] = True
            restored.append((ctype, key))
            trash = [it for it in trash
                     if not (it['type'] == ctype and it['key'] == key)]
        if not restored:
            if vault_ok:
                _publish_components_changed('pieces_restored')
            return vault_ok, skipped
        if not prompt_manager.save_components(
                reason=f"restored {len(restored)} piece(s) from trash"):
            for ctype, key in added:
                prompt_manager._components.get(ctype, {}).pop(key, None)
            return vault_ok, skipped + [{'type': t, 'key': k,
                                         'why': 'store save failed'}
                                        for t, k in restored]
        _save_trash(trash)
    _publish_components_changed('pieces_restored')
    logger.info(f"Restored {len(restored)} plaintext piece(s) from trash: "
                + ", ".join(f"{t}/{k}" for t, k in restored)
                + (f" + {len(vault_ok)} vault piece(s)" if vault_ok else ""))
    return (vault_ok
            + [{'type': t, 'key': k, 'store': 'plain'} for t, k in restored]), skipped


def purge_trash() -> int:
    """Empty the trash — both stores while the vault is unlocked; a locked
    vault's trash survives untouched (sealed content can't be purged blind).
    Returns how many entries died. Hard delete — the one place cleanup is
    allowed to be final, and the user asked."""
    from core import prompt_vault
    with prompt_manager._lock:
        trash = _load_trash()
        if trash and not _save_trash([]):
            return 0
    ok, v = prompt_vault.trash_purge()
    vault_n = v if ok and isinstance(v, int) else 0
    total = len(trash) + vault_n
    if total:
        logger.info(f"Purged {total} piece(s) from trash "
                    f"({len(trash)} plaintext, {vault_n} vault)")
    return total


def reconcile_trash_with_vault() -> int:
    """After unlock: restore any trashed piece a vault prompt still
    references. Live-key collisions are skipped inside restore_pieces —
    a live piece already satisfies the reference."""
    from core import prompt_vault
    pairs = prompt_vault.piece_ref_pairs()
    if not pairs:
        return 0
    in_trash = {(it['type'], it['key']) for it in _load_trash()}
    want = [(t, k) for t, k in pairs if (t, k) in in_trash]
    if not want:
        return 0
    restored, _skipped = restore_pieces(want)
    return len(restored)


# ── ref-rewrite primitive + safe rename + dangler detection ──────────────
# The rewrite is THE engine: safe rename and dangler-strip are thin wrappers.
# It covers all three ref surfaces — user presets, unlocked-vault presets,
# and the runtime assembled state — so a rename can't mint danglers.

def _rewrite_assembled_ref(ctype, old_key, new_key=None) -> bool:
    with prompt_state._state_lock:
        st = prompt_state._assembled_state
        val = st.get(ctype)
        if isinstance(val, list):
            if old_key not in val:
                return False
            if new_key and new_key not in val:
                st[ctype] = [new_key if k == old_key else k for k in val]
            else:
                st[ctype] = [k for k in val if k != old_key]
            return True
        if val == old_key:
            st[ctype] = new_key or ''
            return True
    return False


def rewrite_piece_refs(ctype, old_key, new_key=None, reason=None) -> dict:
    """Repoint (new_key) or strip (None) every reference to ctype/old_key.
    Repointing onto a key a list already holds just drops the old one.
    Returns {'changed': [user prompt names], 'vault_changed': [names],
    'assembled': bool, 'vault_locked': bool} — vault_locked means a sealed
    vault MIGHT still reference the old name (hash says yes or unknown)."""
    from core import prompt_vault
    changed = []
    with prompt_manager._lock:
        for name, comps in prompt_manager._scenario_presets.items():
            if not isinstance(comps, dict):
                continue
            val = comps.get(ctype)
            if isinstance(val, list):
                if old_key in val:
                    if new_key and new_key not in val:
                        comps[ctype] = [new_key if k == old_key else k for k in val]
                    else:
                        comps[ctype] = [k for k in val if k != old_key]
                    changed.append(name)
            elif val == old_key:
                comps[ctype] = new_key or ''
                changed.append(name)
        if changed:
            prompt_manager.save_scenario_presets(
                reason=reason or f"rewrote refs {ctype}/{old_key} -> {new_key or '(removed)'}")
    vault_changed, vcode = prompt_vault.rewrite_piece_refs(ctype, old_key, new_key)
    vault_locked = (vcode == 'locked'
                    and prompt_vault.piece_vault_referenced(ctype, old_key) is not False)
    assembled = _rewrite_assembled_ref(ctype, old_key, new_key)
    if changed or vault_changed or assembled:
        _publish_components_changed('refs_rewritten')
        logger.info(f"Rewrote refs {ctype}/{old_key} -> {new_key or '(removed)'}: "
                    f"{len(changed)} user, {len(vault_changed)} vault"
                    + (", assembled state" if assembled else ""))
    return {'changed': changed, 'vault_changed': vault_changed,
            'assembled': assembled, 'vault_locked': vault_locked}


def rename_piece(ctype, old_key, new_key, reason=None) -> tuple[bool, str]:
    """Safe rename: move the piece text to the new key IN ITS OWN STORE,
    then repoint every reference. Refuses when the new key exists anywhere
    in the merged view (no silent shadowing)."""
    from core import prompt_vault
    new_key = (new_key or '').strip()
    if not new_key or new_key.startswith('_'):
        return False, "Invalid new name"
    if new_key == old_key:
        return False, "That's the same name"
    if new_key in prompt_manager.components.get(ctype, {}):
        return False, f"'{ctype}/{new_key}' already exists"
    with prompt_manager._lock:
        store = prompt_manager._components.get(ctype, {})
        in_plain = old_key in store
        if in_plain:
            store[new_key] = store.pop(old_key)
            if not prompt_manager.save_components(
                    reason=reason or f"renamed {ctype}/{old_key} -> {new_key}"):
                store[old_key] = store.pop(new_key)
                return False, "Store save failed — nothing renamed"
    if not in_plain:
        if not prompt_vault.vault_unlocked() \
                or not prompt_vault.vault_has_piece(ctype, old_key):
            return False, f"'{ctype}/{old_key}' not found (pack-owned or sealed)"
        val = prompt_vault.overlay_components().get(ctype, {}).get(old_key, '')
        ok, code = prompt_vault.set_piece(ctype, new_key, val)
        if not ok:
            return False, f"Vault refused the rename ({code})"
        prompt_vault.delete_piece(ctype, old_key)
    res = rewrite_piece_refs(ctype, old_key, new_key,
                             reason=f"rename {ctype}/{old_key} -> {new_key}")
    n = len(res['changed']) + len(res['vault_changed'])
    msg = f"Renamed to '{new_key}'" + (f" — updated {n} prompt(s)" if n else "")
    if res['vault_locked']:
        msg += " (a locked vault prompt may still use the old name)"
    logger.info(f"Renamed piece {ctype}/{old_key} -> {new_key} ({n} refs updated)")
    return True, msg


def dangling_refs() -> dict:
    """{prompt_name: [{'type','key'}]} — references to pieces missing from
    the merged component view. Vault prompts join only while unlocked, and
    a LOCKED vault makes plaintext refs to sealed pieces LOOK dangling —
    destructive callers must refuse while a vault exists and is locked."""
    comps = prompt_manager.components
    out = {}
    for name, pcomps in prompt_manager.scenario_presets.items():
        if not isinstance(pcomps, dict):
            continue
        bad = []
        for ctype, val in pcomps.items():
            if ctype.startswith('_'):
                continue
            keys = val if isinstance(val, list) else ([val] if val else [])
            for k in keys:
                if isinstance(k, str) and k and k not in comps.get(ctype, {}):
                    bad.append({'type': ctype, 'key': k})
        if bad:
            out[name] = bad
    return out
