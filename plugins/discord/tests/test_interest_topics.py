"""H8 (hunt 2026-09-12): the interest lexicon matched substrings."""

from plugins.discord.memory.interest_service import extract_topics


def test_substrings_no_longer_register_as_interests():
    text = 'said again at the party; my husband found the location on facebook after the competition, then a strip'
    assert extract_topics(text) == []


def test_whole_words_still_register():
    assert set(extract_topics('I love my cat and playing guitar')) == {'music', 'pets'}
    assert extract_topics('AI is neat') == ['tech']
    assert 'travel' in extract_topics('booked a trip to Oslo')


def test_channel_mentions_are_not_interests():
    assert extract_topics('#general hello everyone') == []
    assert extract_topics('see #photography for the pics') == ['photography']
