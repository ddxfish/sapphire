import logging
import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

class PromptManager:
    """Manages prompt templates with hot-reload. Uses ONLY user/prompts/ directory."""
    
    def __init__(self):
        # Single source of truth - user/prompts/ only
        self.CORE_DIR = Path(__file__).parent / "prompt_defaults"
        self.USER_DIR = Path(__file__).parent.parent / "user" / "prompts"
        
        self._components = {}
        self._scenario_presets = {}
        self._monoliths = {}
        self._spices = {}
        self._spice_meta = {}
        self._disabled_categories = set()
        
        # RLock: mutation sites (prompt_crud, routes, meta tools) wrap
        # "mutate private dict + call saver" in one critical section, and the
        # saver acquires the same lock inside — a plain Lock would deadlock.
        self._lock = threading.RLock()
        self._watcher_thread = None
        self._watcher_running = False
        self._last_mtimes = {}
        self._active_preset_name = 'unknown'

        # 2026-04-22 fix E — load-failure tracking. If a load function fails
        # (corrupt JSON, mid-write read, etc.), the corresponding flag is set
        # True; the in-memory dict is preserved at its last-known-good state
        # rather than wiped to {}. save_* functions then refuse to persist
        # when the flag is True — prevents the wipe-then-write cascade where
        # a transient read failure became permanent disk state on next save.
        self._load_failed = {
            'pieces': False,
            'monoliths': False,
            'spices': False,
        }
        
        # Ensure user directory exists (bootstrap should have run, but be safe)
        self.USER_DIR.mkdir(parents=True, exist_ok=True)

        self._load_all()
        self._audit_seed()   # silent — boot state is the baseline, not a change
    
    def _load_all(self):
        """Load all prompt data from user/prompts/ JSON files."""
        self._load_pieces()
        self._load_monoliths()
        self._load_spices()
    
    def _load_pieces(self):
        """Load prompt pieces from user/prompts/."""
        path = self.USER_DIR / "prompt_pieces.json"

        if not path.exists():
            # Missing file is a FAILURE, same as corrupt: without the flag,
            # a file briefly absent (sync/rename in flight) wiped the dicts
            # clean and the next save persisted an EMPTY library over the
            # recreated file. bootstrap creates the files before this loads.
            logger.error(f"prompt_pieces.json not found at {path} — refusing saves until it returns")
            self._components = {}
            self._scenario_presets = {}
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['pieces'] = True
            return

        try:
            # utf-8-sig: PowerShell's `-Encoding utf8` and old Notepad write
            # a BOM; plain utf-8 rejects it and the store looks corrupt.
            # No-op for BOM-less files.
            with open(path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)

            # Normalize like _load_monoliths: build into locals, drop+warn
            # non-conforming entries. A hand-edited piece with a dict/list
            # value used to load fine and detonate later in the renderer,
            # audit snapshot, and list routes.
            raw_components = data.get("components", {})
            new_components = {}
            if isinstance(raw_components, dict):
                for ctype, entries in raw_components.items():
                    if not isinstance(entries, dict):
                        logger.warning(f"Skipping component type '{ctype}' with "
                                       f"unexpected shape: {type(entries).__name__}")
                        continue
                    clean = {}
                    for k, v in entries.items():
                        if isinstance(v, str):
                            clean[k] = v
                        else:
                            logger.warning(f"Skipping piece {ctype}/{k} with "
                                           f"non-string value ({type(v).__name__})")
                    new_components[ctype] = clean

            raw_presets = data.get("scenario_presets", {})
            new_presets = {}
            if isinstance(raw_presets, dict):
                for pname, preset in raw_presets.items():
                    # Component values are str/list; _privacy_required is bool.
                    if isinstance(preset, dict) and all(
                            isinstance(v, (str, list, bool)) for v in preset.values()):
                        new_presets[pname] = preset
                    else:
                        logger.warning(f"Skipping preset '{pname}' with unexpected shape")

            self._components = new_components
            self._scenario_presets = new_presets
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['pieces'] = False
            logger.info(f"Loaded prompt pieces: {len(self._components)} component types")
        except Exception as e:
            # 2026-04-22 fix E1 — preserve in-memory state. Pre-fix we set
            # _components = {} here; next save_components() persisted empty
            # dict over the file, permanently wiping the user's pieces. Now:
            # leave the in-memory state alone, flag the load as failed, let
            # save_components() refuse until a successful reload.
            logger.error(f"[PROMPTS] Failed to load prompt pieces — preserving last-known state: {e}")
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['pieces'] = True
    
    def _load_monoliths(self):
        """Load monolith prompts from user/prompts/."""
        path = self.USER_DIR / "prompt_monoliths.json"

        if not path.exists():
            logger.error(f"prompt_monoliths.json not found at {path} — refusing saves until it returns")
            self._monoliths = {}
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['monoliths'] = True
            return

        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                raw_data = json.load(f)

            # Normalize format: support both old (string) and new (object) formats.
            # Build into a local dict first so a mid-iteration failure doesn't
            # leave self._monoliths half-populated.
            new_monoliths = {}
            for k, v in raw_data.items():
                if k.startswith('_'):
                    continue
                if isinstance(v, str):
                    new_monoliths[k] = {'content': v, 'privacy_required': False}
                elif isinstance(v, dict):
                    new_monoliths[k] = v
                else:
                    logger.warning(f"Skipping monolith '{k}' with unexpected type: {type(v)}")

            self._monoliths = new_monoliths
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['monoliths'] = False
            logger.info(f"Loaded {len(self._monoliths)} monolith prompts")
        except Exception as e:
            # 2026-04-22 fix E1 — preserve state; flag failure. See _load_pieces comment.
            logger.error(f"[PROMPTS] Failed to load monoliths — preserving last-known state: {e}")
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['monoliths'] = True
    
    def _load_spices(self):
        """Load spice pool from user/prompts/."""
        path = self.USER_DIR / "prompt_spices.json"

        if not path.exists():
            logger.error(f"prompt_spices.json not found at {path} — refusing saves until it returns")
            self._spices = {}
            self._spice_meta = {}
            self._disabled_categories = set()
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['spices'] = True
            return

        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                raw_data = json.load(f)

            self._disabled_categories = set(raw_data.get('_disabled_categories', []))
            self._spice_meta = raw_data.get('_meta', {})
            self._spices = {k: v for k, v in raw_data.items() if not k.startswith('_') and isinstance(v, list)}
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['spices'] = False
            logger.info(f"Loaded spice pool: {len(self._spices)} categories, {len(self._disabled_categories)} disabled")
        except Exception as e:
            # 2026-04-22 fix E1 — preserve state; flag failure.
            logger.error(f"[PROMPTS] Failed to load spices — preserving last-known state: {e}")
            if not hasattr(self, '_load_failed'):
                self._load_failed = {'pieces': False, 'monoliths': False, 'spices': False}
            self._load_failed['spices'] = True
    
    def _replace_templates(self, text: str) -> str:
        """Replace {ai_name} and {user_name} with values from settings."""
        if not text:
            return text
        
        try:
            from core.settings_manager import settings
            ai_name = 'Sapphire'
            user_name = settings.get('DEFAULT_USERNAME', 'Human Protagonist')
            # Sanitize curly brackets to prevent template injection
            ai_name = ai_name.replace('{', '').replace('}', '')
            user_name = user_name.replace('{', '').replace('}', '')
            return text.replace('{ai_name}', ai_name).replace('{user_name}', user_name)
        except Exception as e:
            logger.error(f"Template replacement failed: {e}")
            return text
    
    def _write_json_atomic(self, target_path, data, **dump_kwargs):
        """fsync'd atomic JSON write + watcher-echo suppression.

        fsync: without it a torn rename (OOM-kill, power loss) leaves a
        0-byte store and the next boot runs the hardcoded fallback.
        Echo suppression: record the mtime we wrote in _last_mtimes so the
        file watcher doesn't treat our own save as an external edit and
        reload 2.5s later — that echo sat behind a whole class of
        lost-edit races (watcher rebind swallowing in-flight saves,
        reverting pieces set between polls). The tmp file's mtime survives
        the rename, so recording it BEFORE replace leaves zero window.
        """
        from core.fs_utils import replace_with_retry
        tmp_path = target_path.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        mtime = tmp_path.stat().st_mtime
        replace_with_retry(tmp_path, target_path)
        if not hasattr(self, '_last_mtimes'):
            self._last_mtimes = {}
        self._last_mtimes[str(target_path)] = mtime

    def reload(self, audit_reason='file reload'):
        """Reload all prompt data from disk. Diffs the reloaded state against
        the audit snapshot — an out-of-band edit (vim, factory reset, restored
        backup) becomes ledger rows instead of silence. Our own savers refresh
        the snapshot first, so the file watcher's reload after a normal save
        emits nothing."""
        with self._lock:
            self._load_all()
            logger.info("Prompt data reloaded")
        self._audit_diff(('monoliths', 'presets', 'components'),
                         reason=audit_reason)
        # A disk edit under the ACTIVE prompt must reach the running chat —
        # before this, a vim edit to the active monolith stayed invisible
        # until re-activation. No-op when the system isn't up yet.
        try:
            from .prompt_crud import revalidate_active
            revalidate_active(reason=audit_reason)
        except Exception as e:
            logger.warning(f"[PROMPTS] post-reload revalidate skipped: {e}")
    
    def start_file_watcher(self):
        """Start background file watcher for user prompts."""
        if self._watcher_thread is not None and self._watcher_thread.is_alive():
            logger.warning("File watcher already running")
            return
        
        self._watcher_running = True
        self._watcher_thread = threading.Thread(
            target=self._file_watcher_loop,
            daemon=True,
            name="PromptFileWatcher"
        )
        self._watcher_thread.start()
        logger.info("Prompt file watcher started")
    
    def stop_file_watcher(self):
        """Stop the file watcher."""
        if self._watcher_thread is None:
            return
        
        self._watcher_running = False
        if self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=5)
        logger.info("Prompt file watcher stopped")
    
    def _file_watcher_loop(self):
        """Watch user prompt files for changes."""
        watch_files = [
            self.USER_DIR / "prompt_pieces.json",
            self.USER_DIR / "prompt_monoliths.json",
            self.USER_DIR / "prompt_spices.json"
        ]
        
        while self._watcher_running:
            try:
                time.sleep(2)
                
                changed = False
                for path in watch_files:
                    if not path.exists():
                        continue
                    
                    current_mtime = path.stat().st_mtime
                    last_mtime = self._last_mtimes.get(str(path))
                    
                    if last_mtime is not None and current_mtime != last_mtime:
                        logger.info(f"Detected change in {path.name}")
                        changed = True
                    
                    self._last_mtimes[str(path)] = current_mtime
                
                if changed:
                    time.sleep(0.5)  # Debounce
                    self.reload()
            
            except Exception as e:
                logger.error(f"File watcher error: {e}")
                time.sleep(5)
    
    def assemble_from_components(self, components):
        """Render prompt text from a component-key dict. THE single renderer.

        Until 2026-08-09 there were two: this one emitted labeled lines
        ("Goals: ...") for the editor/CRUD paths while prompt_state's
        assemble_prompt() emitted unlabeled prose for the runtime — so the
        live prompt silently changed format at the first spice rotation
        after every activation. Now prompt_state.assemble_prompt() delegates
        here; previews, char counts, and the live prompt are byte-identical.
        Format is the runtime's (the one chats actually marinated in):
        prose parts, location as a sentence, scenario skipped when
        'default', extras/emotions as their own parts, newline-joined.
        No template replacement here — callers that go live apply
        _replace_templates; export paths keep the placeholders."""
        # Merged view — pack pieces resolve here too (user wins collisions)
        comps = self.components

        def _text(comp_type, key):
            # Tolerate junk from hand-edited stores / third-party bundles:
            # a non-string key is unhashable or nonsense, a non-string value
            # would crash .strip() below — both render blank (ruling 3:
            # missing/bad pieces show blank and heal when fixed).
            if not key or not isinstance(key, str):
                return ""
            val = comps.get(comp_type, {}).get(key, "")
            return val if isinstance(val, str) else ""

        def _keys(comp_type):
            keys = components.get(comp_type) or []
            if isinstance(keys, str):
                return [keys]
            return keys if isinstance(keys, list) else []

        parts = [_text('character', components.get('character', 'sapphire'))]

        location = _text('location', components.get('location'))
        if location and location.strip():
            parts.append(f"You are currently {location}.")

        parts.append(_text('relationship', components.get('relationship')))
        parts.append(_text('goals', components.get('goals')))
        parts.append(_text('format', components.get('format')))

        # The 'default' scenario is deliberately silent — matches what the
        # runtime renderer always did.
        scenario_key = components.get('scenario')
        if scenario_key and scenario_key != 'default':
            parts.append(_text('scenario', scenario_key))

        for extra in _keys('extras'):
            parts.append(_text('extras', extra))
        for emotion in _keys('emotions'):
            parts.append(_text('emotions', emotion))

        return "\n".join(p for p in parts if p and p.strip())
    
    def save_scenario_presets(self, reason=None, audit=True) -> bool:
        """Save scenario presets to user/prompts/prompt_pieces.json.
        Returns False on refusal (load-failed latch) — callers surface it;
        a silent None here meant every save after one bad load reported
        success while writing nothing."""
        with self._lock:
            # Scenario presets live in prompt_pieces.json. If its load failed
            # we gate on the 'pieces' flag. Fix E2 2026-04-22.
            if getattr(self, '_load_failed', {}).get('pieces'):
                logger.error(
                    "[PROMPTS] REFUSING to save scenario presets — last load "
                    "of prompt_pieces.json failed. Persisting would overwrite "
                    "a potentially recoverable disk file."
                )
                return False
            target_path = self.USER_DIR / "prompt_pieces.json"

            # Load existing data
            try:
                with open(target_path, 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
            except Exception:
                data = {"_comment": "User prompt pieces", "components": {}, "scenario_presets": {}}

            # Update scenario_presets section
            data['scenario_presets'] = self._scenario_presets

            self._write_json_atomic(target_path, data)
            logger.info(f"Saved scenario presets to {target_path}")
        self._audit_diff(('presets',), reason, audit)
        return True

    def save_monoliths(self, reason=None, audit=True) -> bool:
        """Save monoliths to user/prompts/prompt_monoliths.json.
        Returns False on refusal (load-failed latch)."""
        with self._lock:
            # 2026-04-22 fix E2 — refuse to save if the last load failed.
            # Pre-fix, a failed load left self._monoliths = {} and the next
            # save_monoliths() call persisted the empty dict over the
            # (possibly still-intact) disk file — permanent wipe from a
            # transient read failure.
            if getattr(self, '_load_failed', {}).get('monoliths'):
                logger.error(
                    "[PROMPTS] REFUSING to save monoliths — last load failed. "
                    "In-memory state may be stale; persisting it would overwrite "
                    "a potentially recoverable disk file. Inspect "
                    "user/prompts/prompt_monoliths.json and call reload() "
                    "after the file is valid JSON again."
                )
                return False
            target_path = self.USER_DIR / "prompt_monoliths.json"

            # Load existing to preserve _comment
            try:
                with open(target_path, 'r', encoding='utf-8-sig') as f:
                    old_data = json.load(f)
                comment = old_data.get('_comment')
            except Exception:
                comment = "User monolith prompts"

            # Build fresh dict with new format
            data = {}
            if comment:
                data['_comment'] = comment
            # Ensure each monolith has the full object structure
            for name, mono in self._monoliths.items():
                if isinstance(mono, dict):
                    data[name] = mono
                else:
                    # Shouldn't happen, but handle gracefully
                    data[name] = {'content': str(mono), 'privacy_required': False}

            self._write_json_atomic(target_path, data)
            logger.info(f"Saved monoliths to {target_path}")
        self._audit_diff(('monoliths',), reason, audit)
        return True

    # ── Audit snapshots (prompt ledger, 2026-07-22) ──────────────────────────
    # The savers diff persisted state against these snapshots and emit one
    # core.audit event per changed item — so EVERY writer (routes, her tools,
    # persona import, merge, factory reset via reload) is covered at the
    # mutation layer instead of enumerated per caller. Boot seeds silently.

    def _audit_state(self):
        # Per-store try: one malformed entry must not raise, or every future
        # _audit_diff skips on the exception and the ledger goes permanently
        # dark (snapshot never refreshes). A failed store snapshots as {}
        # for this pass and recovers on the next clean one.
        out = {'monoliths': {}, 'presets': {}, 'components': {}}
        try:
            out['monoliths'] = {k: (v.get('content', '') if isinstance(v, dict) else str(v))
                                for k, v in self._monoliths.items()}
        except Exception as e:
            logger.warning(f"[PROMPTS] audit snapshot (monoliths) failed: {e}")
        try:
            out['presets'] = {k: json.dumps(v, sort_keys=True, ensure_ascii=False)
                              for k, v in self._scenario_presets.items()}
        except Exception as e:
            logger.warning(f"[PROMPTS] audit snapshot (presets) failed: {e}")
        try:
            out['components'] = {t: dict(kv) for t, kv in self._components.items()
                                 if isinstance(kv, dict)}
        except Exception as e:
            logger.warning(f"[PROMPTS] audit snapshot (components) failed: {e}")
        return out

    def _audit_seed(self):
        try:
            self._audit_snap = self._audit_state()
        except Exception:
            self._audit_snap = {'monoliths': {}, 'presets': {}, 'components': {}}

    def _audit_diff(self, stores, reason=None, audit=True):
        """Emit change events for the named stores vs the snapshot, then
        refresh the snapshot. audit=False refreshes silently — used when a
        higher-level event (a piece activation) already describes the change.
        Never raises: audit is evidence, not a gate."""
        try:
            from core.audit import emit, actor
            cur = self._audit_state()
            snap = getattr(self, '_audit_snap', None)
            if snap is None:
                self._audit_snap = cur
                return
            who = actor()
            for store in stores:
                old, new = snap.get(store, {}), cur.get(store, {})
                if audit and store == 'components':
                    for t in set(old) | set(new):
                        o, n = old.get(t, {}), new.get(t, {})
                        for key in set(o) | set(n):
                            if o.get(key, '') != n.get(key, ''):
                                emit({'kind': 'component', 'comp_type': t,
                                      'key': key, 'before': o.get(key, ''),
                                      'after': n.get(key, ''),
                                      'actor': who, 'reason': reason})
                elif audit:
                    for name in set(old) | set(new):
                        if old.get(name, '') != new.get(name, ''):
                            emit({'kind': 'monolith', 'name': name,
                                  'before': old.get(name, ''),
                                  'after': new.get(name, ''),
                                  'actor': who, 'reason': reason})
                snap[store] = new
        except Exception as e:
            logger.warning(f"[PROMPTS] audit diff skipped: {e}")

    def save_components(self, reason=None, audit=True) -> bool:
        """Save components to user/prompts/prompt_pieces.json.
        Returns False on refusal (load-failed latch)."""
        with self._lock:
            if getattr(self, '_load_failed', {}).get('pieces'):
                logger.error(
                    "[PROMPTS] REFUSING to save components — last load of "
                    "prompt_pieces.json failed. Persisting would overwrite a "
                    "potentially recoverable disk file. Fix the JSON and "
                    "reload() before saving."
                )
                return False
            target_path = self.USER_DIR / "prompt_pieces.json"

            # Load existing data
            try:
                with open(target_path, 'r', encoding='utf-8-sig') as f:
                    data = json.load(f)
            except Exception:
                data = {"_comment": "User prompt pieces", "components": {}, "scenario_presets": {}}

            # Update components section
            data['components'] = self._components

            self._write_json_atomic(target_path, data)
            logger.info(f"Saved components to {target_path}")
        self._audit_diff(('components',), reason, audit)
        return True

    def save_spices(self) -> bool:
        """Save spices to user/prompts/prompt_spices.json.
        Returns False on refusal (load-failed latch)."""
        with self._lock:
            if getattr(self, '_load_failed', {}).get('spices'):
                logger.error(
                    "[PROMPTS] REFUSING to save spices — last load of "
                    "prompt_spices.json failed. Persisting would overwrite a "
                    "potentially recoverable disk file. Fix the JSON and "
                    "reload() before saving."
                )
                return False
            target_path = self.USER_DIR / "prompt_spices.json"

            # Build data with metadata
            data = {"_comment": "User spices - managed via Spice Manager"}
            if self._spice_meta:
                data["_meta"] = self._spice_meta
            if self._disabled_categories:
                data["_disabled_categories"] = sorted(list(self._disabled_categories))
            data.update(self._spices)

            self._write_json_atomic(target_path, data, ensure_ascii=False)
            logger.info(f"Saved spices to {target_path}")
        return True
    
    def is_category_enabled(self, category: str) -> bool:
        """Check if a spice category is enabled."""
        return category not in self._disabled_categories
    
    def set_category_enabled(self, category: str, enabled: bool):
        """Enable or disable a spice category."""
        if enabled:
            self._disabled_categories.discard(category)
        else:
            self._disabled_categories.add(category)
        self.save_spices()
        logger.info(f"Spice category '{category}' {'enabled' if enabled else 'disabled'}")
    
    def get_enabled_spices(self) -> list:
        """Get all spices from enabled categories only."""
        return [
            spice 
            for category, spices in self._spices.items() 
            if category not in self._disabled_categories
            for spice in spices
        ]
    
    @property
    def disabled_categories(self):
        return self._disabled_categories
    
    # Read properties merge plugin prompt-packs (core/prompt_packs.py) and
    # the prompt vault (core/prompt_vault.py) under the user entries —
    # precedence packs < vault < user, USER WINS all name collisions.
    # Merged views are ALWAYS fresh dicts (the empty-overlay fast path was
    # killed 2026-08-10: it returned the LIVE dict, so mutability depended
    # on whether a pack happened to be loaded, and a no-pack boot would
    # never merge a vault unlock). ALL mutation paths must write the
    # private attrs (_components, _monoliths, _scenario_presets) — the
    # save_* methods persist only those, so pack/vault content can never
    # leak into user/prompts/*.json.
    @property
    def components(self):
        from core import prompt_packs, prompt_vault
        pack = prompt_packs.overlay_components()
        vault = prompt_vault.overlay_components()
        merged = {}
        for ctype in set(pack) | set(vault) | set(self._components):
            merged[ctype] = {**pack.get(ctype, {}), **vault.get(ctype, {}),
                             **self._components.get(ctype, {})}
        return merged

    @property
    def scenario_presets(self):
        from core import prompt_packs, prompt_vault
        return {**prompt_packs.overlay_presets(),
                **prompt_vault.overlay_presets(),
                **self._scenario_presets}

    @property
    def monoliths(self):
        from core import prompt_packs, prompt_vault
        return {**prompt_packs.overlay_monoliths(),
                **prompt_vault.overlay_monoliths(),
                **self._monoliths}
    
    @property
    def spices(self):
        return self._spices

    @property
    def spice_meta(self):
        return self._spice_meta

    # === Merge / Reset ===

    def _backup_user_files(self, backup_dir=None):
        """Backup user prompt files to timestamped directory. Returns backup path.
        Holds _lock so a concurrent save can't swap a file mid-copy."""
        if backup_dir:
            dest = Path(backup_dir) / "prompts"
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            # user/backups — NOT repo root. The old <root>/backups/ default
            # wasn't gitignored, so a prompts reset dropped the user's
            # private prompt library where `git add -A` would commit it.
            dest = self.USER_DIR.parent / "backups" / ts / "prompts"

        dest.mkdir(parents=True, exist_ok=True)

        with self._lock:
            for fname in ["prompt_pieces.json", "prompt_monoliths.json", "prompt_spices.json"]:
                src = self.USER_DIR / fname
                if src.exists():
                    shutil.copy2(src, dest / fname)

        logger.info(f"Backed up prompt files to {dest}")
        return str(dest.parent)  # return the timestamped dir, not /prompts

    def reset_to_defaults(self):
        """Backup user prompts then overwrite with core defaults. Returns True on success."""
        try:
            with self._lock:
                self._backup_user_files()
                for fname in ["prompt_pieces.json", "prompt_monoliths.json", "prompt_spices.json"]:
                    src = self.CORE_DIR / fname
                    if src.exists():
                        # copyfile, not copy2: copy2 preserves source metadata —
                        # a read-only attr from the install dir (Windows) makes
                        # the provisioned file unwritable, and the stale mtime
                        # confuses the file watcher.
                        shutil.copyfile(src, self.USER_DIR / fname)
                self.reload(audit_reason='reset to factory defaults')
            logger.info("Prompts reset to factory defaults")
            return True
        except Exception as e:
            logger.error(f"Failed to reset prompts: {e}")
            return False

    def merge_defaults(self, backup_dir=None):
        """Additive merge: add missing items from core defaults without touching existing ones.
        Holds _lock for the whole merge — the mid-merge saves used to re-arm
        the file watcher, whose reload rebound the dicts while later merge
        phases were still writing into the abandoned ones."""
        with self._lock:
            return self._merge_defaults_locked(backup_dir)

    def _merge_defaults_locked(self, backup_dir=None):
        try:
            backup_path = self._backup_user_files(backup_dir)
            added = {"components": 0, "presets": 0, "monoliths": 0, "spice_categories": 0}

            # --- Prompt pieces (components + scenario presets) ---
            core_pieces_path = self.CORE_DIR / "prompt_pieces.json"
            if core_pieces_path.exists():
                with open(core_pieces_path, 'r', encoding='utf-8') as f:
                    core_pieces = json.load(f)

                # Merge components: add missing keys per type
                core_components = core_pieces.get("components", {})
                for comp_type, entries in core_components.items():
                    if comp_type not in self._components:
                        self._components[comp_type] = entries
                        added["components"] += len(entries)
                    else:
                        for key, val in entries.items():
                            if key not in self._components[comp_type]:
                                self._components[comp_type][key] = val
                                added["components"] += 1

                # Merge scenario presets
                core_presets = core_pieces.get("scenario_presets", {})
                for name, preset in core_presets.items():
                    if name not in self._scenario_presets:
                        self._scenario_presets[name] = preset
                        added["presets"] += 1

                self.save_components(reason='merged app defaults')
                self.save_scenario_presets(reason='merged app defaults')

            # --- Monoliths ---
            core_mono_path = self.CORE_DIR / "prompt_monoliths.json"
            if core_mono_path.exists():
                with open(core_mono_path, 'r', encoding='utf-8') as f:
                    core_monoliths = json.load(f)

                for key, val in core_monoliths.items():
                    if key.startswith('_'):
                        continue
                    if key not in self._monoliths:
                        if isinstance(val, str):
                            self._monoliths[key] = {'content': val, 'privacy_required': False}
                        elif isinstance(val, dict):
                            self._monoliths[key] = val
                        added["monoliths"] += 1

                self.save_monoliths(reason='merged app defaults')

            # --- Spices ---
            core_spice_path = self.CORE_DIR / "prompt_spices.json"
            if core_spice_path.exists():
                with open(core_spice_path, 'r', encoding='utf-8') as f:
                    core_spices = json.load(f)

                for key, val in core_spices.items():
                    if key.startswith('_'):
                        # Merge _meta entries additively
                        if key == '_meta' and isinstance(val, dict):
                            if not self._spice_meta:
                                self._spice_meta = {}
                            for mk, mv in val.items():
                                if mk not in self._spice_meta:
                                    self._spice_meta[mk] = mv
                        continue
                    if isinstance(val, list) and key not in self._spices:
                        self._spices[key] = val
                        added["spice_categories"] += 1

                self.save_spices()

            self.reload()
            logger.info(f"Merge complete: {added}")
            return {"backup": backup_path, "added": added}
        except Exception as e:
            logger.error(f"Failed to merge defaults: {e}")
            return None


# Create singleton instance
prompt_manager = PromptManager()