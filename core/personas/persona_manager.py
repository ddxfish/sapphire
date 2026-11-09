# core/personas/persona_manager.py
import logging
import json
import shutil
import threading
from pathlib import Path
from core.fs_utils import replace_with_retry

logger = logging.getLogger(__name__)

# Static (non-scope) persona settings keys. Scope keys are merged dynamically
# from SCOPE_REGISTRY by get_persona_settings_keys() — function, not constant, because
# plugins can register new scopes at runtime and a module-import snapshot would miss them.
_STATIC_PERSONA_SETTINGS_KEYS = [
    "prompt", "toolset", "spice_set", "voice", "pitch", "speed",
    "spice_enabled", "spice_turns", "inject_datetime", "custom_context",
    "llm_primary", "llm_model",
    "trim_color", "background", "motion",
]


def get_persona_settings_keys() -> list:
    """Return the full list of keys a persona can bundle. Dynamic — includes
    all scope setting keys currently in SCOPE_REGISTRY at call time.

    Fixes a pre-existing silent bug where telegram_scope and discord_scope
    were missing from the hardcoded list and got stripped from saved personas.
    """
    from core.chat.function_manager import scope_setting_keys
    return _STATIC_PERSONA_SETTINGS_KEYS + scope_setting_keys()


def _scopes_off() -> dict:
    """Every scope a persona can carry, set to 'none'. A NEW persona starts
    with nothing bound: it used to copy the active chat's scopes (Sapphire's
    memory, her email and bot) or read as 'default' — an account most
    installs never made (Krem, 2026-11-08). The user picks what it gets."""
    from core.chat.function_manager import scope_setting_keys
    return {k: 'none' for k in scope_setting_keys()}


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
                with open(user_path, 'r', encoding='utf-8-sig') as f:
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
            with open(core_path, 'r', encoding='utf-8-sig') as f:
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
            replace_with_retry(tmp_path, user_path)
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

    def repoint_prompt(self, old_prompt: str, new_prompt: str) -> list:
        """Prompt-rename upkeep: personas whose prompt was `old_prompt`
        now use `new_prompt`. Returns the touched persona names."""
        with self._lock:
            hit = [n for n, p in self._personas.items()
                   if (p.get('settings') or {}).get('prompt') == old_prompt]
            for n in hit:
                self._personas[n]['settings']['prompt'] = new_prompt
            if hit:
                self._save_to_user()
        return hit

    @staticmethod
    def _vault_ref_sync(new_prompt, old_prompt):
        """Personas are one of the three referrer classes of the vault
        references index. Called OUTSIDE self._lock, best-effort."""
        try:
            from core.prompt_crud import vault_ref_sync
            vault_ref_sync(new_prompt, old_prompt)
        except Exception:
            pass

    def create(self, name: str, data: dict) -> bool:
        """Create a new persona."""
        safe_name = self._sanitize_name(name)
        if not safe_name:
            return False
        if safe_name in self._personas:
            return False

        with self._lock:
            # avatar is server-owned (set_avatar) — a client-supplied
            # filename was later joined onto the avatars dir and unlinked.
            persona = {
                "name": safe_name,
                "tagline": data.get("tagline", ""),
                "avatar": None,
                "avatar_full": None,
                # Scopes the caller names win; the rest start off.
                "settings": self._clean_settings(
                    {**_scopes_off(), **(data.get("settings") or {})})
            }
            self._personas[safe_name] = persona
            ok = self._save_to_user()
        if ok:
            self._vault_ref_sync(persona["settings"].get("prompt"), None)
        return ok

    def rename_problem(self, name: str, new_name) -> str | None:
        """Why renaming `name` to `new_name` can't happen, or None when it
        can (a same-name no-op is fine). The route turns it into a 400/409."""
        safe = self._sanitize_name(new_name or '')
        if not safe:
            return "That name has no usable characters (letters, digits, space, - or _)"
        if safe != name and safe in self._personas:
            return f"A persona named '{safe}' already exists"
        return None

    def update(self, name: str, data: dict) -> bool:
        """Update an existing persona. A rename that can't land refuses the
        WHOLE update — it used to save the settings, skip the rename, and
        report success, so the editor chased the other persona's name and
        the next edit overwrote it (playwright hunt 2026-11-08)."""
        if name not in self._personas:
            return False
        if "name" in data and self.rename_problem(name, data["name"]):
            return False

        with self._lock:
            persona = self._personas[name]
            old_prompt = (persona.get("settings") or {}).get("prompt") \
                if "settings" in data else None
            if "tagline" in data:
                persona["tagline"] = data["tagline"]
            if "settings" in data:
                # MERGE, never replace: the editor only sends what it renders
                # and imports only what a card carries — a replace wiped motion,
                # disabled plugins' scopes, and on card overwrite every scope
                # (memory then landed in 'default'). Hunt 2026-11-08.
                persona["settings"] = self._clean_settings(
                    {**(persona.get("settings") or {}), **(data["settings"] or {})})
            if "name" in data:
                # Rename (validated above; same-name is a no-op)
                new_name = self._sanitize_name(data["name"])
                if new_name != name:
                    persona["name"] = new_name
                    self._personas[new_name] = persona
                    del self._personas[name]
            ok = self._save_to_user()
        if ok and "settings" in data:
            self._vault_ref_sync(persona["settings"].get("prompt"), old_prompt)
        if ok and persona.get("name") != name:
            from core import referrers
            referrers.persona_renamed(name, persona.get("name"))
        return ok

    def delete(self, name: str) -> bool:
        """Delete a persona. Every holder of the name is released through
        core/referrers.persona_deleted (fix D3, 2026-04-22, moved there and
        corrected 2026-11-08: chats reset to '' — 'default' was a dangling
        name the next persona called "default" would have adopted)."""
        if name not in self._personas:
            return False

        with self._lock:
            persona = self._personas[name]

            old_prompt = (persona.get("settings") or {}).get("prompt")
            del self._personas[name]
            ok = self._save_to_user()
            if ok:
                self._unlink_avatar_if_orphan(persona.get("avatar"))
        self._vault_ref_sync(None, old_prompt)
        if ok:
            # Chats, defaults, favorites, API keys — core/referrers.py. Runs
            # AFTER the save: the old inline handoff reset chats first, so a
            # failed save left chats pointing nowhere.
            from core import referrers
            referrers.persona_deleted(name)
        return ok

    def duplicate(self, name: str, new_name: str) -> bool:
        """Duplicate a persona with a new name."""
        if name not in self._personas:
            return False
        safe_new = self._sanitize_name(new_name)
        if not safe_new or safe_new in self._personas:
            return False

        import copy
        persona = copy.deepcopy(self._personas[name])
        # A copy starts with every mind scope off, like a new persona: a
        # shared memory scope had her reading the original's memories.
        kept = {k: v for k, v in (persona.get("settings") or {}).items()
                if not k.endswith("_scope")}
        persona["settings"] = self._clean_settings({**kept, **_scopes_off()})
        src_prompt = persona["settings"].get("prompt")
        fork = self._fork_prompt(src_prompt, safe_new)
        if fork:
            persona["settings"]["prompt"] = fork
        with self._lock:
            persona["name"] = safe_new
            # Don't copy avatar — let user upload their own
            persona["avatar"] = None
            persona["avatar_full"] = None
            self._personas[safe_new] = persona
            ok = self._save_to_user()
            if not ok:
                del self._personas[safe_new]
        if not ok and fork:
            from core import prompt_crud
            prompt_crud.delete_prompt(fork, reason="persona duplicate failed")
        if ok:
            self._vault_ref_sync(persona["settings"].get("prompt"), None)
        return ok

    @staticmethod
    def _fork_prompt(src, base):
        """A duplicate gets its OWN prompt and character piece. Sharing the
        source's made the copy introduce itself as the original, an edit to
        'her' character rewrote the original, and her card shipped under the
        original's prompt name (Teri's Seraphina, 2026-11-08). Other pieces
        stay shared. Returns the fork's name, or None to keep the pointer
        (no prompt, a sentinel, or one the locked vault hides)."""
        if not src or src in ("default", "__story__"):
            return None
        from core import prompt_crud as pc
        from core.prompt_manager import prompt_manager
        data = pc.get_prompt(src)
        if not data:
            return None

        def free(problem):
            return next((n for n in [base] + [f"{base}-{i}" for i in range(2, 100)]
                         if not problem(n)), None)

        name = free(pc.prompt_name_problem)
        if not name:
            return None
        new = {"type": data["type"], "privacy_required": data.get("privacy_required", False)}
        piece = None
        if data["type"] == "monolith":
            new["content"] = data["content"]
        else:
            comps = dict(data["components"])
            char = comps.get("character")
            text = prompt_manager.components.get("character", {}).get(char) \
                if isinstance(char, str) else None
            if isinstance(text, str):
                piece = free(lambda k: pc.piece_key_problem("character", k))
                if piece and pc.save_component("character", piece, text,
                                               reason=f"copied from '{char}' for '{base}'")[0]:
                    comps["character"] = piece
                else:
                    piece = None
            new["components"] = comps
        ok, msg = pc.save_prompt(name, new, allow_overwrite=False,
                                 reason=f"forked from '{src}' for persona '{base}'")
        if not ok:
            logger.warning(f"Duplicate kept the shared prompt '{src}': {msg}")
            if piece:
                pc.delete_component("character", piece, reason="prompt fork failed")
            return None
        return name

    def create_from_settings(self, name: str, chat_settings: dict) -> bool:
        """Create a persona from current chat settings."""
        safe_name = self._sanitize_name(name)
        if not safe_name or safe_name in self._personas:
            return False

        with self._lock:
            # The chat's prompt/voice/model come along; its scopes don't —
            # including keys of a plugin that's off right now (goal/knowledge).
            kept = {k: v for k, v in chat_settings.items() if not k.endswith('_scope')}
            settings = self._clean_settings({**kept, **_scopes_off()})
            persona = {
                "name": safe_name,
                "tagline": "",
                "avatar": None,
                "avatar_full": None,
                "settings": settings
            }
            self._personas[safe_name] = persona
            ok = self._save_to_user()
        if ok:
            self._vault_ref_sync(settings.get("prompt"), None)
        return ok

    # === One-time: phantom 'default' scopes ===

    # Scopes where 'default' is a real bucket memories land in (created on
    # first write) — never released here.
    _STORE_SCOPES = ('memory', 'people', 'goal', 'knowledge')
    PHANTOM_MARKER = ".scope_phantoms_released"

    def release_phantom_scopes(self, lists: dict) -> dict | None:
        """Set account scopes stuck on 'default' to 'none' where that account
        doesn't exist. The old dropdown offered a 'default' option for every
        scope and saved it, so personas pointed email/bitcoin/github at an
        account nobody made (Krem, 2026-11-08). `lists` = {scope_key: [real
        values]} as the scope endpoints return them. An empty or missing list
        is skipped (a failed fetch must not look like "no accounts"). Runs once
        per install (marker file); backs up personas.json first. Returns
        {persona: [setting keys]} changed, or None when already done."""
        marker = self.USER_DIR / self.PHANTOM_MARKER
        if marker.exists():
            return None
        changed = {}
        with self._lock:
            for key, values in (lists or {}).items():
                if key in self._STORE_SCOPES or not values or 'default' in values:
                    continue
                setting = f"{key}_scope"
                for name, p in self._personas.items():
                    st = p.get("settings") or {}
                    if st.get(setting) == 'default':
                        changed.setdefault(name, []).append(setting)
            if changed:
                src = self.USER_DIR / "personas.json"
                if src.exists():
                    import shutil
                    shutil.copy2(src, src.with_name("personas.json.pre-scope-release.bak"))
                for name, keys in changed.items():
                    for k in keys:
                        self._personas[name]["settings"][k] = 'none'
                if not self._save_to_user():
                    raise RuntimeError("personas.json save failed")
            self.USER_DIR.mkdir(parents=True, exist_ok=True)
            marker.write_text("phantom 'default' scopes released\n", encoding='utf-8')
        if changed:
            logger.warning(f"[PERSONAS] released phantom 'default' scopes: {changed}")
        return changed

    # === Avatar ===

    def delete_avatar(self, name: str) -> bool:
        """Remove avatar for a persona, reverting to fallback."""
        if name not in self._personas:
            return False

        with self._lock:
            avatar = self._personas[name].get("avatar")
            self._personas[name]["avatar"] = None
            self._personas[name]["avatar_full"] = None
            ok = self._save_to_user()
            if ok:
                self._unlink_avatar_if_orphan(avatar)
            return ok

    def set_avatar(self, name: str, data: bytes) -> str | None:
        """Save a new avatar for a persona; returns its filename (None on
        failure). One fresh filename per upload: keyed by the persona name,
        a rename left the file behind and the next persona to take that
        name overwrote — then on delete removed — the renamed persona's
        picture (playwright hunt 2026-11-08). The replaced file goes only
        when no other persona still holds it."""
        if name not in self._personas:
            return None
        import secrets
        filename = f"{name}-{secrets.token_hex(4)}.webp"
        path = self.USER_AVATARS / filename
        try:
            path.write_bytes(data)
            with self._lock:
                if name not in self._personas:      # deleted meanwhile
                    path.unlink(missing_ok=True)
                    return None
                old = self._personas[name].get("avatar")
                self._personas[name]["avatar"] = filename
                if not self._save_to_user():
                    self._personas[name]["avatar"] = old
                    path.unlink(missing_ok=True)
                    return None
                self._unlink_avatar_if_orphan(old)
            return filename
        except Exception as e:
            logger.error(f"Failed to save avatar for {name}: {e}")
            return None

    def _unlink_avatar_if_orphan(self, filename):
        """Remove a user avatar file no persona references anymore. Caller
        holds self._lock. Built-in avatars (static dir) are never touched."""
        if not self._is_bare_filename(filename) \
                or any(p.get("avatar") == filename for p in self._personas.values()):
            return
        path = self.USER_AVATARS / filename
        try:
            path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to delete avatar {filename}: {e}")

    def get_avatar_path(self, name: str) -> Path | None:
        """Get filesystem path to persona's avatar."""
        persona = self._personas.get(name)
        if not persona or not persona.get("avatar"):
            return None

        avatar_file = persona["avatar"]
        if not self._is_bare_filename(avatar_file):
            return None

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
            with open(core_path, 'r', encoding='utf-8-sig') as f:
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

    @staticmethod
    def _is_bare_filename(f) -> bool:
        """A plain file name — no separators, no '..'. Legacy records may
        hold anything; never join one onto a directory unchecked."""
        return isinstance(f, str) and bool(f) and f not in ('.', '..') \
            and Path(f).name == f and '\\' not in f

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
        if 'llm_model' in result:
            # The pair rule (2026-10-07): a model beside an auto/none provider
            # is dead at resolve time and lies to every picker — store ''.
            from core.chat.llm_providers.resolve import normalize_pair
            result['llm_model'] = normalize_pair(result.get('llm_primary'), result['llm_model'])[1]
        return result


# Singleton instance
persona_manager = PersonaManager()
