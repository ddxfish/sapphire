"""Relative gating (2026-10-03): a library hit stands out from the scope's
own score distribution for the query, or is strong outright against the
space prior — never an absolute cosine. Numbers below are the measured
nomic pair (calibration in the session notes): unrelated text-text ~0.46
± 0.045, unrelated text-pixel ~0.02 ± 0.012, matches ~0.6+ / ~0.065+."""
import io
import zlib

import numpy as np
import pytest
from PIL import Image

from plugins.mindpalace.tools import library as lib


class _Emb:
    provider_id = 'fake:vec'
    available = True

    def embed(self, texts, prefix='search_document'):
        out = []
        for t in texts:
            rng = np.random.default_rng(zlib.crc32(t.encode('utf-8', 'replace')))
            v = rng.standard_normal(64).astype(np.float32)
            out.append(v / np.linalg.norm(v))
        return out


def test_gate_pixels_tiny_scope():
    assert lib._gate([0.07, 0.02], 'img').tolist() == [True, False]      # two pictures, one real match
    assert lib._gate([0.025, 0.006], 'img').tolist() == [False, False]   # 'foggy diner' on dev: nothing
    assert lib._gate([0.06], 'img').tolist() == [True]                   # a lone picture is judged against the prior
    assert lib._gate([0.03], 'img').tolist() == [False]
    assert lib._gate([0.08] * 4, 'img').all()                             # four beach photos asked for 'beach'
    assert lib._gate([], 'img').shape == (0,)


def test_gate_text_tiny_scope():
    assert lib._gate([1.0], 'text').tolist() == [True]
    assert lib._gate([0.47, 0.47], 'text').tolist() == [False, False]    # the two captions vs 'cyberpunk bar'
    assert lib._gate([0.70, 0.47, 0.40], 'text').tolist() == [True, False, False]
    assert lib._gate([0.40, 0.39, 0.36], 'text').tolist() == [False] * 3  # recipe, tax deadline, a filename


def test_gate_bar_rises_with_scope_size():
    rng = np.random.default_rng(1)
    noise = rng.normal(0.46, 0.045, 2000)
    assert lib._gate(noise, 'text').sum() <= 6                             # the max of 2000 draws is not a find
    assert lib._gate(np.append(noise, 0.72), 'text')[-1]
    assert lib._zmin(5) == lib.Z_MIN and lib._zmin(117) > lib.Z_MIN and lib._zmin(2000) > 3
    assert lib._zmin(5, lib.Z_MIN_MIXED) == lib.Z_MIN_MIXED


def _png():
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (200, 30, 30)).save(buf, 'PNG')
    return buf.getvalue()


def test_photo_rank_names_its_empties(monkeypatch):
    assert lib._photo_rank('default', 'x', 5) == ([], 'no_embedder', 0)   # conftest: embedder down
    monkeypatch.setattr(lib, '_embedder', lambda: _Emb())
    assert lib._photo_rank('default', 'x', 5) == ([], 'no_images', 0)
    did, err = lib.import_image('default', 'p.png', _png())
    assert err is None
    scale, blob = lib.quantize(np.ones(768, dtype=np.float32) / np.sqrt(768))
    with lib.get_connection() as conn:
        conn.execute('INSERT INTO img_vectors (doc_id, provider, dim, scale, q) VALUES (?, ?, ?, ?, ?)',
                     (did, 'other:768', 768, scale, blob))
        conn.commit()
    lib._matrix_cache.clear()
    assert lib._photo_rank('default', 'x', 5) == ([], 'space_mismatch', 0)   # 64-dim query, 768-dim pictures
    scale, blob = lib.quantize(_Emb().embed(['x'])[0])                        # aligned: the query's own vector
    with lib.get_connection() as conn:
        conn.execute('DELETE FROM img_vectors WHERE doc_id = ?', (did,))
        conn.execute('INSERT INTO img_vectors (doc_id, provider, dim, scale, q) VALUES (?, ?, ?, ?, ?)',
                     (did, 'fake:vision', 64, scale, blob))
        conn.commit()
    lib._matrix_cache.clear()
    ranked, status, total = lib._photo_rank('default', 'x', 5)
    assert status == 'ok' and total == 1 and ranked[0][0] == did and ranked[0][2] is True
    assert ranked[0][1] > 0.9
    ranked, _, _ = lib._photo_rank('default', 'something else entirely', 5)
    assert ranked[0][2] is False and lib._photo_hits('default', 'something else entirely', 5) == []


def test_vector_hits_gate_not_floor(monkeypatch):
    monkeypatch.setattr(lib, '_embedder', lambda: _Emb())
    did, _ = lib.import_note('default', 'Note', 'the exact note text')
    while lib.work_once():
        pass
    assert lib._vector_hits('default', 'the exact note text', 5)           # identical text: a hit
    assert lib._vector_hits('default', 'unrelated words here', 5) == []    # a random cosine is not
