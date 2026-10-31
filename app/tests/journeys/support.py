"""A person using CRAFT through its HTTP routes, as the browser does.

A journey signs a generated person in, calls routes with the browser's Origin, reads chat
streams and records which operation each call reached, so a guard can check that every route
has a journey. `as_stranger` checks that a second person can't read, change or delete what the
first one made. `runs` serves chat turns and `worker` runs a queue. Each Journey is one person's
own client, so several can run on threads at once.
"""

import json
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, or_, select, text

from app import create_app
from app.infrastructure.access import PUBLIC, SIGNED_IN
from app.infrastructure.config.settings import Settings
from app.infrastructure.operations.registry import Operation, registry
from app.infrastructure.operations.storage import call_log
from app.infrastructure.queue import worker_thread
from app.infrastructure.traffic import traffic_log
from app.jobs.storage import queue_samples
from app.tests.support import people
from app.tests.support.accounts import ORIGIN, sign_in
from app.tests.support.server import live_server

TERMINAL = {"RUN_FINISHED", "RUN_ERROR"}
# The operations journeys have called, for the guard that every route has a journey.
REACHED: set[str] = set()
_reaching = threading.Lock()


def operation_for(method: str, path: str) -> Operation | None:
    """The operation a request reaches: the one whose route matches its method and path."""
    from starlette.routing import compile_path

    for operation in registry.values():
        if operation.method == method and operation.path:
            mount = "" if operation.name.startswith("health.") else "/api"
            if compile_path(mount + operation.path)[0].match(path):
                return operation
    return None


def browser(app: FastAPI) -> TestClient:
    """A browser on the app: HTTPS for the __Host- cookies, the allowed Origin on every call,
    and redirects left to the journey."""
    return TestClient(
        app, base_url="https://testserver", headers={"Origin": ORIGIN}, follow_redirects=False
    )


def run_body(thread: str, text: str, **body: Any) -> dict:
    """What POST /api/agent/runs takes: `text` as a new run in the conversation, or with
    `runId` and `resume`, going on with a run."""
    messages = [{"role": "user", "content": text}] if text else []
    return {"threadId": thread, "runId": str(uuid4()), "messages": messages} | body


class Journey:
    """One person's browser on the app. Their calls are recorded; their rows go at the end."""

    def __init__(self, app: FastAPI, owner: Engine, stack: ExitStack) -> None:
        self.app, self.owner, self.stack = app, owner, stack
        self.users: list[UUID] = []
        self.request_ids: list[str] = []
        self.person = self._person()
        stack.callback(self._remove_rows)  # after the clients close, before the people go
        self.http = stack.enter_context(browser(app))
        self.http.event_hooks["response"].append(self._record)
        self._stranger: TestClient | None = None

    def sign_in(self) -> str:
        """Sign the person in as the provider's callback does; returns the session cookie."""
        return sign_in(self.http, self.person.id)

    @property
    def stranger(self) -> TestClient:
        """A second signed-in member's browser on the app, made on first use."""
        if self._stranger is None:
            stranger = self._person()
            # Not entered: a second lifespan on the app would take /mcp from the first.
            self._stranger = browser(self.app)
            self.stack.callback(self._stranger.close)
            self._stranger.event_hooks["response"].append(self._remember)
            sign_in(self._stranger, stranger.id)
        return self._stranger

    def events(self, response: Any) -> list[dict]:
        """A chat stream's events, which end with RUN_FINISHED or RUN_ERROR."""
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        found = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert found and found[-1]["type"] in TERMINAL, found[-1:]
        return found

    def chat(self, thread: str, text: str, **body: Any) -> tuple[str, list[dict]]:
        """Send `text` in a conversation as a new run (or with `runId` and `resume`, go on with
        one) and read its stream to the end. Returns the run's id and its events."""
        sent = run_body(thread, text, **body)
        return sent["runId"], self.events(self.http.post("/api/agent/runs", json=sent))

    def as_stranger(self, *calls: tuple) -> None:
        """Each call, made by the stranger, answers 404, or 403 where a permission is needed:
        another person's records don't exist for them. A call is (method, path) or (method,
        path, request keyword arguments)."""
        answers, expected = [], []
        for method, path, *options in calls:
            response = self.stranger.request(method, path, **(options[0] if options else {}))
            operation = operation_for(method, urlsplit(path).path)
            needs_permission = operation is not None and operation.access not in {PUBLIC, SIGNED_IN}
            answers.append((method, path, response.status_code))
            expected.append((method, path, 403 if needs_permission else 404))
        assert answers == expected

    def _person(self) -> people.Person:
        person = self.stack.enter_context(people.person(self.owner))
        self.users.append(person.id)
        return person

    def _remember(self, response: Any) -> None:
        self.request_ids.append(response.headers["x-request-id"])

    def _record(self, response: Any) -> None:
        self._remember(response)
        operation = operation_for(response.request.method, response.request.url.path)
        if operation is not None:
            with _reaching:
                REACHED.add(operation.name)

    def _remove_rows(self) -> None:
        """What the people leave without a foreign key to them: their call and traffic log rows
        (anonymous calls by request ID) and the jobs they queued."""
        users = [str(user) for user in self.users]
        jobs = "select id from procrastinate_jobs where args->>'requested_by' = any(:users)"
        with self.owner.begin() as connection:
            for table in (call_log, traffic_log):
                connection.execute(
                    table.delete().where(
                        or_(
                            table.c.user_id.in_(self.users),
                            table.c.request_id.in_(self.request_ids),
                        )
                    )
                )
            connection.execute(
                text(f"delete from job_attempt_errors where job_id in ({jobs})"), {"users": users}
            )
            connection.execute(
                text(f"delete from procrastinate_jobs where id in ({jobs})"), {"users": users}
            )


@pytest.fixture
def journey(app: FastAPI, owner_engine: Engine) -> Iterator[Journey]:
    """A generated member with a browser on the app, not yet signed in."""
    with ExitStack() as stack:
        yield Journey(app, owner_engine, stack)


@contextmanager
def worker(journey: Journey, settings: Settings, queues: list[str]) -> Iterator[None]:
    """A worker on the queues for the journey: a job someone else left queued (a test that
    stopped short) is cancelled first, so it runs only the journey's. Afterwards the rows it
    leaves with no person on them go: its queue samples and the calls that rang a bell. Leave it
    only once the journey's jobs have ended."""
    owner = journey.owner
    with owner.begin() as connection:
        connection.execute(
            text(
                "update procrastinate_jobs set status = 'cancelled'"
                " where status = 'todo' and queue_name = any(:queues)"
                " and not coalesce(args->>'requested_by', '') = any(:users)"
            ),
            {"queues": queues, "users": [str(user) for user in journey.users]},
        )
        sample = connection.execute(select(func.max(queue_samples.c.id))).scalar_one() or 0
        call = connection.execute(select(func.max(call_log.c.id))).scalar_one() or 0
    try:
        with worker_thread(settings, queues):
            yield
    finally:
        with owner.begin() as connection:
            connection.execute(
                queue_samples.delete().where(
                    queue_samples.c.id > sample, queue_samples.c.queue.in_(queues)
                )
            )
            connection.execute(
                call_log.delete().where(
                    call_log.c.id > call,
                    call_log.c.name == "notifications.job_ended",
                    call_log.c.user_id.is_(None),
                )
            )


@contextmanager
def serve_runs(journey: Journey, settings: Settings) -> Iterator[Settings]:
    """Chat turns for the journey with these settings: the agent's tools on a live server, and
    an agent worker. Yields the runs' settings."""
    server = create_app(settings)  # never a TestClient on it: that would take its /mcp
    try:
        with live_server(server):
            with worker(journey, server.state.settings, ["agent"]):
                yield server.state.settings
    finally:
        server.state.engine.dispose()


@pytest.fixture
def runs(journey: Journey, settings: Settings) -> Iterator[Settings]:
    """Chat turns for the journey with the test settings; they stop before its rows go."""
    with serve_runs(journey, settings) as configured:
        yield configured
