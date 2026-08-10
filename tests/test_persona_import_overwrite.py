"""[REGRESSION_GUARD] Persona import overwrite path.

Bug/feature (2026-06-24): importing a persona whose name already exists used to
hard-409 with no way through. Now `overwrite_persona` lets it replace in place
(persona_manager.update) instead of create. These guard the branch logic with a
mocked persona_manager — no real user data is touched.
"""
import asyncio

import pytest
from fastapi import HTTPException

# Load the app first so core.routes.content is fully initialized — importing
# that route module on its own trips a circular import with core.api_fastapi.
import core.api_fastapi  # noqa: F401
from core.routes.content import _import_persona_from_bundle


@pytest.fixture
def pm(monkeypatch):
    from core.personas import persona_manager
    calls = {"created": [], "updated": []}
    monkeypatch.setattr(persona_manager, "_sanitize_name",
                        lambda n: (n or "").strip().lower().replace(" ", "_"))
    monkeypatch.setattr(persona_manager, "create",
                        lambda name, data: (calls["created"].append(name), True)[1])
    monkeypatch.setattr(persona_manager, "update",
                        lambda name, data: (calls["updated"].append(name), True)[1])
    monkeypatch.setattr(persona_manager, "get_avatar_path", lambda n: None)
    return persona_manager, calls


def _run(bundle):
    return asyncio.run(_import_persona_from_bundle(bundle))


def test_import_new_persona_creates(pm, monkeypatch):
    persona_manager, calls = pm
    monkeypatch.setattr(persona_manager, "exists", lambda n: False)
    r = _run({"sapphire_export": True, "type": "persona", "name": "fresh"})
    assert r["status"] == "success"
    assert calls["created"] == ["fresh"]
    assert not calls["updated"]


def test_import_existing_without_overwrite_409(pm, monkeypatch):
    persona_manager, calls = pm
    monkeypatch.setattr(persona_manager, "exists", lambda n: True)
    with pytest.raises(HTTPException) as ei:
        _run({"sapphire_export": True, "type": "persona", "name": "dup"})
    assert ei.value.status_code == 409
    assert not calls["created"] and not calls["updated"]


def test_import_existing_with_overwrite_updates(pm, monkeypatch):
    persona_manager, calls = pm
    monkeypatch.setattr(persona_manager, "exists", lambda n: True)
    r = _run({"sapphire_export": True, "type": "persona", "name": "dup",
              "overwrite_persona": True})
    assert r["status"] == "success"
    assert calls["updated"] == ["dup"]
    assert not calls["created"]


def test_invalid_bundle_rejected(pm):
    with pytest.raises(HTTPException) as ei:
        _run({"type": "persona", "name": "x"})  # missing sapphire_export
    assert ei.value.status_code == 400


def test_keep_components_skips_unchecked(pm, monkeypatch):
    """Even with overwrite_prompt, a piece in keep_components keeps its local
    value (not written); pieces not in the list are overwritten."""
    persona_manager, calls = pm
    monkeypatch.setattr(persona_manager, "exists", lambda n: False)

    import core.prompt_crud as pc
    import core.prompt_manager as pmmod
    monkeypatch.setattr(pc, "get_prompt", lambda name: None)
    monkeypatch.setattr(pc, "save_prompt",
                        lambda name, data, allow_overwrite=False, origin=None: (True, "ok"))

    class FakePM:
        """Mirrors the real PromptManager contract: reads via the merged
        `.components` property, writes via `._components` (with no packs
        registered the property returns the private dict directly), mutations
        under `._lock`, savers return bool."""
        def __init__(self):
            import threading
            self._components = {}
            self._lock = threading.RLock()
        @property
        def components(self):
            return self._components
        def save_components(self, reason=None):
            return True
    fake = FakePM()
    monkeypatch.setattr(pmmod, "prompt_manager", fake)
    # Piece writes flow through prompt_crud.save_components_batch (phase 0
    # funnel), which uses prompt_crud's module-level manager binding.
    monkeypatch.setattr(pc, "prompt_manager", fake)

    r = _run({
        "sapphire_export": True, "type": "persona", "name": "p",
        "overwrite_prompt": True,
        "keep_components": ["character/keepme"],
        "prompt": {"name": "p", "data": {"type": "assembled", "components": {"character": "keepme"}}},
        "components": {"character": {"keepme": "NEW", "other": "NEW2"}},
    })
    assert r["status"] == "success"
    assert "keepme" not in fake.components.get("character", {})   # kept local
    assert fake.components.get("character", {}).get("other") == "NEW2"  # overwritten
