# core/modules/system/personas/persona_manager.py
import logging
import json
import shutil
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Settings keys that a persona can bundle
PERSONA_SETTINGS_KEYS = [
    "prompt", "toolset", "spice_set", "voice", "pitch", "speed",
    "spice_enabled", "spice_turns", "inject_datetime", "custom_context",
    "llm_primary", "llm_model", "memory_scope", "goal_scope",
    "knowledge_scope", "people_scope", "trim_color",
    "state_engine_enabled", "state_preset", "state_vars_in_prompt",
    "state_story_in_prompt"
]


class PersonaManager:
    """Manages persona definitions with user overrides and avatar storage."""

    def __init__(self):
        self.BASE_DIR = Path(__file__).parent
        project_root = self.BASE_DIR.parent
        while project_root.parent != project_root:
            if (project_root / 'core').exists() or (project_root / 'main.py').exists():
                break
            project_root = project_root.parent

        self.PROJECT_ROOT = project_root
        self.USER_DIR = project_root / "user" / "personas"
        self.USER_AVATARS = self.USER_DIR / "avatars"
        self.BUILTIN_AVATARS = project_root / "interfaces" / "web" / "static" / "personas" / "avatars"

        self._personas = {}
        self._lock = threading.Lock()

        try:
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            self.USER_AVATARS.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create persona directories: {e}")

        self._load()

    def _load(self):
        """Load personas from user file, seeding from core defaults if needed."""
        user_path = self.USER_DIR / "personas.json"
        core_path = self.BASE_DIR / "personas.json"

        core_personas = {}
        try:
            with open(core_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            core_personas = {k: v for k, v in data.items() if not k.startswith('_')}
        except Exception as e:
            logger.error(f"Failed to load core personas: {e}")

        self._personas = {}
        if user_path.exists():
            try:
                with open(user_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._personas = {k: v for k, v in data.items() if not k.startswith('_')}
            except Exception as e:
                logger.error(f"Failed to load user personas: {e}")

        # Seed missing personas from core defaults
        seeded = 0
        for name, persona in core_personas.items():
            if name not in self._personas:
                self._personas[name] = persona
                # Copy built-in avatar to user dir if it exists
                self._seed_avatar(persona.get('avatar'))
                seeded += 1

        if seeded > 0:
            logger.info(f"Seeded {seeded} new personas from defaults")
            self._save_to_user()

        logger.info(f"Loaded {len(self._personas)} personas")

    def _seed_avatar(self, avatar_filename):
        """Copy a built-in avatar to user avatars if not already there."""
        if not avatar_filename:
            return
        src = self.BUILTIN_AVATARS / avatar_filename
        dst = self.USER_AVATARS / avatar_filename
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
                logger.debug(f"Seeded avatar: {avatar_filename}")
            except Exception as e:
                logger.warning(f"Failed to seed avatar {avatar_filename}: {e}")

    def _save_to_user(self) -> bool:
        """Save all personas to user file."""
        user_path = self.USER_DIR / "personas.json"
        try:
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            data = {"_comment": "Your personas"}
            data.update(self._personas)
            with open(user_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.debug(f"Saved {len(self._personas)} personas")
            return True
        except Exception as e:
            logger.error(f"Failed to save personas: {e}")
            return False

    # === Getters ===

    def get_all(self) -> dict:
        return self._personas.copy()

    def get(self, name: str) -> dict | None:
        return self._personas.get(name)

    def exists(self, name: str) -> bool:
        return name in self._personas

    def get_names(self) -> list:
        return list(self._personas.keys())

    def get_list(self) -> list:
        """Get list of personas with summary info."""
        result = []
        for name, p in self._personas.items():
            result.append({
                "name": name,
                "tagline": p.get("tagline", ""),
                "avatar": p.get("avatar"),
                "trim_color": p.get("settings", {}).get("trim_color", ""),
            })
        return result

    # === CRUD ===

    def create(self, name: str, data: dict) -> bool:
        """Create a new persona."""
        safe_name = self._sanitize_name(name)
        if not safe_name:
            return False
        if safe_name in self._personas:
            return False

        with self._lock:
            persona = {
                "name": safe_name,
                "tagline": data.get("tagline", ""),
                "avatar": data.get("avatar"),
                "avatar_full": data.get("avatar_full"),
                "settings": self._clean_settings(data.get("settings", {}))
            }
            self._personas[safe_name] = persona
            return self._save_to_user()

    def update(self, name: str, data: dict) -> bool:
        """Update an existing persona."""
        if name not in self._personas:
            return False

        with self._lock:
            persona = self._personas[name]
            if "tagline" in data:
                persona["tagline"] = data["tagline"]
            if "avatar" in data:
                persona["avatar"] = data["avatar"]
            if "avatar_full" in data:
                persona["avatar_full"] = data["avatar_full"]
            if "settings" in data:
                persona["settings"] = self._clean_settings(data["settings"])
            if "name" in data and data["name"] != name:
                # Rename
                new_name = self._sanitize_name(data["name"])
                if new_name and new_name not in self._personas:
                    persona["name"] = new_name
                    self._personas[new_name] = persona
                    del self._personas[name]
            return self._save_to_user()

    def delete(self, name: str) -> bool:
        """Delete a persona."""
        if name not in self._personas:
            return False

        with self._lock:
            # Remove avatar file if it's user-uploaded
            persona = self._personas[name]
            avatar = persona.get("avatar")
            if avatar:
                avatar_path = self.USER_AVATARS / avatar
                if avatar_path.exists():
                    try:
                        avatar_path.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete avatar {avatar}: {e}")

            del self._personas[name]
            return self._save_to_user()

    def duplicate(self, name: str, new_name: str) -> bool:
        """Duplicate a persona with a new name."""
        if name not in self._personas:
            return False
        safe_new = self._sanitize_name(new_name)
        if not safe_new or safe_new in self._personas:
            return False

        with self._lock:
            import copy
            persona = copy.deepcopy(self._personas[name])
            persona["name"] = safe_new
            # Don't copy avatar — let user upload their own
            persona["avatar"] = None
            persona["avatar_full"] = None
            self._personas[safe_new] = persona
            return self._save_to_user()

    def create_from_settings(self, name: str, chat_settings: dict) -> bool:
        """Create a persona from current chat settings."""
        safe_name = self._sanitize_name(name)
        if not safe_name or safe_name in self._personas:
            return False

        with self._lock:
            settings = self._clean_settings(chat_settings)
            persona = {
                "name": safe_name,
                "tagline": "",
                "avatar": None,
                "avatar_full": None,
                "settings": settings
            }
            self._personas[safe_name] = persona
            return self._save_to_user()

    # === Avatar ===

    def set_avatar(self, name: str, filename: str, data: bytes) -> bool:
        """Save avatar file for a persona."""
        if name not in self._personas:
            return False

        try:
            filepath = self.USER_AVATARS / filename
            with open(filepath, 'wb') as f:
                f.write(data)

            with self._lock:
                self._personas[name]["avatar"] = filename
                return self._save_to_user()
        except Exception as e:
            logger.error(f"Failed to save avatar for {name}: {e}")
            return False

    def get_avatar_path(self, name: str) -> Path | None:
        """Get filesystem path to persona's avatar."""
        persona = self._personas.get(name)
        if not persona or not persona.get("avatar"):
            return None

        avatar_file = persona["avatar"]

        # Check user avatars first
        user_path = self.USER_AVATARS / avatar_file
        if user_path.exists():
            return user_path

        # Fall back to built-in avatars
        builtin_path = self.BUILTIN_AVATARS / avatar_file
        if builtin_path.exists():
            return builtin_path

        return None

    # === Merge ===

    def merge_defaults(self, backup_dir=None):
        """Additive merge: add missing personas from core defaults. Returns count added."""
        if backup_dir:
            dest = Path(backup_dir) / "personas"
            dest.mkdir(parents=True, exist_ok=True)
            src = self.USER_DIR / "personas.json"
            if src.exists():
                shutil.copy2(src, dest / "personas.json")

        core_path = self.BASE_DIR / "personas.json"
        if not core_path.exists():
            return 0

        try:
            with open(core_path, 'r', encoding='utf-8') as f:
                core_personas = {k: v for k, v in json.load(f).items() if not k.startswith('_')}
        except Exception as e:
            logger.error(f"Failed to load core personas for merge: {e}")
            return 0

        added = 0
        with self._lock:
            for name, persona in core_personas.items():
                if name not in self._personas:
                    self._personas[name] = persona
                    self._seed_avatar(persona.get('avatar'))
                    added += 1

            if added > 0:
                self._save_to_user()
                logger.info(f"Merged {added} new personas from defaults")

        return added

    # === Helpers ===

    def _sanitize_name(self, name: str) -> str:
        """Sanitize persona name."""
        if not name or not name.strip():
            return ""
        safe = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        return safe.replace(' ', '_').lower()

    def _clean_settings(self, settings: dict) -> dict:
        """Only keep recognized settings keys."""
        return {k: v for k, v in settings.items() if k in PERSONA_SETTINGS_KEYS}


# Singleton instance
persona_manager = PersonaManager()
