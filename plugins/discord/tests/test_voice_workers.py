from plugins.discord.voice import voice_workers


def test_pool_is_lazy_and_survives_shutdown():
    voice_workers.shutdown()
    assert voice_workers._pool is None
    assert voice_workers.submit(lambda: 41 + 1).result(timeout=2) == 42
    assert voice_workers._pool is not None
    voice_workers.shutdown()
    assert voice_workers._pool is None
    assert voice_workers.submit(lambda: 'again').result(timeout=2) == 'again'   # rebuilt on demand
    voice_workers.shutdown()
