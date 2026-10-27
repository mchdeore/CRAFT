"""Load generated provider files straight into the library tables, with no model calls.

The library index is frozen until datacube V2; tests that need source rows load them here.
"""

import hashlib
import mimetypes
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.database import metadata as m
from app.infrastructure.provider.simulated import SimulatedProvider
from app.library.convert import convert

COLUMNS = ["item", "path", "title", "mission_key", "silo", "stage", "simulated", "classification"]


def load(engine: Engine, provider: SimulatedProvider) -> None:
    """Write provider items, versions and chunks; summaries stay empty extracts."""
    provider.scan()
    with engine.begin() as connection:
        for record in provider.items().values():
            columns = {key: record[key] for key in COLUMNS}
            deleted = record["deleted_at"]
            columns["deleted_at"] = datetime.fromisoformat(deleted) if deleted else None
            connection.execute(
                insert(m.provider_items)
                .values(**columns)
                .on_conflict_do_update(index_elements=[m.provider_items.c.item], set_=columns)
            )
            for version in record["versions"]:
                content = provider.read_content(record["item"], version["version"])
                blocks = convert(content, version.get("file_name", record["path"]))
                document = connection.execute(
                    insert(m.doc_versions)
                    .values(
                        item=record["item"],
                        version=version["version"],
                        sha256=bytes.fromhex(version["sha256"]),
                        media_type=version.get("media_type")
                        or mimetypes.guess_type(record["path"])[0]
                        or "text/plain",
                        summary="",
                        summary_kind="extract",
                    )
                    .on_conflict_do_nothing()
                    .returning(m.doc_versions.c.id)
                ).scalar_one_or_none()
                if document is None:
                    continue
                for block in blocks:
                    connection.execute(
                        m.chunks.insert().values(
                            doc_version_id=document,
                            block=block.block,
                            kind=block.kind,
                            heading_path=block.heading_path,
                            locator=block.locator,
                            text=block.text,
                            text_sha256=hashlib.sha256(block.text.encode()).digest(),
                        )
                    )
