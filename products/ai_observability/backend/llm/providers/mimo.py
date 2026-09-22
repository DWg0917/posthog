"""Mimo provider for unified LLM client.

Mimo exposes an OpenAI-compatible API and is BYOK-only. Model IDs are
discovered from the provider API, so new Mimo releases do not require code
changes.
"""

from products.ai_observability.backend.llm.providers.openai_compatible_byok import OpenAICompatibleByokAdapter

MIMO_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"


class MimoAdapter(OpenAICompatibleByokAdapter):
    """Mimo adapter backed by the OpenAI-compatible API."""

    name = "mimo"
    BASE_URL = MIMO_BASE_URL
    PROVIDER_DISPLAY_NAME = "Mimo"
