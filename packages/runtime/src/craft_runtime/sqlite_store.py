"""An independent local run-store adapter with short SQLite transactions."""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from agent_framework import Content, Message

from craft_runtime.state import RunRequest, RunState, RuntimeErrorCode


def encode(state: RunState) -> str:
    return json.dumps(asdict(state), allow_nan=False, separators=(",", ":"))


def decode(value: str) -> RunState:
    fields = json.loads(value)
    fields["request"] = RunRequest(**fields["request"])
    return RunState(**fields)


class SQLiteRunStore:
    """Prototype persistence; the application can supply a PostgreSQL adapter later."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                create table if not exists conversations (
                    id text primary key,
                    owner text not null
                );
                create table if not exists runs (
                    seq integer primary key autoincrement,
                    id text not null unique,
                    owner text not null,
                    conversation text not null references conversations(id),
                    status text not null check(status in
                        ('running','paused','finished','failed','cancelled')),
                    state text not null
                );
                create unique index if not exists one_active_conversation
                    on runs(conversation) where status in ('running','paused');
                create index if not exists run_history on runs(conversation,seq desc);
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys=on")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    async def start(self, request: RunRequest) -> RunState:
        if not all(
            isinstance(value, str) and 1 <= len(value) <= 200
            for value in [request.owner, request.conversation, request.run]
        ):
            raise RuntimeErrorCode("invalid_identity")
        if request.resume and request.message is not None:
            raise RuntimeErrorCode("not_paused")
        if not request.resume and (
            not isinstance(request.message, str) or not request.message.strip()
        ):
            raise RuntimeErrorCode("invalid_message")
        with self.connection() as connection:
            connection.execute("begin immediate")
            owner = connection.execute(
                "select owner from conversations where id=?", (request.conversation,)
            ).fetchone()
            if owner is not None and owner["owner"] != request.owner:
                raise RuntimeErrorCode("not_found")
            old = connection.execute("select * from runs where id=?", (request.run,)).fetchone()
            if request.resume:
                if old is None or old["status"] != "paused":
                    raise RuntimeErrorCode("not_paused")
                if (old["owner"], old["conversation"]) != (request.owner, request.conversation):
                    raise RuntimeErrorCode("not_found")
                state = decode(old["state"])
                state.request = request
                state.status = "running"
                connection.execute(
                    "update runs set status=?,state=? where id=?",
                    ("running", encode(state), request.run),
                )
            else:
                if old is not None:
                    raise RuntimeErrorCode("run_replayed")
                if connection.execute(
                    "select 1 from runs where conversation=? and status in ('running','paused')",
                    (request.conversation,),
                ).fetchone():
                    raise RuntimeErrorCode("conversation_busy")
                prior = connection.execute(
                    "select state from runs where conversation=? order by seq desc limit 1",
                    (request.conversation,),
                ).fetchone()
                previous = decode(prior["state"]) if prior is not None else None
                state = RunState(
                    request,
                    messages=previous.messages if previous else [],
                    session=previous.session if previous else {},
                )
                assert isinstance(request.message, str)
                state.messages.append(
                    Message(role="user", contents=[Content.from_text(request.message)]).to_dict()
                )
                connection.execute(
                    "insert or ignore into conversations(id,owner) values (?,?)",
                    (request.conversation, request.owner),
                )
                connection.execute(
                    "insert into runs(id,owner,conversation,status,state) values (?,?,?,?,?)",
                    (request.run, request.owner, request.conversation, state.status, encode(state)),
                )
            return state

    async def save(self, state: RunState) -> None:
        with self.connection() as connection:
            result = connection.execute(
                "update runs set status=?,state=? where id=? and owner=? "
                "and conversation=? and status='running'",
                (
                    state.status,
                    encode(state),
                    state.request.run,
                    state.request.owner,
                    state.request.conversation,
                ),
            )
            if result.rowcount != 1:
                raise RuntimeErrorCode("stale_run")

    async def abort(self, run: str, status: str) -> None:
        with self.connection() as connection:
            row = connection.execute("select state from runs where id=?", (run,)).fetchone()
            if row is None:
                raise RuntimeErrorCode("not_found")
            state = decode(row["state"])
            state.status = status
            connection.execute(
                "update runs set status=?,state=? where id=?", (status, encode(state), run)
            )

    def read(self, request: RunRequest) -> RunState:
        with self.connection() as connection:
            row = connection.execute(
                "select state from runs where id=? and owner=? and conversation=?",
                (request.run, request.owner, request.conversation),
            ).fetchone()
            if row is None:
                raise RuntimeErrorCode("not_found")
            return decode(row["state"])
