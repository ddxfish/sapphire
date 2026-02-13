"""Data migrations for Sapphire. Run automatically on startup."""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

USER_PROMPTS_DIR = Path(__file__).parent.parent / "user" / "prompts"


def run_all():
    """Run all pending migrations."""
    migrate_persona_to_character()


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
        with open(path, 'r', encoding='utf-8') as f:
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
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Migrated prompt_pieces.json: persona -> character")
    except Exception as e:
        logger.error(f"Migration failed for prompt_pieces.json: {e}")


def _migrate_user_prompts():
    """Migrate any user-saved prompt files that have persona in components."""
    prompts_dir = USER_PROMPTS_DIR
    if not prompts_dir.exists():
        return

    for path in prompts_dir.glob("*.json"):
        if path.name in ("prompt_pieces.json", "prompt_monoliths.json", "prompt_spices.json"):
            continue

        try:
            with open(path, 'r', encoding='utf-8') as f:
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
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                logger.info(f"Migrated {path.name}: persona -> character")
        except Exception as e:
            logger.warning(f"Could not migrate {path.name}: {e}")
