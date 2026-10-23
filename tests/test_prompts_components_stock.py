"""/api/prompts/components carries `stock_pieces` (2026-09-08) — the modals'
'Custom' quick-select can't be derived client-side (shipped pieces are
merged into the user store at boot), so the route must say which keys
shipped. Store singletons are stubbed; no user data is read."""
from types import SimpleNamespace


def test_components_route_reports_stock_pieces(client, monkeypatch):
    import core.prompts as prompts_mod
    import core.prompt_packs as packs
    import core.prompt_vault as pv
    from core.routes import content

    stub = SimpleNamespace(_components={'character': {'mine': 'x'}},
                           components={'character': {'mine': 'x', 'hidden_engine': 'y'}})
    monkeypatch.setattr(prompts_mod, 'prompt_manager', stub)
    monkeypatch.setattr(prompts_mod, 'visible_components', lambda: {'character': {'mine': 'x'}})
    monkeypatch.setattr(packs, 'component_sources', lambda: {})
    monkeypatch.setattr(pv, 'overlay_components', lambda: {})
    content._STOCK_PIECE_KEYS = {'character': ['ada', 'sapphire'], 'extras': ['be_concise']}

    c, _ = client
    r = c.get('/api/prompts/components')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['components'] == {'character': {'mine': 'x'}}
    assert body['stock_pieces'] == {'character': ['ada', 'sapphire'], 'extras': ['be_concise']}
    assert body['hidden_keys'] == {'character': ['hidden_engine']}
    content._STOCK_PIECE_KEYS = None
