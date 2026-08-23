# llm_providers/gemini.py
"""
Google Gemini provider via OpenAI-compatible endpoint.

Strips unsupported params (penalties, stop).
For thinking models (2.5+), injects reasoning_effort and
include_thoughts so thinking comes back as reasoning_content
on deltas — which openai_compat already reads.
"""

import logging
from typing import Dict, Any

from .openai_compat import OpenAICompatProvider

logger = logging.getLogger(__name__)

GEMINI_THINKING_PREFIXES = ('gemini-2.5', 'gemini-3')


class GeminiProvider(OpenAICompatProvider):
    """Gemini via OpenAI-compatible API with thinking support."""

    @property
    def supports_images(self) -> bool:
        return True

    def __init__(self, llm_config: Dict[str, Any], request_timeout: float = 240.0):
        super().__init__(llm_config, request_timeout)
        self._thinking_enabled = llm_config.get('thinking_enabled', True)
        self._reasoning_effort = llm_config.get('reasoning_effort', 'medium')

    def _transform_params_for_model(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Strip unsupported params, add thinking config for 2.5+ models."""
        # Penalties/top_k/stop before the base lift (else top_k would ride
        # extra_body into an endpoint that doesn't take it).
        params = {k: v for k, v in (params or {}).items()
                  if k not in ('stop', 'frequency_penalty', 'presence_penalty',
                               'repeat_penalty', 'top_k')}
        result = super()._transform_params_for_model(params)

        # reasoning_effort controls thinking depth (2.5+ models)
        # include_thoughts (extra_body.google) only works on gemini-3+
        model_lower = (self.model or '').lower()
        if self._thinking_enabled and any(model_lower.startswith(p) for p in GEMINI_THINKING_PREFIXES):
            result['reasoning_effort'] = self._reasoning_effort
            # gemini-3+ supports include_thoughts to return thinking in response
            if model_lower.startswith('gemini-3'):
                result['extra_body'] = {
                    "google": {"thinking_config": {"include_thoughts": True}}
                }
            logger.info(f"[GEMINI] Thinking enabled (effort={self._reasoning_effort}) for {self.model}")

        return result
