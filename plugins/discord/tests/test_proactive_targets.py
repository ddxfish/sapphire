from plugins.discord.proactive.targets import parse_target


def test_parse_target_two_part():
    assert parse_target('remmi:1516753078223896600') == ('remmi', '1516753078223896600')


def test_parse_target_three_part_leona_format():
    assert parse_target('remmi:123456789:1516753078223896600') == ('remmi', '1516753078223896600')


def test_parse_target_dict():
    assert parse_target({
        'account': 'remmi',
        'guild_id': '123',
        'channel_id': '456',
    }) == ('remmi', '456')
