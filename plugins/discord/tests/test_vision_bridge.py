from plugins.discord.vision.vision_bridge import VisionBridge


class FakeProvider:
    def describe(self, payload):
        assert payload['source_url'] == 'https://cdn/x.gif'
        assert payload['media_kind'] == 'gif'
        assert payload['model'] == 'llava'
        assert payload['base_url'] == 'http://localhost:11434/v1'
        assert payload['api_key'] == ''
        assert payload['timeout_seconds'] == 30
        assert payload['gif_mode'] == 'first_frame'
        assert payload['filename'] == 'fox.gif'
        assert payload['content_type'] == 'image/gif'
        return {
            'summary': 'Animated fox waving hello.',
            'entities': ['fox'],
            'tone': 'playful',
            'ocr_text': '',
            'confidence': 0.82,
        }


class RaisingProvider:
    def describe(self, payload):
        raise RuntimeError('provider unavailable')


class RecordingHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, payload, *, headers, timeout):
        self.calls.append({
            'url': url,
            'payload': payload,
            'headers': headers,
            'timeout': timeout,
        })
        return self.response


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(message % args if args else message)


def _settings():
    return type('S', (), {
        'vision_provider': 'openai_compat',
        'vision_base_url': 'http://localhost:11434/v1',
        'vision_model': 'llava',
        'vision_api_key': '',
        'vision_timeout_seconds': 30,
        'vision_gif_mode': 'first_frame',
        'vision_debug_enabled': False,
    })()


def test_vision_bridge_normalizes_description():
    bridge = VisionBridge(provider_client=FakeProvider())

    result = bridge.describe_media(
        'https://cdn/x.gif',
        media_kind='gif',
        settings=_settings(),
        filename='fox.gif',
        content_type='image/gif',
    )

    assert result == {
        'summary': 'Animated fox waving hello.',
        'entities': ['fox'],
        'tone': 'playful',
        'ocr_text': '',
        'confidence': 0.82,
        'source': 'vision',
    }


def test_vision_bridge_falls_back_without_provider():
    settings = type('S', (), {
        'vision_provider': '',
        'vision_base_url': '',
        'vision_model': '',
        'vision_api_key': '',
        'vision_timeout_seconds': 30,
        'vision_gif_mode': 'first_frame',
        'vision_debug_enabled': False,
    })()
    bridge = VisionBridge(provider_client=None)

    result = bridge.describe_media(
        'https://cdn/screenshot.png',
        media_kind='image',
        settings=settings,
        filename='screenshot.png',
        content_type='image/png',
    )

    assert result == {
        'summary': 'Media attachment: screenshot.png',
        'entities': [],
        'tone': '',
        'ocr_text': '',
        'confidence': 0.2,
        'source': 'fallback',
    }


def test_vision_bridge_auto_detects_openai_compat_from_v1_url():
    client = RecordingHttpClient({
        'choices': [{
            'message': {
                'content': 'A fox waving from an animated image.'
            }
        }]
    })
    bridge = VisionBridge(
        provider_client=None,
        http_client=client,
        fetch_bytes=lambda _url: (b'png-bytes', 'image/png'),
    )

    result = bridge.describe_media(
        'https://cdn/example.png',
        media_kind='image',
        settings=_settings(),
        filename='example.png',
        content_type='image/png',
    )

    assert result['summary'] == 'A fox waving from an animated image.'
    assert result['source'] == 'vision'
    assert client.calls[0]['url'] == 'http://localhost:11434/v1/chat/completions'
    message = client.calls[0]['payload']['messages'][0]
    assert message['role'] == 'user'
    image_block = message['content'][1]['image_url']['url']
    assert image_block.startswith('data:image/png;base64,')


def test_vision_bridge_auto_detects_native_ollama_from_base_url():
    settings = type('S', (), {
        'vision_provider': '',
        'vision_base_url': 'http://localhost:11434',
        'vision_model': 'llava',
        'vision_api_key': '',
        'vision_timeout_seconds': 30,
        'vision_gif_mode': 'first_frame',
    })()
    client = RecordingHttpClient({
        'message': {
            'content': 'A fox waving from an animated image.'
        }
    })
    bridge = VisionBridge(
        provider_client=None,
        http_client=client,
        fetch_bytes=lambda _url: (b'gif-bytes', 'image/gif'),
    )

    result = bridge.describe_media(
        'https://cdn/example.gif',
        media_kind='gif',
        settings=settings,
        filename='example.gif',
        content_type='image/gif',
    )

    assert result['summary'] == 'A fox waving from an animated image.'
    assert result['source'] == 'vision'
    assert client.calls[0]['url'] == 'http://localhost:11434/api/chat'
    message = client.calls[0]['payload']['messages'][0]
    assert message['role'] == 'user'
    assert message['images'] == ['Z2lmLWJ5dGVz']


def test_vision_bridge_falls_back_on_provider_exception():
    bridge = VisionBridge(provider_client=RaisingProvider())

    result = bridge.describe_media(
        'https://cdn/screenshot.png',
        media_kind='image',
        settings=_settings(),
        filename='screenshot.png',
        content_type='image/png',
    )

    assert result == {
        'summary': 'Media attachment: screenshot.png',
        'entities': [],
        'tone': '',
        'ocr_text': '',
        'confidence': 0.2,
        'source': 'fallback',
        'fallback': {
            'reason': 'vision_error',
            'error_type': 'RuntimeError',
            'error_message': 'provider unavailable',
        },
    }


def test_vision_bridge_emits_debug_traces_and_logs_when_enabled():
    settings = _settings()
    settings.vision_debug_enabled = True
    client = RecordingHttpClient({
        'choices': [{
            'message': {
                'content': 'A fox waving from an animated image.'
            }
        }]
    })
    traces = []
    logger = RecordingLogger()
    bridge = VisionBridge(
        provider_client=None,
        http_client=client,
        fetch_bytes=lambda _url: (b'png-bytes', 'image/png'),
        trace_recorder=lambda trace_type, summary, detail: traces.append((trace_type, summary, detail)),
        debug_logger=logger,
    )

    result = bridge.describe_media(
        'https://cdn/example.png',
        media_kind='image',
        settings=settings,
        filename='example.png',
        content_type='image/png',
    )

    assert result['source'] == 'vision'
    assert [item[0] for item in traces] == [
        'vision_provider_detected',
        'vision_request_started',
        'vision_request_succeeded',
    ]
    assert any('openai_compat' in message for message in logger.messages)
    assert any('llava' in message for message in logger.messages)


def test_vision_bridge_emits_debug_failure_when_fetch_fails():
    settings = _settings()
    settings.vision_debug_enabled = True
    traces = []
    logger = RecordingLogger()
    bridge = VisionBridge(
        provider_client=None,
        fetch_bytes=lambda _url: (_ for _ in ()).throw(RuntimeError('HTTP 403')),
        trace_recorder=lambda trace_type, summary, detail: traces.append((trace_type, summary, detail)),
        debug_logger=logger,
    )

    result = bridge.describe_media(
        'https://cdn.example.png',
        media_kind='image',
        settings=settings,
        filename='example.png',
        content_type='image/png',
    )

    assert result['source'] == 'fallback'
    assert result['fallback']['reason'] == 'vision_error'
    assert [item[0] for item in traces] == [
        'vision_provider_detected',
        'vision_fetch_failed',
    ]
    assert any('fetch failed' in message.lower() for message in logger.messages)
    settings = _settings()
    settings.vision_debug_enabled = True
    traces = []
    logger = RecordingLogger()
    bridge = VisionBridge(
        provider_client=None,
        http_client=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')),
        fetch_bytes=lambda _url: (b'png-bytes', 'image/png'),
        trace_recorder=lambda trace_type, summary, detail: traces.append((trace_type, summary, detail)),
        debug_logger=logger,
    )

    result = bridge.describe_media(
        'https://cdn/example.png',
        media_kind='image',
        settings=settings,
        filename='example.png',
        content_type='image/png',
    )

    assert result['source'] == 'fallback'
    assert [item[0] for item in traces] == [
        'vision_provider_detected',
        'vision_request_started',
        'vision_request_failed',
    ]
    assert any('failed' in message.lower() for message in logger.messages)
