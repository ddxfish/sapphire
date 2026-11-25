"""The embedding model's first download: through the door (core/hf_fetch.py),
and a load that failed is not walked into again by every embed() call."""
import inspect

from core.embeddings import LocalEmbedder


def test_the_model_file_comes_through_the_door_with_the_pinned_revision():
    src = inspect.getsource(LocalEmbedder._load)
    fetch = src[src.index('hf_fetch.fetch('):]
    assert 'files=[EMBEDDING_ONNX_FILE]' in fetch and 'revision=rev' in fetch and 'handle=p' in fetch
    # the cache is still asked first, with no network
    assert src.index('local_files_only=True') < src.index('hf_fetch.fetch(')


def test_a_failed_load_waits_before_it_is_tried_again(monkeypatch):
    import core.embeddings as emb
    tries = []

    def boom(*a, **k):
        tries.append(1)
        raise RuntimeError("This network's DNS is giving out-of-date addresses.")

    import transformers
    monkeypatch.setattr(transformers.AutoTokenizer, 'from_pretrained', boom)
    clock = [1000.0]
    monkeypatch.setattr(emb.time, 'monotonic', lambda: clock[0])
    e = LocalEmbedder()
    assert e.embed(['hello']) is None and "out-of-date addresses" in e.load_error
    made = len(tries)
    assert made >= 1
    assert e.embed(['hello']) is None and e.available is False and len(tries) == made   # inside the wait: not tried
    clock[0] += LocalEmbedder.RETRY_S + 1
    assert e.embed(['hello']) is None and len(tries) > made                             # after it: tried again
    assert LocalEmbedder()._retry_at == 0.0                 # a new embedder (a re-pick) tries at once
