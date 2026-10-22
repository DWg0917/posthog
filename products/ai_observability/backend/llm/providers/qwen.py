"""Qwen provider for unified LLM client.

Qwen exposes an OpenAI-compatible API and is BYOK-only.
"""

from products.ai_observability.backend.llm.providers.openai_compatible_byok import OpenAICompatibleByokAdapter

QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenAdapter(OpenAICompatibleByokAdapter):
    """Qwen adapter backed by the OpenAI-compatible API."""

    name = "qwen"
    BASE_URL = QWEN_BASE_URL
    PROVIDER_DISPLAY_NAME = "Qwen"
