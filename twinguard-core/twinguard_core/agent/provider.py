"""LLM provider adapter interface, an offline fake, and a real adapter.

``TwinGuardAgent`` depends only on :class:`LLMProvider`, so the provider can be
swapped without touching the orchestration loop. Tests use :class:`FakeLLMProvider`
exclusively, so the suite needs neither network access nor an API key.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: dict = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Either a tool-call request or a final text explanation, never both.

    The agent loop branches on which field is populated.
    """

    model_config = ConfigDict(extra="forbid")

    text: Optional[str] = None
    tool_call: Optional[ToolCall] = None

    @property
    def is_tool_call(self) -> bool:
        return self.tool_call is not None


def text_response(text: str) -> LLMResponse:
    return LLMResponse(text=text)


def tool_response(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(tool_call=ToolCall(name=name, arguments=arguments))


class LLMProvider(ABC):
    @abstractmethod
    def run_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        conversation_history: list[dict],
    ) -> LLMResponse:
        """One turn. Return a tool call to keep going, or text to stop."""


# ---------------------------------------------------------------------------
# Offline fake
# ---------------------------------------------------------------------------

#: A scripted turn is either a ready-made response, or a callable taking
#: ``(user_message, tools)`` and returning a response.
ScriptedTurn = LLMResponse | Callable[[str, list[dict]], LLMResponse]


class FakeLLMProvider(LLMProvider):
    """Replays a scripted list of turns. No network, fully deterministic.

    ``responses_by_text`` maps a substring of the user message to the turn to
    play for it, which is how a single fake can serve several scenarios. Anything
    not matched falls through to ``script`` in order.
    """

    def __init__(
        self,
        script: Optional[Sequence[ScriptedTurn]] = None,
        responses_by_text: Optional[dict[str, ScriptedTurn]] = None,
    ) -> None:
        self._script: list[ScriptedTurn] = list(script or [])
        self._responses_by_text = dict(responses_by_text or {})
        self._index = 0
        #: Every request the agent made, for assertions.
        self.calls: list[dict] = []

    def run_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        conversation_history: list[dict],
    ) -> LLMResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "tools": tools,
                "conversation_history": list(conversation_history),
            }
        )
        for needle, turn in self._responses_by_text.items():
            if needle in user_message:
                return turn(user_message, tools) if callable(turn) else turn
        if self._index < len(self._script):
            turn = self._script[self._index]
            self._index += 1
            return turn(user_message, tools) if callable(turn) else turn
        raise AssertionError(
            "FakeLLMProvider ran out of scripted turns. "
            f"User message was: {user_message!r}"
        )

    @property
    def turn_count(self) -> int:
        return self._index


# ---------------------------------------------------------------------------
# Real adapter
# ---------------------------------------------------------------------------


class ProviderError(RuntimeError):
    """Raised when the upstream LLM call fails or returns something unusable.

    The HTTP layer maps this to a 502 rather than letting it become a 500.
    """


class OpenAICompatibleProvider(LLMProvider):
    """Adapter for any OpenAI-compatible ``/chat/completions`` endpoint.

    Works with OpenAI, and with xAI/Grok, whose base URL is also
    OpenAI-compatible. Uses ``httpx`` directly so no vendor SDK is pinned into
    the dependency set.

    Never used by the test suite. The API key is read from the environment (or
    from ``.env``) and is never logged or returned.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleProvider":
        """Build from ``LLM_API_KEY`` / ``LLM_MODEL`` / ``LLM_BASE_URL``.

        Also honours the ``GROK_*`` names already present in this repo's
        ``.env.example``.
        """
        api_key = os.getenv("LLM_API_KEY") or os.getenv("GROK_API_KEY") or ""
        model = os.getenv("LLM_MODEL") or os.getenv("GROK_MODEL") or "grok-3-mini"
        base_url = (
            os.getenv("LLM_BASE_URL")
            or os.getenv("GROK_BASE_URL")
            or "https://api.openai.com/v1"
        )
        if not api_key:
            raise ProviderError(
                "no API key found; set LLM_API_KEY (or GROK_API_KEY) in the environment"
            )
        return cls(model=model, api_key=api_key, base_url=base_url)

    def run_with_tools(
        self,
        system_prompt: str,
        user_message: str,
        tools: list[dict],
        conversation_history: list[dict],
    ) -> LLMResponse:
        import httpx

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})

        payload: dict = {
            "model": self._model,
            "messages": messages,
            "tool_choice": "auto" if tools else "none",
        }
        if tools:
            payload["tools"] = tools

        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            # Deliberately does not include headers or the key.
            raise ProviderError(f"LLM request failed: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise ProviderError("LLM response was not valid JSON") from exc

        return self._parse(body)

    @staticmethod
    def _parse(body: dict) -> LLMResponse:
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("LLM response had no choices[0].message") from exc

        calls = message.get("tool_calls") or []
        if calls:
            first = calls[0]
            function = first.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        f"tool call {function.get('name')!r} had malformed JSON arguments"
                    ) from exc
            else:
                arguments = raw_arguments
            return LLMResponse(
                tool_call=ToolCall(name=function.get("name", ""), arguments=arguments)
            )
        return LLMResponse(text=message.get("content") or "")
