import socket
import threading
import time
from collections.abc import Iterator

import msgspec
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from app import create_app
from app.infrastructure.config.settings import Settings
from app.infrastructure.operations.storage import call_log


def app_on_port(settings: Settings, port: int) -> FastAPI:
    return create_app(
        msgspec.structs.replace(settings, database_host="127.0.0.1", database_port=port)
    )


@pytest.fixture
def hung_port() -> Iterator[int]:
    """A socket that accepts connections and never answers: a hung database."""
    listener = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                accepted.append(listener.accept()[0])
            except TimeoutError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield listener.getsockname()[1]
    stop.set()
    thread.join()
    for connection in [*accepted, listener]:
        connection.close()


def test_health_live(settings: Settings) -> None:
    with TestClient(app_on_port(settings, 1)) as client:  # never touches the database
        response = client.get("/health/live")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_health_ready_checks_db_and_head(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_health_probes_add_no_call_log_rows(
    client: TestClient, owner_engine: Engine, last_call_id: int
) -> None:
    for path in ["/health/live", "/health/ready"]:
        assert client.get(path).status_code == 200
    with owner_engine.connect() as connection:
        added = connection.execute(
            select(call_log.c.name).where(
                call_log.c.id > last_call_id, call_log.c.name.startswith("health.")
            )
        ).all()
    assert added == []


def test_queue_health_is_gone(client: TestClient) -> None:
    """Queue numbers are for `craft queues report`, not anonymous callers."""
    assert client.get("/health/queue").status_code == 404


def test_health_ready_503_when_db_down(settings: Settings) -> None:
    with TestClient(app_on_port(settings, 1)) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


@pytest.mark.slow
def test_health_ready_503_within_3_s_when_db_hangs(settings: Settings, hung_port: int) -> None:
    with TestClient(app_on_port(settings, hung_port)) as client:
        started = time.perf_counter()
        response = client.get("/health/ready")
        elapsed = time.perf_counter() - started
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
    assert elapsed < 3.5


def test_health_ready_503_when_migrations_are_behind(settings: Settings) -> None:
    """A database without the code's migration is not ready: here, one never migrated."""
    app = create_app(msgspec.structs.replace(settings, database_name="postgres"))
    app.state.migration_head = "0001"
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
