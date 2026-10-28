"""CRAFT revisions are immutable full copies that always show their source version."""

import asyncio
import hashlib

import pytest

from app.infrastructure.database import metadata as m
from app.infrastructure.errors import NotFound
from app.infrastructure.provider import get_provider
from app.library.handlers import (
    LibrarySearch,
    RevisionList,
    RevisionRef,
    RevisionUpload,
    create_revision,
    export_revision,
    list_revisions,
    read_revision,
    search,
)
from app.tests.support.cube import cube_source as cube_source


@pytest.fixture
def revisions(cube_source):
    yield cube_source
    with cube_source["owner"].begin() as connection:
        connection.execute(
            m.document_revisions.delete().where(
                m.document_revisions.c.author_id == cube_source["caller"].user_id
            )
        )


def test_a_revision_keeps_its_source_version_and_the_source_is_unchanged(revisions):
    caller = revisions["caller"]
    (hit,) = search(caller, LibrarySearch(query="power", kinds=["chunk"], limit=1)).hits
    item, version = hit.handle.item, hit.handle.version
    provider = get_provider(revisions["settings"].provider_root)
    original = provider.read_content(item, version)

    edited = b"CRAFT's edited copy"
    first = asyncio.run(
        create_revision(
            caller,
            RevisionUpload(
                item=item,
                version=version,
                content=edited,
                file_name="edit.md",
                media_type="text/markdown",
            ),
        )
    )
    second = asyncio.run(
        create_revision(
            caller,
            RevisionUpload(
                item=item,
                version=version,
                content=b"again",
                file_name="edit.md",
                media_type="text/markdown",
            ),
        )
    )
    assert (first.revision, second.revision) == (1, 2)
    assert first.source_version == version and first.author_id == caller.user_id
    assert first.content_sha256 == hashlib.sha256(edited).hexdigest()
    assert [r.revision for r in list_revisions(caller, RevisionList(item=item))] == [1, 2]
    assert read_revision(caller, RevisionRef(item=item, revision=1)) == first
    exported = export_revision(caller, RevisionRef(item=item, revision=1))
    assert exported.body == edited
    assert exported.headers["content-disposition"].startswith("attachment")
    assert provider.read_content(item, version) == original
    with pytest.raises(NotFound):
        read_revision(caller, RevisionRef(item=item, revision=9))
    with pytest.raises(NotFound):
        asyncio.run(
            create_revision(
                caller,
                RevisionUpload(
                    item=item,
                    version=99,
                    content=b"x",
                    file_name="x.md",
                    media_type="text/markdown",
                ),
            )
        )
