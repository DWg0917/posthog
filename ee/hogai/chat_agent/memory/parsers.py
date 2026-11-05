from collections.abc import Collection
from typing import Any

from langchain_core.messages import AIMessage


def compressed_memory_parser(memory: str) -> str:
    """
    Remove newlines between paragraphs.
    """
    return memory.replace("\n\n", "\n")


def check_memory_collection_completed(response: Any, *, allowed_tool_names: Collection[str]) -> AIMessage | None:
    if isinstance(response, AIMessage) and (
        "[Done]" in response.content
        or not response.tool_calls
        or any(tool["name"] not in allowed_tool_names for tool in response.tool_calls)
    ):
        return None
    return response
