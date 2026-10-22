"""GLM provider for unified LLM client.

GLM exposes an OpenAI-compatible API and is BYOK-only.
"""

from products.ai_observability.backend.llm.providers.openai_compatible_byok import OpenAICompatibleByokAdapter

GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


class GLMAdapter(OpenAICompatibleByokAdapter):
    """GLM adapter backed by the OpenAI-compatible API."""

    name = "glm"
    BASE_URL = GLM_BASE_URL
    PROVIDER_DISPLAY_NAME = "GLM"
