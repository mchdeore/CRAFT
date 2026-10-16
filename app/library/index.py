"""Owner-selected short writes build a disposable index from provider versions."""

import hashlib
import mimetypes
from collections import Counter

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.convert import convert
from app.library.embed import MODEL, embed


def build(settings: Settings, rebuild: bool = False) -> dict[str, int]:
    provider = SimulatedProvider(settings.provider_root)
    provider.scan()
    sessions, engine = make_owner_sessions(settings)
    counts = Counter()
    try:
        with unit_of_work(sessions) as session:
            if rebuild:
                session.execute(
                    m.vectors.delete().where(m.vectors.c.owner_kind.in_(["chunk", "doc_version"]))
                )
                session.execute(m.version_diffs.delete())
                session.execute(m.chunks.delete())
                session.execute(m.doc_versions.delete())
            for item in provider.items().values():
                columns = {
                    k: item[k]
                    for k in [
                        "item",
                        "path",
                        "title",
                        "mission_key",
                        "silo",
                        "stage",
                        "simulated",
                        "classification",
                        "deleted_at",
                    ]
                }
                from datetime import datetime

                columns["deleted_at"] = (
                    datetime.fromisoformat(columns["deleted_at"]) if columns["deleted_at"] else None
                )
                statement = insert(m.provider_items).values(**columns)
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[m.provider_items.c.item], set_=columns
                    )
                )
        # File conversion and embeddings are outside database transactions.
        for item in provider.items().values():
            for version in item["versions"]:
                with unit_of_work(sessions, read_only=True) as session:
                    present = session.execute(
                        select(m.doc_versions.c.id).where(
                            m.doc_versions.c.item == item["item"],
                            m.doc_versions.c.version == version["version"],
                        )
                    ).first()
                if present:
                    continue
                try:
                    blocks = convert(
                        provider.read_content(item["item"], version["version"]), item["path"]
                    )
                except (ValueError, UnicodeDecodeError):
                    counts["unsupported"] += 1
                    continue
                summary = " ".join(" ".join(b.text for b in blocks).split()[:150])
                doc_vector = embed(item["title"] + " " + summary)
                chunk_vectors = [
                    embed(item["title"] + " " + b.heading_path + " " + b.text) for b in blocks
                ]
                with unit_of_work(sessions) as session:
                    document = session.execute(
                        m.doc_versions.insert()
                        .values(
                            item=item["item"],
                            version=version["version"],
                            sha256=bytes.fromhex(version["sha256"]),
                            media_type=mimetypes.guess_type(item["path"])[0] or "text/plain",
                            summary=summary,
                            summary_kind="extract",
                        )
                        .returning(m.doc_versions.c.id)
                    ).scalar_one()
                    session.execute(
                        m.vectors.insert().values(
                            owner_kind="doc_version",
                            owner_id=document,
                            model=MODEL,
                            recipe="doc-v1",
                            embedding=doc_vector,
                        )
                    )
                    for block, vector in zip(blocks, chunk_vectors, strict=True):
                        chunk = session.execute(
                            m.chunks.insert()
                            .values(
                                doc_version_id=document,
                                block=block.block,
                                kind=block.kind,
                                heading_path=block.heading_path,
                                locator=block.locator,
                                text=block.text,
                                text_sha256=hashlib.sha256(block.text.encode()).digest(),
                            )
                            .returning(m.chunks.c.id)
                        ).scalar_one()
                        session.execute(
                            m.vectors.insert().values(
                                owner_kind="chunk",
                                owner_id=chunk,
                                model=MODEL,
                                recipe="chunk-v1",
                                embedding=vector,
                            )
                        )
                        counts["chunks"] += 1
                    counts["versions"] += 1
        with unit_of_work(sessions) as session:
            cursor = 0
            while True:
                changes, new = provider.list_changes(cursor)
                if not changes:
                    break
                cursor = new
            statement = insert(m.index_cursor).values(provider=str(provider.root), cursor=cursor)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[m.index_cursor.c.provider], set_={"cursor": cursor}
                )
            )
    finally:
        engine.dispose()
    return dict(counts)
