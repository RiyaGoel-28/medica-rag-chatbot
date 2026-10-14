import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from document_registry import delete_document_record, expired_documents
from medical_rag import _get_retriever, _required_settings


def cleanup_expired_documents(database_path):
    load_dotenv()
    settings = _required_settings(require_groq=False)
    expired = expired_documents(database_path)
    cleaned = 0
    for record in expired:
        _get_retriever(settings, record["namespace"]).delete(
            filter={"document_id": record["document_id"]},
            namespace=record["namespace"],
        )
        try:
            Path(record["stored_path"]).unlink()
        except FileNotFoundError:
            pass
        delete_document_record(database_path, record["owner_key"], record["document_id"])
        cleaned += 1
    return cleaned


def main():
    parser = argparse.ArgumentParser(description="Remove expired user PDFs and Pinecone vectors.")
    parser.add_argument(
        "--database",
        default=os.path.join("instance", "documents.sqlite3"),
        help="Path to the SQLite document registry",
    )
    args = parser.parse_args()
    try:
        count = cleanup_expired_documents(args.database)
    except Exception as exc:
        parser.error(str(exc))
    print(f"Removed {count} expired document(s).")


if __name__ == "__main__":
    main()