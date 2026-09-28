"""The hand-written Docker init SQL must match the SQLAlchemy models.

Loads infrastructure/postgres/init/{001,002,003}.sql into one fresh database
and models.Base.metadata into another, then compares tables/columns/indexes/
foreign keys. Catches drift between the Docker first-boot SQL and the ORM.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from models import Base
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
INIT = _ROOT / "infrastructure" / "postgres" / "init"


def _start_pg():
    pgserver = pytest.importorskip("pgserver")
    data_dir = tempfile.mkdtemp(prefix="metascale_parity_")
    server = pgserver.get_server(data_dir, cleanup_mode="stop")
    return server


def _db_uri(server, name: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    import psycopg

    base = server.get_uri()
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    parts = urlsplit(base)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _structure(uri: str) -> dict:
    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg://"))
    try:
        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public'"
                    )
                )
            }
            columns: dict[str, set] = {}
            for table in tables:
                rows = conn.execute(
                    text(
                        "SELECT column_name, is_nullable FROM "
                        "information_schema.columns WHERE table_schema='public' "
                        "AND table_name=:t"
                    ),
                    {"t": table},
                ).fetchall()
                columns[table] = {(r[0], r[1]) for r in rows}

            indexes: set[tuple] = set()
            idx_rows = conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public'")
            ).fetchall()
            for idxname, idxdef in idx_rows:
                cols = tuple(
                    r[0]
                    for r in conn.execute(
                        text(
                            "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                            "ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                            "WHERE i.indexrelid=CAST(:name AS regclass) ORDER BY a.attnum"
                        ),
                        {"name": idxname},
                    ).fetchall()
                )
                indexes.add(
                    (idxdef.split("ON ")[1].split()[0].strip('"'), cols, "UNIQUE" in idxdef)
                )

            fk_rows = conn.execute(
                text(
                    "SELECT tc.table_name, kcu.column_name, ccu.table_name AS ref "
                    "FROM information_schema.table_constraints tc "
                    "JOIN information_schema.key_column_usage kcu "
                    "  ON tc.constraint_name=kcu.constraint_name "
                    "JOIN information_schema.constraint_column_usage ccu "
                    "  ON ccu.constraint_name=tc.constraint_name "
                    "WHERE tc.constraint_type='FOREIGN KEY'"
                )
            ).fetchall()
            fks = {(r[0], r[1], r[2]) for r in fk_rows}
        return {"tables": tables, "columns": columns, "indexes": indexes, "fks": fks}
    finally:
        engine.dispose()


def test_init_sql_matches_models():
    server = _start_pg()
    try:
        sql_uri = _db_uri(server, "sqlbuilt")
        model_uri = _db_uri(server, "modelbuilt")

        engine = create_engine(
            sql_uri.replace("postgresql://", "postgresql+psycopg://"),
            isolation_level="AUTOCOMMIT",
        )
        with engine.connect() as conn:
            for fname in ("001_extensions.sql", "002_schema.sql", "003_indexes.sql"):
                conn.exec_driver_sql((INIT / fname).read_text())
        engine.dispose()

        engine = create_engine(model_uri.replace("postgresql://", "postgresql+psycopg://"))
        Base.metadata.create_all(engine)
        engine.dispose()

        a, b = _structure(sql_uri), _structure(model_uri)
        assert a["tables"] == b["tables"]
        assert a["columns"] == b["columns"]
        assert a["indexes"] == b["indexes"]
        assert a["fks"] == b["fks"]
    finally:
        server.cleanup()


def test_generated_shard_sql_contains_every_table():
    ddl = (
        _ROOT / "infrastructure" / "postgres" / "shards" / "shard-0" / "001_schema.sql"
    ).read_text()
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table}" in ddl, table
