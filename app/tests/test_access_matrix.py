"""The golden access matrix matches the registry, and the pipeline gives each caller its row."""

import re
from typing import NamedTuple
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import OPERATION_MODULES, load_operations
from app.infrastructure.access import PUBLIC, SIGNED_IN, Caller
from app.infrastructure.errors import ApiError
from app.infrastructure.operations.pipeline import checked_access, token_allowed
from app.infrastructure.operations.registry import Operation, registry, tool_operations
from app.infrastructure.operations.storage import call_log
from app.infrastructure.queue import JOB_MODULES, job_operations
from app.tests.access_matrix import MATRIX
from app.tests.support import people
from app.tests.support.accounts import ORIGIN, sign_in


class Row(NamedTuple):
    access: str
    http: str
    tool: str
    workspace: str
    mcp: str


def line(name: str, row: Row) -> str:
    """A row as access_matrix.py writes it."""
    return f"{name:<40}{row.access:<17}{row.http:<8}{row.tool:<10}{row.workspace:<11}{row.mcp}"


ROWS = [line.split() for line in MATRIX.strip().splitlines()[1:]]
GOLDEN = {name: Row(*columns) for name, *columns in ROWS}


@pytest.fixture(scope="module")
def operations() -> dict[str, Operation]:
    """The operations the app and its workers declare, without the probes tests declare."""
    load_operations()
    # JOBS keeps every job, even one a test's registry restore has taken out of the registry.
    declared = registry | job_operations()
    modules = {*OPERATION_MODULES, *JOB_MODULES}
    return {
        name: operation
        for name, operation in sorted(declared.items())
        if (operation.run or operation.function).__module__ in modules
    }


def test_the_matrix_lists_every_operation_as_the_registry_declares_it(operations) -> None:
    read = {operation.name for operation in tool_operations(operations, agent=True)}
    declared = {
        name: Row(
            o.access,
            o.method or "-",
            "read" if name in read else o.effects.lower() if o.tool else "-",
            "yes" if token_allowed(o, "workspace") else "-",
            "yes" if token_allowed(o, "mcp") else "-",
        )
        for name, o in operations.items()
    }
    changed = [line(name, row) for name, row in declared.items() if GOLDEN.get(name) != row]
    gone = [name for name in GOLDEN if name not in declared]
    names = [name for name, *_ in ROWS]
    twice = sorted({name for name in names if names.count(name) > 1})
    assert not changed and not gone and not twice, "\n".join(
        ["access_matrix.py differs from the registry; review, then replace or add these rows:"]
        + changed
        + [f"and remove {name}" for name in gone]
        + [f"and list {name} once" for name in twice]
    )


CALLERS = {
    "anonymous": Caller(origin="http"),
    "member": Caller(user_id=uuid4(), roles=frozenset({"member"}), origin="http"),
    "admin": Caller(user_id=uuid4(), roles=frozenset({"member", "admin"}), origin="http"),
    "workspace token": Caller(
        user_id=uuid4(), roles=frozenset({"member"}), origin="workspace", workspace_token_id=uuid4()
    ),
    "mcp token": Caller(
        user_id=uuid4(), roles=frozenset({"member"}), origin="mcp", workspace_token_id=uuid4()
    ),
}


def expected(row: Row, caller: str) -> int:
    """What a row gives a caller: 401 asks it to sign in, 403 refuses it, 200 lets it call."""
    if row.access != PUBLIC and caller == "anonymous":
        return 401
    if row.access not in {PUBLIC, SIGNED_IN} and caller != "admin":
        return 403  # each permission belongs to the admin role
    reach = {"workspace token": row.workspace, "mcp token": row.mcp}.get(caller, "yes")
    return 200 if reach == "yes" else 403


@pytest.mark.parametrize("caller", CALLERS)
def test_the_pipeline_gives_each_caller_its_row(operations, caller) -> None:
    """The access check alone, called directly: no HTTP and no database."""
    listed = GOLDEN.keys() & operations.keys()  # the test above reports the rest
    answers = {}
    for name in listed:
        try:
            checked_access(operations[name], CALLERS[caller])
            answers[name] = 200
        except ApiError as error:
            answers[name] = error.status
    assert answers == {name: expected(GOLDEN[name], caller) for name in listed}


def url(operation: Operation) -> str:
    return "/api" + re.sub(r"\{[^}]+\}", "x", operation.path or "")


@pytest.fixture
def someone(owner_engine):
    """A member, removed with its call_log rows: a `workspace` row left behind would stop the
    next run's migration test from downgrading call_log's origin check."""
    with people.person(owner_engine) as person:
        yield person
        with owner_engine.begin() as connection:
            connection.execute(call_log.delete().where(call_log.c.user_id == person.id))


def test_a_signed_in_browser_without_an_allowed_origin_cannot_change_anything(
    app, someone, operations
) -> None:
    unsafe = [o for o in operations.values() if o.method in {"POST", "PUT", "PATCH", "DELETE"}]
    with TestClient(app, base_url="https://testserver") as browser:
        sign_in(browser, someone.id)
        for headers in [{}, {"Origin": "https://elsewhere.example"}]:
            answers = {o.name: browser.request(o.method, url(o), headers=headers) for o in unsafe}
            assert {name: answer.status_code for name, answer in answers.items()} == dict.fromkeys(
                answers, 403
            )


def test_a_refused_caller_with_an_empty_body_hears_401_or_403_not_400(
    app, someone, operations
) -> None:
    routes = [o for o in operations.values() if o.method and o.access != PUBLIC]
    empty = {"Origin": ORIGIN, "Content-Type": "application/json"}
    with TestClient(app, base_url="https://testserver") as client:
        anonymous = {
            o.name: client.request(o.method, url(o), content=b"", headers=empty) for o in routes
        }
        sign_in(client, someone.id)
        issued = client.post("/api/me/workspace-tokens", json={"label": "matrix"}, headers=empty)
        client.cookies.clear()
        token = {**empty, "Authorization": "Bearer " + issued.json()["token"]}
        refused = [o for o in routes if not token_allowed(o, "workspace")]
        bearer = {
            o.name: client.request(o.method, url(o), content=b"", headers=token) for o in refused
        }
    assert {name: answer.status_code for name, answer in anonymous.items()} == dict.fromkeys(
        anonymous, 401
    )
    assert {name: answer.status_code for name, answer in bearer.items()} == dict.fromkeys(
        bearer, 403
    )
