"""LLM layer: provider adapter interface, offline fake, tool definitions, loop.

Import order note: ``provider`` and ``tools`` are independent of each other, and
``agent`` depends on both. Nothing here imports a database driver.
"""

from .agent import TwinGuardAgent
from .provider import (
    FakeLLMProvider,
    LLMProvider,
    LLMResponse,
    OpenAICompatibleProvider,
    ProviderError,
    ToolCall,
    text_response,
    tool_response,
)
from .tools import TOOL_DEFINITIONS, TOOL_NAMES, TwinGuardTools

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOL_NAMES",
    "FakeLLMProvider",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ToolCall",
    "TwinGuardAgent",
    "TwinGuardTools",
    "text_response",
    "tool_response",
]
