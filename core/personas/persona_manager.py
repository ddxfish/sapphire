# core/personas/persona_manager.py
import logging
import json
import shutil
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Static (non-scope) persona settings keys. Scope keys are merged dynamically
# from SCOPE_REGISTRY by get_persona_settings_keys() — function, not constant, because
# plugins can register new scopes at runtime and a module-import snapshot would miss them.
_STATIC_PERSONA_SETTINGS_KEYS = [
    "prompt", "toolset", "spice_set", "voice", "pitch", "speed",
    "spice_enabled", "spice_turns", "inject_datetime", "custom_context",
    "llm_primary", "llm_model",
    "trim_color",
]


def get_persona_settings_keys() -> list:
    """Return the full list of keys a persona can bundle. Dynamic — includes
    all scope setting keys currently in SCOPE_REGISTRY at call time.

    Fixes a pre-existing silent bug where telegram_scope and discord_scope
    were missing from the hardcoded list and got stripped from saved personas.
    """
    from core.chat.function_manager import scope_setting_keys
    return _STATIC_PERSONA_SETTINGS_KEYS + scope_setting_keys()


def __getattr__(name):
    """Module-level backcompat shim for `from ... import PERSONA_SETTINGS_KEYS`.

    External read-only callers still work — they get the current dynamic list.
    Tests that PATCH this name must migrate to patching `get_persona_settings_keys`.
    """
    if name == 'PERSONA_SETTINGS_KEYS':
        return get_persona_settings_keys()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
        """Load personas from user file. Seeds from core defaults on first run only.

        After first run, user/personas/personas.json is authoritative — deleted
        personas stay deleted. Use merge_defaults() for explicit user-initiated
        restore of built-ins from the Backup UI.
        """
        user_path = self.USER_DIR / "personas.json"

        if user_path.exists():
            # User file is authoritative — no re-seeding on boot
            try:
                with open(user_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._personas = {k: v for k, v in data.items() if not k.startswith('_')}
                logger.info(f"Loaded {len(self._personas)} personas")
                return
            except Exception as e:
                # 2026-04-22 fix D1 — pre-fix, `self._personas = {}` ran here
                # and the next persona create/update/delete saved the empty
                # dict OVER the corrupt file, permanently destroying whatever
                # salvageable content it had. Now: preserve the corrupt file
                # (timestamped) so manual recovery is possible, and fall
                # through to first-run seed from core defaults. User gets a
                # working Sapphire + their corrupt file on disk for forensics.
                logger.error(f"[PERSONA] Failed to load user personas: {e}")
                try:
                    from datetime import datetime as _dt
                    ts = _dt.now().strftime('%Y%m%d_%H%M%S')
                    preserved = user_path.with_name(user_path.name + f'.corrupt-{ts}')
                    user_path.rename(preserved)
                    logger.warning(f"[PERSONA] Corrupt personas.json preserved at {preserved}")
                except Exception as rename_err:
                    logger.error(f"[PERSONA] Could not preserve corrupt file: {rename_err}")
                try:
                    from core.event_bus import publish, Events
                    publish(Events.CONTINUITY_TASK_ERROR, {
                        "task": "Personas",
                        "error": f"personas.json was corrupt — preserved as backup, "
                                 f"seeded from core defaults. Check user/personas/ "
                                 f"for the corrupt-* backup to recover custom "
                                 f"personas manually.",
                    })
                except Exception:
                    pass
                # Fall through to first-run seed path below — safer than
                # leaving _personas = {} where the next save wipes the file.

        # First run — seed from core defaults
        core_path = self.BASE_DIR / "personas.json"
        try:
            with open(core_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._personas = {k: v for k, v in data.items() if not k.startswith('_')}
        except Exception as e:
            logger.error(f"Failed to load core personas: {e}")
            self._personas = {}

        # Copy built-in avatars into user dir
        for persona in self._personas.values():
            self._seed_avatar(persona.get('avatar'))

        if self._personas:
            self._save_to_user()
            logger.info(f"First run — seeded {len(self._personas)} personas from defaults")
        else:
            logger.info("Loaded 0 personas")

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
            tmp_path = user_path.with_suffix('.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            tmp_path.replace(user_path)
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
        """Delete a persona.

        2026-04-22 fix D3 — detect chats that have this persona set as their
        active persona, rewrite them to 'default' BEFORE we delete, and log
        a WARN + publish SETTINGS_CHANGED event. Pre-fix: chats silently
        pointed at a now-missing persona; next load fell through to whatever
        default behavior the resolver chose — the silent-default class we've
        been closing all week.
        """
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

            # Active-persona handoff. Rewrite any chat that points at this
            # persona to 'default' so activation doesn't silently no-op.
            try:
                from core.api_fastapi import get_system
                system = get_system()
                sm = getattr(getattr(system, 'llm_chat', None), 'session_manager', None)
                if sm is not None and hasattr(sm, 'list_chats'):
                    affected_chats = []
                    for chat_entry in sm.list_chats() or []:
                        chat_name = chat_entry.get('name') if isinstance(chat_entry, dict) else str(chat_entry)
                        if not chat_name:
                            continue
                        try:
                            settings = sm.read_chat_settings(chat_name) or {}
                            if settings.get('persona') == name:
                                sm.update_chat_settings(chat_name, {'persona': 'default'})
                                affected_chats.append(chat_name)
                        except Exception as chat_err:
                            logger.debug(f"[PERSONA] Could not check chat '{chat_name}': {chat_err}")
                    if affected_chats:
                        logger.warning(
                            f"[PERSONA] Deleted persona '{name}' was active in "
                            f"{len(affected_chats)} chat(s): {affected_chats}. "
                            f"Each was reset to 'default'."
                        )
                        try:
                            from core.event_bus import publish, Events
                            publish(Events.SETTINGS_CHANGED, {
                                "key": "chat_persona_fallback",
                                "value": "default",
                                "reason": f"deleted_persona:{name}",
                                "affected_chats": affected_chats,
                            })
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[PERSONA] Active-persona handoff check skipped: {e}")

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

    def delete_avatar(self, name: str) -> bool:
        """Remove avatar for a persona, reverting to fallback."""
        if name not in self._personas:
            return False

        with self._lock:
            avatar = self._personas[name].get("avatar")
            if avatar:
                avatar_path = self.USER_AVATARS / avatar
                if avatar_path.exists():
                    try:
                        avatar_path.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete avatar file {avatar}: {e}")
            self._personas[name]["avatar"] = None
            self._personas[name]["avatar_full"] = None
            return self._save_to_user()

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
                else:
                    # Seed avatar for existing personas that are missing theirs
                    core_avatar = persona.get('avatar')
                    if core_avatar and not self._personas[name].get('avatar'):
                        self._personas[name]['avatar'] = core_avatar
                        self._seed_avatar(core_avatar)
                        added += 1

            if added > 0:
                self._save_to_user()
                logger.info(f"Merged {added} new personas/avatars from defaults")

        return added

    # === Helpers ===

    def _sanitize_name(self, name: str) -> str:
        """Sanitize persona name."""
        if not name or not name.strip():
            return ""
        safe = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
        return safe.replace(' ', '_').lower()

    def _clean_settings(self, settings: dict) -> dict:
        """Only keep recognized settings keys. Calls get_persona_settings_keys()
        so new plugin scopes are picked up without restart.

        2026-04-22 fix D2 — preserve keys matching the `*_scope` pattern even
        if not currently in the allowlist. Rationale: plugin-provided scope
        keys enter SCOPE_REGISTRY when the plugin loads. If the plugin is
        temporarily unloaded (boot race, user-toggled off during upgrade),
        persona.update() strict-strips those keys from disk — silently
        losing the user's scope binding that would have come back when the
        plugin reloaded. Keep unknown *_scope keys in the persona; log at
        DEBUG so it's visible but not alarming.
        """
        allowed = set(get_persona_settings_keys())
        result = {}
        preserved_scope_keys = []
        for k, v in settings.items():
            if k in allowed:
                result[k] = v
            elif k.endswith('_scope'):
                # Looks like a plugin-provided scope key. Keep it — the plugin
                # may come back. Better to hold a stale binding than silently
                # wipe the user's intent.
                result[k] = v
                preserved_scope_keys.append(k)
        if preserved_scope_keys:
            logger.debug(
                f"[PERSONA] Preserved unknown scope keys not in current "
                f"SCOPE_REGISTRY: {preserved_scope_keys}"
            )
        return result


# Singleton instance
persona_manager = PersonaManager()
