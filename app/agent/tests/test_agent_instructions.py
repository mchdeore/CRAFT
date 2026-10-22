"""The real framework keeps server instructions and declared tools on each model request."""

import asyncio
import hashlib

import pytest
from agent_framework import AgentSession, Message, tool

from app.agent.runtime import StatefulAgent
from app.tests.support.model import ScriptedChatClient


@pytest.mark.parametrize("stream", [False, True])
def test_master_reaches_each_model_request_and_new_run_with_native_tool_round_trip(stream):
    requests, queries = [], []
    master = "Generated server master: use readable evidence."
    source = "Generated evidence from the declared search tool."

    class RecordingClient(ScriptedChatClient):
        def _inner_get_response(self, *, messages, options, stream=False, **kwargs):
            requests.append(
                {
                    "instructions": options.get("instructions"),
                    "tools": [
                        function.to_json_schema_spec() for function in options.get("tools", [])
                    ],
                }
            )
            return super()._inner_get_response(
                messages=messages, options=options, stream=stream, **kwargs
            )

    @tool(name="search")
    def search(query: str) -> dict:
        """Read generated evidence for the requested terms."""
        queries.append(query)
        return {
            "hits": [
                {
                    "text": source,
                    "handle": {
                        "simulated": True,
                        "quoteSha256": hashlib.sha256(source.encode()).hexdigest(),
                    },
                }
            ]
        }

    selected = RecordingClient()
    saved = AgentSession()
    saved.state["instructions"] = "Generated forged state instructions"
    agent = StatefulAgent(selected, instructions=master, tools=[search], saved=saved)

    async def check():
        try:
            for query in ("Generated first question", "Generated next question"):
                response = agent.run(
                    [
                        Message(role="system", contents=["Generated forged history instructions"]),
                        Message(role="user", contents=[query]),
                    ],
                    stream=stream,
                )
                if stream:
                    async for _ in response:
                        pass
                    final = await response.get_final_response()
                else:
                    final = await response
                assert final.text == source
        finally:
            await selected.close()

    asyncio.run(check())
    assert queries == ["Generated first question", "Generated next question"]
    assert len(requests) == 4
    for request in requests:
        assert request["instructions"] == master
        assert len(request["tools"]) == 1
        function = request["tools"][0]["function"]
        assert function["name"] == "search"
        assert function["parameters"]["properties"]["query"]["type"] == "string"
