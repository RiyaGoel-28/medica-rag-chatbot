from datetime import datetime, timedelta, timezone
import sqlite3

from document_registry import (
    delete_document_record,
    expired_documents,
    initialize_registry,
    list_documents,
    upsert_document,
)
import cleanup_expired


def test_registry_lists_documents_only_for_owner_and_hides_expired(tmp_path):
    database = tmp_path / "documents.sqlite3"
    initialize_registry(database)
    upsert_document(database, "alice", "ns-alice", "doc-a", "a.pdf", "a", 2, 90)
    upsert_document(database, "bob", "ns-bob", "doc-b", "b.pdf", "b", 3, 90)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE documents SET expires_at = ? WHERE document_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), "doc-a"),
        )

    assert list_documents(database, "alice") == []
    assert [document["filename"] for document in list_documents(database, "bob")] == ["b.pdf"]
    assert [record["document_id"] for record in expired_documents(database)] == ["doc-a"]


def test_delete_record_is_scoped_to_owner(tmp_path):
    database = tmp_path / "documents.sqlite3"
    initialize_registry(database)
    upsert_document(database, "alice", "ns-alice", "same-id", "a.pdf", "a", 2, 90)
    upsert_document(database, "bob", "ns-bob", "same-id", "b.pdf", "b", 3, 90)

    delete_document_record(database, "alice", "same-id")

    assert list_documents(database, "alice") == []
    assert [document["filename"] for document in list_documents(database, "bob")] == ["b.pdf"]


def test_expiry_cleanup_deletes_vectors_in_record_namespace(monkeypatch, tmp_path):
    database = tmp_path / "documents.sqlite3"
    initialize_registry(database)
    local_pdf = tmp_path / "expired.pdf"
    local_pdf.write_bytes(b"pdf")
    upsert_document(
        database, "alice", "ns-alice", "doc-expired", "expired.pdf", str(local_pdf), 2, 1
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE documents SET expires_at = ? WHERE document_id = ?",
            (expired_at, "doc-expired"),
        )
    captured = []

    class FakeRetriever:
        def delete(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(cleanup_expired, "load_dotenv", lambda: None)
    monkeypatch.setattr(cleanup_expired, "_required_settings", lambda require_groq=False: {})
    monkeypatch.setattr(
        cleanup_expired,
        "_get_retriever",
        lambda settings, namespace: captured.append({"namespace": namespace}) or FakeRetriever(),
    )

    assert cleanup_expired.cleanup_expired_documents(database) == 1
    assert captured[0] == {"namespace": "ns-alice"}
    assert captured[1] == {
        "filter": {"document_id": "doc-expired"},
        "namespace": "ns-alice",
    }
    assert not local_pdf.exists()
    assert list_documents(database, "alice") == []