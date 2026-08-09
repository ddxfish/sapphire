# core/routes/settings.py - System status, settings, credentials, privacy, LLM provider routes
import asyncio
import json
import os
import re
import logging
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException

import config
from core.auth import require_login
from core.api_fastapi import get_system, _apply_chat_settings
from core.event_bus import publish, Events
from core import prompts

logger = logging.getLogger(__name__)

router = APIRouter()

PROJECT_ROOT = Path(__file__).parent.parent.parent


# =============================================================================
# SYSTEM STATUS ROUTES
# =============================================================================

@router.get("/api/system/status")
async def get_system_status(request: Request, _=Depends(require_login), system=Depends(get_system)):
    """Get system status."""
    try:
        prompt_state = prompts.get_current_state()
        function_names = system.llm_chat.function_manager.get_enabled_function_names()
        toolset_info = system.llm_chat.function_manager.get_current_toolset_info()
        has_cloud_tools = system.llm_chat.function_manager.has_network_tools_enabled()

        chat_settings = system.llm_chat.session_manager.get_chat_settings()
        spice_enabled = chat_settings.get('spice_enabled', True)
        current_spice = prompts.get_current_spice()
        next_spice = prompts.get_next_spice()
        is_assembled = prompts.is_assembled_mode()

        return {
            "prompt": prompt_state,
            "prompt_name": prompts.get_active_preset_name(),
            "prompt_char_count": prompts.get_prompt_char_count(),
            "functions": function_names,
            "toolset": toolset_info,
            "tts_enabled": config.TTS_ENABLED,
            "has_cloud_tools": has_cloud_tools,
            "spice": {"current": current_spice, "next": next_spice, "enabled": spice_enabled, "available": is_assembled}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get system status")


@router.get("/api/system/prompt")
async def get_system_prompt(request: Request, prompt_name: str = None, _=Depends(require_login), system=Depends(get_system)):
    """Get system prompt."""
    if prompt_name:
        prompt_data = prompts.get_prompt(prompt_name)
        if not prompt_data:
            raise HTTPException(status_code=404, detail=f"Prompt '{prompt_name}' not found.")
        content = prompt_data.get('content') if isinstance(prompt_data, dict) else str(prompt_data)
        return {"prompt": content, "source": f"storage: {prompt_name}"}
    else:
        prompt_template = system.llm_chat.get_system_prompt_template()
        return {"prompt": prompt_template, "source": "active_memory_template"}


# POST /api/system/prompt (raw transient prompt setter) removed 2026-08-09:
# no frontend or tool caller; it bypassed preset tracking, and in assembled
# mode the next spice rotation silently clobbered the injected text anyway.
# Use /api/prompts (save + activate) instead.


# =============================================================================
# SETTINGS ROUTES
# =============================================================================

_SENSITIVE_SUFFIXES = ('_API_KEY', '_SECRET', '_PASSWORD', '_TOKEN')
_SENSITIVE_KEYS = {'SAPPHIRE_ROUTER_URL', 'SAPPHIRE_ROUTER_TENANT_ID'}

@router.get("/api/settings")
async def get_all_settings(request: Request, _=Depends(require_login)):
    """Get all current settings."""
    from core.settings_manager import settings
    try:
        all_settings = settings.get_all_settings()
        # Mask sensitive values — frontend only needs to know if they're set
        for key in all_settings:
            if all_settings[key] and (
                any(key.upper().endswith(s) for s in _SENSITIVE_SUFFIXES)
                or key in _SENSITIVE_KEYS
            ):
                all_settings[key] = '••••••••'
        user_overrides = settings.get_user_overrides()
        # Defaults define the canonical type per key — frontend uses them
        # for save-time type coercion. Without this, parseValue would fall
        # back to the currently-stored value's type, which gets corrupted
        # by any bug that writes a wrong type. 2026-05-16.
        defaults = settings.get_defaults()
        # Same masking as settings — never expose stored secrets even via defaults
        for key in list(defaults.keys()):
            if defaults.get(key) and (
                any(key.upper().endswith(s) for s in _SENSITIVE_SUFFIXES)
                or key in _SENSITIVE_KEYS
            ):
                defaults[key] = '••••••••'
        return {
            "settings": all_settings,
            "defaults": defaults,
            "user_overrides": list(user_overrides.keys()),
            "count": len(all_settings),
            "managed": settings.is_managed(),
            "docker": settings.is_docker(),
            "unrestricted": settings.is_unrestricted(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/settings/reload")
async def reload_settings(request: Request, _=Depends(require_login)):
    """Reload settings from disk."""
    from core.settings_manager import settings
    settings.reload()
    return {"status": "success", "message": "Settings reloaded"}


@router.post("/api/settings/reset")
async def reset_settings(request: Request, _=Depends(require_login)):
    """Reset all settings to defaults and re-init providers."""
    from core.settings_manager import settings
    if settings.reset_to_defaults():
        # Re-init providers so runtime matches the reset config
        try:
            system = get_system()
            await asyncio.to_thread(system.toggle_wakeword, False)
            await asyncio.to_thread(system.switch_tts_provider, 'none')
            await asyncio.to_thread(system.switch_stt_provider, 'none')
            await asyncio.to_thread(system.switch_embedding_provider, 'none')
        except Exception as e:
            logger.warning(f"Provider re-init after reset: {e}")
        return {"status": "success", "message": "All settings reset to defaults"}
    else:
        raise HTTPException(status_code=500, detail="Failed to reset settings")


@router.get("/api/settings/tiers")
async def get_tiers(request: Request, _=Depends(require_login)):
    """Get tier classification for all settings."""
    from core.settings_manager import settings
    all_settings = settings.get_all_settings()
    tiers = {'hot': [], 'component': [], 'restart': []}
    for key in all_settings.keys():
        tier = settings.validate_tier(key)
        tiers[tier].append(key)
    return {"tiers": tiers, "counts": {k: len(v) for k, v in tiers.items()}}


@router.put("/api/settings/batch")
async def update_settings_batch(request: Request, _=Depends(require_login)):
    """Update multiple settings at once."""
    from core.settings_manager import settings
    data = await request.json()
    if not data or 'settings' not in data:
        raise HTTPException(status_code=400, detail="Missing 'settings'")
    settings_dict = data['settings']
    persist = data.get('persist', True)
    # Skip masked values sent back by frontend (don't overwrite real secrets with dots)
    settings_dict = {k: v for k, v in settings_dict.items() if v != '••••••••'}
    # Filter out locked keys in managed mode
    if settings.is_managed():
        locked = [k for k in settings_dict if settings.is_locked(k)]
        if locked:
            logger.warning(f"[MANAGED] Batch: filtered locked keys: {locked}")
        settings_dict = {k: v for k, v in settings_dict.items() if not settings.is_locked(k)}
    # Server-side gate for EMBEDDING_PROVIDER swap. The JS shows a count-of-affected
    # confirmation dialog, but that gate is client-side only — a direct curl, a stale
    # browser tab, or a toolmaker-generated plugin could silently swap the provider
    # and leave N memories/knowledge rows invisible to search. Require explicit
    # `confirm_embedding_swap: true` when the swap would invalidate vectors.
    # Scout finding #3 — 2026-04-20.
    if 'EMBEDDING_PROVIDER' in settings_dict:
        new_provider = settings_dict['EMBEDDING_PROVIDER']
        current_provider = settings.get('EMBEDDING_PROVIDER')
        if new_provider and new_provider != current_provider and not data.get('confirm_embedding_swap'):
            try:
                from core.embeddings import integrity_report
                # to_thread — see /api/embedding/integrity for rationale
                # (scout #14). Same 6-select blocking scan.
                report = await asyncio.to_thread(integrity_report)
                tables = report.get('tables', {}) or {}
                def _count(t):
                    return (t.get('matching_active') or 0) + (t.get('legacy_unstamped') or 0)
                affected = sum(_count(tables.get(t, {})) for t in ('memories', 'knowledge_entries', 'people'))
                if affected > 0:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "embedding_swap_requires_confirmation",
                            "affected_vectors": affected,
                            "message": (
                                f"Swapping EMBEDDING_PROVIDER would make {affected} stored "
                                "vectors invisible to semantic search until re-embedded. "
                                "Resubmit with `confirm_embedding_swap: true` to proceed."
                            ),
                        },
                    )
            except HTTPException:
                raise
            except Exception as e:
                # 2026-04-22 fix C — fail CLOSED, not open. Pre-fix, a transient
                # integrity_report failure (DB busy >10s during re-embed/VACUUM/
                # heavy-search) caused the gate to silently pass, proceeding with
                # a destructive swap the user would have refused if the warning
                # had rendered. The only cases where that's acceptable are ones
                # the caller explicitly opts into via `confirm_embedding_swap`.
                logger.error(f"Embedding swap gate: integrity check failed: {e}")
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "embedding_swap_gate_unavailable",
                        "reason": str(e),
                        "message": (
                            "Cannot verify swap safety — integrity check failed "
                            "(DB may be busy with re-embed, VACUUM, or heavy "
                            "search). Wait for the busy operation to finish, or "
                            "resubmit with `confirm_embedding_swap: true` to "
                            "bypass the gate if you accept the risk of "
                            "invalidating stored vectors."
                        ),
                    },
                )
    results = []
    # Defer provider switches until after all settings are applied
    # (e.g. API key must be in config before provider init reads it)
    deferred_actions = []
    deferred_keys = set()
    persisted_keys = {}  # Collect keys for single disk write
    # Provider-switch keys that must only persist AFTER their deferred switch
    # succeeds. Previously all keys persisted eagerly and the switch ran
    # afterward — if the switch raised (e.g. re-embed running blocked the
    # swap), settings.json had the new provider but `_embedder` singleton was
    # still old. Next process restart would boot with the wrong provider
    # silently. These get added to persisted_keys in the deferred-action loop
    # only on successful switch. Scout race #4 — 2026-04-21.
    _PROVIDER_SWITCH_KEYS = {'STT_PROVIDER', 'TTS_PROVIDER', 'EMBEDDING_PROVIDER'}
    # Service API keys that should route to credentials manager
    _SERVICE_CRED_MAP = {
        'STT_FIREWORKS_API_KEY': 'stt_fireworks',
        'TTS_ELEVENLABS_API_KEY': 'tts_elevenlabs',
        'EMBEDDING_API_KEY': 'embedding',
    }
    for key, value in settings_dict.items():
        try:
            # Route service API keys to credentials, not settings.json
            if key in _SERVICE_CRED_MAP and value and isinstance(value, str) and value.strip():
                from core.credentials_manager import credentials
                credentials.set_service_api_key(_SERVICE_CRED_MAP[key], value.strip())
                results.append({"key": key, "status": "success", "tier": "hot"})
                continue
            tier = settings.validate_tier(key)
            # Skip reload callback for provider-switch keys — the deferred
            # action below runs the switch explicitly. Without skip, callback
            # + explicit switch double-fire (see voice-prep review #J).
            settings.set(
                key, value,
                persist=False,
                _skip_callbacks=(key in _PROVIDER_SWITCH_KEYS),
            )
            # Provider-switch keys defer their persist until post-switch success.
            if persist and key not in _PROVIDER_SWITCH_KEYS:
                persisted_keys[key] = value
            results.append({"key": key, "status": "success", "tier": tier})
            if key == 'WAKE_WORD_ENABLED':
                deferred_actions.append(('toggle_wakeword', value, key, tier))
                deferred_keys.add(key)
            if key == 'WAKEWORD_MODEL':
                # Skip if WAKE_WORD_ENABLED is in the same batch — the
                # toggle_wakeword cold-start already reads WAKEWORD_MODEL
                # from settings. Avoids a double-init.
                if 'WAKE_WORD_ENABLED' not in settings_dict:
                    deferred_actions.append(('reload_wakeword_model', value, key, tier))
                deferred_keys.add(key)
            if key == 'STT_PROVIDER':
                deferred_actions.append(('switch_stt_provider', value, key, tier))
                deferred_keys.add(key)
            if key == 'STT_ENABLED':
                if 'STT_PROVIDER' not in settings_dict:
                    deferred_actions.append(('toggle_stt', value, key, tier))
                deferred_keys.add(key)
            if key == 'TTS_PROVIDER':
                deferred_actions.append(('switch_tts_provider', value, key, tier))
                deferred_keys.add(key)
            if key == 'TTS_ENABLED':
                # Skip if TTS_PROVIDER is in the same batch (it already handles the switch)
                if 'TTS_PROVIDER' not in settings_dict:
                    deferred_actions.append(('toggle_tts', value, key, tier))
                deferred_keys.add(key)
            if key == 'EMBEDDING_PROVIDER':
                deferred_actions.append(('switch_embedding', value, key, tier))
                deferred_keys.add(key)
            if key == 'ALLOW_UNSIGNED_PLUGINS' and not value:
                try:
                    from core.plugin_loader import plugin_loader
                    disabled = plugin_loader.enforce_unsigned_policy()
                    if disabled:
                        logger.info(f"Unsigned policy enforced, disabled: {disabled}")
                except Exception as e:
                    logger.warning(f"Failed to enforce unsigned policy: {e}")
            # Defer SETTINGS_CHANGED for provider keys until after switch completes
            if key not in deferred_keys:
                publish(Events.SETTINGS_CHANGED, {"key": key, "value": value, "tier": tier})
        except Exception as e:
            results.append({"key": key, "status": "error", "error": str(e)})
    # Execute deferred provider switches (runtime values are already set via
    # settings.set above; persistence for provider-switch keys is held until
    # we confirm the switch succeeded, then written to persisted_keys below.
    # Scout race #4 — persist-before-switch caused settings.json / singleton
    # divergence on switch failure).
    #
    # Sequence wake word init LAST. 2026-04-25 user report: heap corruption
    # SIGABRT when wake word starts during a multi-component boot batch
    # (TTS/STT/embedding loading from worker threads in the same window).
    # Manual single-toggle works fine because nothing else competes — proves
    # the issue is concurrency-with-other-C-extension-init, not wake word
    # itself. Stable sort: non-wakeword actions keep their original order;
    # wakeword(s) drop to the end so they init on a fully-settled environment.
    deferred_actions.sort(key=lambda a: a[0] in ('toggle_wakeword', 'reload_wakeword_model'))
    system = get_system()
    for action, value, key, tier in deferred_actions:
        switch_ok = False
        try:
            if action == 'switch_embedding':
                from core.embeddings import switch_embedding_provider
                switch_embedding_provider(value)
            else:
                result = await asyncio.to_thread(getattr(system, action), value)
                # switch_*/toggle_* report failure by RETURNING False, not
                # raising (HDF scout, 2026-07-19) — without this, a failed
                # provider switch persisted as success and the UI toasted OK
                # while the runtime sat on the fallback.
                if result is False:
                    raise RuntimeError(f"{action} reported failure "
                                       f"(returned False)")
            switch_ok = True
        except Exception as e:
            logger.error(f"Deferred action {action} failed: {e}")
            # Switch failed — undo the runtime set so the runtime layer stays
            # aligned with what's actually persisted (the old value).
            if key in _PROVIDER_SWITCH_KEYS:
                try:
                    # settings.get falls back to _persisted / _defaults when
                    # runtime isn't populated — we did NOT persist, so this
                    # returns the pre-swap value.
                    settings._runtime.pop(key, None)
                except Exception as rollback_err:
                    logger.warning(f"Runtime rollback for {key} failed: {rollback_err}")
            # Record the failure in results so the UI can surface it.
            results.append({"key": key, "status": "error", "error": f"{action}: {e}"})
        if switch_ok and persist and key in _PROVIDER_SWITCH_KEYS:
            # Only now does this provider key earn its seat in persisted_keys.
            persisted_keys[key] = value
    # Persist all settings in ONE disk write, now that provider switches have
    # had a chance to succeed/fail and update persisted_keys accordingly.
    if persist and persisted_keys:
        with settings._lock:
            settings._user.update(persisted_keys)
            for key in persisted_keys:
                settings._runtime.pop(key, None)
            settings.save()
    # Re-apply chat settings so voice gets validated for new provider
    if any(a[0].startswith('switch_tts') or a[0] == 'toggle_tts' for a in deferred_actions):
        try:
            chat_settings = system.llm_chat.session_manager.get_chat_settings()
            _apply_chat_settings(system, chat_settings)
        except Exception as e:
            logger.warning(f"Failed to re-apply chat settings after TTS switch: {e}")
    # Now publish SETTINGS_CHANGED for deferred keys (provider is ready)
    for _, value, key, tier in deferred_actions:
        publish(Events.SETTINGS_CHANGED, {"key": key, "value": value, "tier": tier})
    return {"status": "success", "results": results}


@router.get("/api/settings/help")
async def get_settings_help(request: Request, _=Depends(require_login)):
    """Get help text for settings."""
    help_path = Path(__file__).parent.parent / "settings_help.json"
    try:
        with open(help_path, encoding='utf-8') as f:
            return {"help": json.load(f)}
    except Exception:
        return {"help": {}}


@router.get("/api/settings/help/{key}")
async def get_setting_help(key: str, request: Request, _=Depends(require_login)):
    """Get help for a specific setting."""
    help_path = Path(__file__).parent.parent / "settings_help.json"
    try:
        with open(help_path, encoding='utf-8') as f:
            all_help = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Could not load help data")
    help_text = all_help.get(key)
    if not help_text:
        raise HTTPException(status_code=404, detail=f"No help for '{key}'")
    return {"key": key, "help": help_text}


@router.get("/api/settings/tool-settings")
async def get_tool_settings(request: Request, _=Depends(require_login)):
    """Get settings declared by tool modules, grouped by tool name."""
    from core.settings_manager import settings as sm
    return sm.get_tool_settings_meta()


@router.get("/api/settings/chat-defaults")
async def get_chat_defaults(request: Request, _=Depends(require_login)):
    """Get chat defaults."""
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    if defaults_path.exists():
        with open(defaults_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


@router.put("/api/settings/chat-defaults")
async def save_chat_defaults(request: Request, _=Depends(require_login)):
    """Save chat defaults."""
    data = await request.json()
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    defaults_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = defaults_path.with_suffix('.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    tmp_path.replace(defaults_path)
    return {"status": "success"}


@router.delete("/api/settings/chat-defaults")
async def reset_chat_defaults(request: Request, _=Depends(require_login)):
    """Reset chat defaults."""
    defaults_path = PROJECT_ROOT / "user" / "settings" / "chat_defaults.json"
    if defaults_path.exists():
        defaults_path.unlink()
    return {"status": "success"}


@router.get("/api/settings/wakeword-models")
async def get_wakeword_models(request: Request, _=Depends(require_login)):
    """Get available wakeword models.

    Delegates to core.wakeword.get_available_models() so the dropdown
    matches what the detector can actually load: OpenWakeWord builtins
    (alexa, hey_mycroft, hey_jarvis, hey_rhasspy, timer, weather) +
    Sapphire-bundled core models + user .onnx/.tflite drops in
    user/wakeword/models/. The earlier hand-rolled scan missed all
    builtins and used the wrong user directory name. 2026-04-27.
    """
    try:
        from core.wakeword import get_available_models
        return get_available_models()
    except Exception as e:
        logger.warning(f"get_wakeword_models fell back: {e}")
        return {"all": ['alexa', 'hey_mycroft', 'hey_jarvis', 'hey_rhasspy', 'timer', 'weather']}


# Parameterized settings routes MUST come after specific ones (FastAPI matches in registration order)
@router.get("/api/settings/{key}")
async def get_setting(key: str, request: Request, _=Depends(require_login)):
    """Get a specific setting."""
    from core.settings_manager import settings
    value = settings.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    if value and (any(key.upper().endswith(s) for s in _SENSITIVE_SUFFIXES) or key in _SENSITIVE_KEYS):
        value = '••••••••'
    tier = settings.validate_tier(key)
    is_user_override = key in settings.get_user_overrides()
    return {"key": key, "value": value, "tier": tier, "user_override": is_user_override}


@router.put("/api/settings/{key}")
async def update_setting(key: str, request: Request, _=Depends(require_login)):
    """Update a setting."""
    from core.settings_manager import settings
    from core.socks_proxy import clear_session_cache
    if settings.is_locked(key):
        raise HTTPException(status_code=403, detail=f"Setting '{key}' is locked in managed mode")
    data = await request.json()
    if data is None or 'value' not in data:
        raise HTTPException(status_code=400, detail="Missing 'value'")
    value = data['value']
    # Don't overwrite real secrets with masked placeholder
    if value == '••••••••':
        return {"status": "success", "key": key, "value": value, "tier": settings.validate_tier(key), "persisted": False}
    persist = data.get('persist', True)
    tier = settings.validate_tier(key)
    # Provider-switch keys: defer persist until after switch verifies. Pre-fix,
    # persist happened FIRST then switch ran — if switch raised (bad API key,
    # provider plugin missing), settings.json had the new value, runtime had
    # the new value, but the live singleton stayed on the old provider. Next
    # restart booted with the wrong provider silently. Voice mode hammers
    # this endpoint per provider/voice swap so the failure rate compounds.
    # Mirrors the batch endpoint's deferred-persist pattern (race #4 fix).
    # Day-ruiner scout 2026-05-07 #3.
    _PROVIDER_SWITCH_KEYS = {'STT_PROVIDER', 'TTS_PROVIDER', 'EMBEDDING_PROVIDER'}
    is_provider_switch = key in _PROVIDER_SWITCH_KEYS
    # Skip the reload callback — this route runs the switch explicitly below.
    # Without _skip_callbacks=True, the callback fires here AND the explicit
    # _do_*_switch fires later, double-running the switch (Kokoro restart
    # twice, STT recorder flap risk). Voice-prep review 2026-05-07 #J.
    settings.set(
        key, value,
        persist=(persist and not is_provider_switch),
        _skip_callbacks=is_provider_switch,
    )
    if key in {'SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT'}:
        clear_session_cache()
    # Tracks switch outcome so we know whether to persist + rollback.
    # Defined BEFORE the first toggle site that can set it.
    switch_ok = {'value': True}  # default True; set False on switch failure
    if key == 'WAKE_WORD_ENABLED':
        system = get_system()
        ok = await asyncio.to_thread(system.toggle_wakeword, value)
        if ok is False:
            # toggle_* report failure by RETURN, not raise (scout,
            # 2026-07-20 — same class as the batch-route fix).
            switch_ok['value'] = False
    # Provider switches: fire-and-forget when ?async=true (setup wizard uses this)
    run_async = request.query_params.get('async') == 'true'

    async def _do_stt_switch(val):
        try:
            ok = await asyncio.to_thread(get_system().switch_stt_provider, val)
            if ok is False:
                # switch_* reports failure by RETURN, not raise (HDF scout,
                # 2026-07-19) — without this a failed switch persisted as OK.
                raise RuntimeError("switch_stt_provider returned False")
        except Exception as e:
            logger.error(f"Background STT switch failed: {e}")
            switch_ok['value'] = False

    async def _do_tts_switch(val):
        try:
            ok = await asyncio.to_thread(get_system().switch_tts_provider, val)
            if ok is False:
                raise RuntimeError("switch_tts_provider returned False")
            try:
                system = get_system()
                chat_settings = system.llm_chat.session_manager.get_chat_settings()
                _apply_chat_settings(system, chat_settings)
            except Exception as e:
                logger.warning(f"Failed to re-apply chat settings after TTS switch: {e}")
        except Exception as e:
            logger.error(f"Background TTS switch failed: {e}")
            switch_ok['value'] = False

    async def _do_embedding_switch(val):
        try:
            from core.embeddings import switch_embedding_provider
            await asyncio.to_thread(switch_embedding_provider, val)
        except Exception as e:
            logger.error(f"Background embedding switch failed: {e}")
            switch_ok['value'] = False

    if key == 'STT_PROVIDER':
        if run_async:
            # async path: persist optimistically since we can't await the result.
            # Worse than sync but matches prior behavior and is opt-in via ?async=true.
            if persist:
                with settings._lock:
                    settings._user[key] = value
                    settings._runtime.pop(key, None)
                    settings.save()
            asyncio.create_task(_do_stt_switch(value))
        else:
            await _do_stt_switch(value)
    if key == 'STT_ENABLED':
        ok = await asyncio.to_thread(get_system().toggle_stt, value)
        if ok is False:
            switch_ok['value'] = False
    if key == 'TTS_PROVIDER':
        if run_async:
            if persist:
                with settings._lock:
                    settings._user[key] = value
                    settings._runtime.pop(key, None)
                    settings.save()
            asyncio.create_task(_do_tts_switch(value))
        else:
            await _do_tts_switch(value)
    if key == 'EMBEDDING_PROVIDER':
        if run_async:
            if persist:
                with settings._lock:
                    settings._user[key] = value
                    settings._runtime.pop(key, None)
                    settings.save()
            asyncio.create_task(_do_embedding_switch(value))
        else:
            await _do_embedding_switch(value)
    if key == 'TTS_ENABLED':
        ok = await asyncio.to_thread(get_system().toggle_tts, value)
        if ok is False:
            switch_ok['value'] = False
        if value:
            try:
                system = get_system()
                chat_settings = system.llm_chat.session_manager.get_chat_settings()
                _apply_chat_settings(system, chat_settings)
            except Exception as e:
                logger.warning(f"Failed to re-apply chat settings after TTS toggle: {e}")
    # Provider keys: persist now (on success) or rollback runtime (on failure).
    # Async paths persisted optimistically above and skip this block.
    persisted = persist
    if is_provider_switch and persist and not run_async:
        if switch_ok['value']:
            with settings._lock:
                settings._user[key] = value
                settings._runtime.pop(key, None)
                settings.save()
        else:
            # Switch failed — drop the runtime override so the runtime layer
            # stays aligned with the still-old persisted value.
            try:
                with settings._lock:
                    settings._runtime.pop(key, None)
            except Exception:
                pass
            persisted = False
            raise HTTPException(status_code=500, detail=f"Provider switch failed for {key} — value not persisted")
    publish(Events.SETTINGS_CHANGED, {"key": key, "value": value, "tier": tier})
    return {"status": "success", "key": key, "value": value, "tier": tier, "persisted": persisted}


@router.delete("/api/settings/{key}")
async def delete_setting(key: str, request: Request, _=Depends(require_login)):
    """Remove user override for a setting."""
    from core.settings_manager import settings
    if settings.is_locked(key):
        raise HTTPException(status_code=403, detail=f"Setting '{key}' is locked in managed mode")
    if settings.remove_user_override(key):
        default_value = settings.get(key)
        return {"status": "success", "key": key, "reverted_to": default_value}
    else:
        raise HTTPException(status_code=404, detail=f"No user override exists for '{key}'")


# =============================================================================
# CREDENTIALS ROUTES
# =============================================================================

@router.get("/api/credentials")
async def get_credentials(request: Request, _=Depends(require_login)):
    """Get credentials status (not actual values)."""
    from core.credentials_manager import credentials
    return credentials.get_masked_summary()


@router.put("/api/credentials/llm/{provider}")
async def set_llm_credential(provider: str, request: Request, _=Depends(require_login)):
    """Set LLM API key for a provider."""
    from core.credentials_manager import credentials
    data = await request.json()
    api_key = data.get('api_key', '')
    if credentials.set_llm_api_key(provider, api_key):
        return {"status": "success", "provider": provider}
    else:
        raise HTTPException(status_code=500, detail="Failed to save credential")


@router.delete("/api/credentials/llm/{provider}")
async def delete_llm_credential(provider: str, request: Request, _=Depends(require_login)):
    """Delete LLM API key for a provider."""
    from core.credentials_manager import credentials
    if credentials.clear_llm_api_key(provider):
        return {"status": "success", "provider": provider}
    else:
        raise HTTPException(status_code=404, detail="Credential not found")


@router.get("/api/credentials/socks")
async def get_socks_credential(request: Request, _=Depends(require_login)):
    """Get SOCKS credentials (masked)."""
    from core.credentials_manager import credentials
    return {"has_credentials": credentials.has_socks_credentials()}


@router.put("/api/credentials/socks")
async def set_socks_credential(request: Request, _=Depends(require_login)):
    """Set SOCKS credentials."""
    from core.credentials_manager import credentials
    data = await request.json()
    username = data.get('username', '')
    password = data.get('password', '')
    if credentials.set_socks_credentials(username, password):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save credentials")


@router.delete("/api/credentials/socks")
async def delete_socks_credential(request: Request, _=Depends(require_login)):
    """Delete SOCKS credentials."""
    from core.credentials_manager import credentials
    if credentials.clear_socks_credentials():
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete credentials")


@router.post("/api/credentials/socks/test")
async def test_socks_connection(request: Request, _=Depends(require_login)):
    """Test SOCKS proxy connection."""
    if not config.SOCKS_ENABLED:
        return {"status": "error", "error": "SOCKS proxy is disabled"}

    def _test_socks():
        from core.socks_proxy import get_session, SocksAuthError, clear_session_cache
        import requests as req
        clear_session_cache()
        try:
            session = get_session()
            resp = session.get('https://icanhazip.com', timeout=8)
            if resp.ok:
                return {"status": "success", "message": f"Connected via {resp.text.strip()}"}
            return {"status": "error", "error": f"HTTP {resp.status_code}"}
        except SocksAuthError as e:
            return {"status": "error", "error": str(e)}
        except req.exceptions.Timeout:
            return {"status": "error", "error": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

    return await asyncio.to_thread(_test_socks)


# =============================================================================
# LLM PROVIDER ROUTES
# =============================================================================

@router.get("/api/llm/providers")
async def get_llm_providers(request: Request, _=Depends(require_login)):
    """Get all providers (core + custom) with metadata."""
    from core.chat.llm_providers import provider_registry, PROVIDER_METADATA
    providers_list = provider_registry.get_all_providers()
    metadata = {k: {
                    'model_options': v.get('model_options'),
                    'is_local': v.get('is_local', False),
                    'required_fields': v.get('required_fields', []),
                    'default_timeout': v.get('default_timeout', 10.0),
                    'supports_reasoning': v.get('supports_reasoning', False),
                    'api_key_env': v.get('api_key_env', ''),
                }
                for k, v in PROVIDER_METADATA.items()}
    return {"providers": providers_list, "metadata": metadata}


@router.put("/api/llm/providers/{provider_key}")
async def update_llm_provider(provider_key: str, request: Request, _=Depends(require_login)):
    """Update LLM provider settings (core or custom)."""
    from core.settings_manager import settings
    from core.chat.llm_providers import provider_registry
    data = await request.json()

    # Route API keys to credentials manager
    api_key = data.pop('api_key', None)
    if api_key is not None and api_key.strip():
        from core.credentials_manager import credentials
        credentials.set_llm_api_key(provider_key, api_key.strip())

    # Determine if core or custom
    if provider_registry.is_core_provider(provider_key):
        providers = settings.get('LLM_PROVIDERS', {})
        if provider_key not in providers:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_key}' not found")
        providers[provider_key].update(data)
        for prov in providers.values():
            prov.pop('api_key', None)
        settings.set('LLM_PROVIDERS', providers, persist=True)
    else:
        custom = settings.get('LLM_CUSTOM_PROVIDERS', {})
        if provider_key not in custom:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_key}' not found")
        custom[provider_key].update(data)
        for prov in custom.values():
            prov.pop('api_key', None)
        settings.set('LLM_CUSTOM_PROVIDERS', custom, persist=True)

    publish(Events.SETTINGS_CHANGED, {"key": "LLM_PROVIDERS", "value": provider_key})
    return {"status": "success", "provider": provider_key}


@router.put("/api/llm/fallback-order")
async def update_fallback_order(request: Request, _=Depends(require_login)):
    """Update LLM fallback order."""
    from core.settings_manager import settings
    data = await request.json()
    order = data.get('order', [])
    settings.set('LLM_FALLBACK_ORDER', order, persist=True)
    return {"status": "success", "order": order}


@router.post("/api/llm/test/{provider_key}")
async def test_llm_provider(provider_key: str, request: Request, _=Depends(require_login)):
    """Test LLM provider connection via health_check()."""
    from core.chat.llm_providers import get_provider_by_key
    try:
        providers_config = {**dict(getattr(config, 'LLM_PROVIDERS', {})), **dict(getattr(config, 'LLM_CUSTOM_PROVIDERS', {}))}
        if provider_key not in providers_config:
            return {"status": "error", "error": f"Unknown provider: {provider_key}"}

        was_enabled = bool(providers_config[provider_key].get('enabled'))
        test_config = dict(providers_config[provider_key])
        test_config['enabled'] = True

        try:
            body = await request.json()
        except Exception:
            body = {}
        for field in ('api_key', 'base_url', 'model'):
            if body.get(field):
                test_config[field] = body[field]

        # Probe budget: is_local providers default to a 0.3s health timeout —
        # right for boot fallback scans, wrong for a human-clicked Test against
        # a LAN box with a big model loaded (models.list can take seconds while
        # completions work fine). Floor it at 5s for the test only. 2026-08-06.
        try:
            test_config['timeout'] = max(float(test_config.get('timeout') or 0), 5.0)
        except (TypeError, ValueError):
            test_config['timeout'] = 5.0

        providers_config[provider_key] = test_config

        def _test_provider():
            # Bounded request timeout — a button press shouldn't hang for the
            # full 240s reply budget on a dead-but-accepting endpoint.
            _t = float(getattr(config, 'LLM_REQUEST_TIMEOUT', 30) or 30)
            provider = get_provider_by_key(provider_key, providers_config, min(30.0, _t))
            if not provider:
                return {"status": "error", "error": f"Could not create provider '{provider_key}' — check API key and settings"}

            health = provider.test_connection()
            disabled_note = "" if was_enabled else " — note: provider is DISABLED in settings, enable it before use"

            # Real-path probe: replies never call models.list — they call
            # chat_completion with the reply-path generation params. Test what
            # a reply actually does whenever a model is known; the health probe
            # alone passed boxes that couldn't complete and failed boxes that
            # could (0.3s models.list vs working completions). 2026-08-06.
            model = str(test_config.get('model') or getattr(provider, 'model', '') or '').strip()
            if model:
                try:
                    from core.chat.llm_providers import get_generation_params
                    gen = get_generation_params(provider_key, model, providers_config)
                    gen['model'] = model
                    provider.chat_completion(
                        [{"role": "user", "content": "Reply with the single word: OK"}],
                        generation_params=gen)
                    quirk = "" if health.get('ok') else " (models.list probe failed — endpoint quirk, completions work)"
                    return {"status": "success", "response": f"Completion OK ({model}){quirk}{disabled_note}"}
                except Exception as e:
                    return {"status": "error", "error": f"Completion failed — this is the call replies use: {str(e)[:160]}"}

            if health.get('ok'):
                return {"status": "success", "response": f"Endpoint reachable (no model set — completion untested){disabled_note}"}
            return {"status": "error", "error": health.get("error", "Connection failed")}

        return await asyncio.to_thread(_test_provider)
    except Exception as e:
        logger.error(f"LLM provider test failed for '{provider_key}': {e}")
        return {"status": "error", "error": "Provider test failed — check API key and endpoint configuration"}


# A prompt that reliably provokes reasoning on models that support it — the point
# is to give the model every excuse to think, so a lack of thinking is meaningful.
_THINKING_PROBE = ("Reason through this step by step using your thinking, then give a "
                   "one-line final answer: is 91 a prime number?")
_THINK_TAG_RE = re.compile(r'<think>(.*?)</think>', re.DOTALL | re.IGNORECASE)


def _clean_llm_error(e) -> str:
    """Pull the useful message out of an LLM/HTTP error (esp. OpenAI-client 400s)."""
    body = getattr(e, 'body', None)
    if isinstance(body, dict):
        inner = body.get('error') if isinstance(body.get('error'), dict) else body
        msg = (inner or {}).get('message')
        if msg:
            return str(msg)[:250]
    return str(e)[:250]


def _analyze_thinking(resp) -> dict:
    """Split an LLMResponse into reasoning vs prose and measure both.
    Reasoning shows up either as a separate `thinking` field (reasoning_content)
    or as inline <think>...</think> tags in the content."""
    content = resp.content or ''
    reasoning = (getattr(resp, 'thinking', None) or '').strip()
    prose = content
    m = _THINK_TAG_RE.search(content)
    if m:
        if not reasoning:
            reasoning = m.group(1).strip()
        prose = _THINK_TAG_RE.sub('', content).strip()
    usage = getattr(resp, 'usage', None) or {}
    return {
        'had_reasoning': bool(reasoning),
        'reasoning_chars': len(reasoning),
        'prose_chars': len(prose.strip()),
        'completion_tokens': usage.get('completion_tokens'),
    }


@router.post("/api/llm/test-thinking/{provider_key}")
async def test_llm_thinking(provider_key: str, request: Request, _=Depends(require_login)):
    """Provoke reasoning, then check whether 'disable thinking' actually suppresses it.

    Runs a baseline (no suppression) and — if suppression is configured — the
    configured suppression, and compares. The baseline is what makes the verdict
    honest: it distinguishes 'the switch worked' from 'the model just didn't think'.
    """
    from core.chat.llm_providers import get_provider_by_key
    try:
        providers_config = {**dict(getattr(config, 'LLM_PROVIDERS', {})),
                            **dict(getattr(config, 'LLM_CUSTOM_PROVIDERS', {}))}
        if provider_key not in providers_config:
            return {"status": "error", "error": f"Unknown provider: {provider_key}"}

        try:
            body = await request.json()
        except Exception:
            body = {}

        base = dict(providers_config[provider_key])
        base['enabled'] = True
        for field in ('api_key', 'base_url', 'model', 'extra_body'):
            if body.get(field) is not None:
                base[field] = body[field]
        if 'disable_thinking' in body:
            base['disable_thinking'] = bool(body['disable_thinking'])

        suppress_active = bool(base.get('disable_thinking') or base.get('disable_thinking_qwen')
                               or base.get('extra_body'))
        timeout = getattr(config, 'LLM_REQUEST_TIMEOUT', 30)
        probe = [{"role": "user", "content": _THINKING_PROBE}]

        def _run(cfg):
            pc = dict(providers_config)
            pc[provider_key] = cfg
            provider = get_provider_by_key(provider_key, pc, timeout)
            if not provider:
                return {"error": "Could not create provider — check credentials and settings"}
            try:
                resp = provider.chat_completion(probe, generation_params={"max_tokens": 512, "temperature": 0.6})
            except Exception as e:
                return {"error": _clean_llm_error(e)}
            return {"result": _analyze_thinking(resp)}

        def _work():
            b = _run({**base, 'disable_thinking': False,
                      'disable_thinking_qwen': False, 'extra_body': ''})
            if 'error' in b:
                return {"status": "error", "error": f"Baseline call failed: {b['error']}"}
            baseline = b['result']
            if suppress_active:
                d = _run(dict(base))
                if 'error' in d:
                    # The suppression config itself was rejected — this is the useful signal.
                    return {"status": "error", "baseline": baseline,
                            "error": f"Your suppression config was rejected: {d['error']}"}
                disabled = d['result']
            else:
                disabled = baseline

            if not suppress_active:
                verdict = (f"Disable thinking is OFF — the model reasoned ({baseline['reasoning_chars']} chars). "
                           f"Turn it on and re-test." if baseline['had_reasoning']
                           else "Disable thinking is OFF, and the model didn't reason on this prompt.")
                ok = None
            elif not baseline['had_reasoning']:
                verdict = ("Inconclusive — the model didn't reason even unsuppressed "
                           "(it may not support thinking, or chose not to).")
                ok = None
            elif not disabled['had_reasoning']:
                verdict = f"✓ Works — reasoning {baseline['reasoning_chars']} → 0 chars when disabled."
                ok = True
            else:
                verdict = (f"⚠ Not suppressed — still {disabled['reasoning_chars']} chars of reasoning. "
                           f"Try the Extra body escape hatch.")
                ok = False

            return {"status": "success", "ok": ok, "suppress_active": suppress_active,
                    "baseline": baseline, "disabled": disabled, "verdict": verdict}

        return await asyncio.to_thread(_work)
    except Exception as e:
        logger.error(f"LLM thinking test failed for '{provider_key}': {e}")
        return {"status": "error", "error": "Thinking test failed — check the model and connection"}


@router.post("/api/llm/custom-providers")
async def add_custom_provider(request: Request, _=Depends(require_login)):
    """Create a new custom provider instance."""
    from core.settings_manager import settings
    from core.chat.llm_providers import provider_registry
    data = await request.json()

    name = data.get('name', '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="Provider name required")

    # Sanitize name
    name = "".join(c for c in name if c.isalnum() or c in "-_").lower()
    if not name:
        raise HTTPException(status_code=400, detail="Invalid provider name")

    # Check reserved / duplicate
    if provider_registry.is_core_provider(name):
        raise HTTPException(status_code=400, detail=f"Name '{name}' is reserved for a core provider")

    custom = settings.get('LLM_CUSTOM_PROVIDERS', {})
    if name in custom:
        raise HTTPException(status_code=400, detail=f"Provider '{name}' already exists")

    # Validate BEFORE storing the API key — the old order persisted the key
    # first, so a 400 on template/base_url left an orphaned credential entry.
    template = data.get('template', 'openai')
    if template not in provider_registry._classes:
        raise HTTPException(status_code=400, detail=f"Unknown provider template '{template}'")
    base_url = (data.get('base_url') or '').strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="A base URL is required for a custom provider")

    # Route API key to credentials
    api_key = data.pop('api_key', None)
    if api_key and api_key.strip():
        from core.credentials_manager import credentials
        credentials.set_llm_api_key(name, api_key.strip())
    provider_config = {
        'template': template,
        'display_name': data.get('display_name', name),
        'base_url': base_url,
        'model': data.get('model', ''),
        'enabled': data.get('enabled', True),
        'use_as_fallback': data.get('use_as_fallback', True),
        'is_local': data.get('is_local', False),
        'timeout': data.get('timeout', 0.3 if data.get('is_local') else 10.0),
    }
    # Optional fields
    for field in ('api_key_env', 'generation_params', 'auto_discover_models',
                  'session_affinity', 'strip_penalties', 'suggested_models',
                  'thinking_enabled', 'thinking_budget', 'reasoning_effort',
                  'supports_images', 'disable_thinking', 'extra_body'):
        if field in data:
            provider_config[field] = data[field]

    custom[name] = provider_config
    settings.set('LLM_CUSTOM_PROVIDERS', custom, persist=True)

    # Add to fallback order
    order = settings.get('LLM_FALLBACK_ORDER', [])
    if name not in order:
        order.append(name)
        settings.set('LLM_FALLBACK_ORDER', order, persist=True)

    publish(Events.SETTINGS_CHANGED, {"key": "LLM_CUSTOM_PROVIDERS", "value": name})
    return {"status": "success", "name": name, "config": provider_config}


@router.delete("/api/llm/custom-providers/{provider_key}")
async def delete_custom_provider(provider_key: str, request: Request, _=Depends(require_login)):
    """Remove a custom provider instance."""
    from core.settings_manager import settings
    from core.chat.llm_providers import provider_registry

    if provider_registry.is_core_provider(provider_key):
        raise HTTPException(status_code=400, detail="Cannot delete core providers")

    custom = settings.get('LLM_CUSTOM_PROVIDERS', {})
    if provider_key not in custom:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_key}' not found")

    custom.pop(provider_key)
    settings.set('LLM_CUSTOM_PROVIDERS', custom, persist=True)

    # Remove from fallback order
    order = settings.get('LLM_FALLBACK_ORDER', [])
    if provider_key in order:
        order.remove(provider_key)
        settings.set('LLM_FALLBACK_ORDER', order, persist=True)

    # Clean up credentials
    try:
        from core.credentials_manager import credentials
        credentials.clear_llm_api_key(provider_key)
    except Exception as e:
        logger.warning(f"Failed to clear credentials for deleted provider '{provider_key}': {e}")

    publish(Events.SETTINGS_CHANGED, {"key": "LLM_CUSTOM_PROVIDERS", "value": provider_key})
    return {"status": "success", "name": provider_key}


@router.get("/api/llm/presets")
async def get_llm_presets(request: Request, _=Depends(require_login)):
    """Get available provider presets."""
    from core.chat.llm_providers import provider_registry
    return {"presets": provider_registry.get_presets(), "templates": provider_registry.get_templates()}


@router.get("/api/llm/custom-providers/{provider_key}/models")
async def discover_models(provider_key: str, request: Request, _=Depends(require_login)):
    """Discover available models from a provider (hits /v1/models)."""
    from core.chat.llm_providers import provider_registry

    def _discover():
        models = provider_registry.discover_models(provider_key)
        return models

    try:
        models = await asyncio.to_thread(_discover)
        if models is None:
            return {"models": [], "error": "Model discovery not supported for this provider"}
        return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


# =============================================================================
# PROVIDER REGISTRY ENDPOINTS (TTS, STT, Embedding)
# =============================================================================

@router.get("/api/tts/providers")
async def get_tts_providers(request: Request, _=Depends(require_login)):
    """List available TTS providers (core + plugin)."""
    from core.tts.providers import tts_registry
    active = tts_registry.get_active_key()
    providers = tts_registry.get_all()
    for p in providers:
        p['is_active'] = p['key'] == active
    return {"providers": providers, "active": active}


@router.get("/api/stt/providers")
async def get_stt_providers(request: Request, _=Depends(require_login)):
    """List available STT providers (core + plugin)."""
    from core.stt.providers import stt_registry
    active = stt_registry.get_active_key()
    providers = stt_registry.get_all()
    for p in providers:
        p['is_active'] = p['key'] == active
    return {"providers": providers, "active": active}


@router.get("/api/embedding/providers")
async def get_embedding_providers(request: Request, _=Depends(require_login)):
    """List available embedding providers (core + plugin). Mirrors the TTS/STT
    endpoints so the Settings UI's `mergeRegistryProviders` helper can surface
    plugin-registered providers in the dropdown. Without this, a plugin
    provider (e.g. minilm) doesn't appear in the EMBEDDING_PROVIDER selector —
    the backend swap works via API but the UI dropdown renders the setting as
    'Disabled' because the current value doesn't match any hardcoded option.
    2026-04-21."""
    from core.embeddings import embedding_registry
    active = embedding_registry.get_active_key()
    providers = embedding_registry.get_all()
    for p in providers:
        p['is_active'] = p['key'] == active
    return {"providers": providers, "active": active}
