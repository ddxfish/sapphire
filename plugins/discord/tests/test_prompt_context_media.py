from plugins.discord.conversation.batching_service import ChannelBatch
from plugins.discord.conversation.prompt_context_service import PromptContextService
from plugins.discord.models.observations import TextMessageObservation


class FakeMessageRepository:
    def __init__(self, rows=None):
        self.rows = rows or [{'message_id': 'm0', 'author_name': 'Bob', 'content': 'earlier message'}]

    def get_recent_messages(self, account_name, channel_id, limit=20):
        return list(self.rows)


class FakeMediaService:
    def __init__(self, payload, *, recent_message_id='', context_map=None):
        self.payload = payload
        self.calls = []
        self.recent_message_id = recent_message_id
        self.context_map = context_map or {}

    def build_context(self, message_id):
        self.calls.append(message_id)
        return list(self.payload)

    def build_context_map(self, message_ids):
        return {
            message_id: list(items)
            for message_id, items in self.context_map.items()
            if message_id in message_ids
        }

    def get_recent_media_message_id(self, channel_id, *, max_age_seconds=300):
        return self.recent_message_id

    def message_has_media(self, message_id):
        return message_id in self.context_map or message_id in {'m1', 'img-1'} or bool(self.payload)


def make_observation(message_id: str, *, content='hello', clean_content='hello', attachments=None, reply_to_message_id='') -> TextMessageObservation:
    return TextMessageObservation(
        observation_id=f'obs-{message_id}',
        account_name='alpha',
        guild_id='g1',
        guild_name='Guild',
        channel_id='c1',
        channel_name='general',
        author_id='u1',
        username='alice',
        display_name='Alice',
        message_id=message_id,
        content=content,
        clean_content=clean_content,
        created_at=0.0,
        is_dm=False,
        mentioned=True,
        attachments=attachments or [],
        reply_to_message_id=reply_to_message_id,
    )


def make_batch(*observations: TextMessageObservation) -> ChannelBatch:
    latest = observations[-1]
    return ChannelBatch(
        account_name=latest.account_name,
        guild_id=latest.guild_id,
        guild_name=latest.guild_name,
        channel_id=latest.channel_id,
        channel_name=latest.channel_name,
        is_dm=latest.is_dm,
        observations=list(observations),
    )


def test_includes_media_only_for_latest_message_attachments():
    media_service = FakeMediaService(
        [{
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }],
        context_map={
            'm1': [{
                'media_kind': 'image',
                'source_url': 'https://cdn/a.png',
                'interpretation': {'summary': 'a cat picture'},
            }],
        },
    )
    service = PromptContextService(
        message_repository=FakeMessageRepository([
            {'message_id': 'm1', 'author_name': 'Alice', 'content': ''},
        ]),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm1',
            attachments=[{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}],
        )
    )

    context = service.build(batch)

    assert context['media'] == [
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }
    ]
    assert media_service.calls == ['m1']


def test_includes_media_for_explicit_follow_up_about_prior_image():
    media_service = FakeMediaService([
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }
    ])
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm1',
            attachments=[{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}],
        ),
        make_observation('m2', content='what is this image?', clean_content='what is this image?'),
    )

    context = service.build(batch)

    assert context['media'] == [
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }
    ]
    assert media_service.calls == ['m1']


def test_includes_media_for_immediate_recent_turn_continuity():
    media_service = FakeMediaService([
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }
    ])
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm1',
            attachments=[{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}],
        ),
        make_observation('m2', content='lol', clean_content='lol', reply_to_message_id='m1'),
    )

    context = service.build(batch)

    assert context['media'] == [
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }
    ]
    assert media_service.calls == ['m1']


def test_includes_media_for_can_you_see_follow_up_in_same_batch():
    media_service = FakeMediaService([
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture', 'source': 'vision'},
        }
    ])
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm1',
            attachments=[{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}],
        ),
        make_observation('m2', content='can you see the image?', clean_content='can you see the image?'),
    )

    context = service.build(batch)

    assert 'media' in context
    assert media_service.calls == ['m1']


def test_includes_media_for_follow_up_from_recent_channel_artifact():
    media_service = FakeMediaService(
        [{
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture', 'source': 'vision'},
        }],
        recent_message_id='m1',
    )
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation('m2', content='can you see the image?', clean_content='can you see the image?'),
    )

    context = service.build(batch)

    assert 'media' in context
    assert media_service.calls == ['m1']


def test_includes_media_when_replying_to_prior_image_message():
    media_service = FakeMediaService([
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture', 'source': 'vision'},
        }
    ])
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm2',
            content='thoughts?',
            clean_content='thoughts?',
            reply_to_message_id='m1',
        ),
    )

    context = service.build(batch)

    assert 'media' in context
    assert media_service.calls == ['m1']


def test_recent_history_includes_prior_image_description_for_follow_up():
    media_service = FakeMediaService(
        [],
        context_map={
            'm1': [{
                'media_kind': 'image',
                'interpretation': {'summary': 'a cat picture'},
            }],
        },
    )
    service = PromptContextService(
        message_repository=FakeMessageRepository([
            {'message_id': 'm1', 'author_name': 'Alice', 'content': ''},
            {'message_id': 'm2', 'author_name': 'Alice', 'content': 'can you see the image?'},
        ]),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation('m2', content='can you see the image?', clean_content='can you see the image?'),
    )

    context = service.build(batch)

    assert context['recent_history'] == ['Alice: [sent image: a cat picture]']


def test_omits_media_when_latest_message_has_no_attachments_or_explicit_follow_up():
    media_service = FakeMediaService([
        {
            'media_kind': 'image',
            'source_url': 'https://cdn/a.png',
            'interpretation': {'summary': 'a cat picture'},
        }
    ])
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm1',
            attachments=[{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}],
        ),
        make_observation('m2', content='totally unrelated', clean_content='totally unrelated'),
    )

    context = service.build(batch)

    assert 'media' not in context
    assert media_service.calls == []


def test_omits_media_when_latest_message_has_attachments_but_no_media_context():
    media_service = FakeMediaService([])
    service = PromptContextService(
        message_repository=FakeMessageRepository(),
        media_service=media_service,
    )
    batch = make_batch(
        make_observation(
            'm1',
            attachments=[{'url': 'https://cdn/a.png', 'filename': 'cat.png', 'content_type': 'image/png'}],
        )
    )

    context = service.build(batch)

    assert 'media' not in context
    assert media_service.calls == ['m1']
