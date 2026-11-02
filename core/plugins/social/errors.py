# core/plugins/social/errors.py - the one error a provider raises (tmp/social-media-plan.md)


class SocialError(Exception):
    """Honest, user-readable failure: what the platform said, or what is missing."""
