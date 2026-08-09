import logging
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

    return sorted(all_prompts)


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
    """
    # Check monoliths
    if name in prompt_manager.monoliths:
        mono = prompt_manager.monoliths[name]
        return {
            'name': name,
            'type': 'monolith',
            'content': mono.get('content', ''),
            'privacy_required': mono.get('privacy_required', False)
        }
    
    # Check scenario presets
    if name in prompt_manager.scenario_presets:
        components = prompt_manager.scenario_presets[name]
        privacy_required = components.get('_privacy_required', False)
        # Filter out metadata for assembly
        clean_components = {k: v for k, v in components.items() if not k.startswith('_')}
        assembled_text = prompt_manager.assemble_from_components(clean_components)
        return {
            'name': name,
            'type': 'assembled',
            'components': clean_components,
            'content': assembled_text,
            'privacy_required': privacy_required
        }

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


def save_prompt(name: str, data: dict, allow_overwrite: bool = True,
                reason: str = None, audit: bool = True) -> tuple[bool, str]:
    """
    Save a prompt - updates user JSON files (monoliths or scenario_presets).

    Args:
        name: Prompt name
        data: Prompt data with 'type' and 'content'/'components'
        allow_overwrite: False refuses to replace an existing same-type USER
            prompt (pack prompts don't count — shadowing them is the intended
            edit path). Accepted-but-ignored until 2026-08-09; persona import
            passed it believing it worked and silently overwrote.
        reason: optional why — rides the prompt-ledger audit event
        audit: False when a higher-level event already describes this save
            (piece activations) — the audit snapshot still refreshes

    Returns:
        (success: bool, message: str)
    """
    try:
        prompt_type = data.get('type', 'monolith')

        # Check for name collision with opposite type
        if prompt_type == 'monolith':
            if name in prompt_manager.scenario_presets:
                msg = f"Name '{name}' already exists as assembled prompt"
                logger.warning(msg)
                return False, msg

        elif prompt_type == 'assembled':
            if name in prompt_manager.monoliths:
                msg = f"Name '{name}' already exists as monolith prompt"
                logger.warning(msg)
                return False, msg

        if not allow_overwrite:
            existing = (name in prompt_manager._monoliths if prompt_type == 'monolith'
                        else name in prompt_manager._scenario_presets)
            if existing:
                msg = f"Prompt '{name}' already exists (overwrite not allowed)"
                logger.warning(msg)
                return False, msg

        # Proceed with save
        if prompt_type == 'monolith':
            prompt_manager._monoliths[name] = {
                'content': data['content'],
                'privacy_required': data.get('privacy_required', False)
            }
            prompt_manager.save_monoliths(reason=reason, audit=audit)
            logger.info(f"Saved monolith '{name}'")
            return True, f"Saved monolith '{name}'"

        elif prompt_type == 'assembled':
            components = data['components'].copy()
            # Store privacy_required at top level of preset
            components['_privacy_required'] = data.get('privacy_required', False)
            prompt_manager._scenario_presets[name] = components
            prompt_manager.save_scenario_presets(reason=reason, audit=audit)
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
            del prompt_manager._monoliths[name]
            prompt_manager.save_monoliths(reason=reason)
            logger.info(f"Deleted monolith '{name}'")
            deleted = True

        # Delete from scenario_presets if present
        if name in prompt_manager._scenario_presets:
            del prompt_manager._scenario_presets[name]
            prompt_manager.save_scenario_presets(reason=reason)
            logger.info(f"Deleted assembled prompt '{name}'")
            deleted = True

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
        stream_chat = None
    if stream_chat and stream_chat != system.llm_chat.session_manager.get_active_chat_name():
        # update_chat_settings routes to the effective (stream) chat.
        system.llm_chat.session_manager.update_chat_settings({"prompt": name})
        return True, f"Activated '{name}' for chat '{stream_chat}' (takes effect next turn)"

    content = data.get('content') if isinstance(data, dict) else str(data)
    system.llm_chat.set_system_prompt(content)
    prompt_state.set_active_preset_name(name)
    if name in getattr(prompt_manager, 'scenario_presets', {}):
        prompt_state.apply_scenario(name)
    system.llm_chat.session_manager.update_chat_settings({"prompt": name})
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
            from core.api_fastapi import get_system
            system = get_system()
        if system is None or not getattr(system, 'llm_chat', None):
            return False

        name = prompt_state.get_active_preset_name()
        if not name or name == 'unknown':
            return False

        if name in prompt_manager.scenario_presets:
            prompt_state.apply_scenario(name)

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