"""Deterministic scripted model responses and separate hash-derived embeddings."""

import hashlib
import html
import json
import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

import msgspec
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
        handle = value.get("handle")
        if (
            isinstance(value.get("text"), str)
            and isinstance(handle, dict)
            and hashlib.sha256(value["text"].encode()).hexdigest() == handle.get("quoteSha256")
        ):
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


def summary_task(question: str):
    try:
        task = json.loads(question)
    except ValueError:
        return None
    if not isinstance(task, dict) or task.get("task") != "summarize":
        return None
    sentences = []
    filings = []
    patterns = {
        "mission": r"\bmission(?:\s+key)?[\s|:=]*(msn-[a-z0-9-]+|M-SYN-\d+)",
        "silo": r"\bsilo[\s|:=]*(engineering|requirements|quality|reviews|budget|launch)",
        "stage": r"\bstage[\s|:=]*(st[1-7]-[a-z-]+|[BC])\b",
    }
    for block in task["blocks"]:
        for sentence in re.split(r"(?<=[.!?])\s+", block["text"].strip()):
            if sentence and len(sentences) < 2:
                sentences.append({"text": sentence, "blocks": [block["block"]]})
        context = block["text"] + " " + block.get("headingPath", "")
        for field, pattern in patterns.items():
            for matched in re.finditer(pattern, context, re.IGNORECASE):
                filings.append({"field": field, "value": matched[1], "blocks": [block["block"]]})
    return {"sentences": sentences, "filings": filings}


def catalog_task(question: str):
    question = re.sub(r"\s+", " ", question.casefold()).strip(" .?!")
    listing = (
        r"(?:please )?(?:list|show|enumerate)"
        r"(?: (?:me|my|the|all|available|saved|installed))* "
    )
    if re.fullmatch(listing + r"skills", question) or re.fullmatch(
        r"(?:what|which) (?:saved )?skills (?:are available|are installed|do you have)", question
    ):
        return "skills"
    if re.fullmatch(listing + r"tools", question) or re.fullmatch(
        r"(?:what|which) tools (?:can you use|are available|do you have)", question
    ):
        return "tools"
    documents = (
        r"(?:please )?(?:list|show|enumerate)"
        r"(?: (?:me|my|the|all|available|readable|indexed|current))* "
        r"(?:documents|files|database)(?: in (?:the )?database)?"
    )
    if (
        re.fullmatch(documents, question)
        or re.fullmatch(
            r"(?:what|which)(?: (?:readable|indexed|current|available))* documents "
            r"(?:are in (?:the )?database|can (?:you|i|we) read|are available)",
            question,
        )
        or re.fullmatch(r"what is in (?:the )?database", question)
    ):
        return "documents"
    return None


def checked_catalog(value, kind: str, depth: int = 0):
    from app.library.handlers import DocumentCatalog
    from app.tools.handlers import CapabilityCatalog

    if depth > 3:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        if value.get("isError") is True:
            return None
        try:
            if kind == "documents":
                return msgspec.convert(value, DocumentCatalog, strict=True)
            catalog = msgspec.convert(value, CapabilityCatalog, strict=True)
            return None if catalog.skills else catalog
        except (msgspec.ValidationError, TypeError):
            pass
        if "structuredContent" in value:
            return checked_catalog(value["structuredContent"], kind, depth + 1)
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            return checked_catalog(value["text"], kind, depth + 1)
        if isinstance(value.get("content"), list):
            return checked_catalog(value["content"], kind, depth + 1)
    elif isinstance(value, list):
        for block in value:
            if isinstance(block, dict) and block.get("type") == "text":
                found = checked_catalog(block, kind, depth + 1)
                if found is not None:
                    return found
    return None


def catalog_text(value: str):
    value = "".join(char for char in value if not unicodedata.category(char).startswith("C"))
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", html.escape(value))


def catalog_summary(catalog, kind: str):
    if kind == "skills":
        return "Saved skills are not installed. No saved skills are available."
    if kind == "tools":
        lines = ["Available tools:", "Saved skills are not installed."]
        rows = [f"- `{tool.name}`: {catalog_text(tool.description)}" for tool in catalog.tools]
    else:
        lines = ["Readable indexed documents (default result limit 25; source scan limit 200):"]
        if catalog.truncated:
            lines.append(
                "Listing truncated at a scan or result limit; more readable documents may exist."
            )
        if not catalog.documents:
            lines.append("No currently readable indexed documents were returned.")
        rows = [
            f"- {catalog_text(row.title)} — version {row.version}; {row.classification}; "
            + ("simulated" if row.simulated else "not simulated")
            for row in catalog.documents
        ]
    size = sum(len(line) + 1 for line in lines)
    for row in rows:
        if size + len(row) + 1 > 11900:
            lines.append(
                "Catalog display limit reached; this answer omits additional catalog rows."
            )
            break
        lines.append(row)
        size += len(row) + 1
    return "\n".join(lines)


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
        task = summary_task(question)
        catalog_kind = catalog_task(question)
        if task is not None:
            contents = [Content.from_text(json.dumps(task))]
        elif self.role == "check":
            contents = [
                Content.from_text(json.dumps({"supported": bool(tool_results), "simulated": True}))
            ]
        elif catalog_kind is not None:
            if tool_results:
                catalog = next(
                    (
                        result
                        for content in tool_results
                        if (result := checked_catalog(content.result, catalog_kind)) is not None
                    ),
                    None,
                )
                contents = [
                    Content.from_text(
                        catalog_summary(catalog, catalog_kind)
                        if catalog is not None
                        else "No checked readable catalog was returned."
                    )
                ]
            else:
                contents = [
                    Content.from_function_call(
                        call_id="call_" + signature[:16],
                        name="list_documents"
                        if catalog_kind == "documents"
                        else "list_capabilities",
                        arguments={},
                    )
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
                text = " ".join(
                    content.text or json.dumps(content.arguments or {})
                    for message in response.messages
                    for content in message.contents
                )
                budget = options.get("max_tokens") or 4000
                if (
                    len(re.findall(r"\w+|[^\w\s]", text)) > budget
                    or len(text.encode()) > budget * 16
                ):
                    from app.infrastructure.errors import ApiError

                    raise ApiError()
                for message in response.messages:
                    yield ChatResponseUpdate(
                        role="assistant",
                        contents=message.contents,
                        message_id=message.message_id,
                        model="fake",
                    )

            source = ResponseStream(updates(), finalizer=lambda _: self.reply(messages))
            from app.infrastructure.operations.context import current_call

            if current_call.get() is not None:
                from app.agent.model import observed_stream

                return observed_stream(source)
            return source

        async def complete():
            return self.reply(messages)

        return complete()
