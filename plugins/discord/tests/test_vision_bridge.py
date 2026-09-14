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


def test_vision_bridge_falls_back_without_provider(monkeypatch):
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
    # Hermetic: never let the house-provider auto-scan touch the box's real
    # LLM config from a unit test.
    monkeypatch.setattr(bridge, '_resolve_house_provider', lambda payload: None)

    result = bridge.describe_media(
        'https://cdn/screenshot.png',
        media_kind='image',
        settings=settings,
        filename='screenshot.png',
        content_type='image/png',
    )

    assert result['summary'] == 'Media attachment: screenshot.png'
    assert result['source'] == 'fallback'
    assert result['confidence'] == 0.2
    # The failed path now annotates why (house + legacy both unconfigured).
    assert result['fallback']['error_type'] == 'AttributeError'


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
    # Legacy-path test: hermetic — no house provider (the real resolver would
    # read the live environment's provider config).
    bridge._resolve_house_provider = lambda payload: None

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
    bridge._resolve_house_provider = lambda payload: None  # hermetic legacy-path test

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
    bridge._resolve_house_provider = lambda payload: None  # hermetic legacy-path test

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
    bridge._resolve_house_provider = lambda payload: None  # hermetic legacy-path test

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
    bridge._resolve_house_provider = lambda payload: None  # hermetic legacy-path test

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


class _FakeHouseResponse:
    def __init__(self, content):
        self.content = content


class _FakeHouseProvider:
    def __init__(self, content='A cat on a keyboard.'):
        self.content = content
        self.calls = []

    def chat_completion(self, messages, tools=None, generation_params=None):
        self.calls.append(messages)
        return _FakeHouseResponse(self.content)


def _house_settings(**over):
    from plugins.discord.models.settings import MediaSettings
    base = dict(enabled=True, image_understanding_enabled=True)
    base.update(over)
    return MediaSettings(**base)


def test_house_provider_path_describes_image(monkeypatch):
    from plugins.discord.vision.vision_bridge import VisionBridge
    provider = _FakeHouseProvider()
    bridge = VisionBridge(fetch_bytes=lambda url: (b'\x89PNG fakebytes', 'image/png'))
    monkeypatch.setattr(bridge, '_resolve_house_provider', lambda payload: provider)
    result = bridge.describe_media('https://cdn.example/cat.png', media_kind='image', settings=_house_settings())
    assert result['summary'] == 'A cat on a keyboard.'
    assert result['source'] == 'vision'
    content = provider.calls[0][0]['content']
    assert content[1]['type'] == 'image'
    assert content[1]['media_type'] == 'image/png'


def test_no_house_provider_and_no_base_url_falls_back(monkeypatch):
    from plugins.discord.vision.vision_bridge import VisionBridge
    bridge = VisionBridge(fetch_bytes=lambda url: (b'x', 'image/png'))
    monkeypatch.setattr(bridge, '_resolve_house_provider', lambda payload: None)
    result = bridge.describe_media('https://cdn.example/cat.png', media_kind='image',
                                   settings=_house_settings(), filename='cat.png')
    assert result['source'] != 'vision'
    assert 'cat.png' in (result.get('summary') or '') or 'cat.png' in str(result)


def test_legacy_base_url_still_used_when_no_house_provider(monkeypatch):
    from plugins.discord.vision.vision_bridge import VisionBridge
    seen = {}

    def fake_http(url, payload, headers=None, timeout=None):
        seen['url'] = url
        return {'choices': [{'message': {'content': 'legacy caption'}}]}

    bridge = VisionBridge(http_client=fake_http, fetch_bytes=lambda url: (b'x', 'image/png'))
    monkeypatch.setattr(bridge, '_resolve_house_provider', lambda payload: None)
    settings = _house_settings(vision_base_url='http://localhost:9999/v1', vision_model='llava')
    result = bridge.describe_media('https://cdn.example/cat.png', media_kind='image', settings=settings)
    assert result['summary'] == 'legacy caption'
    assert seen['url'].endswith('/chat/completions')


def test_daemon_chain_threads_reply_provider(monkeypatch):
    # 'auto' = daemon chooses: the Reply LLM override must reach the resolver.
    from plugins.discord.vision.vision_bridge import VisionBridge
    seen = {}
    provider = _FakeHouseProvider('caption')
    bridge = VisionBridge(fetch_bytes=lambda url: (b'x', 'image/png'))

    def fake_resolve(payload):
        seen.update(payload)
        return provider

    monkeypatch.setattr(bridge, '_resolve_house_provider', fake_resolve)
    bridge.describe_media('https://cdn.example/x.png', media_kind='image',
                          settings=_house_settings(), reply_llm_provider='claude')
    assert seen['reply_llm_provider'] == 'claude'
    assert seen['llm_provider'] == 'auto'


class _PropertyVisionProvider:
    # Mirrors BaseProvider: supports_images is a @property, NOT a method.
    @property
    def supports_images(self):
        return True


class _BlindProvider:
    @property
    def supports_images(self):
        return False


def _patch_provider_world(monkeypatch, made, order):
    import config
    import core.chat.llm_providers as llm_providers
    provs = {k: {'enabled': True, 'use_as_fallback': True} for k in made}
    monkeypatch.setattr(config, 'LLM_PROVIDERS', provs, raising=False)
    monkeypatch.setattr(config, 'LLM_CUSTOM_PROVIDERS', {}, raising=False)
    monkeypatch.setattr(config, 'LLM_FALLBACK_ORDER', order, raising=False)
    monkeypatch.setattr(llm_providers, 'get_provider_by_key',
                        lambda key, cfg, timeout, model_override=None: made.get(key))


def test_auto_scan_handles_property_supports_images(monkeypatch):
    # supports_images is a @property on BaseProvider; the resolver used to
    # CALL it — TypeError('bool' not callable) swallowed by a bare except into
    # 'does not support images'. Vision-auto could never pick ANY provider,
    # and the scan also stopped at the first constructed provider instead of
    # the first SIGHTED one. 2026-08-06.
    from plugins.discord.vision.vision_bridge import VisionBridge
    made = {'blind': _BlindProvider(), 'sighted': _PropertyVisionProvider()}
    _patch_provider_world(monkeypatch, made, ['blind', 'sighted'])
    monkeypatch.setattr(VisionBridge, '_local_only', staticmethod(lambda: False))   # M6 gate tested separately
    resolved = VisionBridge()._resolve_house_provider({'llm_provider': 'auto'})
    assert resolved is made['sighted']


def test_auto_scan_skips_cloud_providers_when_side_lanes_are_local_only(monkeypatch):
    from plugins.discord.vision.vision_bridge import VisionBridge
    made = {'sighted': _PropertyVisionProvider()}
    _patch_provider_world(monkeypatch, made, ['sighted'])
    monkeypatch.setattr(VisionBridge, '_local_only', staticmethod(lambda: True))
    monkeypatch.setattr(VisionBridge, '_provider_is_local', staticmethod(lambda key, conf: False))
    assert VisionBridge()._resolve_house_provider({'llm_provider': 'auto'}) is None
    monkeypatch.setattr(VisionBridge, '_provider_is_local', staticmethod(lambda key, conf: True))
    assert VisionBridge()._resolve_house_provider({'llm_provider': 'auto'}) is made['sighted']


def test_pinned_reply_provider_without_vision_degrades(monkeypatch):
    # Reply LLM pinned to a blind provider + vision auto: degrade gracefully,
    # never hop to a provider the user didn't pick.
    from plugins.discord.vision.vision_bridge import VisionBridge
    made = {'blind': _BlindProvider(), 'sighted': _PropertyVisionProvider()}
    _patch_provider_world(monkeypatch, made, ['blind', 'sighted'])
    resolved = VisionBridge()._resolve_house_provider(
        {'llm_provider': 'auto', 'reply_llm_provider': 'blind'})
    assert resolved is None
