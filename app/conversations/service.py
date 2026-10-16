"""Ownership is checked in each short conversation transaction."""

import hashlib
import re
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, select
from starlette.responses import Response

from app.conversations import storage
from app.infrastructure.database import metadata as m
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.operations.context import current_call


def scope(caller):
    if caller.user_id is None or caller.sessions is None:
        raise NotFound()
    return caller.sessions


def load(caller, identifier):
    with unit_of_work(scope(caller), read_only=True) as session:
        if storage.read_owned(session, caller.user_id, identifier) is None:
            raise NotFound()
        return storage.history(session, identifier)


def append(caller, request):
    with unit_of_work(scope(caller)) as session:
        if storage.read_owned(session, caller.user_id, request.id) is None:
            raise NotFound()
        run = session.execute(
            select(m.metadata.tables["agent_runs"].c.id).where(
                m.metadata.tables["agent_runs"].c.id == request.run,
                m.metadata.tables["agent_runs"].c.conversation_id == request.id,
                m.metadata.tables["agent_runs"].c.started_by == caller.user_id,
            )
        ).first()
        if run is None:
            raise NotFound()
        storage.append(session, request.id, request.run, request.messages)


def owned(caller, session, identifier, *, active=True, lock=False):
    row = storage.read_owned(session, caller.user_id, identifier, active=active, lock=lock)
    if row is None:
        raise NotFound()
    context = current_call.get()
    if context is not None:
        context.caller = replace(context.caller, conversation_id=identifier)
    return row


def summary(row) -> dict[str, Any]:
    return dict(
        id=row["id"],
        title=row["title"],
        updated_at=row["updated_at"].isoformat(),
        archived=row["archived_at"] is not None,
        pending_approvals=0,
    )


def list_owned(caller):
    with unit_of_work(scope(caller), read_only=True) as session:
        return [summary(row) for row in storage.summaries(session, caller.user_id)]


def create(caller, title):
    with unit_of_work(scope(caller)) as session:
        return summary(storage.create(session, caller.user_id, title))


def read(caller, identifier) -> dict[str, Any]:
    with unit_of_work(scope(caller), read_only=True) as session:
        row = owned(caller, session, identifier, active=False)
        return summary(row) | {
            "messages": [
                {key: value for key, value in message.items() if not key.startswith("_")}
                for message in storage.history(session, identifier)
            ]
        }


def update(caller, request):
    with unit_of_work(scope(caller)) as session:
        storage.serialise_runs(session, caller.user_id)
        owned(caller, session, request.id, active=False, lock=True)
        values: dict[str, Any] = {"updated_at": func.now()}
        if request.title is not None:
            values["title"] = request.title
        if request.archived is not None:
            if storage.active_run(session, request.id):
                raise Conflict()
            values["archived_at"] = func.now() if request.archived else None
            if request.archived:
                storage.clear_files(session, request.id)
        return summary(storage.update_conversation(session, request.id, values))


def file_shape(row) -> dict[str, Any]:
    keys = [
        "id",
        "conversation_id",
        "name",
        "media_type",
        "size_bytes",
        "origin",
        "state",
        "simulated",
        "classification",
        "status_detail",
        "export_approval_id",
    ]
    return {key: row[key] for key in keys} | {"created_at": row["created_at"].isoformat()}


def files(caller, identifier):
    with unit_of_work(scope(caller), read_only=True) as session:
        owned(caller, session, identifier)
        return [file_shape(row) for row in storage.file_rows(session, identifier)]


def upload(caller, request, *, origin="upload"):
    name = request.file_name
    if name in {".", ".."} or any(c in name for c in "/\\") or any(ord(c) < 32 for c in name):
        raise InvalidInput()
    if not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", request.media_type):
        raise InvalidInput()
    with unit_of_work(scope(caller)) as session:
        owned(caller, session, request.id, lock=True)
        if storage.file_count(session, request.id) >= 50:
            raise Conflict()
        identifier = storage.insert_file(
            session,
            dict(
                conversation_id=request.id,
                name=name,
                media_type=request.media_type,
                size_bytes=len(request.content),
                sha256=hashlib.sha256(request.content).digest(),
                bytes=request.content,
                origin=origin,
                state="uploading",
                simulated=request.simulated,
                classification=request.classification,
            ),
        )
    with unit_of_work(scope(caller)) as session:
        owned(caller, session, request.id, lock=True)
        storage.file_state(session, identifier, "processing")
    with unit_of_work(scope(caller)) as session:
        owned(caller, session, request.id, lock=True)
        row = storage.file_state(session, identifier, "ready")
        if row is None:
            raise NotFound()
        return file_shape(row)


def content(caller, request):
    with unit_of_work(scope(caller), read_only=True) as session:
        owned(caller, session, request.id)
        row = storage.read_file(session, request.id, request.file_id)
        if row is None:
            raise NotFound()
        return Response(
            row["bytes"],
            media_type=row["media_type"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(row["name"], safe=""),
            },
        )


def delete(caller, request):
    with unit_of_work(scope(caller)) as session:
        owned(caller, session, request.id, lock=True)
        if storage.remove_file(session, request.id, request.file_id) is None:
            raise NotFound()
