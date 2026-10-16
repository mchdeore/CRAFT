"""Deterministic scripted model responses and separate hash-derived embeddings."""

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
    ResponseStream,
)


def embedding(text: str) -> list[float]:
    values = []
    for index in range(1536):
        digest = hashlib.sha256(text.encode() + index.to_bytes(2, "big")).digest()
        values.append((int.from_bytes(digest[:4]) / (2**32 - 1)) * 2 - 1)
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


def evidence(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        if isinstance(value.get("text"), str) and "handle" in value:
            return value
        for child in value.values():
            found = evidence(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = evidence(child)
            if found is not None:
                return found
    return None


class FakeChatClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, role: str = "main"):
        super().__init__()
        self.role = role
        self.model = "fake"

    async def close(self):
        pass

    def reply(self, messages: Sequence[Message]) -> ChatResponse:
        user_index = max(
            (index for index, message in enumerate(messages) if message.role == "user"), default=-1
        )
        newest = messages[user_index] if user_index >= 0 else None
        question = newest.text if newest else ""
        tool_results = [
            content
            for message in messages[user_index + 1 :]
            for content in message.contents
            if content.type == "function_result"
        ]
        signature = hashlib.sha256(
            (self.role + question + str(len(tool_results))).encode()
        ).hexdigest()
        if self.role == "check":
            contents = [
                Content.from_text(json.dumps({"supported": bool(tool_results), "simulated": True}))
            ]
        elif tool_results:
            found = next(
                (row for content in tool_results if (row := evidence(content.result)) is not None),
                None,
            )
            contents = [
                Content.from_text(found["text"] if found else "No readable evidence was returned.")
            ]
        else:
            contents = [
                Content.from_function_call(
                    call_id="call_" + signature[:16],
                    name="search",
                    arguments={"query": question[:500] or "synthetic evidence"},
                )
            ]
        return ChatResponse(
            messages=[Message(role="assistant", contents=contents, message_id=signature[:32])],
            response_id="fake_" + signature[:24],
            model="fake",
        )

    def _inner_get_response(self, *, messages, options, stream=False, **kwargs) -> Any:
        if stream:

            async def updates():
                response = self.reply(messages)
                for message in response.messages:
                    yield ChatResponseUpdate(
                        role="assistant",
                        contents=message.contents,
                        message_id=message.message_id,
                        model="fake",
                    )

            return ResponseStream(updates(), finalizer=lambda _: self.reply(messages))

        async def complete():
            return self.reply(messages)

        return complete()
