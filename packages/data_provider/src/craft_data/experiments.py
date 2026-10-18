from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from craft_data.embeddings import FakeEmbedding, cosine_similarity


@dataclass(frozen=True)
class QuerySpec:
    name: str
    text: str
    principal: str
    expected_ids: tuple[str, ...]


def _embed(text: str, dims: int = 8) -> tuple[float, ...]:
    return FakeEmbedding.embed(text, dimensions=dims)


def _cos(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return cosine_similarity(a, b)


def _setup(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE documents(document_id TEXT PRIMARY KEY,
            mission TEXT NOT NULL,
            text TEXT NOT NULL,
            label TEXT NOT NULL);
        CREATE TABLE mission_grants(principal TEXT NOT NULL,
            mission TEXT NOT NULL,
            can_read INTEGER NOT NULL,
            PRIMARY KEY(principal,
            mission));
        CREATE TABLE representations(representation_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            rep_recipe TEXT NOT NULL,
            text TEXT NOT NULL);

        CREATE TABLE poly_vectors(vector_id TEXT PRIMARY KEY,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            recipe TEXT NOT NULL,
            vector_json TEXT NOT NULL);

        CREATE TABLE norm_payloads(payload_id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            recipe TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            UNIQUE(model,
            dimensions,
            recipe,
            text_hash));
        CREATE TABLE norm_embeddings(embedding_id TEXT PRIMARY KEY,
            representation_id TEXT NOT NULL REFERENCES representations(representation_id),
            payload_id TEXT NOT NULL REFERENCES norm_payloads(payload_id),
            UNIQUE(representation_id,
            payload_id));
        CREATE TABLE norm_similarity(
            source_embedding_id TEXT REFERENCES norm_embeddings(embedding_id),
            target_embedding_id TEXT REFERENCES norm_embeddings(embedding_id),
            model TEXT,
            dimensions INTEGER,
            recipe TEXT);
        CREATE TRIGGER norm_similarity_contract BEFORE INSERT ON norm_similarity BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM norm_embeddings ne
                JOIN norm_payloads np ON np.payload_id = ne.payload_id
                WHERE ne.embedding_id = NEW.source_embedding_id
            AND np.model = NEW.model
            AND np.dimensions = NEW.dimensions
            AND np.recipe = NEW.recipe
            ) THEN RAISE(ABORT, 'source embedding contract mismatch') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM norm_embeddings ne
                JOIN norm_payloads np ON np.payload_id = ne.payload_id
                WHERE ne.embedding_id = NEW.target_embedding_id
            AND np.model = NEW.model
            AND np.dimensions = NEW.dimensions
            AND np.recipe = NEW.recipe
            ) THEN RAISE(ABORT, 'target embedding contract mismatch') END;
        END;

        CREATE TABLE sep_embed_body_v1(embedding_id TEXT PRIMARY KEY,
            representation_id TEXT NOT NULL REFERENCES representations(representation_id),
            model TEXT NOT NULL CHECK(model='fake-v1'),
            dimensions INTEGER NOT NULL CHECK(dimensions=8),
            recipe TEXT NOT NULL CHECK(recipe='body_v1'),
            vector_json TEXT NOT NULL);
        CREATE TABLE sep_embed_summary_v1(embedding_id TEXT PRIMARY KEY,
            representation_id TEXT NOT NULL REFERENCES representations(representation_id),
            model TEXT NOT NULL CHECK(model='fake-v1'),
            dimensions INTEGER NOT NULL CHECK(dimensions=8),
            recipe TEXT NOT NULL CHECK(recipe='summary_v1'),
            vector_json TEXT NOT NULL);
        """
    )


def _seed(conn: sqlite3.Connection) -> None:
    docs = [
        ("d1", "alpha", "solar battery efficiency for grid storage", "energy"),
        ("d2", "alpha", "wind turbine maintenance and safety checklist", "energy"),
        ("d3", "beta", "hospital staffing and triage procedure", "health"),
        ("d4", "beta", "vaccine cold chain storage logistics", "health"),
        ("d5", "alpha", "budget forecast financing for grid upgrade", "finance"),
        ("d6", "beta", "public health budget and vaccine procurement", "finance"),
    ]
    grants = [
        ("reader_a", "alpha", 1),
        ("reader_a", "beta", 0),
        ("reader_all", "alpha", 1),
        ("reader_all", "beta", 1),
    ]
    conn.executemany("INSERT INTO documents VALUES (?, ?, ?, ?)", docs)
    conn.executemany("INSERT INTO mission_grants VALUES (?, ?, ?)", grants)

    for did, _, text, _ in docs:
        reps = [
            (f"{did}:body_v1", did, "body_v1", text),
            (f"{did}:summary_v1", did, "summary_v1", "summary " + text),
        ]
        conn.executemany("INSERT INTO representations VALUES (?, ?, ?, ?)", reps)
        for rid, _, recipe, rtext in reps:
            vec = json.dumps(_embed(rtext), separators=(",", ":"))
            conn.execute(
                "INSERT INTO poly_vectors VALUES (?, 'representation', ?, 'fake-v1', 8, ?, ?)",
                ("pv:" + rid, rid, recipe, vec),
            )
            th = hashlib.sha256(rtext.encode()).hexdigest()
            pid = "np:" + recipe + ":" + th
            conn.execute(
                "INSERT OR IGNORE INTO norm_payloads VALUES (?, 'fake-v1', 8, ?, ?, ?)",
                (pid, recipe, th, vec),
            )
            conn.execute("INSERT INTO norm_embeddings VALUES (?, ?, ?)", ("ne:" + rid, rid, pid))
            if recipe == "body_v1":
                conn.execute(
                    "INSERT INTO sep_embed_body_v1 VALUES (?, ?, 'fake-v1', 8, 'body_v1', ?)",
                    ("sb:" + rid, rid, vec),
                )
            else:
                conn.execute(
                    "INSERT INTO sep_embed_summary_v1 VALUES (?, ?, 'fake-v1', 8, 'summary_v1', ?)",
                    ("ss:" + rid, rid, vec),
                )


def _query(conn: sqlite3.Connection, q: QuerySpec, strategy: str, top_k: int = 3) -> dict:
    qv = _embed(q.text)
    t0 = time.perf_counter_ns()
    if strategy == "poly":
        sql = """
            SELECT d.document_id, r.representation_id, r.text, pv.vector_json
            FROM poly_vectors pv
            JOIN representations r ON r.representation_id = pv.owner_id
            AND pv.owner_type='representation'
            AND pv.recipe='body_v1'
            JOIN documents d ON d.document_id = r.document_id
            JOIN mission_grants g ON g.mission = d.mission
            WHERE g.principal=? AND g.can_read=1
        """
    elif strategy == "norm":
        sql = """
            SELECT d.document_id, r.representation_id, r.text, np.vector_json
            FROM norm_embeddings ne
            JOIN norm_payloads np ON np.payload_id = ne.payload_id AND np.recipe='body_v1'
            JOIN representations r ON r.representation_id = ne.representation_id
            JOIN documents d ON d.document_id = r.document_id
            JOIN mission_grants g ON g.mission = d.mission
            WHERE g.principal=? AND g.can_read=1
        """
    else:
        sql = """
            SELECT d.document_id, r.representation_id, r.text, se.vector_json
            FROM sep_embed_body_v1 se
            JOIN representations r ON r.representation_id = se.representation_id
            JOIN documents d ON d.document_id = r.document_id
            JOIN mission_grants g ON g.mission = d.mission
            WHERE g.principal=? AND g.can_read=1
        """
    rows = conn.execute(sql, (q.principal,)).fetchall()
    scored = []
    for did, _rid, text, vj in rows:
        kv = tuple(json.loads(vj))
        c = _cos(qv, kv)
        k = sum(1 for token in q.text.split() if token in text)
        if k:
            scored.append((did, k + c * 0.02))
    ids = tuple([d for d, _ in sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]])
    ms = (time.perf_counter_ns() - t0) / 1_000_000.0
    exp = set(q.expected_ids)
    got = set(ids)
    p = 0.0 if not got else len(exp & got) / len(got)
    r = 0.0 if not exp else len(exp & got) / len(exp)
    return {"ids": ids, "duration_ms": ms, "joins": sql.count("JOIN"), "precision": p, "recall": r}


def _integrity(conn: sqlite3.Connection) -> dict:
    out = {}
    conn.execute("SAVEPOINT integrity_probe")
    conn.execute(
        "INSERT INTO poly_vectors VALUES "
        "('dang','representation','missing','fake-v1',8,'body_v1','[0,0,0,0,0,0,0,0]')"
    )
    out["poly_dangling_allowed"] = True
    try:
        payload = conn.execute("SELECT payload_id FROM norm_payloads LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO norm_embeddings VALUES ('bad','missing',?)", (payload,))
        out["norm_dangling_allowed"] = True
    except sqlite3.IntegrityError:
        out["norm_dangling_allowed"] = False
    try:
        conn.execute(
            "INSERT INTO norm_similarity VALUES "
            "('ne:d1:body_v1','ne:d2:body_v1','fake-v1',8,'wrong_recipe')"
        )
        out["norm_contract_enforced"] = False
    except sqlite3.IntegrityError:
        out["norm_contract_enforced"] = True
    try:
        conn.execute(
            "INSERT INTO sep_embed_body_v1 VALUES "
            "('badsep','d1:body_v1','fake-v1',16,'body_v1','[]')"
        )
        out["separate_contract_enforced"] = False
    except sqlite3.IntegrityError:
        out["separate_contract_enforced"] = True
    conn.execute("ROLLBACK TO integrity_probe")
    conn.execute("RELEASE integrity_probe")
    return out


def run_experiment(db_path: str) -> dict:
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError("experiment database must be new")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _setup(conn)
    _seed(conn)
    queries = [
        QuerySpec("q_energy", "solar battery efficiency", "reader_a", ("d1",)),
        QuerySpec("q_health_blocked", "vaccine cold chain", "reader_a", ()),
        QuerySpec("q_budget", "budget forecast financing", "reader_all", ("d5", "d6")),
    ]
    results = {"poly": {}, "norm": {}, "separate": {}, "keyword": {}}
    for q in queries:
        results["poly"][q.name] = _query(conn, q, "poly")
        results["norm"][q.name] = _query(conn, q, "norm")
        results["separate"][q.name] = _query(conn, q, "separate")
        t0 = time.perf_counter_ns()
        rows = conn.execute(
            """
            SELECT d.document_id, d.text FROM documents d
            JOIN mission_grants g ON g.mission=d.mission
            WHERE g.principal=? AND g.can_read=1
            """,
            (q.principal,),
        ).fetchall()
        ranked = sorted(
            (
                (r["document_id"], sum(1 for t in q.text.split() if t in r["text"]))
                for r in rows
                if any(t in r["text"] for t in q.text.split())
            ),
            key=lambda x: x[1],
            reverse=True,
        )
        ids = tuple(d for d, _ in ranked[:3])
        exp = set(q.expected_ids)
        got = set(ids)
        results["keyword"][q.name] = {
            "ids": ids,
            "duration_ms": (time.perf_counter_ns() - t0) / 1_000_000.0,
            "joins": 1,
            "precision": 0.0 if not got else len(exp & got) / len(got),
            "recall": 0.0 if not exp else len(exp & got) / len(exp),
        }
    counts = {}
    for tbl in [
        "representations",
        "poly_vectors",
        "norm_payloads",
        "norm_embeddings",
        "sep_embed_body_v1",
        "sep_embed_summary_v1",
    ]:
        has_vectors = tbl in {
            "poly_vectors",
            "norm_payloads",
            "sep_embed_body_v1",
            "sep_embed_summary_v1",
        }
        byte_sum = "COALESCE(SUM(LENGTH(vector_json)),0)" if has_vectors else "0"
        c = conn.execute(f"SELECT COUNT(*) AS n, {byte_sum} AS b FROM {tbl}").fetchone()
        counts[tbl] = {"rows": c["n"], "vector_bytes": c["b"]}
    report = {
        "simulation": True,
        "embedding_note": "Deterministic fake vectors test plumbing, not model quality.",
        "queries": [
            {"name": q.name, "principal": q.principal, "expected_source_ids": q.expected_ids}
            for q in queries
        ],
        "results": results,
        "counts": counts,
        "recipe_vector_counts": {
            "body_v1": conn.execute(
                "SELECT COUNT(*) FROM representations WHERE rep_recipe='body_v1'"
            ).fetchone()[0],
            "summary_v1": conn.execute(
                "SELECT COUNT(*) FROM representations WHERE rep_recipe='summary_v1'"
            ).fetchone()[0],
        },
        "integrity": _integrity(conn),
    }
    conn.commit()
    conn.close()
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("db_path")
    args = parser.parse_args()
    print(json.dumps(run_experiment(args.db_path), indent=2, sort_keys=True))
