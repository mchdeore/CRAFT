"""Configured framework model roles and observed, stateless model requests."""

import asyncio
from contextlib import suppress
from types import TracebackType
from typing import Any, Literal

from agent_framework import Message, ResponseStream
from agent_framework_foundry import FoundryChatClient
from azure.ai.projects.aio import AIProjectClient
from openai import DefaultAsyncHttpxClient

from app.infrastructure.config.settings import Settings, validate_models
from app.infrastructure.errors import ApiError, InvalidInput
from app.infrastructure.operations import observe

Role = Literal["main", "check"]
EMBEDDING_BATCH = 64


class NoProjectIdentity:
    async def get_token(self, *scopes, **kwargs):
        raise ApiError()

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ):
        pass


class KeyProjectClient(AIProjectClient):
    def __init__(self, settings: Settings):
        super().__init__(
            endpoint=settings.model_endpoint, credential=NoProjectIdentity(), logging_enable=False
        )
        self.model_key = settings.model_key

    def get_openai_client(self, agent_name: str | None = None, **kwargs: Any) -> Any:
        return super().get_openai_client(
            agent_name=agent_name,
            **(
                kwargs
                | {
                    "api_key": self.model_key,
                    "max_retries": 0,
                    "timeout": 120,
                    "http_client": transport(),
                }
            ),
        )


def transport():
    """Build the standard verified transport; outside calls are intercepted in checks."""
    return DefaultAsyncHttpxClient(timeout=120)


@observe("agent.model")
async def complete(source):
    try:
        return await source
    except Exception:
        raise ApiError() from None


@observe("agent.model")
async def forward(source, queue):
    try:
        async for update in source:
            await queue.put(update)
    except Exception:
        raise ApiError() from None


def observed_stream(source):
    async def updates():
        queue = asyncio.Queue(maxsize=1)

        async def produce():
            try:
                await forward(source, queue)
                await queue.put(None)
            except Exception:
                await queue.put(ApiError())

        task = asyncio.create_task(produce())
        try:
            while True:
                update = await queue.get()
                if update is None:
                    break
                if isinstance(update, ApiError):
                    raise update
                yield update
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return ResponseStream(updates(), finalizer=lambda _: source.get_final_response())


class ConfiguredChatClient(FoundryChatClient):
    def __init__(self, settings: Settings, role: Role):
        self.selected_model = settings.model_main if role == "main" else settings.model_check
        self.effort = "medium" if role == "main" else "low"
        self.budget = settings.model_max_output_tokens
        super().__init__(project_client=KeyProjectClient(settings), model=self.selected_model)

    def _inner_get_response(self, *, messages, options, stream=False, **kwargs) -> Any:
        if any(
            options.get(key)
            for key in [
                "continuation_token",
                "conversation_id",
                "conversation",
                "previous_response_id",
                "background",
            ]
        ):
            raise InvalidInput()
        sealed = dict(options) | {"store": False, "max_tokens": self.budget}
        source = super()._inner_get_response(
            messages=messages, options=sealed, stream=stream, **kwargs
        )
        return observed_stream(source) if stream else complete(source)

    async def _prepare_options(self, messages, options):
        prepared = await super()._prepare_options(messages, options)
        for field in [
            "extra_body",
            "extra_query",
            "previous_response_id",
            "conversation",
            "background",
        ]:
            prepared.pop(field, None)
        prepared.update(
            store=False,
            model=self.selected_model,
            max_output_tokens=self.budget,
            reasoning={"effort": self.effort},
        )
        return prepared

    async def close(self):
        await self.client.close()
        await self.project_client.close()


def configured(settings: Settings) -> bool:
    return bool(settings.model_key.strip() and settings.model_endpoint.strip())


def client(settings: Settings, role: Role = "main"):
    if role not in {"main", "check"}:
        raise InvalidInput()
    if not configured(settings):
        raise ApiError()
    validate_models(settings)
    try:
        return ConfiguredChatClient(settings, role)
    except Exception:
        raise ApiError() from None


async def request(settings: Settings, messages, role: Role):
    selected = client(settings, role)
    try:
        result = await selected.get_response(
            [Message(role=message.role, contents=[message.text]) for message in messages]
        )
        return result.text, result.model or getattr(selected, "selected_model", "")
    finally:
        await selected.close()


async def embedding(settings: Settings, text: str) -> list[float]:
    return (await embeddings(settings, [text]))[0]


@observe("agent.embedding")
async def embeddings(settings: Settings, texts: list[str]) -> list[list[float]]:
    """Embed texts in batches through one client; vectors keep the input order."""
    if not configured(settings):
        raise ApiError()
    validate_models(settings)
    project = KeyProjectClient(settings)
    remote = project.get_openai_client()
    try:
        vectors = []
        for start in range(0, len(texts), EMBEDDING_BATCH):
            batch = texts[start : start + EMBEDDING_BATCH]
            response = await remote.embeddings.create(
                model=settings.model_embedding,
                input=batch,
                dimensions=1536,
                encoding_format="float",
            )
            values = [row.embedding for row in sorted(response.data, key=lambda row: row.index)]
            if len(values) != len(batch) or any(len(value) != 1536 for value in values):
                raise ApiError()
            vectors.extend(values)
        return vectors
    except Exception:
        raise ApiError() from None
    finally:
        await remote.close()
        await project.close()
