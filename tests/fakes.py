"""Test doubles for the model provider.

The fake returns real ``openai`` response objects rather than dictionaries, so
any attribute mismatch between our loop and the SDK fails here instead of in a
live run. No test in this suite touches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, RateLimitError
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessage,
    ChatCompletionMessageToolCall,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import Function
from openai.types.completion_usage import CompletionUsage

TEST_URL = "https://example.test/v1/chat/completions"


def tool_call(name: str, arguments: dict[str, Any] | str, id: str = "call_1"):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ChatCompletionMessageToolCall(
        id=id, type="function", function=Function(name=name, arguments=raw)
    )


def message(content: str | None = None, tool_calls: list | None = None):
    return ChatCompletionMessage(
        role="assistant", content=content, tool_calls=tool_calls or None
    )


def completion(
    msg: ChatCompletionMessage,
    *,
    finish_reason: str = "stop",
    model: str = "fake/model",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
):
    return ChatCompletion(
        id="chatcmpl-test",
        choices=[Choice(index=0, message=msg, finish_reason=finish_reason)],
        created=0,
        model=model,
        object="chat.completion",
        usage=CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers,
        request=httpx.Request("POST", TEST_URL),
    )


def rate_limit_error(retry_after: float | None = None) -> RateLimitError:
    headers = {} if retry_after is None else {"retry-after": str(retry_after)}
    return RateLimitError("rate limited", response=_response(429, headers), body=None)


def status_error(status: int) -> APIStatusError:
    return APIStatusError(f"status {status}", response=_response(status), body=None)


def connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", TEST_URL))


@dataclass
class FakeClient:
    """Mimics ``OpenAI`` closely enough for ``main.request_completion``."""

    responses: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs):
        # Snapshot the message list: the real SDK serialises the request
        # immediately, while our loop keeps appending to the same list.
        if "messages" in kwargs:
            kwargs["messages"] = list(kwargs["messages"])
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake client ran out of scripted responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def messages_sent(self) -> list[list[dict[str, Any]]]:
        return [call["messages"] for call in self.calls]
