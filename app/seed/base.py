"""Load made-up base users, scopes and provider documents as the owner."""

import json
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.config.settings import Settings
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.metadata import metadata, mission_grants
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.paths import ROOT
from app.infrastructure.provider.simulated import SimulatedProvider


def seed(settings: Settings) -> dict[str, int]:
    root = Path(settings.provider_root)
    for source in (ROOT / "seeds/base/provider").rglob("*"):
        if source.is_file():
            target = root / source.relative_to(ROOT / "seeds/base/provider")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    SimulatedProvider(root, clock="2026-01-01T00:00:00+00:00").scan()
    sessions, engine = make_owner_sessions(settings)
    users = metadata.tables["users"]
    roles = metadata.tables["user_roles"]
    try:
        with unit_of_work(sessions) as session:
            for row in json.loads((ROOT / "seeds/base/users.json").read_text()):
                session.execute(
                    insert(users)
                    .values(username=row["username"], display_name=row["display_name"])
                    .on_conflict_do_nothing()
                )
                user = session.execute(
                    select(users.c.id).where(users.c.username == row["username"])
                ).scalar_one()
                session.execute(
                    insert(roles).values(user_id=user, role=row["role"]).on_conflict_do_nothing()
                )
            for row in json.loads((ROOT / "seeds/base/grants.json").read_text()):
                user = session.execute(
                    select(users.c.id).where(users.c.username == row["username"])
                ).scalar_one()
                session.execute(
                    insert(mission_grants)
                    .values(user_id=user, mission_key=row["mission"], silo=row["silo"])
                    .on_conflict_do_nothing()
                )
    finally:
        engine.dispose()
    return {"users": 3, "documents": 5}
