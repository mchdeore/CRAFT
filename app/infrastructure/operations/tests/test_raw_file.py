"""Raw file transport authenticates before reading and shares strict Python validation."""

import asyncio
import hashlib
from uuid import uuid4

import msgspec
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app import create_app
from app.infrastructure.access import SIGNED_IN, Caller
from app.infrastructure.database import metadata as m
from app.infrastructure.errors import TooLarge
from app.infrastructure.operations import operation
from app.infrastructure.shapes import RawFile, Shape


class FileStats(Shape):
    sha256: str
    count: int
    file_name: str
    media_type: str


def test_raw_transport_and_auth(settings, owner_engine):
    called = []

    @operation(SIGNED_IN, post="/raw-probe")
    def ingest(caller: Caller, request: RawFile) -> FileStats:
        called.append(True)
        return FileStats(
            sha256=hashlib.sha256(request.content).hexdigest(),
            count=len(request.content),
            file_name=request.file_name,
            media_type=request.media_type,
        )

    username = "raw_" + uuid4().hex
    password = uuid4().hex
    users = m.metadata.tables["users"]
    with owner_engine.begin() as connection:
        user = connection.execute(
            users.insert()
            .values(username=username, display_name="Synthetic upload account")
            .returning(users.c.id)
        ).scalar_one()
        connection.execute(
            m.metadata.tables["user_roles"].insert().values(user_id=user, role="member")
        )
        connection.execute(
            m.metadata.tables["local_credentials"]
            .insert()
            .values(user_id=user, password_hash=PasswordHasher().hash(password))
        )
    configured = msgspec.structs.replace(settings, allowed_origins="https://testserver")
    app = create_app(configured)
    data = b"Simulated byte source."
    headers = {
        "X-File-Name": "source.txt",
        "Content-Type": "text/plain",
        "Origin": "https://testserver",
    }
    try:
        with TestClient(app, base_url="https://testserver") as http:
            assert http.post("/api/raw-probe", content=data, headers=headers).status_code == 401
            huge = b"x" * (RawFile.max_bytes + 1)
            assert http.post("/api/raw-probe", content=huge, headers=headers).status_code == 401
            signed = http.post(
                "/api/session",
                json={"username": username, "password": password},
                headers={"Origin": "https://testserver"},
            )
            assert signed.status_code == 200
            assert (
                http.post(
                    "/api/raw-probe",
                    content=data,
                    headers=headers | {"Origin": "https://elsewhere.invalid"},
                ).status_code
                == 403
            )
            assert http.post("/api/raw-probe", content=huge, headers=headers).status_code == 413
            assert not called
            result = http.post("/api/raw-probe", content=data, headers=headers)
            correct = (
                result.status_code == 200
                and result.json()["sha256"] == hashlib.sha256(data).hexdigest()
            )
            assert correct
            assert (
                http.post(
                    "/api/raw-probe?userId=unknown", content=data, headers=headers
                ).status_code
                == 400
            )
            assert len(called) == 1
        trusted = Caller(
            user_id=user,
            roles=frozenset({"member"}),
            sessions=app.state.sessions,
            settings=configured,
        )
        received = asyncio.run(
            ingest(trusted, RawFile(content=data, file_name="source.txt", media_type="text/plain"))
        )
        correct = received.sha256 == hashlib.sha256(data).hexdigest()
        assert correct
        try:
            asyncio.run(
                ingest(
                    trusted, RawFile(content=huge, file_name="source.txt", media_type="text/plain")
                )
            )
        except TooLarge:
            pass
        else:
            raise AssertionError("Oversized input was accepted.")
    finally:
        with owner_engine.begin() as connection:
            for name in ["auth_sessions", "local_credentials", "user_roles"]:
                table = m.metadata.tables[name]
                connection.execute(table.delete().where(table.c.user_id == user))
            connection.execute(users.delete().where(users.c.id == user))
