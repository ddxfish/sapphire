"""Inbound media detection, storage, and interpretation."""

from __future__ import annotations

from plugins.discord.models.media import MediaArtifact
from plugins.discord.vision.vision_bridge import VisionBridge

IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
GIF_HINTS = ('gif', 'tenor', 'giphy')


class MediaService:
    def __init__(self, *, media_repository=None, llm_bridge=None, vision_bridge=None, trace_repository=None):
        self.media_repository = media_repository
        self.llm_bridge = llm_bridge
        self.trace_repository = trace_repository
        self.vision_bridge = vision_bridge or VisionBridge(
            provider_client=llm_bridge,
            trace_recorder=self._record_vision_trace,
        )

    def detect_artifacts(self, message_id: str, channel_id: str, account_name: str, attachments: list[dict]) -> list[MediaArtifact]:
        artifacts = []
        for item in attachments or []:
            content_type = (item.get('content_type') or '').lower()
            filename = (item.get('filename') or '').lower()
            url = item.get('url') or ''
            if not url:
                continue
            media_kind = 'attachment'
            if content_type in IMAGE_TYPES or filename.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                media_kind = 'gif' if 'gif' in content_type or filename.endswith('.gif') or any(h in url.lower() for h in GIF_HINTS) else 'image'
            elif any(h in url.lower() for h in GIF_HINTS):
                media_kind = 'gif'
            artifacts.append(MediaArtifact(
                message_id=message_id,
                channel_id=channel_id,
                account_name=account_name,
                media_kind=media_kind,
                source_url=url,
                filename=item.get('filename') or '',
                content_type=content_type,
                raw_metadata=dict(item),
            ))
        return artifacts

    def interpret_artifact(self, artifact: MediaArtifact, *, settings=None, image_understanding_enabled: bool = True) -> dict:
        media_settings = settings.media if settings and hasattr(settings, 'media') else settings
        if not image_understanding_enabled:
            return self._fallback_interpretation(artifact, source='metadata')

        bridge = self.vision_bridge
        if bridge and hasattr(bridge, 'describe_media'):
            try:
                return bridge.describe_media(
                    artifact.source_url,
                    media_kind=artifact.media_kind,
                    settings=media_settings,
                    filename=artifact.filename,
                    content_type=artifact.content_type,
                )
            except Exception as exc:
                return self._fallback_interpretation(
                    artifact,
                    source='fallback',
                    reason='vision_error',
                    error=exc,
                )

        return self._fallback_interpretation(artifact, source='metadata')

    def store_and_interpret(self, artifact: MediaArtifact, *, settings=None, image_understanding_enabled: bool = True) -> MediaArtifact:
        artifact.interpretation = self.interpret_artifact(
            artifact,
            settings=settings,
            image_understanding_enabled=image_understanding_enabled,
        )
        if self.media_repository:
            self.media_repository.save_artifact(artifact)
        return artifact

    def _fallback_interpretation(self, artifact: MediaArtifact, *, source: str, reason: str | None = None, error: Exception | None = None) -> dict:
        media_label = self._describe_artifact_label(artifact)
        interpretation = {
            'summary': media_label,
            'entities': [],
            'tone': '',
            'ocr_text': '',
            'confidence': 0.2 if source == 'fallback' else 0.3,
            'source': source,
        }
        if reason or error:
            interpretation['fallback'] = {
                'reason': reason or 'unavailable',
                'error_type': type(error).__name__ if error else '',
                'error_message': str(error) if error else '',
            }
        return interpretation

    def _describe_artifact_label(self, artifact: MediaArtifact) -> str:
        kind = 'GIF' if artifact.media_kind == 'gif' else 'Image' if artifact.media_kind == 'image' else 'Media'
        if artifact.filename:
            return f'{kind} attachment named {artifact.filename}'
        if artifact.content_type:
            return f'{kind} attachment ({artifact.content_type})'
        return f'{kind} attachment'

    def build_context(self, message_id: str) -> list[dict]:
        return self.build_context_map([message_id]).get(str(message_id), [])

    def build_context_map(self, message_ids: list[str]) -> dict[str, list[dict]]:
        if not self.media_repository:
            return {}
        grouped = self.media_repository.get_by_message_ids(message_ids)
        return {
            message_id: [
                {
                    'media_kind': item['media_kind'],
                    'source_url': item['source_url'],
                    'filename': item.get('filename') or '',
                    'interpretation': item.get('interpretation') or {},
                }
                for item in items
            ]
            for message_id, items in grouped.items()
        }

    def get_recent_media_message_id(self, channel_id: str, *, max_age_seconds: float = 300) -> str:
        if not self.media_repository:
            return ''
        return self.media_repository.get_recent_message_id(channel_id, max_age_seconds=max_age_seconds) or ''

    def message_has_media(self, message_id: str) -> bool:
        return bool(self.build_context(message_id))

    def _record_vision_trace(self, trace_type: str, summary: str, detail: dict) -> None:
        if self.trace_repository:
            self.trace_repository.record_trace(trace_type, summary, detail)
