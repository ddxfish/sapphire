from plugins.discord.lib.server_time import now_local


def test_now_local_is_naive_local_datetime():
    now = now_local()
    assert now.tzinfo is None
    assert 0 <= now.hour <= 23
