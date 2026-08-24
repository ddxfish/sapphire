from plugins.discord.conversation.mention_map_service import MentionMapService
from plugins.discord.conversation.mentions import apply_mention_map, merge_user_into_mention_map


def test_apply_mention_map_replaces_bare_at_username():
    mention_map = {}
    merge_user_into_mention_map(mention_map, '123456789012345678', username='ddxfish', display_name='ddxfish')
    text = apply_mention_map(
        'Hey @ddxfish — happy birthday!',
        mention_map,
        account='remmi',
        guild_id='999',
    )
    assert text == 'Hey <@123456789012345678> — happy birthday!'
    assert mention_map['ddxfish'] == '123456789012345678'


def test_apply_mention_map_fixes_angle_name_mention():
    mention_map = {}
    merge_user_into_mention_map(mention_map, '123456789012345678', username='spike', display_name='Spike le Vain')
    text = apply_mention_map(
        'say hello to <@Spike le Vain>',
        mention_map,
        account='remmi',
        guild_id='999',
    )
    assert text == 'say hello to <@123456789012345678>'


def test_apply_mention_map_preserves_real_snowflake():
    mention_map = {}
    text = apply_mention_map('ping <@123456789012345678>', mention_map, account='remmi', guild_id='999')
    assert text == 'ping <@123456789012345678>'


def test_mention_map_service_builds_from_author():
    service = MentionMapService()
    mention_map = service.build_for_channel(
        'remmi',
        'c1',
        author_id='42',
        username='ddxfish',
        display_name='ddxfish',
    )
    assert mention_map['ddxfish'] == '42'
    assert service.apply_text('Hey @ddxfish!', 'remmi', 'c1') == 'Hey <@42>!'
