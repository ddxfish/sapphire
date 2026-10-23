"""The Debug tab's feed: recent decisions, ids only."""
from plugins.discord.observability.decisions import DecisionLog


def test_decisions_keep_the_last_n_newest_first_and_never_content():
    log = DecisionLog(limit=3)
    for i in range(5):
        log.note('rejected', account='bot', channel_id='c', channel_name='general', message_id=f'm{i}', stage='trigger',
                 reason='mentions_only')
    log.note('sent', account='bot', channel_id='c', message_id='m9', chunks=2)
    rows = log.list()
    assert [r['message_id'] for r in rows] == ['m9', 'm4', 'm3']
    assert rows[0]['kind'] == 'sent' and rows[0]['chunks'] == 2 and rows[0]['at'] > 0
    assert rows[1]['stage'] == 'trigger' and rows[1]['reason'] == 'mentions_only'
    assert all(set(r) == {'at', 'kind', 'account', 'channel_id', 'channel_name', 'message_id', 'stage', 'reason', 'chunks'} for r in rows)
    assert log.list(limit=1)[0]['message_id'] == 'm9'
    assert log.clear() == 3 and log.list() == []
