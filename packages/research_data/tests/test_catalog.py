import hashlib
import sqlite3
import tempfile
import threading
import unittest
from typing import Any

from research_data.catalog import Catalog, CatalogError, CorruptSnapshot, ReviewRequired


def updated(arguments: dict[str, Any], **extra: Any) -> dict[str, Any]:
    result = arguments.copy()
    result.update(extra)
    return result


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.catalog = Catalog(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, data=b'[{"match_id":1,"winner":"A"}]', **kwargs):
        args: dict[str, Any] = dict(
            namespace="sports",
            dataset_id="opendota-pro",
            kind="results",
            source_url="https://example.invalid/public.json",
            provenance="public fetch",
            license_status="reviewed",
            data=data,
            format="json",
            fetched="unknown",
            published="unknown",
            observed="2025-01-01T00:00:00Z",
        )
        args.update(kwargs)
        return self.catalog.add(**args)

    def test_duplicate_bytes_preserve_distinct_provenance_and_restart(self):
        payload = b'[{"match_id":1,"winner":"A"}]'
        first = self.add(payload)
        second = self.add(
            payload, source_url="https://example.invalid/again", provenance="second fetch"
        )
        self.assertEqual(first, second)
        reopened = Catalog(self.root)
        with reopened._connect() as con:
            rows = con.execute(
                "SELECT source_url,provenance FROM acquisitions ORDER BY id"
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("https://example.invalid/public.json", "public fetch"),
                ("https://example.invalid/again", "second fetch"),
            ],
        )

    def test_changed_schema_requires_review_and_no_blob_leak(self):
        self.add()
        changed = b'[{"match_id":2,"score":3}]'
        leaked = self.catalog.blobs / hashlib.sha256(changed).hexdigest()
        with self.assertRaises(ReviewRequired):
            self.add(changed)
        self.assertFalse(leaked.exists())

    def test_csv_and_json_shape_validation(self):
        self.add(b"id,score\n1,2\n", format="csv", dataset_id="csv-results")
        for data, fmt in (
            (b'{"not":"an array"}', "json"),
            (b"id,id\n1,2\n", "csv"),
            (b"id,\n1,2\n", "csv"),
        ):
            with self.assertRaises(CatalogError):
                self.add(data, format=fmt, dataset_id="invalid-" + fmt)

    def test_corruption_is_detected(self):
        digest = self.add()
        (self.catalog.blobs / digest).write_bytes(b"corrupt")
        with self.assertRaises(CorruptSnapshot):
            self.catalog.snapshot(digest)
        with self.assertRaises(CorruptSnapshot):
            self.add()

    def test_time_and_url_validation(self):
        self.add(fetched="unknown", published="unknown", observed="unknown")
        with self.assertRaises(CatalogError):
            self.add(observed="2025-01-01T00:00:00")
        with self.assertRaises(CatalogError):
            self.add(source_url="http://example.invalid/x")
        with self.assertRaises(CatalogError):
            self.add(source_url="https://u:p@example.invalid/x")
        with self.assertRaises(CatalogError):
            self.add(source_url="https://example.invalid/x?token=abc")

    def test_derived_recipe_required_and_namespaces_are_distinct(self):
        data = b'[{"x":1}]'
        with self.assertRaises(CatalogError):
            self.add(data, derived=True)
        self.add(data)
        source_id = self.catalog.acquisitions()[0]["id"]
        self.add(
            data,
            namespace="finance",
            dataset_id="same-id",
            kind="odds",
            derived=True,
            recipe="normalize raw prices; version 1",
            source_ids=(source_id,),
        )
        self.add(data, namespace="sports", dataset_id="same-id", kind="results")
        with self.catalog._connect() as con:
            rows = con.execute(
                "SELECT namespace,kind,recipe FROM acquisitions "
                "WHERE dataset_id='same-id' ORDER BY id"
            ).fetchall()
        self.assertEqual(
            rows,
            [("finance", "odds", "normalize raw prices; version 1"), ("sports", "results", None)],
        )

    def test_concurrent_duplicate_acquisitions(self):
        out = []
        err = []

        def worker(i):
            try:
                out.append(self.add(source_url=f"https://example.invalid/{i}", provenance=str(i)))
            except Exception as exc:  # pragma: no cover
                err.append(exc)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertFalse(err)
        self.assertEqual(len(set(out)), 1)
        with self.catalog._connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM acquisitions").fetchone()[0], 2)

    def test_recording_failure_cleans_unreferenced_blob(self):
        data = b'[{"new":1}]'
        digest = hashlib.sha256(data).hexdigest()
        with self.catalog._connect() as con:
            con.execute(
                "CREATE TRIGGER fail_insert BEFORE INSERT ON acquisitions "
                "BEGIN SELECT RAISE(ABORT, 'nope'); END;"
            )
        with self.assertRaises(sqlite3.DatabaseError):
            self.add(data, dataset_id="will-fail")
        self.assertFalse((self.catalog.blobs / digest).exists())


if __name__ == "__main__":
    unittest.main()


class CatalogBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Catalog(self.temp.name)
        self.kwargs: dict[str, Any] = dict(
            namespace="sports",
            dataset_id="prices",
            kind="odds",
            source_url="https://example.invalid/data",
            provenance="fixture",
            license_status="reviewed",
            data=b'[{"price":2}]',
            format="json",
            fetched="2026-01-01T00:00:00Z",
            published="unknown",
            observed="unknown",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_connection_is_closed_after_context(self):
        with self.catalog._connect() as connection:
            connection.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_blob_symlink_never_reads_external_source(self):
        from pathlib import Path

        digest = self.catalog.add(**self.kwargs)
        blob = self.catalog.blobs / digest
        blob.unlink()
        target = Path(self.temp.name) / "external"
        target.write_bytes(self.kwargs["data"])
        blob.symlink_to(target)
        with self.assertRaises(CorruptSnapshot):
            self.catalog.snapshot(digest)
        with self.assertRaises(CorruptSnapshot):
            self.catalog.add(**self.kwargs)
        self.assertEqual(target.read_bytes(), self.kwargs["data"])

    def test_url_credentials_rejected_and_query_encoding_preserved(self):
        for url in [
            "https://example.invalid/?api_key=private",
            "https://example.invalid/?X-Amz-Signature=private",
        ]:
            with self.assertRaises(CatalogError):
                self.catalog.add(**(updated(self.kwargs, source_url=url)))
        url = "https://example.invalid/data?name=a%26b&token_id=123"
        self.catalog.add(**(updated(self.kwargs, source_url=url)))
        with self.catalog._connect() as connection:
            stored = connection.execute("SELECT source_url FROM acquisitions").fetchone()[0]
        self.assertEqual(stored, url)

    def test_storage_failure_rolls_back_metadata(self):
        from unittest.mock import patch

        with patch.object(self.catalog, "_store_blob", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.catalog.add(**self.kwargs)
        with self.catalog._connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM acquisitions").fetchone()[0], 0
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 0)

    def test_public_records_preserve_counts_and_source_identity(self):
        digest = self.catalog.add(**self.kwargs)
        records = self.catalog.acquisitions(namespace="sports", dataset_id="prices")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["sha256"], digest)
        self.assertEqual(records[0]["row_count"], 1)
        self.assertEqual(records[0]["byte_count"], len(self.kwargs["data"]))
        self.assertEqual(records[0]["format"], "json")
        self.assertEqual(records[0]["shape"]["keys"], ["price"])
        self.assertEqual(self.catalog.acquisitions(namespace="finance"), [])


class CatalogLineageTests(unittest.TestCase):
    def test_derived_records_require_real_parent_and_keep_lineage(self):
        with tempfile.TemporaryDirectory() as root:
            catalog = Catalog(root)
            args: dict[str, Any] = dict(
                namespace="finance",
                dataset_id="raw",
                kind="odds",
                source_url="https://example.invalid/data",
                provenance="fixture",
                license_status="reviewed",
                data=b'[{"price":2}]',
                format="json",
                fetched="unknown",
                published="unknown",
                observed="unknown",
            )
            catalog.add(**args)
            source_id = catalog.acquisitions()[0]["id"]
            derived: dict[str, Any] = args | dict(
                dataset_id="derived", derived=True, recipe="normalize-v1"
            )
            with self.assertRaises(CatalogError):
                catalog.add(**derived)
            with self.assertRaises(CatalogError):
                catalog.add(**(updated(derived, source_ids=(999,))))
            catalog.add(**(updated(derived, source_ids=(source_id,))))
            record = catalog.acquisitions(dataset_id="derived")[0]
            self.assertEqual(record["source_ids"], [source_id])
            self.assertEqual(record["recipe"], "normalize-v1")


class PointerTests(unittest.TestCase):
    def test_lfs_pointer_is_not_a_csv_dataset(self):
        from research_data.catalog import _shape

        pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 10\n"
        with self.assertRaises(CatalogError):
            _shape(pointer, "csv")
