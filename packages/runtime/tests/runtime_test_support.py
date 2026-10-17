"""Native framework runs using synthetic tools and server-owned memory state."""

import asyncio
import hashlib
import json
from typing import Any
from uuid import uuid4

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    FunctionTool,
    Message,
    ResponseStream,
)

from craft_runtime.decisions import MemoryDecisionGate
from craft_runtime.runner import Binding, Runtime
from craft_runtime.state import MemoryRunStore

QUOTE = "Synthetic voltage is 12 V."


class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, mode="read"):
        super().__init__()
        self.mode, self.closed = mode, False
        self.offered = []

    async def close(self):
        self.closed = True

    def reply(self, messages):
        last_user = max(
            (i for i, message in enumerate(messages) if message.role == "user"), default=-1
        )
        results = [
            content
            for message in messages[last_user + 1 :]
            for content in message.contents
            if content.type == "function_result"
        ]
        if self.mode == "readwrite" and len(results) == 1:
            contents = [
                Content.from_function_call(
                    call_id="write_after_read", name="write_value", arguments={"value": 12}
                )
            ]
        elif results and self.mode != "repeat":
            raw = results[-1].result
            try:
                value = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                value = {"text": "Synthetic action declined."}
            text = (
                value.get("text", "Synthetic operation completed.")
                if isinstance(value, dict)
                else "Synthetic operation completed."
            )
            if self.mode == "output":
                text = "word " * 100
            contents = [Content.from_text(text)]
        else:
            name = (
                "write_value"
                if self.mode == "write"
                else "ask_agent"
                if self.mode == "recursion"
                else "lookup"
            )
            arguments = {"value": 12} if name == "write_value" else {"query": "synthetic"}
            contents = [
                Content.from_function_call(
                    call_id=f"call_{len(results)}", name=name, arguments=arguments
                )
            ]
        return ChatResponse(
            messages=[
                Message(role="assistant", contents=contents, message_id=f"message_{len(results)}")
            ],
            response_id=f"reply_{len(results)}",
            model="scripted",
        )

    def _inner_get_response(self, *, messages, options, stream=False, **kwargs) -> Any:
        self.offered = [function.name for function in options.get("tools", [])]
        if stream:

            async def updates():
                response = self.reply(messages)
                for message in response.messages:
                    yield ChatResponseUpdate(
                        role="assistant",
                        contents=message.contents,
                        message_id=message.message_id,
                        model="scripted",
                    )

            return ResponseStream(updates(), finalizer=lambda _: self.reply(messages))

        async def response():
            return self.reply(messages)

        return response()


class SyntheticTools:
    def __init__(self, token, mode="read"):
        self.token, self.mode, self.closed = token, mode, False
        self.entered = asyncio.Event()
        self.writes = []
        self.bindings = {
            "lookup": Binding("generated-evidence"),
            "write_value": Binding("generated-values", True),
            "ask_agent": Binding("recursive"),
        }
        self.functions = [
            FunctionTool(
                name="lookup",
                func=self.lookup,
                result_parser=lambda value: json.dumps(value, allow_nan=False),
            ),
            FunctionTool(
                name="write_value",
                func=self.write_value,
                approval_mode="always_require",
                result_parser=json.dumps,
            ),
            FunctionTool(name="ask_agent", func=self.lookup, result_parser=json.dumps),
        ]

    async def connect(self):
        self.entered.set()
        if self.mode == "timeout":
            await asyncio.Event().wait()

    async def close(self):
        self.closed = True

    async def lookup(self, query: str):
        """Read synthetic evidence."""
        if self.mode == "malformed":
            return "invalid structured result"
        return {
            "text": QUOTE,
            "handle": {
                "item": "generated-document",
                "version": 1,
                "block": 1,
                "quoteSha256": hashlib.sha256(QUOTE.encode()).hexdigest(),
            },
        }

    async def write_value(self, value: int):
        """Write one synthetic value after a frozen human decision."""
        self.writes.append(value)
        return {"text": "Synthetic value stored.", "written": 1}


class SyntheticCredentials:
    def __init__(self):
        self.active, self.issued = {}, []

    async def issue(self, request):
        token = "synthetic-credential-" + uuid4().hex
        self.active[request.run] = token
        self.issued.append(token)
        return token

    async def revoke(self, request):
        self.active.pop(request.run, None)


def fixture(mode="read", store=None, **limits):
    state = store or MemoryRunStore()
    decisions, credentials, models, sources = MemoryDecisionGate(), SyntheticCredentials(), [], []

    def model():
        result = ScriptedClient(mode)
        models.append(result)
        return result

    def tools(request, token):
        result = SyntheticTools(token, mode)
        sources.append(result)
        return result

    runtime = Runtime(
        store=state,
        decisions=decisions,
        credentials=credentials,
        model_factory=model,
        tools_factory=tools,
        **limits,
    )
    return runtime, state, decisions, credentials, models, sources


async def collect(runtime, request):
    return [event async for event in runtime.run(request)]
