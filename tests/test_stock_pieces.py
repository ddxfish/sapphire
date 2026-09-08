"""Shipped-piece keys for the prompts modals' 'Custom' quick-select
(2026-09-08). Shipped pieces are merged into the user store at boot, so
core/prompt_defaults/prompt_pieces.json is the only source of "built-in"."""


def test_stock_piece_keys_reads_the_shipped_components():
    from core.api_fastapi import app  # noqa: F401  (routes import through the app)
    from core.routes import content
    content._STOCK_PIECE_KEYS = None
    keys = content._stock_piece_keys()
    assert isinstance(keys, dict) and keys
    for t in ('character', 'location', 'extras', 'emotions'):
        assert t in keys and keys[t], f"shipped type {t} missing"
    assert all(isinstance(k, str) and not k.startswith('_') for ks in keys.values() for k in ks)
    assert content._stock_piece_keys() is keys, "cached — the file is static at runtime"
