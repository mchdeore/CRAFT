"""Exercise queue authorization and path binding through real HTTP transport."""

from uuid import uuid4

import msgspec
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app import create_app
from app.infrastructure.database import metadata as m


def test_queue_http_auth_and_job_path_binding(settings, owner_engine, fake_queue):
    configured = msgspec.structs.replace(settings, allowed_origins="https://testserver")
    users = m.metadata.tables["users"]
    roles = m.metadata.tables["user_roles"]
    credentials = m.metadata.tables["local_credentials"]
    accounts = []
    headers = {"Origin": "https://testserver"}
    routes = [
        ("POST", "/api/library/index/jobs"),
        ("GET", "/api/library/index/jobs"),
        ("GET", "/api/library/index/jobs/generated-missing"),
        ("POST", "/api/library/index/jobs/generated-missing/retry"),
        ("POST", "/api/library/index/jobs/generated-missing/cancel"),
        ("GET", "/api/library/index/queue"),
    ]
    try:
        for role in ("member", "admin"):
            username, password = "queue_" + uuid4().hex, uuid4().hex
            with owner_engine.begin() as connection:
                user_id = connection.execute(
                    users.insert()
                    .values(username=username, display_name="Generated queue account")
                    .returning(users.c.id)
                ).scalar_one()
                connection.execute(roles.insert().values(user_id=user_id, role=role))
                connection.execute(
                    credentials.insert().values(
                        user_id=user_id, password_hash=PasswordHasher().hash(password)
                    )
                )
            accounts.append((user_id, username, password, role))
        with TestClient(create_app(configured), base_url="https://testserver") as http:
            for method, path in routes:
                assert http.request(method, path, json={}, headers=headers).status_code == 401
            for _, username, password, role in accounts:
                http.cookies.clear()
                assert (
                    http.post(
                        "/api/session",
                        json={"username": username, "password": password},
                        headers=headers,
                    ).status_code
                    == 200
                )
                if role == "member":
                    for method, path in routes:
                        assert (
                            http.request(method, path, json={}, headers=headers).status_code == 403
                        )
                    continue
                queued = http.post("/api/library/index/jobs", json={}, headers=headers)
                assert queued.status_code == 200
                job_id = queued.json()["jobId"]
                observed = http.get(f"/api/library/index/jobs/{job_id}")
                assert observed.status_code == 200
                assert observed.json()["jobId"] == job_id
                page = http.get("/api/library/index/jobs", params={"limit": 1})
                assert page.status_code == 200
                assert [job["jobId"] for job in page.json()["jobs"]] == [job_id]
                assert http.get("/api/library/index/jobs", params={"limit": 101}).status_code == 400
                assert (
                    http.get("/api/library/index/jobs", params={"offset": 100001}).status_code
                    == 400
                )
                canceled = http.post(
                    f"/api/library/index/jobs/{job_id}/cancel", json={}, headers=headers
                )
                assert canceled.status_code == 200
                assert canceled.json()["status"] == "canceled"
                retried = http.post(
                    f"/api/library/index/jobs/{job_id}/retry", json={}, headers=headers
                )
                assert retried.status_code == 200
                assert retried.json()["retryOf"] == job_id
                assert http.get("/api/library/index/queue").status_code == 200
    finally:
        with owner_engine.begin() as connection:
            for user_id, *_ in accounts:
                for table in (m.metadata.tables["auth_sessions"], credentials, roles):
                    connection.execute(table.delete().where(table.c.user_id == user_id))
                connection.execute(users.delete().where(users.c.id == user_id))
