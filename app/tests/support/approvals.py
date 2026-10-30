"""Two generated people, a conversation and a run with a pending-decision tool operation."""

import json
from contextlib import contextmanager
from uuid import UUID, uuid4

import msgspec
import pytest
from agent_framework import ChatResponse, Content, Message
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import create_app
from app.agent import service as agent_service
from app.agent.handlers import RunAgentInput
from app.approvals.handlers import ApprovalRequest
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.database.metadata import metadata
from app.infrastructure.mcp.tests.http_fixture import live_server
from app.infrastructure.operations import DATA, operation
from app.infrastructure.operations.registry import registry
from app.infrastructure.shapes import Shape
from app.tests.support.chat import agent_worker
from app.tests.support.model import ScriptedChatClient
from app.tests.support.people import person


class MutationInput(Shape):
    amount: int


@pytest.fixture
def context(settings, owner_engine):
    """A requester and a foreign person sharing one password, the requester's conversation with
    a started run, and a tool operation that needs a decision. Every row of theirs goes after."""
    snapshot = registry.copy()

    @operation(SIGNED_IN, post="/decision-fixture", tool=True, effects=DATA)
    def proposed_mutation(caller: Caller, request: MutationInput) -> None:
        """A generated mutation declaration for approval records."""
        raise AssertionError("requesting a decision must not execute")

    operation_name = proposed_mutation.__operation__.name
    configured = msgspec.structs.replace(settings, allowed_origins="https://testserver")
    app = create_app(configured)
    password = uuid4().hex
    conversations = metadata.tables["conversations"]
    try:
        with (
            person(owner_engine, password=password) as requester,
            person(owner_engine, password=password) as foreign,
        ):
            with owner_engine.begin() as db:
                conversation = db.execute(
                    conversations.insert()
                    .values(owner_id=requester.id, title="Generated approval conversation")
                    .returning(conversations.c.id)
                ).scalar_one()
            caller = Caller(
                user_id=requester.id,
                roles=frozenset({"member"}),
                sessions=app.state.sessions,
                settings=configured,
            )
            run = uuid4()
            data = RunAgentInput(
                thread_id=conversation,
                run_id=run,
                messages=[{"role": "user", "content": "Generated request"}],
            )
            agent_service.start(caller, data, "fixture-version")

            def proposal(amount=12, interrupt="native-call"):
                return ApprovalRequest(
                    run_id=run,
                    conversation_id=conversation,
                    operation=operation_name,
                    arguments={"amount": amount},
                    evidence=[],
                    reasoning="Store a generated amount.",
                    retrieval_path=[],
                    interrupt_id=interrupt,
                )

            with agent_worker(owner_engine, configured) as worker:
                yield {
                    "caller": caller,
                    "proposal": proposal,
                    "owner": owner_engine,
                    "settings": configured,
                    "users": [(someone.id, someone.username) for someone in (requester, foreign)],
                    "password": password,
                    "run": run,
                    "conversation": conversation,
                    "operation": operation_name,
                    "worker": worker,
                }
    finally:
        app.state.engine.dispose()
        registry.clear()
        registry.update(snapshot)


def login(http, context, index=0):
    response = http.post(
        "/api/session",
        json={"username": context["users"][index][1], "password": context["password"]},
        headers={"Origin": "https://testserver"},
    )
    assert response.status_code == 200


def decision_call(context, response):
    logs = metadata.tables["call_log"]
    with context["owner"].connect() as db:
        return (
            db.execute(
                select(logs).where(
                    logs.c.request_id == UUID(response.headers["x-request-id"]),
                    logs.c.name == "approvals.decide_approval",
                )
            )
            .mappings()
            .one()
        )


def latest_auth_session(context, index=0):
    sessions = metadata.tables["auth_sessions"]
    with context["owner"].connect() as db:
        return db.execute(
            select(sessions.c.id)
            .where(sessions.c.user_id == context["users"][index][0])
            .order_by(sessions.c.created_at.desc(), sessions.c.id.desc())
            .limit(1)
        ).scalar_one()


class WriteClient(ScriptedChatClient):
    def reply(self, messages):
        results = [
            content
            for message in messages
            for content in message.contents
            if content.type == "function_result"
        ]
        content = (
            Content.from_text("Generated write decision processed.")
            if results
            else (
                Content.from_function_call(
                    call_id="generated-note-call",
                    name="write_note",
                    arguments={"title": "Generated approved scratchpad", "body": "12"},
                )
            )
        )
        return ChatResponse(messages=[Message(role="assistant", contents=[content])])


def events(response):
    assert response.status_code == 200
    return [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


@contextmanager
def runtime_client(context):
    app = create_app(context["settings"])
    try:
        with live_server(app), TestClient(app, base_url="https://testserver") as http:
            http.headers["Origin"] = "https://testserver"
            login(http, context)
            # Runs execute on the agent worker, which calls this app's MCP endpoint.
            with context["worker"].using(app.state.settings):
                yield http
    finally:
        app.state.engine.dispose()


def clear_run_approvals(context, identifier):
    with context["owner"].begin() as connection:
        requests = metadata.tables["approval_requests"]
        ids = select(requests.c.id).where(requests.c.run_id == identifier)
        connection.execute(
            metadata.tables["approval_events"]
            .delete()
            .where(metadata.tables["approval_events"].c.request_id.in_(ids))
        )
        connection.execute(requests.delete().where(requests.c.run_id == identifier))
