"""Data migrations for Sapphire. Run automatically on startup."""
import json
import logging
from pathlib import Path

from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

USER_DIR = Path(__file__).parent.parent / "user"
USER_PROMPTS_DIR = USER_DIR / "prompts"


def run_all():
    """Run all pending migrations."""
    migrate_persona_to_character()
    migrate_loose_prompt_files()
    migrate_misfiled_preset_pieces()
    migrate_stt_to_provider()
    migrate_tts_to_provider()


def migrate_loose_prompt_files():
    """Fold legacy per-prompt JSON files into the two real stores.

    The third prompt lane (`_user_prompts`, loaded from stray files in
    user/prompts/) was retired 2026-08-09: it had no writers left, shadowed
    every name collision, and couldn't re-render mid-session. Files fold
    into prompt_monoliths.json / prompt_pieces.json and are renamed
    .imported; a name collision keeps the store version and renames the
    file .duplicate (nothing loads either — rename is bookkeeping).

    Armored (scout findings, same day): whole body try-wrapped — this runs
    at import time ABOVE the boot try/except, so an exception here was a
    no-boot with no log. Renames use replace() (Path.rename raises
    FileExistsError on Windows when the target exists — re-dropping an
    already-imported file bricked every subsequent boot). System-file guard
    is case-insensitive AND prefix-based so backup copies like
    prompt_pieces-backup.json can't be folded as garbage presets.
    """
    try:
        _migrate_loose_prompt_files_inner()
    except Exception as e:
        logger.error(f"Loose-prompt fold failed — skipped this boot: {e}")


def _migrate_loose_prompt_files_inner():
    prompts_dir = USER_PROMPTS_DIR
    if not prompts_dir.exists():
        return
    system_stems = ("prompt_pieces", "prompt_monoliths", "prompt_spices",
                    "vault_refs")

    loose = [p for p in prompts_dir.glob("*.json")
             if not p.name.lower().startswith(system_stems)]
    if not loose:
        return

    mono_path = prompts_dir / "prompt_monoliths.json"
    pieces_path = prompts_dir / "prompt_pieces.json"
    try:
        monoliths = json.loads(mono_path.read_text(encoding='utf-8-sig')) if mono_path.exists() else {}
        pieces = json.loads(pieces_path.read_text(encoding='utf-8-sig')) if pieces_path.exists() else {
            "components": {}, "scenario_presets": {}}
    except Exception as e:
        logger.error(f"Loose-prompt fold skipped — store files unreadable: {e}")
        return
    if not isinstance(monoliths, dict) or not isinstance(pieces, dict) \
            or not isinstance(pieces.get("scenario_presets", {}), dict):
        logger.error("Loose-prompt fold skipped — store files have unexpected shape")
        return
    presets = pieces.setdefault("scenario_presets", {})

    def _preset_shaped(components):
        """Preset components are {type: str-or-list}. A store-backup copy
        (components = {type: {key: text}}) must NOT fold — its dict values
        would poison every list-all-prompts route."""
        return all(isinstance(v, (str, list)) for v in components.values())

    def _fold_one(name, entry):
        """Insert one legacy prompt into the right store. Returns True if inserted."""
        if not isinstance(name, str) or not name or name.startswith('_'):
            return False
        if name in monoliths or name in presets:
            return False
        comps = entry.get('components')
        if isinstance(comps, dict) and _preset_shaped(comps):
            preset = {k: v for k, v in comps.items() if not k.startswith('_')}
            preset['_privacy_required'] = bool(entry.get('privacy_required', False))
            presets[name] = preset
        elif isinstance(entry.get('content'), str):
            monoliths[name] = {'content': entry['content'],
                               'privacy_required': bool(entry.get('privacy_required', False))}
        else:
            return False
        return True

    def _set_aside(path, suffix):
        # replace(), not rename(): rename raises on Windows if a previous
        # run already left the target behind. Retry wrapper for transient
        # PermissionError (AV scanner holding the file).
        target = path.with_suffix(suffix)
        replace_with_retry(path, target)

    changed = False
    for path in loose:
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception as e:
            logger.warning(f"Loose prompt {path.name} unreadable — left in place: {e}")
            continue

        entries = {}
        if isinstance(data, dict):
            if 'components' in data or 'content' in data:
                entries[data.get('name', path.stem)] = data
            else:
                # Legacy collection shape: {name: {components/content...}}
                for k, v in data.items():
                    if isinstance(v, dict) and ('components' in v or 'content' in v):
                        entries[v.get('name', k)] = v

        folded = any([_fold_one(n, e) for n, e in entries.items()])
        if folded:
            changed = True
            _set_aside(path, '.json.imported')
            logger.info(f"Folded legacy prompt file {path.name} into store ({', '.join(entries)})")
        elif entries:
            _set_aside(path, '.json.duplicate')
            logger.warning(f"Legacy prompt file {path.name} collides with existing "
                           f"store names — kept store version, file set aside")
        else:
            logger.warning(f"Legacy prompt file {path.name} has no recognizable "
                           f"prompt shape — left in place")

    if changed:
        for target, payload in ((mono_path, monoliths), (pieces_path, pieces)):
            tmp = target.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            replace_with_retry(tmp, target)


def migrate_misfiled_preset_pieces():
    """Move preset piece keys filed under the wrong list component.

    The shipped 'sapphire' preset listed 'memory_use' (an extras key) under
    emotions; both assemblers silently dropped unknown keys, so the flagship
    default ran without its memory-use instruction. Generic fix: any key in
    a preset's extras/emotions list that doesn't exist in that component
    type but DOES exist in the sibling moves over.
    """
    path = USER_PROMPTS_DIR / "prompt_pieces.json"
    if not path.exists():
        return
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        comps = data.get("components", {})
        pair = {"extras": "emotions", "emotions": "extras"}
        changed = False
        for preset_name, preset in data.get("scenario_presets", {}).items():
            if not isinstance(preset, dict):
                continue
            for ltype, sibling in pair.items():
                keys = preset.get(ltype)
                if not isinstance(keys, list):
                    continue
                for key in list(keys):
                    if key not in comps.get(ltype, {}) and key in comps.get(sibling, {}):
                        keys.remove(key)
                        sib_list = preset.setdefault(sibling, [])
                        if key not in sib_list:
                            sib_list.append(key)
                        changed = True
                        logger.info(f"Preset '{preset_name}': moved '{key}' "
                                    f"{ltype} -> {sibling}")
        if changed:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            replace_with_retry(tmp, path)
    except Exception as e:
        logger.error(f"Mis-filed piece migration failed: {e}")


def migrate_persona_to_character():
    """Rename 'persona' component key to 'character' in user prompt JSON files.

    Affects:
    - prompt_pieces.json: components.persona -> components.character
    - prompt_pieces.json: scenario_presets.*.persona -> *.character
    - Any user-saved prompt JSON with components.persona
    """
    _migrate_prompt_pieces()
    _migrate_user_prompts()


def _migrate_prompt_pieces():
    """Migrate prompt_pieces.json persona -> character."""
    path = USER_PROMPTS_DIR / "prompt_pieces.json"
    if not path.exists():
        return

    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        changed = False

        # Rename components.persona -> components.character
        components = data.get("components", {})
        if "persona" in components and "character" not in components:
            components["character"] = components.pop("persona")
            changed = True

        # Rename persona key in scenario_presets
        for preset_name, preset in data.get("scenario_presets", {}).items():
            if isinstance(preset, dict) and "persona" in preset and "character" not in preset:
                preset["character"] = preset.pop("persona")
                changed = True

        if changed:
            tmp = path.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            replace_with_retry(tmp, path)
            logger.info("Migrated prompt_pieces.json: persona -> character")
    except Exception as e:
        logger.error(f"Migration failed for prompt_pieces.json: {e}")


def _migrate_user_prompts():
    """Migrate any user-saved prompt files that have persona in components."""
    prompts_dir = USER_PROMPTS_DIR
    if not prompts_dir.exists():
        return

    for path in prompts_dir.glob("*.json"):
        if path.name in ("prompt_pieces.json", "prompt_monoliths.json",
                         "prompt_spices.json", "vault_refs.json"):
            continue

        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)

            changed = False

            # Handle single prompt objects and collections
            prompts_to_check = []
            if isinstance(data, dict):
                if "components" in data:
                    prompts_to_check.append(data)
                else:
                    # Could be a dict of prompts
                    for v in data.values():
                        if isinstance(v, dict) and "components" in v:
                            prompts_to_check.append(v)

            for prompt in prompts_to_check:
                comps = prompt.get("components", {})
                if isinstance(comps, dict) and "persona" in comps and "character" not in comps:
                    comps["character"] = comps.pop("persona")
                    changed = True

            if changed:
                tmp = path.with_suffix('.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                replace_with_retry(tmp, path)
                logger.info(f"Migrated {path.name}: persona -> character")
        except Exception as e:
            logger.warning(f"Could not migrate {path.name}: {e}")


def migrate_stt_to_provider():
    """Migrate STT_ENABLED + STT_ENGINE → STT_PROVIDER.

    If user has STT_ENABLED in their settings but no STT_PROVIDER,
    convert: enabled=true → provider='faster_whisper', enabled=false → provider='none'.
    Removes old STT_ENABLED and STT_ENGINE keys from user settings.
    """
    settings_path = USER_DIR / "settings.json"
    if not settings_path.exists():
        return

    try:
        with open(settings_path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        stt = data.get('stt', {})
        if not isinstance(stt, dict):
            stt = {}

        # Already migrated?
        if 'STT_PROVIDER' in stt:
            # Clean up root-level STT_ENABLED if present (legacy wizard path)
            if 'STT_ENABLED' in data or 'STT_ENGINE' in data:
                data.pop('STT_ENABLED', None)
                data.pop('STT_ENGINE', None)
                data['stt'] = stt
                tmp = settings_path.with_suffix('.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                replace_with_retry(tmp, settings_path)
                logger.info("Cleaned up root-level STT keys (already migrated)")
            return

        # Check both nested (stt.STT_ENABLED) and root-level (STT_ENABLED)
        was_enabled = stt.get('STT_ENABLED', data.get('STT_ENABLED', False))
        engine = stt.get('STT_ENGINE', data.get('STT_ENGINE', 'faster_whisper'))

        # Nothing to migrate?
        if 'STT_ENABLED' not in stt and 'STT_ENABLED' not in data and 'STT_ENGINE' not in stt:
            return

        if 'STT_PROVIDER' not in stt:
            stt['STT_PROVIDER'] = engine if was_enabled else 'none'
        stt.pop('STT_ENABLED', None)
        stt.pop('STT_ENGINE', None)
        data.pop('STT_ENABLED', None)
        data.pop('STT_ENGINE', None)
        data['stt'] = stt

        tmp = settings_path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        replace_with_retry(tmp, settings_path)
        logger.info(f"Migrated STT settings: enabled={was_enabled} engine={engine} -> provider={stt['STT_PROVIDER']}")
    except Exception as e:
        logger.error(f"STT settings migration failed: {e}")


def migrate_tts_to_provider():
    """Migrate TTS_ENABLED → TTS_PROVIDER.

    If user has TTS_ENABLED but no TTS_PROVIDER,
    convert: enabled=true → provider='kokoro', enabled=false → provider='none'.
    Removes old TTS_ENABLED key from user settings.
    """
    settings_path = USER_DIR / "settings.json"
    if not settings_path.exists():
        return

    try:
        with open(settings_path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        tts = data.get('tts', {})
        if not isinstance(tts, dict):
            tts = {}

        # Already migrated?
        if 'TTS_PROVIDER' in tts:
            # Clean up root-level TTS_ENABLED if present
            if 'TTS_ENABLED' in data:
                data.pop('TTS_ENABLED', None)
                data['tts'] = tts
                tmp = settings_path.with_suffix('.tmp')
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                replace_with_retry(tmp, settings_path)
                logger.info("Cleaned up root-level TTS keys (already migrated)")
            return

        # Check both nested and root-level
        was_enabled = tts.get('TTS_ENABLED', data.get('TTS_ENABLED', False))

        # Nothing to migrate?
        if 'TTS_ENABLED' not in tts and 'TTS_ENABLED' not in data:
            return

        if 'TTS_PROVIDER' not in tts:
            tts['TTS_PROVIDER'] = 'kokoro' if was_enabled else 'none'
        tts.pop('TTS_ENABLED', None)
        data.pop('TTS_ENABLED', None)
        data['tts'] = tts

        tmp = settings_path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        replace_with_retry(tmp, settings_path)
        logger.info(f"Migrated TTS settings: enabled={was_enabled} -> provider={tts['TTS_PROVIDER']}")
    except Exception as e:
        logger.error(f"TTS settings migration failed: {e}")
