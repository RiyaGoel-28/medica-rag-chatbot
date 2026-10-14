import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


@contextmanager
def _connect(database_path):
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_registry(database_path):
    with _connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                owner_key TEXT NOT NULL,
                document_id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                filename TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (owner_key, document_id)
            )
            """
        )


def upsert_document(database_path, owner_key, namespace, document_id, filename, stored_path, chunk_count, retention_days):
    now = datetime.now(timezone.utc)
    uploaded_at = now.isoformat()
    expires_at = (now + timedelta(days=retention_days)).isoformat()
    with _connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO documents
                (owner_key, document_id, namespace, filename, stored_path, chunk_count, uploaded_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_key, document_id) DO UPDATE SET
                filename = excluded.filename,
                stored_path = excluded.stored_path,
                chunk_count = excluded.chunk_count,
                uploaded_at = excluded.uploaded_at,
                expires_at = excluded.expires_at
            """,
            (owner_key, document_id, namespace, filename, stored_path, chunk_count, uploaded_at, expires_at),
        )
    return {"uploaded_at": uploaded_at, "expires_at": expires_at}


def list_documents(database_path, owner_key):
    now = datetime.now(timezone.utc).isoformat()
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT document_id, filename, chunk_count, uploaded_at, expires_at
            FROM documents WHERE owner_key = ? AND expires_at > ? ORDER BY uploaded_at DESC
            """,
            (owner_key, now),
        ).fetchall()
    return [dict(row) for row in rows]


def get_document(database_path, owner_key, document_id):
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE owner_key = ? AND document_id = ?",
            (owner_key, document_id),
        ).fetchone()
    return dict(row) if row else None


def delete_document_record(database_path, owner_key, document_id):
    with _connect(database_path) as connection:
        connection.execute(
            "DELETE FROM documents WHERE owner_key = ? AND document_id = ?",
            (owner_key, document_id),
        )


def expired_documents(database_path, now=None):
    cutoff = (now or datetime.now(timezone.utc)).isoformat()
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM documents WHERE expires_at <= ? ORDER BY expires_at",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]