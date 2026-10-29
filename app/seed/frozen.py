"""Check a frozen dataset set against its manifest: its files before a restore, its rows after.

A set is a private folder (`.local/seed/<name>/`, never committed) holding `craft_dev.dump`,
`provider.tgz`, `embedding` and `manifest.json`. The manifest records each file's SHA-256, the
migration the dump was taken at, the embedding model, every dataset table's row count, the
tables that must be empty (call log, sessions, tokens, credentials) and each tag family's values.

`scripts/seed` runs this module. It only reads, as the owner login migrations use, and prints
table names, counts and file names, never row content:

    python -m app.seed.frozen files <set>      every listed file matches its SHA-256
    python -m app.seed.frozen restored <set>   just restored: counts, tags and the empty tables
    python -m app.seed.frozen verify <set>     in use: counts and tags only (the call log and
                                               credentials grow with use)
"""

import hashlib
import sys
from pathlib import Path

import msgspec
from sqlalchemy import func, select, table
from sqlalchemy.orm import Session

from app.infrastructure.database import metadata as m


class Embedding(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    model: str
    dimensions: int


class Manifest(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """`manifest.json`: written once when a set is frozen, read by every check."""

    name: str
    frozen: str
    source: str = msgspec.field(name="from")
    note: str
    migration: str
    embedding: Embedding
    files: dict[str, str]
    rows: dict[str, int]
    empty: list[str]
    tags: dict[str, list[str]]
    tag_registry: dict[str, str]


# The files scripts/seed restores; a manifest must list both.
RESTORED_FILES = ("craft_dev.dump", "provider.tgz")

# Each tag family's values, read from the table or column that defines them.
TAGS = {
    "missions": select(m.missions.c.key),
    "silos": select(m.silos.c.key),
    "stages": select(m.stages.c.key),
    "facets": select(m.facet_texts.c.facet).distinct(),
    "classification": select(m.provider_items.c.classification).distinct(),
}


def read(directory: Path) -> Manifest:
    """The set's manifest; a missing or malformed one stops the check."""
    return msgspec.json.decode((directory / "manifest.json").read_bytes(), type=Manifest)


def file_problems(directory: Path, manifest: Manifest) -> list[str]:
    """One line per file that is unlisted, missing or has another SHA-256; empty if all match."""
    problems = [
        f"{name}: not listed in the manifest"
        for name in RESTORED_FILES
        if name not in manifest.files
    ]
    for name, expected in sorted(manifest.files.items()):
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f"{name}: SHA-256 differs from the manifest")
    return problems


def snapshot(session: Session, tables: list[str]) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Row counts of these tables and every tag family's sorted values, as a manifest holds them."""
    rows = {
        name: session.execute(
            select(func.count()).select_from(table(name, schema="public"))
        ).scalar_one()
        for name in tables
    }
    tags = {family: sorted(session.execute(query).scalars()) for family, query in TAGS.items()}
    return rows, tags


def database_problems(
    session: Session, manifest: Manifest, *, restored: bool
) -> tuple[dict[str, int], list[str]]:
    """The counts read, and one line per count or tag family that differs or is not listed.

    Just after a restore (`restored`), the tables the manifest lists as empty must be empty too.
    """
    empty = manifest.empty if restored else []
    rows, tags = snapshot(session, [*manifest.rows, *empty])
    problems = ["rows: the manifest lists no table"] if not manifest.rows else []
    problems += [
        f"{name}: {rows[name]} rows, expected {count}"
        for name, count in manifest.rows.items()
        if rows[name] != count
    ]
    problems += [f"{name}: {rows[name]} rows, expected none" for name in empty if rows[name]]
    problems += [
        f"{family}: tag values differ from the manifest"
        for family, values in sorted(manifest.tags.items())
        if tags.get(family) != sorted(values)
    ]
    problems += [
        f"{family}: tag values not in the manifest"
        for family in TAGS
        if family not in manifest.tags
    ]
    return rows, problems


def check_database(manifest: Manifest, *, restored: bool) -> list[str]:
    """Compare the database in the settings, read-only, and print each table's count."""
    from app.infrastructure.config.settings import Settings
    from app.infrastructure.database.engine import make_owner_sessions
    from app.infrastructure.database.unit_of_work import unit_of_work

    sessions, engine = make_owner_sessions(Settings.from_env())
    try:
        with unit_of_work(sessions, read_only=True) as session:
            rows, problems = database_problems(session, manifest, restored=restored)
    finally:
        engine.dispose()
    for name, count in rows.items():
        print(f"  {name:<18} {count}")
    return problems


def main(argv: list[str] | None = None) -> None:
    """Run one check (files, restored or verify) on a set folder; exit non-zero on a mismatch."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"files", "restored", "verify"}:
        raise SystemExit("usage: python -m app.seed.frozen files|restored|verify <set folder>")
    directory = Path(args[1])
    manifest = read(directory)
    if args[0] == "files":
        problems = file_problems(directory, manifest)
    else:
        problems = check_database(manifest, restored=args[0] == "restored")
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    if problems:
        raise SystemExit(f"Does not match {manifest.name}'s manifest")
    print(f"Matches {manifest.name}'s manifest ({args[0]})")


if __name__ == "__main__":
    main()
