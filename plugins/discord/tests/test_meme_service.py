from plugins.discord.conversation.meme_service import MemeService
from plugins.discord.models.intentions import SendMemeIntention


def test_classifies_meme_filename():
    service = MemeService()
    attachments = [{'url': 'https://cdn/meme.jpg', 'filename': 'drake_meme.jpg', 'content_type': 'image/jpeg'}]

    result = service.classify(attachments)

    assert result.is_meme is True
    assert result.sentiment in {'humorous', 'reaction', 'neutral'}


def test_build_send_meme_intention():
    service = MemeService(meme_library={'reaction': ['https://meme/1.gif']})
    intention = service.build_intention('alpha', 'c1', theme='reaction')

    assert isinstance(intention, SendMemeIntention)
    assert intention.meme_url
