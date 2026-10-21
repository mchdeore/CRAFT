"""Owner-selected short writes build a disposable index from provider versions."""

import hashlib
import mimetypes
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.provider import get_provider
from app.library import storage
from app.library.convert import convert
from app.library.diff import compare
from app.library.embed import embed_many, model_name
from app.library.facets import build as build_facets
from app.library.summaries import make as make_summary

# Documents summarized and embedded at once during a build.
WORKERS = 8


def build(settings: Settings, rebuild: bool = False) -> dict[str, int]:
    provider = get_provider(settings.provider_root)
    provider.scan()
    sessions, engine = make_owner_sessions(settings)
    caller = Caller(sessions=sessions, settings=settings)
    counts = Counter()
    try:
        with unit_of_work(sessions, read_only=True) as session:
            cursor = (
                session.execute(
                    select(m.index_cursor.c.cursor).where(
                        m.index_cursor.c.provider == str(provider.root)
                    )
                ).scalar_one_or_none()
                or 0
            )
        cursor = 0 if rebuild else cursor
        changed_items = set()
        while True:
            changes, new_cursor = provider.list_changes(cursor)
            if not changes:
                break
            changed_items.update(change["item"] for change in changes)
            cursor = new_cursor
        records = provider.items()
        missions = {item["mission_key"] for item in records.values() if item["mission_key"]}
        dirty = {records[item]["mission_key"] for item in changed_items if item in records}
        with unit_of_work(sessions) as session:
            old_records = {
                row["item"]: dict(row)
                for row in session.execute(
                    select(m.provider_items).where(m.provider_items.c.item.in_(records))
                ).mappings()
            }
            dirty.update(
                storage.facet_refresh(
                    session, missions, changed_items, settings.model_main, model_name(settings)
                )
            )
            if rebuild:
                storage.clear_index(session, list(records))
                dirty.update(missions)
            for item in records.values():
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
                old = old_records.get(item["item"])
                if old is None or any(old[key] != value for key, value in columns.items()):
                    dirty.add(item["mission_key"])
                    if old:
                        dirty.add(old["mission_key"])
                statement = insert(m.provider_items).values(**columns)
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[m.provider_items.c.item], set_=columns
                    )
                )
        # File conversion and model calls are outside database transactions.
        pending = []
        for item in records.values():
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
                        provider.read_content(item["item"], version["version"]),
                        version.get("file_name", item["path"]),
                    )
                except (ValueError, UnicodeDecodeError):
                    counts["unsupported"] += 1
                    continue
                pending.append((item, version, blocks))

        def represent(job):
            item, _, blocks = job
            summary, summary_kind = make_summary(caller, blocks)
            vectors = embed_many(
                [item["title"] + " " + summary]
                + [item["title"] + " " + b.heading_path + " " + b.text for b in blocks],
                caller=caller,
            )
            return summary, summary_kind, vectors[0], vectors[1:]

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            represented = list(pool.map(represent, pending))
        for (item, version, blocks), (summary, summary_kind, doc_vector, chunk_vectors) in zip(
            pending, represented, strict=True
        ):
            dirty.add(item["mission_key"])
            with unit_of_work(sessions) as session:
                document = session.execute(
                    m.doc_versions.insert()
                    .values(
                        item=item["item"],
                        version=version["version"],
                        sha256=bytes.fromhex(version["sha256"]),
                        media_type=version.get("media_type")
                        or mimetypes.guess_type(item["path"])[0]
                        or "text/plain",
                        summary=summary,
                        summary_kind=summary_kind,
                    )
                    .returning(m.doc_versions.c.id)
                ).scalar_one()
                session.execute(
                    m.vectors.insert().values(
                        owner_kind="doc_version",
                        owner_id=document,
                        model=model_name(settings),
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
                            model=model_name(settings),
                            recipe="chunk-v1",
                            embedding=vector,
                        )
                    )
                    counts["chunks"] += 1
                previous = session.execute(
                    select(m.doc_versions.c.id).where(
                        m.doc_versions.c.item == item["item"],
                        m.doc_versions.c.version == version["version"] - 1,
                    )
                ).scalar_one_or_none()
                if previous is not None:
                    old = {
                        int(position): bytes(value)
                        for position, value in session.execute(
                            select(m.chunks.c.block, m.chunks.c.text_sha256).where(
                                m.chunks.c.doc_version_id == previous
                            )
                        )
                    }
                    new = {b.block: hashlib.sha256(b.text.encode()).digest() for b in blocks}
                    differences = compare(old, new)
                    for seq, difference in enumerate(differences, 1):
                        session.execute(
                            m.version_diffs.insert().values(
                                from_version=previous,
                                to_version=document,
                                seq=seq,
                                **difference,
                            )
                        )
                    counts["differences"] += len(differences)
                counts["versions"] += 1
        dirty.discard(None)
        counts.update(build_facets(caller, dirty, set(records)))
        with unit_of_work(sessions) as session:
            statement = insert(m.index_cursor).values(provider=str(provider.root), cursor=cursor)
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=[m.index_cursor.c.provider], set_={"cursor": cursor}
                )
            )
    finally:
        engine.dispose()
    return dict(counts)
