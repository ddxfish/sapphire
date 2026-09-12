from __future__ import annotations


class ObservationInterpreter:
    def interpret(self, observation) -> dict:
        attachments = getattr(observation, 'attachments', []) or []
        return {
            'channel_id': observation.channel_id,
            'author_id': observation.author_id,
            'mentioned': getattr(observation, 'mentioned', False),
            'has_attachments': bool(attachments),
            'attachment_count': len(attachments),
            'has_image': any('image' in (a.get('content_type') or '') for a in attachments),
            'has_gif': any('gif' in (a.get('content_type') or '') or 'gif' in (a.get('filename') or '').lower() for a in attachments),
        }
