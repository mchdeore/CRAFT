"""Owner-selected short writes build a disposable index from provider versions."""

import hashlib
import mimetypes
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import case, select, text
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.access import Caller
from app.infrastructure.config.settings import Settings
from app.infrastructure.database import metadata as m
from app.infrastructure.database.engine import make_owner_sessions
from app.infrastructure.database.unit_of_work import unit_of_work
from app.infrastructure.errors import ApiError
from app.infrastructure.provider import get_provider
from app.library import storage
from app.library.convert import convert
from app.library.diff import compare
from app.library.embed import embed_many, model_name
from app.library.facets import build as build_facets
from app.library.summaries import make as make_summary

# Documents summarized and embedded at once during a build.
WORKERS = 8
# Model calls for one document are tried this often, waiting 2, 4 then 8 seconds between.
ATTEMPTS = 4


def build(settings: Settings, rebuild: bool = False, *, enrich_ocr: bool = False) -> dict[str, int]:
    provider = get_provider(settings.provider_root)
    sessions, engine = make_owner_sessions(settings)
    caller = Caller(sessions=sessions, settings=settings)
    counts = Counter()
    lock = None
    try:
        # A dedicated autocommit connection serializes CLI and worker builds without
        # holding a service transaction open across conversion or model calls.
        lock = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        lock.execute(text("SELECT pg_advisory_lock(1129464134, 1)"))
        provider.scan()
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
        change_kinds: dict[str, set[str]] = {}
        while True:
            changes, new_cursor = provider.list_changes(cursor)
            if not changes:
                break
            changed_items.update(change["item"] for change in changes)
            for change in changes:
                if (item := change.get("item")) and (kind := change.get("kind")):
                    change_kinds.setdefault(item, set()).add(kind)
            cursor = new_cursor
        new_items = {item for item, kinds in change_kinds.items() if "added" in kinds}
        changed_existing = {
            item
            for item, kinds in change_kinds.items()
            if "changed" in kinds and item not in new_items
        }
        moved_items = {item for item, kinds in change_kinds.items() if "moved" in kinds}
        deleted_items = {item for item, kinds in change_kinds.items() if "deleted" in kinds}
        for key, value in [
            ("new_items", len(new_items)),
            ("changed_items", len(changed_existing)),
            ("moved_items", len(moved_items)),
            ("deleted_items", len(deleted_items)),
        ]:
            if value:
                counts[key] += value
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
                if present and not rebuild:
                    continue
                try:
                    content = provider.read_content(
                        item["item"], version["version"], max_bytes=settings.index_max_bytes
                    )
                except ValueError:
                    counts["oversized"] += 1
                    continue
                try:
                    blocks = convert(content, version.get("file_name", item["path"]))
                except (ValueError, UnicodeDecodeError):
                    media_type = version.get("media_type", "")
                    if (
                        enrich_ocr
                        and isinstance(media_type, str)
                        and (media_type == "application/pdf" or media_type.startswith("image/"))
                    ):
                        # OCR-only versions have no searchable text or embeddings.
                        blocks = []
                    else:
                        counts["unsupported"] += 1
                        continue
                pending.append((item, version, blocks))

        def represent(job):
            item, _, blocks = job
            if not blocks:
                return "", "extract", None, []
            attempt = 0
            while True:
                try:
                    summary, summary_kind = make_summary(caller, blocks)
                    vectors = embed_many(
                        [item["title"] + " " + summary]
                        + [item["title"] + " " + b.heading_path + " " + b.text for b in blocks],
                        caller=caller,
                    )
                    return summary, summary_kind, vectors[0], vectors[1:]
                except ApiError:
                    # One failed call would otherwise discard the whole build.
                    attempt += 1
                    if attempt == ATTEMPTS:
                        raise
                    time.sleep(2**attempt)

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            represented = list(pool.map(represent, pending))
        # Replace derived rows only after conversion/model preparation succeeds.
        with unit_of_work(sessions) as session:
            if rebuild:
                # Versions that failed to convert this time keep their previous search rows.
                storage.clear_index(
                    session,
                    list(records),
                    preserve_versions=True,
                    preserve_facets=True,
                    versions=[(item["item"], version["version"]) for item, version, _ in pending],
                )
            for (item, version, blocks), (summary, summary_kind, doc_vector, chunk_vectors) in zip(
                pending, represented, strict=True
            ):
                dirty.add(item["mission_key"])
                statement = insert(m.doc_versions).values(
                    item=item["item"],
                    version=version["version"],
                    sha256=bytes.fromhex(version["sha256"]),
                    media_type=version.get("media_type")
                    or mimetypes.guess_type(item["path"])[0]
                    or "text/plain",
                    summary=summary,
                    summary_kind=summary_kind,
                )
                updates = {
                    key: getattr(statement.excluded, key)
                    for key in ("sha256", "media_type", "summary", "summary_kind")
                }
                for key in ("ocr_text", "ocr_model", "ocr_updated_at"):
                    updates[key] = case(
                        (
                            m.doc_versions.c.sha256 == statement.excluded.sha256,
                            getattr(m.doc_versions.c, key),
                        ),
                        else_=None,
                    )
                document = session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[m.doc_versions.c.item, m.doc_versions.c.version],
                        set_=updates,
                    ).returning(m.doc_versions.c.id)
                ).scalar_one()
                if doc_vector is not None:
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
        if enrich_ocr:
            from app.library.enrichment import enqueue_version_jobs

            with unit_of_work(sessions, read_only=True) as session:
                missing_ocr = list(
                    session.execute(
                        select(m.doc_versions.c.item, m.doc_versions.c.version)
                        .join(m.provider_items, m.provider_items.c.item == m.doc_versions.c.item)
                        .where(
                            m.doc_versions.c.item.in_(records),
                            m.doc_versions.c.ocr_text.is_(None),
                            m.provider_items.c.deleted_at.is_(None),
                            (
                                (m.doc_versions.c.media_type == "application/pdf")
                                | m.doc_versions.c.media_type.like("image/%")
                            ),
                            ~select(m.chunks.c.id)
                            .where(m.chunks.c.doc_version_id == m.doc_versions.c.id)
                            .exists(),
                        )
                    ).tuples()
                )
            missing_ocr = [
                (item, version)
                for item, version in missing_ocr
                if version == records[item]["versions"][-1]["version"]
            ]
            if missing_ocr:
                counts["ocr_jobs_enqueued"] += enqueue_version_jobs(settings, missing_ocr)
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
        try:
            if lock is not None:
                try:
                    lock.execute(text("SELECT pg_advisory_unlock(1129464134, 1)"))
                finally:
                    lock.close()
        finally:
            engine.dispose()
    return dict(counts)
